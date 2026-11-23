<#
.SYNOPSIS
    Safe, reproducible Windows setup script for RYNA 0.1.0.
    Checks prerequisites, creates .env from .env.example if absent, and initializes SQLite.
#>
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    & "$PSScriptRoot\check_env.ps1"
    if ($LASTEXITCODE -ne 0) {
        throw "Prerequisite check failed."
    }

    $envFile = Join-Path $Root ".env"
    $envExample = Join-Path $Root ".env.example"
    if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
        Copy-Item $envExample $envFile
        Write-Host "[SETUP] Created .env from .env.example" -ForegroundColor Green
    } else {
        Write-Host "[SETUP] Existing .env preserved." -ForegroundColor Cyan
    }

    Write-Host "[SETUP] Initializing local SQLite database and verifying core engine..." -ForegroundColor Cyan
    python -c "from core.python.engine import RynaEngine; e = RynaEngine(); print('DB ready:', e.config.db_path); e.close()"
    if ($LASTEXITCODE -ne 0) {
        throw "Database initialization failed."
    }

    Write-Host ""
    Write-Host "RYNA 0.1.0 setup complete." -ForegroundColor Green
    Write-Host "Run RYNA:        powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI" -ForegroundColor Cyan
    Write-Host "With Companion:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI -Companion" -ForegroundColor Cyan
    Write-Host "Run Tests:       powershell -ExecutionPolicy Bypass -File scripts\test.ps1" -ForegroundColor Cyan
} finally {
    Pop-Location
}
