[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

try {
    $terminalPath = "C:\Program Files\MetaTrader 5\terminal64.exe"
    $installRoot = Split-Path -Parent $terminalPath
    $sourceProfiles = Join-Path $installRoot "Profiles"
    $terminalDataRoot = Join-Path $env:APPDATA "MetaQuotes\Terminal"

    if (-not (Test-Path -LiteralPath $terminalPath)) {
        throw "MetaTrader 5 was not found at $terminalPath"
    }
    if (-not (Test-Path -LiteralPath $sourceProfiles)) {
        throw "The default MetaTrader Profiles folder is missing from $installRoot"
    }

    $dataDirectory = Get-ChildItem -LiteralPath $terminalDataRoot -Directory |
        Where-Object {
            $originFile = Join-Path $_.FullName "origin.txt"
            if (-not (Test-Path -LiteralPath $originFile)) {
                return $false
            }
            $origin = (Get-Content -LiteralPath $originFile -Raw).Trim().TrimEnd("\")
            return $origin -ieq $installRoot.TrimEnd("\")
        } |
        Select-Object -First 1

    if (-not $dataDirectory) {
        throw "Could not identify MetaTrader's active Windows data folder."
    }

    $targetProfiles = Join-Path $dataDirectory.FullName "Profiles"
    if (Test-Path -LiteralPath $targetProfiles) {
        Write-Host "The active Profiles folder already exists; no files were overwritten." -ForegroundColor Yellow
    }
    else {
        Write-Host "Restoring MetaTrader's default Profiles folder..." -ForegroundColor Cyan
        Copy-Item -LiteralPath $sourceProfiles -Destination $targetProfiles -Recurse
        Write-Host "Default profiles restored successfully." -ForegroundColor Green
    }

    Get-Process -Name "terminal64" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -ieq $terminalPath } |
        Stop-Process -Force

    Start-Sleep -Seconds 2
    Start-Process -FilePath $terminalPath -ArgumentList "/profile:Default"

    Write-Host "MetaTrader 5 is starting with the restored Default profile." -ForegroundColor Green
    Write-Host "This repair did not modify account credentials or place any trades." -ForegroundColor Cyan
    Read-Host "Press Enter after the MetaTrader window appears"
}
catch {
    Write-Host ""
    Write-Host "REPAIR FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Take a screenshot of this window and send it to Codex." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

