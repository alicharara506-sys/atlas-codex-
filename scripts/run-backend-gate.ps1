$ErrorActionPreference = "Stop"
$deliveryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $deliveryRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Atlas backend environment is missing. Run scripts\setup-backend.ps1 first."
}

Push-Location (Join-Path $deliveryRoot "backend")
try {
    & $python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed." }
    & $python -m mypy app
    if ($LASTEXITCODE -ne 0) { throw "Strict mypy failed." }
    & $python -m coverage run -m unittest discover -s tests -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    & $python -m coverage report --fail-under=80
    if ($LASTEXITCODE -ne 0) { throw "Coverage is below 80 percent." }
} finally {
    Pop-Location
}

Write-Output "Atlas backend quality gate passed."
