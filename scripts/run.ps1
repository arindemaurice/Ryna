param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$Headless,
    [switch]$WebUI
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $argsList = @("-m", "ai.orchestration.main", "--host", $BindHost, "--port", "$Port")
    if ($Headless) { $argsList += "--headless" }
    if ($WebUI) { $argsList += "--open-ui" }
    & python @argsList
    exit $LASTEXITCODE
} finally { Pop-Location }
