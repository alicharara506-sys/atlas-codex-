$ErrorActionPreference = "Stop"
$deliveryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $deliveryRoot "docker-compose.infrastructure.yml"
$environmentFile = Join-Path $deliveryRoot ".env.infrastructure"

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
if (-not $docker) { throw "Docker is not installed." }
if (-not (Test-Path -LiteralPath $environmentFile)) { throw ".env.infrastructure does not exist." }

& $docker compose --env-file $environmentFile -f $composeFile stop
if ($LASTEXITCODE -ne 0) { throw "Unable to stop Atlas infrastructure." }
Write-Output "Atlas PostgreSQL and Redis containers are stopped. Persistent volumes were preserved."
