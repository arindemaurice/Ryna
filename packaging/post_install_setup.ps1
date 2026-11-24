param(
    [string]$InstallDir = "",
    [switch]$InstallOllama,
    [switch]$TrustCert,
    [switch]$EnableAutostart
)
$ErrorActionPreference = "Continue"
if (-not $InstallDir) { $InstallDir = Split-Path -Parent $PSScriptRoot }

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RYNA AI Desktop Assistant - Post-Install Environment" -ForegroundColor Cyan
Write-Host "  Copyright (c) 2026 Arinde Maurice" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Optional: Trust Arinde Maurice Publisher Certificate (Current User TrustedPublisher, silent)
if ($TrustCert) {
    $cer = Join-Path $InstallDir "certs\ArindeMaurice-CodeSign.cer"
    if (Test-Path $cer) {
        try {
            Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\CurrentUser\TrustedPublisher" | Out-Null
            Write-Host "[OK] Installed publisher certificate for Arinde Maurice (TrustedPublisher)." -ForegroundColor Green
        } catch {
            Write-Host ("[WARN] Certificate trust skipped: " + $_.Exception.Message) -ForegroundColor Yellow
        }
    }
}

# 2. Optional: Auto-start RYNA with Windows (per-user Startup folder, zero terminal flash)
if ($EnableAutostart) {
    try {
        $startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
        New-Item -ItemType Directory -Force -Path $startup | Out-Null
        $rynaExe = Join-Path $InstallDir "RYNA.exe"
        $vbsPath = Join-Path $startup "RYNA.vbs"
        $vbs = @(
            "' RYNA Always-On Launcher (Copyright (c) 2026 Arinde Maurice)",
            'Set sh = CreateObject("WScript.Shell")',
            ('sh.CurrentDirectory = "' + $InstallDir + '"'),
            ('sh.Run """' + $rynaExe + '""", 1, False')
        ) -join "`r`n"
        Set-Content -Path $vbsPath -Value $vbs -Encoding ASCII
        Write-Host "[OK] Enabled RYNA automatic startup with Windows." -ForegroundColor Green
    } catch {
        Write-Host ("[WARN] Auto-start setup failed: " + $_.Exception.Message) -ForegroundColor Yellow
    }
}

# 3. Install Ollama + pull fast local offline AI model (qwen2.5:1.5b)
if ($InstallOllama) {
    function Find-OllamaExe {
        $c = Get-Command ollama -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
        $cand = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $cand) { return $cand }
        $cand2 = "C:\Program Files\Ollama\ollama.exe"
        if (Test-Path $cand2) { return $cand2 }
        return $null
    }

    $ollamaExe = Find-OllamaExe
    if (-not $ollamaExe) {
        Write-Host "[1/3] Installing Ollama local AI engine..." -ForegroundColor Cyan
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            & winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
        }
        $ollamaExe = Find-OllamaExe
        if (-not $ollamaExe) {
            Write-Host "      Downloading official OllamaSetup.exe..." -ForegroundColor Cyan
            $tmpSetup = Join-Path $env:TEMP "OllamaSetup.exe"
            try {
                Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $tmpSetup -UseBasicParsing
                Start-Process -FilePath $tmpSetup -ArgumentList @("/VERYSILENT", "/NORESTART") -Wait
                Remove-Item $tmpSetup -Force -ErrorAction SilentlyContinue
            } catch {
                Write-Host ("[WARN] Could not download Ollama (offline?): " + $_.Exception.Message) -ForegroundColor Yellow
            }
        }
        $ollamaExe = Find-OllamaExe
    } else {
        Write-Host ("[OK] Ollama already installed: " + $ollamaExe) -ForegroundColor Green
    }

    if ($ollamaExe) {
        Write-Host "[2/3] Ensuring Ollama local AI service is running..." -ForegroundColor Cyan
        $ready = $false
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
            $ready = $true
        } catch {}

        if (-not $ready) {
            Start-Process -FilePath $ollamaExe -ArgumentList @("serve") -WindowStyle Hidden
            for ($i = 0; $i -lt 15; $i++) {
                Start-Sleep -Seconds 1
                try {
                    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
                    $ready = $true
                    break
                } catch {}
            }
        }

        if ($ready) {
            Write-Host "[3/3] Checking/downloading fast offline AI model (qwen2.5:1.5b)..." -ForegroundColor Cyan
            $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
            $hasFast = @($tags.models | Where-Object { $_.name -like "qwen2.5:1.5b*" }).Count -gt 0
            if ($hasFast) {
                Write-Host "[OK] Offline AI model qwen2.5:1.5b is already ready." -ForegroundColor Green
            } else {
                & $ollamaExe pull qwen2.5:1.5b
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[OK] Downloaded qwen2.5:1.5b offline AI model." -ForegroundColor Green
                } else {
                    Write-Host "[WARN] Model pull skipped (will pull automatically once online)." -ForegroundColor Yellow
                }
            }
        } else {
            Write-Host "[WARN] Ollama service did not respond yet; RYNA will connect automatically once Ollama starts." -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "RYNA setup complete! Launching RYNA on your desktop..." -ForegroundColor Green
Start-Sleep -Seconds 2
