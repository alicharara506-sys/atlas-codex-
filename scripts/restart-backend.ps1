param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8800
)

$ErrorActionPreference = "Stop"

function Get-ListenerProcessIds {
    param([int]$ListenerPort)

    $processIds = @(
        Get-NetTCPConnection -State Listen -LocalPort $ListenerPort -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess }
    )
    if ($processIds.Count -eq 0) {
        # Get-NetTCPConnection can return no rows in restricted Windows shells
        # even when netstat confirms a listener. Use a read-only, exact-port
        # fallback so the restart cannot race the old Atlas worker.
        $pattern = "^\s*TCP\s+\S+:$ListenerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
        $processIds = @(
            netstat -ano -p TCP |
                ForEach-Object {
                    if ($_ -match $pattern) { [int]$matches[1] }
                }
        )
    }
    return @($processIds | Sort-Object -Unique)
}

Write-Output "Checking Atlas backend on port $Port..."
$listenerProcessIds = @(Get-ListenerProcessIds -ListenerPort $Port)

if ($listenerProcessIds.Count -gt 1) {
    throw "More than one process is listening on port $Port. No process was stopped."
}

if ($listenerProcessIds.Count -eq 1) {
    $processId = [int]$listenerProcessIds[0]
    $process = Get-Process -Id $processId -ErrorAction Stop
    if ($process.ProcessName -notin @("python", "pythonw")) {
        throw "Port $Port belongs to '$($process.ProcessName)' (PID $processId), not Atlas Python. No process was stopped."
    }

    Write-Output "Stopping the current Atlas backend (PID $processId)..."
    try {
        Stop-Process -Id $processId -ErrorAction Stop
    }
    catch [System.NullReferenceException] {
        # Some restricted Windows hosts return a valid Process object but the
        # Stop-Process cmdlet fails internally. The already-validated exact
        # Python listener is safe to terminate through its Process instance.
        $process.Kill()
    }

    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ListenerProcessIds -ListenerPort $Port).Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
    if ((Get-ListenerProcessIds -ListenerPort $Port).Count -gt 0) {
        throw "The previous Atlas backend did not release port $Port within 15 seconds."
    }
}

Write-Output "Starting the updated Atlas background orchestration service..."
& (Join-Path $PSScriptRoot "start-backend.ps1") -Port $Port
