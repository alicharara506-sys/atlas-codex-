param(
    [string]$OutputRoot = "",
    [string]$AtlasUrl = "http://127.0.0.1:3003"
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "..\..\outputs"))
} else {
    $OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
}

$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$exportName = "ATLAS_FULL_DATA_EXPORT_$stamp"
$exportDir = Join-Path $OutputRoot $exportName
$sourceDir = Join-Path $exportDir "SOURCE"
$dataDir = Join-Path $exportDir "DATA"

New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

# Copy the complete authored project while excluding generated dependencies,
# build output, machine-local credentials, and compiler caches.
$rootFiles = Get-ChildItem -LiteralPath $projectRoot -Force -File | Where-Object {
    $_.Name -notin @(".env.local", "tsconfig.tsbuildinfo")
}
foreach ($file in $rootFiles) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $sourceDir $file.Name)
}
foreach ($directoryName in @("app", "scripts", "tests")) {
    $from = Join-Path $projectRoot $directoryName
    if (Test-Path -LiteralPath $from) {
        Copy-Item -LiteralPath $from -Destination (Join-Path $sourceDir $directoryName) -Recurse
    }
}

# Record configured environment variable names only. Values are deliberately
# excluded because the local bridge token and provider credentials are secrets.
$configurationKeys = @()
$envFile = Join-Path $projectRoot ".env.local"
$envValues = @{}
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            $envValues[$name] = $value
            $configurationKeys += "$name=<REDACTED>"
        }
    }
}
[System.IO.File]::WriteAllLines(
    (Join-Path $dataDir "CONFIGURATION_KEYS_REDACTED.txt"),
    ($configurationKeys | Sort-Object),
    [System.Text.UTF8Encoding]::new($false)
)

$symbols = @(
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
    "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL"
)
$timeframes = @("D1", "4H", "1H", "15M")
$capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")

# Capture every public Atlas analysis response. The route intentionally omits
# MT5 login/server and never returns bridge or provider credentials.
$analysisRows = @()
foreach ($symbol in $symbols) {
    try {
        $payload = Invoke-RestMethod -Uri "$AtlasUrl/api/atlas?symbol=$symbol" -TimeoutSec 30
        $analysisRows += [ordered]@{
            symbol = $symbol
            capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
            payload = $payload
        }
    } catch {
        $analysisRows += [ordered]@{
            symbol = $symbol
            capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
            error = $_.Exception.Message
        }
    }
}
$analysisExport = [ordered]@{
    schema = "atlas.analysis.snapshot.v1"
    capturedAtUtc = $capturedAtUtc
    source = $AtlasUrl
    timeSensitive = $true
    analyses = $analysisRows
}
[System.IO.File]::WriteAllText(
    (Join-Path $dataDir "ATLAS_ANALYSES_ALL_SYMBOLS.json"),
    ($analysisExport | ConvertTo-Json -Depth 30),
    [System.Text.UTF8Encoding]::new($false)
)

# Capture the actual closed-bar and recent-tick windows used by the local MT5
# engine. The bridge token is read in memory and is never written to the export.
$bridgePort = if ($envValues.ContainsKey("MT5_BRIDGE_PORT")) { $envValues["MT5_BRIDGE_PORT"] } else { "8765" }
$bridgeUrl = "http://127.0.0.1:$bridgePort"
$headers = @{}
if ($envValues.ContainsKey("MT5_BRIDGE_TOKEN") -and -not [string]::IsNullOrWhiteSpace($envValues["MT5_BRIDGE_TOKEN"])) {
    $headers["Authorization"] = "Bearer $($envValues["MT5_BRIDGE_TOKEN"])"
}

