$ErrorActionPreference = "Stop"
$deliveryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $deliveryRoot "backend"
$python = Join-Path $deliveryRoot ".venv\Scripts\python.exe"
$environmentFile = Join-Path $deliveryRoot ".env.infrastructure"
$postgresHostPort = 55432

if (-not (Test-Path -LiteralPath $python)) { throw "Run scripts\setup-backend.ps1 first." }
if (-not $env:DATABASE_URL) {
    if (-not (Test-Path -LiteralPath $environmentFile)) {
        throw "DATABASE_URL is not set and .env.infrastructure is unavailable."
    }
    $values = @{}
    foreach ($line in (Get-Content -LiteralPath $environmentFile)) {
        if ($line -match '^([^#=]+)=(.+)$') { $values[$matches[1]] = $matches[2] }
    }
    if (-not $values.ATLAS_POSTGRES_PASSWORD) { throw ".env.infrastructure is missing its PostgreSQL password." }
    $env:DATABASE_URL = "postgresql+psycopg://atlas:$($values.ATLAS_POSTGRES_PASSWORD)@127.0.0.1:$postgresHostPort/atlas"
}

Push-Location $backendRoot
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Atlas database migration failed." }
    & $python -m alembic current --check-heads
    if ($LASTEXITCODE -ne 0) { throw "Atlas database is not at the migration head." }
}
finally {
    Pop-Location
}

Write-Output "Atlas database migration is at head; no destructive downgrade was performed."
