# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/credenziali.py — credenziali locali (R10, D08).

Riferimenti: decisione D08 (accesso dei clienti privati senza SPID); REGOLE
§19.3 (segreti), §19.1; NIST SP 800-63B (password: lunghezza, nessuna regola
di composizione; secondo fattore; limitazione dei tentativi).

  - password: bcrypt (costo 12; la libreria è già del gateway), al più 72 byte
    (limite di bcrypt: oltre si rifiuta, mai un troncamento silenzioso),
    lunghezza minima dal parametro ``PASSWORD_LUNGHEZZA_MINIMA``;
  - codice a tempo TOTP (RFC 6238, pyotp): 6 cifre, passo di 30 s, tolleranza di
    un passo; un codice già usato (passo non successivo all'ultimo) è rifiutato;
    segreto CIFRATO con AES-256-GCM (``libs.shared.security.cifratura``, chiave
    ``CREDENZIALI_CHIAVE_CIFRATURA``, dato associato ``totp:<user_id>``);
  - codici di recupero: 10 codici monouso da 80 bit, salvati come impronta SHA-256;
  - passkey (WebAuthn, py_webauthn): verifica di registrazione e di firma, con
    contatore delle firme che non torna indietro;
  - impronta fittizia: con un'e-mail sconosciuta si calcola comunque un bcrypt,
    così il tempo di risposta non rivela se l'utente esiste.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any

import bcrypt
import pyotp

from app.config import settings
from libs.shared.security.cifratura import Cifratore, ErroreCifratura

COSTO_BCRYPT = 12
MASSIMO_BYTE_PASSWORD = 72
TOTP_CIFRE = 6
TOTP_PASSO = 30
TOTP_TOLLERANZA = 1
CODICI_RECUPERO = 10
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # senza 0/O, 1/I: si dettano al telefono


class CredenzialiNonDisponibili(RuntimeError):
    """Chiave di cifratura dei segreti TOTP non configurata o non valida."""


# ── Password ─────────────────────────────────────────────────────────

def motivo_password_non_ammessa(password: str, *, minimo: int, email: str = "") -> str | None:
    if len(password) < minimo:
        return f"La password deve avere almeno {minimo} caratteri"
    if len(password.encode("utf-8")) > MASSIMO_BYTE_PASSWORD:
        return f"La password può avere al più {MASSIMO_BYTE_PASSWORD} byte"
    if password.strip() != password:
        return "La password non può iniziare o finire con uno spazio"
    if len(set(password)) < 4:
        return "La password è troppo ripetitiva"
    if email and email.split("@")[0].lower() in password.lower() and len(email) > 3:
        return "La password non può contenere il nome dell'indirizzo e-mail"
    return None


def impronta_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(COSTO_BCRYPT)).decode("ascii")


def verifica_password(password: str, impronta: str | None) -> bool:
    """Confronto a tempo costante; senza impronta si usa quella fittizia (stesso costo)."""
    dati = password.encode("utf-8")[:MASSIMO_BYTE_PASSWORD]
    bersaglio = (impronta or _IMPRONTA_FITTIZIA).encode("ascii")
    try:
        esito = bcrypt.checkpw(dati, bersaglio)
    except ValueError:
        return False
    return bool(esito and impronta)


_IMPRONTA_FITTIZIA = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(COSTO_BCRYPT)
                                   ).decode("ascii")


# ── TOTP ─────────────────────────────────────────────────────────────

def cifratore() -> Cifratore:
    try:
        c = Cifratore.da_impostazioni(
            settings.credenziali_chiave_cifratura.get_secret_value(),
            settings.credenziali_chiave_cifratura_id,
            settings.credenziali_chiavi_cifratura_precedenti.get_secret_value(),
            nome="CREDENZIALI_CHIAVE_CIFRATURA",
            nome_precedenti="CREDENZIALI_CHIAVI_CIFRATURA_PRECEDENTI")
    except ErroreCifratura as exc:
        raise CredenzialiNonDisponibili(str(exc)) from None
    if c is None:
        raise CredenzialiNonDisponibili("CREDENZIALI_CHIAVE_CIFRATURA non configurata")
    return c


def contesto_totp(user_id: str) -> str:
    return f"totp:{user_id}"


def nuovo_segreto_totp() -> str:
    return pyotp.random_base32(32)


def cifra_segreto(segreto: str, user_id: str) -> str:
    return cifratore().cifra(segreto, contesto_totp(user_id))


def decifra_segreto(salvato: str, user_id: str) -> str:
    try:
        return cifratore().decifra(salvato, contesto_totp(user_id))
    except ErroreCifratura as exc:
        raise CredenzialiNonDisponibili(str(exc)) from None


def uri_totp(segreto: str, *, email: str, emittente: str = "VerumDoc") -> str:
    return pyotp.TOTP(segreto, digits=TOTP_CIFRE, interval=TOTP_PASSO).provisioning_uri(
        name=email, issuer_name=emittente)


