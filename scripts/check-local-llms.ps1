$ErrorActionPreference = "Stop"
$models = @(
  @{ Port = 11434; Model = "ministral-3:3b" },
  @{ Port = 11435; Model = "llama3.2:1b-instruct-q3_K_L" },
  @{ Port = 11436; Model = "qwen2.5:3b" },
  @{ Port = 11437; Model = "phi4-mini" }
)

$results = foreach ($item in $models) {
  $endpoint = "http://127.0.0.1:$($item.Port)"
  try {
    $version = Invoke-RestMethod -Uri "$endpoint/api/version" -TimeoutSec 3
    $tags = Invoke-RestMethod -Uri "$endpoint/api/tags" -TimeoutSec 5
    $installed = @($tags.models | ForEach-Object {
      $_.name -eq $item.Model -or $_.name -eq "$($item.Model):latest"
    }) -contains $true
    [pscustomobject]@{ Port = $item.Port; Model = $item.Model; Listening = $true; Installed = $installed; Version = $version.version }
  } catch {
    [pscustomobject]@{ Port = $item.Port; Model = $item.Model; Listening = $false; Installed = $false; Version = $null }
  }
}

$results | Format-Table -AutoSize
if (@($results | Where-Object { -not $_.Listening -or -not $_.Installed }).Count -gt 0) { exit 1 }
