$ErrorActionPreference = "Continue"
Write-Host "=== RYNA Environment Check ===" -ForegroundColor Cyan

$tools = @(
    @{ Name = "python";  Required = $true;  Purpose = "Core AI, SQLite, voice, agents & Windows robot host" },
    @{ Name = "git";     Required = $true;  Purpose = "Version control" },
    @{ Name = "ollama";  Required = $false; Purpose = "Offline local AI inference" },
    @{ Name = "rustc";   Required = $false; Purpose = "Compile Rust core supervisor (core/rust)" },
    @{ Name = "cargo";   Required = $false; Purpose = "Build & test Rust core supervisor" },
    @{ Name = "flutter"; Required = $false; Purpose = "Build Flutter UI (app/flutter_ui)" }
)

$missing = $false
foreach ($t in $tools) {
    $cmd = Get-Command $t.Name -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host ("[OK]       {0,-8} -> {1}" -f $t.Name, $cmd.Source) -ForegroundColor Green
    } else {
        if ($t.Required) { $missing = $true; Write-Host ("[MISSING]  {0,-8} (REQUIRED) - {1}" -f $t.Name, $t.Purpose) -ForegroundColor Red }
        else { Write-Host ("[OPTIONAL] {0,-8} (Not installed) - {1}" -f $t.Name, $t.Purpose) -ForegroundColor Yellow }
    }
}

$assetPath = Join-Path (Split-Path -Parent $PSScriptRoot) "app\desktop\assets\ryna_robot.png"
if (Test-Path $assetPath) {
    Write-Host "[OK]       robot    -> Authoritative RYNA robot asset found." -ForegroundColor Green
} else {
    Write-Host "[AWAITING] robot    -> Supply your robot reference image to app\desktop\assets\ryna_robot.png" -ForegroundColor Yellow
}

if ($missing) { exit 1 }
exit 0
