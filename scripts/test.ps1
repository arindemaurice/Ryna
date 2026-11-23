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
    } else { Write-Host "[SKIP] Rust tests skipped (cargo not on PATH)." -ForegroundColor Yellow }

    Write-Host "All RYNA tests passed." -ForegroundColor Green
    exit 0
} finally { Pop-Location }
