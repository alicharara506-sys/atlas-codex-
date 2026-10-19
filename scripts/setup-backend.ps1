param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$deliveryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvRoot = Join-Path $deliveryRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the Atlas Python environment." }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Unable to update pip." }

& $venvPython -m pip install -e "$deliveryRoot\backend[dev]"
if ($LASTEXITCODE -ne 0) { throw "Unable to install Atlas backend dependencies." }

Write-Output "Atlas backend dependencies are installed."
Write-Output "Run scripts\run-backend-gate.ps1 before enabling Phase 2."
