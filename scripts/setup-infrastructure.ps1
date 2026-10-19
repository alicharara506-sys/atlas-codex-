$ErrorActionPreference = "Stop"
$deliveryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $deliveryRoot "docker-compose.infrastructure.yml"
$environmentFile = Join-Path $deliveryRoot ".env.infrastructure"
$python = Join-Path $deliveryRoot ".venv\Scripts\python.exe"
$postgresHostPort = 55432
$redisHostPort = 56379

function Find-AtlasDocker {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

$docker = Find-AtlasDocker
if (-not $docker) {
    Write-Output "Docker Desktop is not installed."
    Write-Output "Install it from: https://docs.docker.com/desktop/setup/install/windows-install/"
    Write-Output "Start Docker Desktop, then run this command again."
    exit 2
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Atlas backend is not installed. Run scripts\setup-backend.ps1 first."
}

Write-Output "Checking Docker Desktop engine..."
$dockerInfo = & $docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    if ($dockerInfo) { $dockerInfo | Write-Output }
    throw "Docker Desktop is installed but its engine is not running."
}
Write-Output "Docker Desktop engine is ready."

function New-AtlasSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

if (-not (Test-Path -LiteralPath $environmentFile)) {
    $postgresPassword = New-AtlasSecret
    $redisPassword = New-AtlasSecret
    [System.IO.File]::WriteAllLines($environmentFile, @(
        "ATLAS_POSTGRES_PASSWORD=$postgresPassword",
        "ATLAS_REDIS_PASSWORD=$redisPassword"
    ))
}

$values = @{}
foreach ($line in [System.IO.File]::ReadAllLines($environmentFile)) {
    if ($line -match '^([^#=]+)=(.+)$') { $values[$matches[1]] = $matches[2] }
}
if (-not $values.ATLAS_POSTGRES_PASSWORD -or -not $values.ATLAS_REDIS_PASSWORD) {
    throw ".env.infrastructure is missing required passwords."
}

Write-Output "Starting PostgreSQL and Redis containers..."
& $docker compose --env-file $environmentFile -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { throw "Unable to start Atlas infrastructure containers." }

$env:ATLAS_TEST_DATABASE_URL = "postgresql+psycopg://atlas:$($values.ATLAS_POSTGRES_PASSWORD)@127.0.0.1:$postgresHostPort/atlas"
$env:ATLAS_TEST_REDIS_URL = "redis://:$($values.ATLAS_REDIS_PASSWORD)@127.0.0.1:$redisHostPort/0"
$env:DATABASE_URL = $env:ATLAS_TEST_DATABASE_URL
$env:REDIS_URL = $env:ATLAS_TEST_REDIS_URL

Write-Output "Waiting for PostgreSQL and Redis health checks..."
$ready = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    & $python -c "from app.models.database import Database; from app.core.infrastructure import RedisProbe; import os, sys; sys.exit(0 if Database(os.environ['DATABASE_URL']).healthy() and RedisProbe(os.environ['REDIS_URL']).healthy() else 1)"
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw "PostgreSQL and Redis did not become healthy within two minutes." }

Write-Output "PostgreSQL and Redis are healthy on loopback only."
Write-Output "Applying and validating non-destructive database migrations..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "validate-migrations.ps1")
if ($LASTEXITCODE -ne 0) { throw "The Atlas database migration gate failed." }
Write-Output "Running the complete backend gate against the real services..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run-backend-gate.ps1")
if ($LASTEXITCODE -ne 0) { throw "The real-service backend gate failed." }

Write-Output "Atlas local infrastructure and backend gate passed."
