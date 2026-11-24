param(
    [ValidateSet("status", "enable", "disable")]
    [string]$Action = "status",
    [switch]$WithRobot
)
$ErrorActionPreference = "Stop"
$RegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunScript = Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\run.ps1"

if ($Action -eq "status") {
    $v = Get-ItemProperty -Path $RegPath -Name "RYNA" -ErrorAction SilentlyContinue
    if ($v) { Write-Host "RYNA Windows Startup: ENABLED ($($v.RYNA))" -ForegroundColor Green }
    else { Write-Host "RYNA Windows Startup: DISABLED" -ForegroundColor Yellow }
    exit 0
}
if ($Action -eq "enable") {
    $flag = if ($WithRobot) { " -Robot" } else { "" }
    $cmd = "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`"$flag"
    Set-ItemProperty -Path $RegPath -Name "RYNA" -Value $cmd
    Write-Host "Enabled RYNA Windows startup: $cmd" -ForegroundColor Green
    exit 0
}
if ($Action -eq "disable") {
    if (Get-ItemProperty -Path $RegPath -Name "RYNA" -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $RegPath -Name "RYNA"
        Write-Host "Disabled RYNA Windows startup." -ForegroundColor Green
    } else { Write-Host "Already disabled." -ForegroundColor Yellow }
}
