<#
.SYNOPSIS
    Checks RYNA 0.1.0 development and runtime prerequisites on Windows.
#>
$ErrorActionPreference = "Continue"

Write-Host "=== RYNA 0.1.0 Environment Check ===" -ForegroundColor Cyan

$tools = @(
    @{ Name = "python";  Required = $true;  Purpose = "Core runtime, AI router, SQLite, local UI & Windows companion"; Install = "https://www.python.org/downloads/ (Python 3.11+)" },
    @{ Name = "git";     Required = $true;  Purpose = "Version control"; Install = "https://git-scm.com/download/win" },
    @{ Name = "ollama";  Required = $false; Purpose = "Offline local AI models"; Install = "https://ollama.com/download/windows" },
    @{ Name = "rustc";   Required = $false; Purpose = "Compile Rust core engine (core/rust)"; Install = "https://rustup.rs/" },
    @{ Name = "cargo";   Required = $false; Purpose = "Build & test Rust core engine"; Install = "https://rustup.rs/" },
    @{ Name = "flutter"; Required = $false; Purpose = "Build & run Flutter desktop/mobile app (apps/desktop)"; Install = "https://docs.flutter.dev/get-started/install/windows" },
    @{ Name = "dart";    Required = $false; Purpose = "Dart SDK (bundled with Flutter)"; Install = "https://docs.flutter.dev/get-started/install/windows" }
)

$missingRequired = $false

foreach ($t in $tools) {
    $cmd = Get-Command $t.Name -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host ("[OK]       {0,-8} -> {1}" -f $t.Name, $cmd.Source) -ForegroundColor Green
    } else {
        if ($t.Required) {
            $missingRequired = $true
            Write-Host ("[MISSING]  {0,-8} (REQUIRED) - {1}" -f $t.Name, $t.Purpose) -ForegroundColor Red
        } else {
            Write-Host ("[OPTIONAL] {0,-8} (Not installed) - {1}" -f $t.Name, $t.Purpose) -ForegroundColor Yellow
        }
        Write-Host ("           Install: {0}" -f $t.Install) -ForegroundColor DarkGray
    }
}

Write-Host ""
if ($missingRequired) {
    Write-Host "One or more REQUIRED tools are missing. Install them before running RYNA." -ForegroundColor Red
    exit 1
} else {
    Write-Host "Core Python/SQLite RYNA runtime is ready to run on this machine." -ForegroundColor Green
    exit 0
}
