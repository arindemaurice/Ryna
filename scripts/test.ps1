$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Host "=== Running RYNA Python Test Suite ===" -ForegroundColor Cyan
    python -m unittest discover -s tests -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Push-Location (Join-Path $Root "core\rust")
        try { cargo test; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
    } else { Write-Host "[SKIP] Rust tests skipped (cargo not installed)." -ForegroundColor Yellow }

    if (Get-Command flutter -ErrorAction SilentlyContinue) {
        Push-Location (Join-Path $Root "app\flutter_ui")
        try { flutter test; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
    } else { Write-Host "[SKIP] Flutter tests skipped (flutter not installed)." -ForegroundColor Yellow }

    Write-Host "All available RYNA tests passed." -ForegroundColor Green
    exit 0
} finally { Pop-Location }
