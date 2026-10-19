param(
  [string]$ArchivePath = "$env:USERPROFILE\Downloads\ollama-windows-amd64.zip"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$localRoot = Join-Path $projectRoot ".atlas-local-llm"
$runtimeDirectory = Join-Path $localRoot "runtime"
$modelsDirectory = Join-Path $localRoot "models"
$ollamaExecutable = Join-Path $runtimeDirectory "ollama.exe"
$expectedRuntimeSha256 = "7B4F6CE09C1F2C3B21561B323779BEAF3CA3C7012F8E4522605A13CBBB19F0B8"
$models = @(
  "ministral-3:3b",
  "llama3.2:1b-instruct-q3_K_L",
  "qwen2.5:3b",
  "phi4-mini"
)

if (-not (Test-Path -LiteralPath $ArchivePath)) {
  throw "Official Ollama archive not found at $ArchivePath"
}

$actualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash
if ($actualHash -ne $expectedRuntimeSha256) {
  throw "Ollama archive SHA-256 mismatch. Expected $expectedRuntimeSha256 but received $actualHash"
}

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $modelsDirectory -Force | Out-Null
Expand-Archive -LiteralPath $ArchivePath -DestinationPath $runtimeDirectory -Force
if (-not (Test-Path -LiteralPath $ollamaExecutable)) {
  throw "The verified archive did not contain $ollamaExecutable"
}

$serverEnvironment = @{
  OLLAMA_HOST = "127.0.0.1:11434"
  OLLAMA_MODELS = $modelsDirectory
  OLLAMA_KEEP_ALIVE = "-1"
  OLLAMA_MAX_LOADED_MODELS = "1"
  OLLAMA_NUM_PARALLEL = "1"
  OLLAMA_NOHISTORY = "true"
}

$temporaryServer = $null
$previousHost = $env:OLLAMA_HOST
$previousModels = $env:OLLAMA_MODELS
try {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null
  } catch {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $ollamaExecutable
    $startInfo.Arguments = "serve"
    $startInfo.WorkingDirectory = $runtimeDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    foreach ($entry in $serverEnvironment.GetEnumerator()) {
      $startInfo.EnvironmentVariables[$entry.Key] = $entry.Value
    }
    $temporaryServer = [System.Diagnostics.Process]::Start($startInfo)
    $deadline = (Get-Date).AddSeconds(45)
    do {
      Start-Sleep -Milliseconds 500
      try { $ready = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 } catch { $ready = $null }
    } until ($ready -or (Get-Date) -gt $deadline)
    if (-not $ready) { throw "The local Ollama bootstrap server did not start on port 11434." }
  }

  $env:OLLAMA_HOST = "127.0.0.1:11434"
  $env:OLLAMA_MODELS = $modelsDirectory
  foreach ($model in $models) {
    Write-Host "Pulling $model..."
    & $ollamaExecutable pull $model
    if ($LASTEXITCODE -ne 0) { throw "Ollama failed to pull $model" }
  }
  & $ollamaExecutable list
  if ($LASTEXITCODE -ne 0) { throw "Ollama could not verify the local model inventory." }
} finally {
  $env:OLLAMA_HOST = $previousHost
  $env:OLLAMA_MODELS = $previousModels
  if ($temporaryServer -and -not $temporaryServer.HasExited) {
    $temporaryServer.Kill()
    $temporaryServer.WaitForExit(10_000)
  }
}

Write-Host "Four local models are installed. Set LOCAL_LLM_ENABLED=true in .env.local and restart Atlas."
