$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    & python -m ai.orchestration.main
    exit $LASTEXITCODE
} finally { Pop-Location }
