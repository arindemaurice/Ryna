$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    & "$PSScriptRoot\check_env.ps1"
    if ($LASTEXITCODE -ne 0) { throw "Required prerequisites missing." }

    $envFile = Join-Path $Root ".env"
    $envExample = Join-Path $Root ".env.example"
    if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
        Copy-Item $envExample $envFile
        Write-Host "[SETUP] Created .env from .env.example" -ForegroundColor Green
    } else {
        Write-Host "[SETUP] Existing .env preserved." -ForegroundColor Cyan
    }

    python -c "from ai.orchestration.core import RynaCore; c = RynaCore(); print('RYNA DB ready:', c.config.db_path); c.close()"
    if ($LASTEXITCODE -ne 0) { throw "Database initialization failed." }
    Write-Host "Setup complete." -ForegroundColor Green
} finally { Pop-Location }
