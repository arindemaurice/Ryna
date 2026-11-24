$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Host "=== [1/5] Generating / Verifying Arinde Maurice Code Signing Certificate ===" -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "packaging\create_certificate.ps1") -TrustLocally
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "=== [2/5] Building Standalone RYNA.exe (PyInstaller) ===" -ForegroundColor Cyan
    $assetsSrc = Join-Path $Root "app\desktop\assets"
    $iconPath = Join-Path $Root "app\desktop\assets\ryna_icon.ico"
    $verFile = Join-Path $Root "packaging\ryna_version_info.txt"
    $entry = Join-Path $Root "ai\orchestration\main.py"

    & python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name RYNA `
        --icon $iconPath `
        --version-file $verFile `
        --add-data "$assetsSrc;app\desktop\assets" `
        --collect-all faster_whisper `
        --collect-all ctranslate2 `
        --collect-all tokenizers `
        --collect-all sounddevice `
        --collect-all pystray `
        --collect-all mss `
        --hidden-import PIL._tkinter_finder `
        --hidden-import tkinter `
        --hidden-import tkinter.ttk `
        --hidden-import app.desktop.settings_panel `
        --hidden-import app.desktop.autostart `
        --hidden-import computer.system_manager `
        $entry
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $rynaExe = Join-Path $Root "dist\RYNA\RYNA.exe"
    if (-not (Test-Path $rynaExe)) { throw "Expected RYNA.exe was not built at $rynaExe" }

    Write-Host "=== [3/5] Bundling Offline faster-whisper tiny.en Speech Model ===" -ForegroundColor Cyan
    $hfSnap = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--Systran--faster-whisper-tiny.en\snapshots"
    if (Test-Path $hfSnap) {
        $snapDir = Get-ChildItem -Path $hfSnap -Directory | Select-Object -First 1
        if ($snapDir) {
            $modelDest = Join-Path $Root "dist\RYNA\_internal\models\faster-whisper-tiny.en"
            New-Item -ItemType Directory -Force -Path $modelDest | Out-Null
            Get-ChildItem -Path $snapDir.FullName -File | ForEach-Object {
                Copy-Item -Path $_.FullName -Destination (Join-Path $modelDest $_.Name) -Force
            }
            Write-Host ("Bundled offline Whisper model from " + $snapDir.FullName) -ForegroundColor Green
        }
    } else {
        Write-Host "[INFO] Local Whisper cache not found; RYNA will auto-download tiny.en on first voice use." -ForegroundColor Yellow
    }

    Write-Host "=== [4/5] Signing RYNA.exe with Arinde Maurice Certificate ===" -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "packaging\create_certificate.ps1") -SignFiles @($rynaExe)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "=== [5/5] Compiling & Signing RYNA-Setup-1.0.0.exe (Inno Setup 6) ===" -ForegroundColor Cyan
    $iscc = @(
        (Get-Command iscc -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found." }

    & $iscc (Join-Path $Root "packaging\ryna_installer.iss")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $setupExe = Join-Path $Root "dist\RYNA-Setup-1.0.0.exe"
    if (-not (Test-Path $setupExe)) { throw "Expected installer was not built at $setupExe" }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "packaging\create_certificate.ps1") -SignFiles @($setupExe)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host ""
    Write-Host "=== BUILD & SIGNING COMPLETE ===" -ForegroundColor Green
    $fi = Get-Item $setupExe
    Write-Host ("Installer : " + $fi.FullName + " (" + [math]::Round($fi.Length / 1MB, 1) + " MB)") -ForegroundColor Green
    Write-Host ("Public Cer: " + (Join-Path $Root "certs\ArindeMaurice-CodeSign.cer")) -ForegroundColor Green
    foreach ($f in @($rynaExe, $setupExe)) {
        $sig = Get-AuthenticodeSignature $f
        Write-Host ("Verified  : " + (Split-Path -Leaf $f) + " | Status=" + $sig.Status + " | Signer=" + $sig.SignerCertificate.Subject) -ForegroundColor Cyan
    }
} finally { Pop-Location }