def passo_totp_valido(segreto: str, codice: str, *, ultimo_passo: int,
                      adesso: float | None = None) -> int | None:
    """Passo del codice se valido (±1 passo) e successivo all'ultimo usato; altrimenti None."""
    codice = (codice or "").strip().replace(" ", "")
    if len(codice) != TOTP_CIFRE or not codice.isdigit():
        return None
    istante = time.time() if adesso is None else adesso
    totp = pyotp.TOTP(segreto, digits=TOTP_CIFRE, interval=TOTP_PASSO)
    attuale = int(istante // TOTP_PASSO)
    for passo in range(attuale - TOTP_TOLLERANZA, attuale + TOTP_TOLLERANZA + 1):
        if passo <= ultimo_passo:
            continue  # un codice già usato non vale di nuovo
        if hmac.compare_digest(totp.at(passo * TOTP_PASSO), codice):
            return passo
    return None


# ── Codici di recupero ───────────────────────────────────────────────

def nuovi_codici_recupero(quanti: int = CODICI_RECUPERO) -> list[str]:
    codici = []
    for _ in range(quanti):
        grezzo = "".join(secrets.choice(_ALFABETO) for _ in range(16))
        codici.append("-".join(grezzo[i:i + 4] for i in range(0, 16, 4)))
    return codici


def impronta_codice_recupero(codice: str) -> str:
    normale = "".join(c for c in (codice or "").upper() if c.isalnum())
    return hashlib.sha256(normale.encode("ascii", "ignore")).hexdigest()


# ── Passkey (WebAuthn) ───────────────────────────────────────────────

def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def _origine(url: str) -> str:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def rp_id(configurazione: dict[str, Any] | None = None) -> str:
    conf = configurazione or {}
    return str(conf.get("rp_id") or settings.webauthn_rp_id or _host(settings.base_url_console))


def origini(configurazione: dict[str, Any] | None = None) -> list[str]:
    conf = configurazione or {}
    elenco = conf.get("origini") if isinstance(conf.get("origini"), list) else None
    return [str(o) for o in (elenco or settings.webauthn_origini
                             or [_origine(settings.base_url_console)])]


def b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def da_b64url(testo: str) -> bytes:
    return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))


@dataclass(frozen=True)
class PasskeyRegistrata:
    credential_id: bytes
    chiave_pubblica: bytes
    contatore: int


def opzioni_registrazione(*, user_id: str, email: str, nome: str, sfida: bytes,
                          escludi: list[bytes], configurazione: dict[str, Any] | None = None
                          ) -> str:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    opzioni = generate_registration_options(
        rp_id=rp_id(configurazione), rp_name=settings.webauthn_rp_nome, user_name=email,
        user_id=user_id.encode("ascii"), user_display_name=nome, challenge=sfida,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in escludi])
    return str(options_to_json(opzioni))


def verifica_registrazione(credenziale: dict[str, Any], *, sfida: bytes,
                           configurazione: dict[str, Any] | None = None) -> PasskeyRegistrata:
    from webauthn import verify_registration_response

    esito = verify_registration_response(
        credential=credenziale, expected_challenge=sfida,
        expected_rp_id=rp_id(configurazione), expected_origin=origini(configurazione),
        require_user_verification=True)
    return PasskeyRegistrata(esito.credential_id, esito.credential_public_key, esito.sign_count)


def opzioni_accesso(*, sfida: bytes, configurazione: dict[str, Any] | None = None) -> str:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    # nessun elenco di credenziali: passkey individuabili, nessuna enumerazione
    return str(options_to_json(generate_authentication_options(
        rp_id=rp_id(configurazione), challenge=sfida,
        user_verification=UserVerificationRequirement.REQUIRED)))


def verifica_accesso(credenziale: dict[str, Any], *, sfida: bytes, chiave_pubblica: bytes,
                     contatore: int, configurazione: dict[str, Any] | None = None) -> int:
    """Nuovo contatore delle firme se la firma è valida; eccezione altrimenti."""
    from webauthn import verify_authentication_response

    esito = verify_authentication_response(
        credential=credenziale, expected_challenge=sfida, expected_rp_id=rp_id(configurazione),
        expected_origin=origini(configurazione), credential_public_key=chiave_pubblica,
        credential_current_sign_count=contatore, require_user_verification=True)
    return int(esito.new_sign_count)


__all__ = [
    "CODICI_RECUPERO", "CredenzialiNonDisponibili", "PasskeyRegistrata", "b64url",
    "cifra_segreto", "da_b64url", "decifra_segreto", "impronta_codice_recupero",
    "impronta_password", "motivo_password_non_ammessa", "nuovi_codici_recupero",
    "nuovo_segreto_totp", "opzioni_accesso", "opzioni_registrazione", "passo_totp_valido",
    "uri_totp", "verifica_accesso", "verifica_password", "verifica_registrazione",
]
