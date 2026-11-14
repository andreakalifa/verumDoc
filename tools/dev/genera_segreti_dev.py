# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Segreti di SVILUPPO locale, multipiattaforma (Windows senza bash né PowerShell 7).

Equivale a tools/dev/genera_chiavi_dev.sh + infra/scripts/genera_segreti_dev_r03.sh
(stessi file, stesso formato) e scrive, dalla radice del monorepo:

  infra/dev_jwt.env          JWT_PRIVATE_KEY, JWT_PUBLIC_KEY (RS256 2048), API_KEY_PEPPER
  infra/dev_sftp.env         SFTP_TOKEN_CHIAVE_PRIVATA  (sftp-ingest, RSA 3072)
  infra/dev_canali_pub.env   SFTP_TOKEN_CHIAVE_PUBBLICA (gateway)
  infra/dev_ciclo_vita.env   CICLO_VITA_TOKEN_CHIAVE_PRIVATA (ciclo-vita, RSA 3072, R05)
  infra/dev_ciclo_vita_pub.env  CICLO_VITA_TOKEN_CHIAVE_PUBBLICA (gateway, R05)
  infra/dev_webhook.env      WEBHOOK_CHIAVE_CIFRATURA   (notification, 32 byte base64)
  infra/dev_catena.env       CATENA_CHIAVE_PRIVATA (hashchain, Ed25519 «PRIVATE+KEY+
                             verumdoc-registro+…»), TESTIMONE_VERIFICATORE (chiave
                             pubblica del testimone), TESTIMONE_SERVICE_TOKEN
  infra/dev_testimone.env    TESTIMONE_CHIAVE_PRIVATA (Ed25519 «PRIVATE+KEY+
                             verumdoc-testimone+…»), TESTIMONE_CHIAVI_REGISTRO (chiave
                             pubblica del registro), TESTIMONE_IMPRONTE_TOKEN_REGISTRO
                             (SHA-256 del token di servizio del registro)
  infra/dev_posta_priv.env   POSTA_CHIAVE_PRIVATA  (notification-posta, X25519 grezza 32 byte
                             in base64url senza «=», R12a VD-C-152)
  infra/dev_posta_pub.env    POSTA_CHIAVE_PUBBLICA (gateway e onboarding: sigillo degli inviti)
  infra/dev_conferme.env     CONFERME_CHIAVE_PRIVATA (gateway, Ed25519: seme di 32 byte in
                             base64), CONFERME_KID (R17, VD-P-164)
  infra/dev_conferme_pub.env CONFERME_CHIAVI_PUBBLICHE (ciclo-vita, API e lavoratore: JSON
                             {kid: chiave pubblica di 32 byte in base64})

Le chiavi delle conferme servono solo se si accende la conferma firmata delle
decisioni nello stack locale (`VD_CONFERME=ATTIVO docker compose up -d`); senza,
in sviluppo la conferma resta DISATTIVO e i file non sono usati.

Su Windows basta questo script (nessun PowerShell 7, openssl né .ps1): stesso
formato di infra/scripts/genera_segreti_dev_r03.ps1.

