param(
    [string]$Subject = "CN=Arinde Maurice, O=Arinde Maurice",
    [string[]]$SignFiles = @(),
    [switch]$TrustLocally
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$CertDir = Join-Path $Root "certs"
New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
$PublicCer = Join-Path $CertDir "ArindeMaurice-CodeSign.cer"

# Reuse existing valid Arinde Maurice code-signing cert in CurrentUser\My if present
$cert = Get-ChildItem -Path Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $Subject -and $_.NotAfter -gt (Get-Date).AddDays(30) } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1

if (-not $cert) {
    Write-Host "Generating RSA-4096 Code Signing Certificate for $Subject ..." -ForegroundColor Cyan
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $Subject `
        -FriendlyName "Arinde Maurice - RYNA Code Signing" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyAlgorithm RSA `
        -KeyLength 4096 `
        -HashAlgorithm SHA256 `
        -NotAfter (Get-Date).AddYears(5)
}

Export-Certificate -Cert $cert -FilePath $PublicCer -Force | Out-Null
Write-Host ("Certificate Ready: " + $cert.Subject + " | Thumbprint: " + $cert.Thumbprint) -ForegroundColor Green
Write-Host ("Public Certificate: " + $PublicCer) -ForegroundColor Green

if ($TrustLocally) {
    # Import into TrustedPublisher silently (avoids modal Root CA confirmation dialog during automated builds)
    Import-Certificate -FilePath $PublicCer -CertStoreLocation "Cert:\CurrentUser\TrustedPublisher" | Out-Null
    Write-Host "Installed public certificate into CurrentUser\TrustedPublisher." -ForegroundColor Cyan
}

foreach ($file in $SignFiles) {
    if (-not (Test-Path $file)) {
        throw "File to sign not found: $file"
    }
    Write-Host ("Signing " + $file + " ...") -ForegroundColor Cyan
    $sig = $null
    try {
        $sig = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com" -ErrorAction Stop
    } catch {
        $sig = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256 -ErrorAction Stop
    }
    Write-Host ("Signature Status: " + $sig.Status + " | Signer: " + $sig.SignerCertificate.Subject) -ForegroundColor Green
}
