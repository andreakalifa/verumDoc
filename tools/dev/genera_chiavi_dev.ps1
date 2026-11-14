# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# =============================================================================
# tools/dev/genera_chiavi_dev.ps1
#
# Genera in locale la coppia di chiavi RS256 per lo sviluppo e il pepper delle
# chiavi API (API_KEY_PEPPER, 32 byte casuali) e scrive infra/dev_jwt.env (letto
# da docker-compose.yml). Il file NON va committato. I valori non vengono stampati.
#
# USO: .\tools\dev\genera_chiavi_dev.ps1 [-Forza]
#   senza -Forza un file esistente resta com'è; se manca solo API_KEY_PEPPER lo aggiunge.
#   -Forza rigenera tutto (le chiavi API generate prima smettono di valere).
# Richiede PowerShell 7+ (.NET con ExportRSAPrivateKeyPem).
# =============================================================================
param([switch]$Forza)
$ErrorActionPreference = "Stop"
$Dest = "infra/dev_jwt.env"

# Pepper HMAC delle chiavi API (gateway e onboarding): 32 byte casuali in esadecimale.
function Nuovo-Pepper {
    $byte = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    return [System.Convert]::ToHexString($byte).ToLowerInvariant()
}

if ((Test-Path $Dest) -and -not $Forza) {
    if (Select-String -Path $Dest -Pattern '^API_KEY_PEPPER=' -Quiet) {
        Write-Host "$Dest esiste già: lo lascio com'è (usa -Forza per rigenerarlo)."
    } else {
        # File di una versione precedente: si aggiunge solo il pepper, le chiavi JWT restano.
        Add-Content -Path $Dest -Value "API_KEY_PEPPER=$(Nuovo-Pepper)" -Encoding utf8NoBOM
        Write-Host "Aggiunto API_KEY_PEPPER a $Dest (chiavi JWT invariate)."
    }
    exit 0
}

$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$priv = $rsa.ExportPkcs8PrivateKeyPem()
$pub  = $rsa.ExportSubjectPublicKeyInfoPem()

$privLine = ($priv.Trim() -replace "`r", "") -replace "`n", "\n"
$pubLine  = ($pub.Trim()  -replace "`r", "") -replace "`n", "\n"

New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
Set-Content -Path $Dest -Value @("# GENERATO_LOCALMENTE da tools/dev/genera_chiavi_dev.ps1 - non committare", "JWT_PRIVATE_KEY=$privLine", "JWT_PUBLIC_KEY=$pubLine", "API_KEY_PEPPER=$(Nuovo-Pepper)") -Encoding utf8NoBOM
Write-Host "Creato $Dest (solo per sviluppo locale, non committarlo)."