$marketRows = @()
foreach ($symbol in $symbols) {
    $symbolRow = [ordered]@{ symbol = $symbol; capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o") }
    try {
        $symbolRow["quote"] = Invoke-RestMethod -Headers $headers -Uri "$bridgeUrl/quote?symbol=$symbol" -TimeoutSec 30
        $symbolRow["contractSpecification"] = Invoke-RestMethod -Headers $headers -Uri "$bridgeUrl/symbol_info?symbol=$symbol" -TimeoutSec 30
        $bars = [ordered]@{}
        foreach ($timeframe in $timeframes) {
            $bars[$timeframe] = Invoke-RestMethod -Headers $headers -Uri "$bridgeUrl/rates?symbol=$symbol&timeframe=$timeframe&count=260" -TimeoutSec 30
        }
        $symbolRow["closedBars"] = $bars
        $symbolRow["recentTicks"] = Invoke-RestMethod -Headers $headers -Uri "$bridgeUrl/ticks?symbol=$symbol&count=2000" -TimeoutSec 30
    } catch {
        $symbolRow["error"] = $_.Exception.Message
    }
    $marketRows += $symbolRow
}
$marketExport = [ordered]@{
    schema = "atlas.mt5.market-snapshot.v1"
    capturedAtUtc = $capturedAtUtc
    source = "MT5 loopback bridge"
    closedBarsExcludeFormingBar = $true
    timeSensitive = $true
    symbols = $marketRows
}
[System.IO.File]::WriteAllText(
    (Join-Path $dataDir "MT5_MARKET_DATA_ALL_SYMBOLS.json"),
    ($marketExport | ConvertTo-Json -Depth 30),
    [System.Text.UTF8Encoding]::new($false)
)

$summaryLines = @()
foreach ($row in $analysisRows) {
    if ($row.Contains("error")) {
        $summaryLines += "| $($row.symbol) | ERROR | - | - | - | - |"
        continue
    }
    $payload = $row.payload
    $rr = if ($null -eq $payload.risk -or $null -eq $payload.risk.rewardToRiskRatio) { "-" } else { "{0:N2}R" -f [double]$payload.risk.rewardToRiskRatio }
    $summaryLines += "| $($row.symbol) | $($payload.effectiveAction) | $($payload.signal.direction) | $($payload.signal.score) | $($payload.signal.confidence) | $rr |"
}
$summaryTable = $summaryLines -join "`n"

$account = $analysisRows | Where-Object { -not $_.Contains("error") -and $null -ne $_.payload.account } | Select-Object -First 1
$accountText = if ($null -eq $account) {
    "Account metadata was unavailable at capture time."
} else {
    $a = $account.payload.account
    "Currency $($a.currency); balance $($a.balance); equity $($a.equity); free margin $($a.marginFree); leverage 1:$($a.leverage); mode $($a.tradeMode); trading permission $($a.tradeAllowed)."
}

$report = @"
# Atlas Quant - Complete Data Report

Generated: $capturedAtUtc  
Active application: $AtlasUrl  
Project: $projectRoot

## Export boundary

This package contains the complete authored source of the active Atlas application, all project documentation and tests, every current /api/atlas analysis response, 260 closed MT5 bars for every supported symbol and timeframe, 2,000 recent ticks per supported symbol where the broker returned them, quotes, broker contract specifications, and a SHA-256 file manifest.

Secrets are intentionally excluded: .env.local, bearer tokens, API keys, MT5 login, and broker server identifiers are not copied. Generated dependency/build folders (node_modules, .next, dist) and compiler caches are excluded because they can be reproduced from package-lock.json.

## Active system

- Product: local-first, read-only quantitative research terminal.
- Runtime: React 19, TypeScript 5.9, Vinext/Vite, Node.js 22+.
- Market provider: MetaTrader 5 loopback bridge; deterministic demo fallback exists.
- Execution: disabled. The API always reports readOnly: true and orderSubmission: false.
- Instruments: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD, EURGBP, XAUUSD, XAGUSD, USOIL, UKOIL.
- Timeframes: D1, 4H, 1H, 15M. Each live analysis uses 260 closed bars per timeframe.
- Signal timeframe: 1H, supported by D1/4H/1H/15M alignment.
- Browser persistence schema: IndexedDB atlas-trading-terminal, version 2; stores trades, settings, watchlists, and outcomes. Browser-private IndexedDB contents were not inspected or exported.

## Calculation path

MT5 closed bars + recent ticks -> canonical provider types -> indicators -> structure/regime -> multi-timeframe direction -> eight-factor score -> conditional pending filter -> broker risk sizing -> API/UI

The core calculation is deterministic. LLM text does not decide trade direction.

## Indicators and factors

Implemented indicators: SMA, EMA, RSI(14), ATR(14), ROC(10), 20-bar standard-deviation volatility, volume ratio, and directional-efficiency approximation. Structure is HH/HL, LH/LL, or mixed. Regimes include trend, range, breakout, reversal, high/low volatility, and unclear.

| Factor | Weight |
| --- | ---: |
| Structure | 20% |
| Trend | 15% |
| Momentum | 15% |
| Multi-timeframe alignment | 15% |
| Volatility | 10% |
| Volume | 10% |
| Support/resistance | 10% |
| Macro | 5% |

Unavailable macro data is excluded and the remaining weights are normalized; it is not silently scored as zero.

## Direction and conditional setups

Core direction requires absolute weighted trend lean of at least 0.15; three conflicting timeframes force WAIT. Timeframe direction weights are D1 40%, 4H 30%, 1H 20%, and 15M 10%.

When the core returns WAIT, a conditional BUY STOP or SELL STOP may be emitted only with LIVE data, score at least 45, absolute lean at least 0.04, no more than one conflict, aligned 1H structure, and no opposing order-flow pressure. Trigger is placed beyond the recent 20-bar structure plus 0.10 ATR; stop distance is 1.50 ATR; targets are 1R, 2R, and 3R; expiry is four hours. These setups are labeled CONDITIONAL_UNCALIBRATED and are not market orders.

## Risk and calibration

The active route sizes at 0.5% account risk, rounds down to the broker lot step, and warns when the result is below the broker minimum. Reward/risk is the dimensionless price-distance ratio. Historical probability is unavailable until at least 200 independently resolved eligible outcomes exist, including at least 40 wins, 40 losses, and 30 outcomes in a score segment.

## Live broker/account snapshot

$accountText

This is private, time-sensitive account metadata. The export contains no account login or broker server identifier.

## Live signal snapshot

Captured from the running Atlas API. Values will change with new MT5 bars and ticks.

| Symbol | Effective action | Core direction | Score | Confidence | R/R |
| --- | --- | --- | ---: | --- | ---: |
$summaryTable

## Implementation truth table

| Capability | Active status |
| --- | --- |
| Real MT5 closed candles and quotes | Implemented |
| 15M/1H/4H/D1 analysis | Implemented |
| LONG/SHORT/WAIT | Implemented |
| Conditional BUY STOP/SELL STOP | Implemented, uncalibrated |
| Entry, stop/invalidation, three targets, expiry | Implemented |
| Broker-aware risk sizing | Implemented |
| Tick-rule order-flow approximation | Implemented; not Level 2 order flow |
| Historical win probability | Evidence-gated; currently uncalibrated |
| Four-LLM reasoning committee | Not connected in the active 3003 code |
| Protected OpenAI analyst route | Mentioned in legacy documentation but absent from active source |
| Probability grades B/A/A+/A+++++ | Not implemented in the active source |
| Yahoo market-data adapter | Not implemented in the active source |
| Economic-calendar filter | Not implemented in the active source |
| Gann Box | Not implemented in the active source |
| Kelly Criterion | Not implemented in the active source |
| Bollinger Band Width | Not implemented in the active source |
| Verified market microstructure/liquidity | Not available from this OTC MT5 feed |
| Volume | Implemented using broker-reported tick volume |
| Automatic trade execution | Disabled |

## Included files

- DATA/ATLAS_ANALYSES_ALL_SYMBOLS.json: complete active API response for every supported instrument.
- DATA/MT5_MARKET_DATA_ALL_SYMBOLS.json: quotes, contract specifications, 260 closed bars per timeframe, and recent ticks.
- DATA/CONFIGURATION_KEYS_REDACTED.txt: configuration names only.
- SOURCE/: complete authored active application source, documentation, lockfile, and tests.
- FILE_MANIFEST_SHA256.csv: integrity hashes for every exported file.

## Safety note

Atlas is research software, not a financial adviser. A displayed setup is not proof of profitability. The current calibration field is null and conditional setups are explicitly uncalibrated. No order was placed while producing this export.
"@
[System.IO.File]::WriteAllText(
    (Join-Path $exportDir "ATLAS_COMPLETE_DATA_REPORT.md"),
    $report,
    [System.Text.UTF8Encoding]::new($false)
)

$manifestPath = Join-Path $exportDir "FILE_MANIFEST_SHA256.csv"
$manifestRows = Get-ChildItem -LiteralPath $exportDir -Recurse -File | Where-Object {
    $_.FullName -ne $manifestPath
} | ForEach-Object {
    $relative = $_.FullName.Substring($exportDir.TrimEnd("\").Length + 1)
    $hash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
    [pscustomobject]@{ Path = $relative; Bytes = $_.Length; SHA256 = $hash.Hash }
}
$manifestRows | Sort-Object Path | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding utf8

$zipPath = "$exportDir.zip"
Compress-Archive -LiteralPath $exportDir -DestinationPath $zipPath -CompressionLevel Optimal

[pscustomobject]@{
    ExportDirectory = $exportDir
    ZipFile = $zipPath
    ZipBytes = (Get-Item -LiteralPath $zipPath).Length
    ExportFiles = (Get-ChildItem -LiteralPath $exportDir -Recurse -File).Count
    CapturedAtUtc = $capturedAtUtc
} | ConvertTo-Json
