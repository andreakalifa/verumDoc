# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# =============================================================================
# infra/scripts/genera_segreti_dev_r03.ps1
#
# Versione PowerShell di genera_segreti_dev_r03.sh (Windows senza bash/WSL).
# Segreti di SVILUPPO dei canali R03 per docker-compose.yml (mai committati:
# infra/*.env è in .gitignore). I valori non vengono mai stampati.
#
#   infra/dev_sftp.env          SFTP_TOKEN_CHIAVE_PRIVATA  (sftp-ingest: firma i token di canale)
#   infra/dev_canali_pub.env    SFTP_TOKEN_CHIAVE_PUBBLICA (gateway: verifica quei token)
#   infra/dev_webhook.env       WEBHOOK_CHIAVE_CIFRATURA   (notification: 32 byte in base64)
#
# USO (dalla radice): .\infra\scripts\genera_segreti_dev_r03.ps1 [-Forza]
# Senza -Forza un file esistente non si tocca. Richiede PowerShell 7+ (non serve openssl).
# =============================================================================
param([switch]$Forza)
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "Serve PowerShell 7 o successivo (pwsh). Versione attuale: $($PSVersionTable.PSVersion)"
    exit 2
}

$Radice = Resolve-Path (Join-Path $PSScriptRoot "../..")
$Priv = Join-Path $Radice "infra/dev_sftp.env"
$Pub  = Join-Path $Radice "infra/dev_canali_pub.env"
$Wh   = Join-Path $Radice "infra/dev_webhook.env"
$PostaPriv = Join-Path $Radice "infra/dev_posta_priv.env"
$PostaPub  = Join-Path $Radice "infra/dev_posta_pub.env"
$Intestazione = "# GENERATO_LOCALMENTE da infra/scripts/genera_segreti_dev_r03.ps1 - non committare"

# PEM su una riga con \n letterali, come si aspettano le Settings
function Una-Riga([string]$Pem) { return (($Pem.Trim() -replace "`r", "") -replace "`n", "\n") }

if ($Forza -or -not (Test-Path $Priv) -or -not (Test-Path $Pub)) {
    $rsa = [System.Security.Cryptography.RSA]::Create(3072)
    try {
        $privLine = Una-Riga $rsa.ExportPkcs8PrivateKeyPem()
        $pubLine  = Una-Riga $rsa.ExportSubjectPublicKeyInfoPem()
    } finally { $rsa.Dispose() }
    Set-Content -Path $Priv -Value @($Intestazione, "SFTP_TOKEN_CHIAVE_PRIVATA=$privLine") -Encoding utf8NoBOM
    Set-Content -Path $Pub  -Value @($Intestazione, "SFTP_TOKEN_CHIAVE_PUBBLICA=$pubLine")  -Encoding utf8NoBOM
    Write-Host "Creati $Priv e $Pub (coppia del canale SFTP, solo sviluppo)."
} else {
    Write-Host "$Priv e $Pub esistono già: lasciati com'erano (-Forza per rigenerarli)."
}

if ($Forza -or -not (Test-Path $Wh)) {
    $chiave = [System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    Set-Content -Path $Wh -Value @($Intestazione, "WEBHOOK_CHIAVE_CIFRATURA=$chiave") -Encoding utf8NoBOM
    Write-Host "Creato $Wh (chiave di cifratura dei segreti webhook, solo sviluppo)."
} else {
    Write-Host "$Wh esiste già: lasciato com'era."
}

# R12a (VD-C-152): coppia X25519 della posta. .NET non espone X25519: serve openssl
# (Git for Windows lo include); senza, gli inviti rispondono 503 finché non si crea.
if ($Forza -or -not (Test-Path $PostaPriv) -or -not (Test-Path $PostaPub)) {
    if (Get-Command openssl -ErrorAction SilentlyContinue) {
        $tmp = New-TemporaryFile
        & openssl genpkey -algorithm X25519 -out $tmp.FullName 2>$null
        function Ultimi32([byte[]]$b) { return [System.Convert]::ToBase64String($b[($b.Length-32)..($b.Length-1)]).TrimEnd('=').Replace('+','-').Replace('/','_') }
        & openssl pkey -in $tmp.FullName -outform DER -out "$($tmp.FullName).d" 2>$null
        $privB = [System.IO.File]::ReadAllBytes("$($tmp.FullName).d")
        & openssl pkey -in $tmp.FullName -pubout -outform DER -out "$($tmp.FullName).p" 2>$null
        $pubB = [System.IO.File]::ReadAllBytes("$($tmp.FullName).p")
        Set-Content -Path $PostaPriv -Value @($Intestazione, "POSTA_CHIAVE_PRIVATA=$(Ultimi32 $privB)") -Encoding utf8NoBOM
        Set-Content -Path $PostaPub  -Value @($Intestazione, "POSTA_CHIAVE_PUBBLICA=$(Ultimi32 $pubB)") -Encoding utf8NoBOM
        Remove-Item $tmp.FullName, "$($tmp.FullName).d", "$($tmp.FullName).p" -ErrorAction SilentlyContinue
        Write-Host "Creati $PostaPriv e $PostaPub (coppia della posta per gli inviti, solo sviluppo)."
    } else {
        Write-Warning "openssl non trovato: $PostaPriv e $PostaPub non creati (inviti in 503)."
    }
} else {
    Write-Host "$PostaPriv e $PostaPub esistono già: lasciati com'erano."
}
