<#
.SYNOPSIS
    Runs all available automated test suites for RYNA 0.1.0 (Python + optional Rust/Flutter if installed).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Host "=== Running Python Core & Integration Tests ===" -ForegroundColor Cyan
    python -m unittest discover -s tests -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Python tests failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Write-Host "=== Running Rust Core Engine Tests ===" -ForegroundColor Cyan
        Push-Location (Join-Path $Root "core\rust")
        try {
            cargo test
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally { Pop-Location }
    } else {
        Write-Host "[SKIP] Rust tests skipped (cargo not installed)." -ForegroundColor Yellow
    }

    if (Get-Command flutter -ErrorAction SilentlyContinue) {
        Write-Host "=== Running Flutter Tests ===" -ForegroundColor Cyan
        Push-Location (Join-Path $Root "apps\desktop")
        try {
            flutter test
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally { Pop-Location }
    } else {
        Write-Host "[SKIP] Flutter tests skipped (flutter not installed)." -ForegroundColor Yellow
    }

    Write-Host "All available RYNA test suites passed." -ForegroundColor Green
    exit 0
} finally {
    Pop-Location
}
