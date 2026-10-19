param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8800,
    [string]$Instruments = ""
)

$ErrorActionPreference = "Stop"
$deliveryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$environmentFile = Join-Path $deliveryRoot ".env.infrastructure"
$localEnvironmentFile = Join-Path $deliveryRoot ".env.local"
$python = Join-Path $deliveryRoot ".venv\Scripts\python.exe"
$postgresHostPort = 55432
$redisHostPort = 56379

if (-not (Test-Path -LiteralPath $python)) { throw "Run scripts\setup-backend.ps1 first." }
if (-not (Test-Path -LiteralPath $environmentFile)) { throw "Run scripts\setup-infrastructure.ps1 first." }

$values = @{}
foreach ($line in (Get-Content -LiteralPath $environmentFile)) {
    if ($line -match '^([^#=]+)=(.+)$') { $values[$matches[1]] = $matches[2] }
}
if (-not $values.ATLAS_POSTGRES_PASSWORD -or -not $values.ATLAS_REDIS_PASSWORD) {
    throw ".env.infrastructure is missing required passwords."
}

$env:DATABASE_URL = "postgresql+psycopg://atlas:$($values.ATLAS_POSTGRES_PASSWORD)@127.0.0.1:$postgresHostPort/atlas"
$env:REDIS_URL = "redis://:$($values.ATLAS_REDIS_PASSWORD)@127.0.0.1:$redisHostPort/0"

# The backend is a separate process from the UI server, so explicitly import
# only the V4 MT5 boundary settings it needs. Secrets are never printed.
if (Test-Path -LiteralPath $localEnvironmentFile) {
    $allowedMt5Settings = @(
        "MT5_ENABLED",
        "MT5_BRIDGE_BASE_URL",
        "MT5_BRIDGE_PORT",
        "MT5_BRIDGE_TOKEN",
        "MT5_EXPECTED_ACCOUNT_MODE",
        "MT5_TIMEOUT_SECONDS",
        "V4_AI_REVIEW_ENABLED",
        "V4_AI_CANDIDATE_THRESHOLD",
        "V4_ORCHESTRATION_ENABLED",
        "V4_ORCHESTRATION_INTERVAL_SECONDS",
        "V4_ORCHESTRATION_INSTRUMENTS",
        "V4_ORCHESTRATION_TRAIN_MODELS",
        "V4_ORCHESTRATION_AUTO_SHADOW",
        "ATLAS_ADMIN_TOKEN",
        "ATLAS_MAX_REQUEST_BYTES",
        "ATLAS_MUTATION_RATE_LIMIT"
    )
    foreach ($line in (Get-Content -LiteralPath $localEnvironmentFile)) {
        if ($line -match '^([^#=]+)=(.*)$') {
            $name = $matches[1].Trim()
            if ($allowedMt5Settings -contains $name) {
                [Environment]::SetEnvironmentVariable($name, $matches[2].Trim(), "Process")
            }
        }
    }
}

if ($Instruments.Trim()) {
    [Environment]::SetEnvironmentVariable(
        "V4_ORCHESTRATION_INSTRUMENTS",
        $Instruments.Trim(),
        "Process"
    )
}

& $python -c "from app.models.database import database_from_settings; from app.core.infrastructure import RedisProbe; from app.core.config import AtlasSettings; import sys; settings=AtlasSettings.from_env(); database=database_from_settings(settings); healthy=database.healthy() and RedisProbe(settings.redis_url).healthy(); sys.exit(0 if healthy else 2)"
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL or Redis is unavailable. Run scripts\setup-infrastructure.ps1 first."
}

Push-Location (Join-Path $deliveryRoot "backend")
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Atlas database migration failed; backend startup was stopped." }
}
finally {
    Pop-Location
}

Write-Output "Atlas backend starting at http://127.0.0.1:$Port"
& $python -m uvicorn app.main:app --app-dir (Join-Path $deliveryRoot "backend") --host 127.0.0.1 --port $Port