I file sono esclusi da git (infra/*.env) e i valori non vengono mai stampati.
Un file esistente non si tocca; a infra/dev_jwt.env senza pepper si aggiunge
solo API_KEY_PEPPER. dev_catena.env e dev_testimone.env si generano insieme
(le chiavi pubbliche e il token dell'uno servono all'altro): se ne manca uno si
rigenerano entrambi. Le chiavi della catena (R04, contratti K2/K3) sono chiavi
di SERVIZIO Ed25519 nel formato delle note firmate C2SP (libs/catena/nota.py).
Con --forza si rigenera tutto (chiavi API, token di canale
e segreti dei webhook emessi prima smettono di valere).

USO (dalla radice):  python tools/dev/genera_segreti_dev.py [--forza]
Richiede il pacchetto «cryptography» (pip install cryptography).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

INTESTAZIONE = "# GENERATO_LOCALMENTE da tools/dev/genera_segreti_dev.py — non committare"


def _coppia_rsa(bit: int) -> tuple[str, str]:
    """(privata PKCS#8, pubblica SPKI) in PEM su una riga con \\n letterali."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    chiave = rsa.generate_private_key(public_exponent=65537, key_size=bit)
    priv = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    pub = chiave.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return _una_riga(priv), _una_riga(pub)


def _una_riga(pem: str) -> str:
    return pem.strip().replace("\r", "").replace("\n", "\\n")


def _scrivi(percorso: Path, righe: list[str]) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": niente CRLF su Windows; niente BOM (utf-8 semplice)
    with open(percorso, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join([INTESTAZIONE, *righe]) + "\n")
    try:
        os.chmod(percorso, 0o600)
    except OSError:  # Windows: permessi POSIX non applicabili
        pass


def _coppia_ed25519(nome: str) -> tuple[str, str]:
    """(chiave privata «PRIVATE+KEY+nome+keyhash+base64», verifier key) di una
    nota firmata C2SP (stesso formato di libs/catena/nota.py, qui senza importare
    libs: lo script gira dalla radice anche senza PYTHONPATH)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    privata = Ed25519PrivateKey.generate()
    seme = privata.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
    pubblica = privata.public_key().public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw)
    algoritmo = bytes([1])  # Ed25519 nelle note firmate
    key_hash = hashlib.sha256(nome.encode() + b"\n" + algoritmo + pubblica).digest()[:4].hex()
    privata_b64 = base64.b64encode(algoritmo + seme).decode()
    pubblica_b64 = base64.b64encode(algoritmo + pubblica).decode()
    return (f"PRIVATE+KEY+{nome}+{key_hash}+{privata_b64}", f"{nome}+{key_hash}+{pubblica_b64}")


def _coppia_x25519() -> tuple[str, str]:
    """(privata, pubblica) X25519 grezze di 32 byte in base64url senza «=»: lo stesso
    formato di libs/shared/security/sigillo.py e dello script .ps1 (qui senza
    importare libs: lo script gira dalla radice anche senza PYTHONPATH)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    privata = X25519PrivateKey.generate()
    grezza = privata.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                   serialization.NoEncryption())
    pubblica = privata.public_key().public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw)

    def b64(dati: bytes) -> str:
        return base64.urlsafe_b64encode(dati).decode().rstrip("=")

    return b64(grezza), b64(pubblica)


def _coppia_ed25519_grezza() -> tuple[str, str]:
    """(seme privato, chiave pubblica) Ed25519 di 32 byte in base64 standard: il formato
    di CONFERME_CHIAVE_PRIVATA e di CONFERME_CHIAVI_PUBBLICHE (qui senza importare libs)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    privata = Ed25519PrivateKey.generate()
    seme = privata.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
    pubblica = privata.public_key().public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw)
    return base64.b64encode(seme).decode(), base64.b64encode(pubblica).decode()


def genera(radice: Path, forza: bool = False) -> list[str]:
    """Crea i file mancanti (o tutti con forza). Restituisce i messaggi d'esito."""
    esiti: list[str] = []
    infra = radice / "infra"

    jwt = infra / "dev_jwt.env"
    if jwt.exists() and not forza:
        testo = jwt.read_text(encoding="utf-8")
        if any(r.startswith("API_KEY_PEPPER=") for r in testo.splitlines()):
            esiti.append(f"{jwt} esiste già: lasciato com'era.")
        else:
            with open(jwt, "a", encoding="utf-8", newline="\n") as f:
                if testo and not testo.endswith("\n"):
                    f.write("\n")
                f.write(f"API_KEY_PEPPER={secrets.token_hex(32)}\n")
            esiti.append(f"Aggiunto API_KEY_PEPPER a {jwt} (chiavi JWT invariate).")
    else:
        priv, pub = _coppia_rsa(2048)
        _scrivi(jwt, [f"JWT_PRIVATE_KEY={priv}", f"JWT_PUBLIC_KEY={pub}",
                      f"API_KEY_PEPPER={secrets.token_hex(32)}"])
        esiti.append(f"Creato {jwt}.")

    sftp, canali = infra / "dev_sftp.env", infra / "dev_canali_pub.env"
    if forza or not sftp.exists() or not canali.exists():
        priv, pub = _coppia_rsa(3072)
        _scrivi(sftp, [f"SFTP_TOKEN_CHIAVE_PRIVATA={priv}"])
        _scrivi(canali, [f"SFTP_TOKEN_CHIAVE_PUBBLICA={pub}"])
        esiti.append(f"Creati {sftp} e {canali} (coppia del canale SFTP).")
    else:
        esiti.append(f"{sftp} e {canali} esistono già: lasciati com'erano.")

    # R05: token di canale CICLO_VITA (versamenti di rettifica e riversamento).
    # I token di SERVIZIO verso hashchain e distribution (HASHCHAIN_TOKEN_REGISTRO,
    # DISTRIBUTION_TOKEN_SERVIZIO) scadono: si emettono a mano con
    # libs.shared.security.emetti_token_servizio (infra/k8s/secrets/README.md).
    ciclo, ciclo_pub = infra / "dev_ciclo_vita.env", infra / "dev_ciclo_vita_pub.env"
    if forza or not ciclo.exists() or not ciclo_pub.exists():
        priv, pub = _coppia_rsa(3072)
        _scrivi(ciclo, [f"CICLO_VITA_TOKEN_CHIAVE_PRIVATA={priv}"])
        _scrivi(ciclo_pub, [f"CICLO_VITA_TOKEN_CHIAVE_PUBBLICA={pub}"])
        esiti.append(f"Creati {ciclo} e {ciclo_pub} (coppia del canale CICLO_VITA).")
    else:
        esiti.append(f"{ciclo} e {ciclo_pub} esistono già: lasciati com'erano.")

    webhook = infra / "dev_webhook.env"
    if forza or not webhook.exists():
        chiave = base64.b64encode(secrets.token_bytes(32)).decode()
        _scrivi(webhook, [f"WEBHOOK_CHIAVE_CIFRATURA={chiave}"])
        esiti.append(f"Creato {webhook} (cifratura dei segreti webhook).")
    else:
        esiti.append(f"{webhook} esiste già: lasciato com'era.")

    catena, testimone = infra / "dev_catena.env", infra / "dev_testimone.env"
    if forza or not catena.exists() or not testimone.exists():
        reg_priv, reg_pub = _coppia_ed25519("verumdoc-registro")
        tes_priv, tes_pub = _coppia_ed25519("verumdoc-testimone")
        token = secrets.token_urlsafe(32)
        _scrivi(catena, [f"CATENA_CHIAVE_PRIVATA={reg_priv}", f"TESTIMONE_VERIFICATORE={tes_pub}",
                         f"TESTIMONE_SERVICE_TOKEN={token}"])
        _scrivi(testimone, [f"TESTIMONE_CHIAVE_PRIVATA={tes_priv}",
                            f"TESTIMONE_CHIAVI_REGISTRO={reg_pub}",
                            "TESTIMONE_IMPRONTE_TOKEN_REGISTRO="
                            + hashlib.sha256(token.encode()).hexdigest()])
        esiti.append(f"Creati {catena} e {testimone} (chiavi del registro e del testimone).")
    else:
        esiti.append(f"{catena} e {testimone} esistono già: lasciati com'erano.")

    # R12a (VD-C-152): coppia della posta (sigillo dei segreti degli inviti)
    posta_priv, posta_pub = infra / "dev_posta_priv.env", infra / "dev_posta_pub.env"
    if forza or not posta_priv.exists() or not posta_pub.exists():
        priv, pub = _coppia_x25519()
        _scrivi(posta_priv, [f"POSTA_CHIAVE_PRIVATA={priv}"])
        _scrivi(posta_pub, [f"POSTA_CHIAVE_PUBBLICA={pub}"])
        esiti.append(f"Creati {posta_priv} e {posta_pub} (coppia della posta per gli inviti).")
    else:
        esiti.append(f"{posta_priv} e {posta_pub} esistono già: lasciati com'erano.")

    # R17 (VD-P-164): coppia Ed25519 della conferma firmata delle decisioni (privata e kid
    # al gateway, pubblica a ciclo-vita); stesso formato di libs/shared/conferme_decisione.py
    conferme, conferme_pub = infra / "dev_conferme.env", infra / "dev_conferme_pub.env"
    if forza or not conferme.exists() or not conferme_pub.exists():
        seme, pubblica = _coppia_ed25519_grezza()
        kid = "dev-" + secrets.token_hex(4)
        _scrivi(conferme, [f"CONFERME_CHIAVE_PRIVATA={seme}", f"CONFERME_KID={kid}"])
        _scrivi(conferme_pub, ["CONFERME_CHIAVI_PUBBLICHE="
                               + json.dumps({kid: pubblica}, separators=(",", ":"))])
        esiti.append(f"Creati {conferme} e {conferme_pub} (coppia delle conferme delle decisioni).")
    else:
        esiti.append(f"{conferme} e {conferme_pub} esistono già: lasciati com'erano.")
    return esiti


def main() -> int:
    parser = argparse.ArgumentParser(description="Segreti di sviluppo locale di VerumDoc")
    parser.add_argument("--forza", action="store_true", help="rigenera anche i file esistenti")
    args = parser.parse_args()
    radice = Path.cwd()
    if not ((radice / "docker-compose.yml").is_file() and (radice / "services").is_dir()):
        print("ERRORE: lancia lo script dalla radice del monorepo VerumDoc.", file=sys.stderr)  # noqa: T201
        return 2
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("ERRORE: manca il pacchetto «cryptography»: pip install cryptography",  # noqa: T201
              file=sys.stderr)
        return 2
    for riga in genera(radice, forza=args.forza):
        print(riga)  # noqa: T201
    print("File solo per lo sviluppo locale: non committarli.")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
