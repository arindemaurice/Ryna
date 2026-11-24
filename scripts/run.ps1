param(
    [switch]$Foreground,
    [switch]$OpenUI,
    [switch]$Robot
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ($Foreground) {
    Push-Location $Root
    try {
        & python -m ai.orchestration.main
        exit $LASTEXITCODE
    } finally { Pop-Location }
}

# Launch RYNA completely DETACHED via windowless pythonw.exe so closing this terminal NEVER closes RYNA!
$pyCmd = Get-Command pythonw -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    $pyExe = (Get-Command python -ErrorAction Stop).Source
    $candidate = Join-Path (Split-Path -Parent $pyExe) "pythonw.exe"
    $pythonw = if (Test-Path $candidate) { $candidate } else { $pyExe }
} else {
    $pythonw = $pyCmd.Source
}

$env:PYTHONPATH = $Root
# NOTE: Do NOT pass -WindowStyle Hidden to pythonw.exe! pythonw.exe already has zero console window,
# and SW_HIDE hides Tkinter's first GUI window (RYNA herself).
Start-Process -FilePath $pythonw -ArgumentList @("-m", "ai.orchestration.main") -WorkingDirectory $Root
Write-Host "RYNA launched in always-on standalone mode (independent of this terminal)." -ForegroundColor Cyan
Write-Host "You can safely close this terminal - RYNA will stay active on your desktop." -ForegroundColor Green
