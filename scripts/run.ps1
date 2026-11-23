<#
.SYNOPSIS
    Launches the RYNA 0.1.0 local engine, optional web UI browser tab, and optional native Windows companion.
#>
param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$OpenUI,
    [switch]$Companion
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) {
        Write-Host "ERROR: Python is not installed or not on PATH." -ForegroundColor Red
        exit 1
    }

    $argsList = @("-m", "core.python.main", "--host", $BindHost, "--port", "$Port")
    if ($OpenUI) { $argsList += "--open-ui" }
    if ($Companion) { $argsList += "--companion" }

    Write-Host "Launching RYNA 0.1.0..." -ForegroundColor Cyan
    & python @argsList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
