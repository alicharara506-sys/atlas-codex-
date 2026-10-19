[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$terminalPath = "C:\Program Files\MetaTrader 5\terminal64.exe"
$configurationPath = Join-Path $PSScriptRoot "atlas-metatrader.ini"

if (-not (Test-Path -LiteralPath $terminalPath)) {
    throw "MetaTrader 5 was not found at $terminalPath"
}
if (-not (Test-Path -LiteralPath $configurationPath)) {
    throw "Atlas's MetaTrader startup configuration is missing: $configurationPath"
}

$running = @(
    Get-Process -Name "terminal64" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -ieq $terminalPath }
)

if ($running.Count -gt 0 -and -not $Restart) {
    Write-Host "MetaTrader 5 is already running." -ForegroundColor Yellow
    Write-Host "Run this launcher with -Restart to attach the Atlas exporter at startup." -ForegroundColor Cyan
    exit 0
}

if ($Restart) {
    foreach ($process in $running) {
        Stop-Process -Id $process.Id -Force
    }
    if ($running.Count -gt 0) {
        Start-Sleep -Seconds 2
    }
}

# MetaTrader's command-line parser can silently ignore quoted paths. Convert
# the configuration to its Windows short path so the argument contains no
# spaces and the startup Expert directive is always parsed.
$shortConfigurationPath = (
    & $env:ComSpec /d /c "for %I in (`"$configurationPath`") do @echo %~sI"
).Trim()
if (-not $shortConfigurationPath) {
    throw "Could not resolve MetaTrader's startup configuration path."
}
$argument = "/config:$shortConfigurationPath"
$terminal = Start-Process -FilePath $terminalPath -ArgumentList $argument -PassThru
Write-Host "MetaTrader 5 started with the Atlas read-only exporter (PID $($terminal.Id))." -ForegroundColor Green
Write-Host "The exporter cannot place, modify, or cancel orders." -ForegroundColor Cyan
