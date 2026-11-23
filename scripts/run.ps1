param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$Headless,
    [switch]$WebUI,
    [switch]$OpenUI,
    [switch]$Robot
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $argsList = @("-m", "ai.orchestration.main", "--host", $BindHost, "--port", "$Port")
    if ($Headless) { $argsList += "--headless" }
    if ($WebUI -or $OpenUI) { $argsList += "--open-ui" }
    if ($Robot) { $argsList += "--robot" }
    & python @argsList
    exit $LASTEXITCODE
} finally { Pop-Location }
