<#
.SYNOPSIS
    Safely manages RYNA startup on Windows user login via HKCU Run registry key.
#>
param(
    [ValidateSet("status", "enable", "disable")]
    [string]$Action = "status",
    [switch]$WithCompanion
)

$ErrorActionPreference = "Stop"
$RegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$ValueName = "RYNA"
$Root = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $Root "scripts\run.ps1"

if ($Action -eq "status") {
    $item = Get-ItemProperty -Path $RegPath -Name $ValueName -ErrorAction SilentlyContinue
    if ($item) {
        Write-Host "RYNA Windows Startup: ENABLED" -ForegroundColor Green
        Write-Host "Command: $($item.$ValueName)"
    } else {
        Write-Host "RYNA Windows Startup: DISABLED" -ForegroundColor Yellow
    }
    exit 0
}

if ($Action -eq "enable") {
    $compFlag = if ($WithCompanion) { " -Companion" } else { "" }
    $cmd = "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`"$compFlag"
    Set-ItemProperty -Path $RegPath -Name $ValueName -Value $cmd
    Write-Host "Enabled RYNA user-login startup." -ForegroundColor Green
    Write-Host "Command: $cmd"
    exit 0
}

if ($Action -eq "disable") {
    if (Get-ItemProperty -Path $RegPath -Name $ValueName -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $RegPath -Name $ValueName
        Write-Host "Disabled RYNA user-login startup." -ForegroundColor Green
    } else {
        Write-Host "RYNA user-login startup is already disabled." -ForegroundColor Yellow
    }
    exit 0
}
