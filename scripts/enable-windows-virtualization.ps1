[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Host "Requesting Administrator permission..." -ForegroundColor Yellow
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs
    exit 0
}

try {
    Write-Host "ATLAS - Enable Windows virtualization support" -ForegroundColor Cyan
    Write-Host ""

    $processor = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
    if (-not $processor.VirtualizationFirmwareEnabled) {
        throw "BIOS virtualization is still disabled. Enable Intel Virtualization in the Dell BIOS first."
    }

    $features = @(
        "Microsoft-Windows-Subsystem-Linux",
        "VirtualMachinePlatform"
    )

    foreach ($featureName in $features) {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $featureName
        if ($feature.State -eq "Enabled") {
            Write-Host "[OK] $featureName is already enabled." -ForegroundColor Green
            continue
        }

        Write-Host "Enabling $featureName..." -ForegroundColor Yellow
        Enable-WindowsOptionalFeature -Online -FeatureName $featureName -All -NoRestart | Out-Null
        Write-Host "[OK] $featureName enabled." -ForegroundColor Green
    }

    Write-Host "Enabling the Windows hypervisor at startup..." -ForegroundColor Yellow
    & "$env:SystemRoot\System32\bcdedit.exe" /set hypervisorlaunchtype auto
    if ($LASTEXITCODE -ne 0) {
        throw "Windows could not configure the hypervisor startup setting."
    }

    Write-Host ""
    Write-Host "Windows virtualization support is configured." -ForegroundColor Green
    Write-Host "The computer must restart before Docker Desktop can use it." -ForegroundColor Cyan
    Write-Host ""
    $answer = Read-Host "Restart now? Type Y and press Enter (Y/N)"
    if ($answer -match "^[Yy]$") {
        Restart-Computer
    }

    Write-Host "Please restart Windows manually, then open Docker Desktop." -ForegroundColor Yellow
}
catch {
    Write-Host ""
    Write-Host "SETUP FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Take a screenshot of this window and send it to Codex." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

