# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/sigillo.py — segreti sigillati per un solo destinatario
(R12a, VD-C-152).

Un segreto che deve attraversare una coda persistente (outbox di onboarding,
``notification.send``, ``public.notifications``) prima di essere usato da UN
SOLO servizio si sigilla con la chiave PUBBLICA di quel servizio: chi legge il
database o il topic vede solo il sigillo; lo apre solo chi ha la chiave privata
(il servizio di posta, al momento dell'invio).

Costruzione (cifratura ibrida, nessuna dipendenza nuova):
  X25519 effimero ⟶ segreto condiviso ⟶ HKDF-SHA256 (info lega le due chiavi
  pubbliche) ⟶ AES-256-GCM con il CONTESTO come dato associato.
Formato: ``vdsig1$<id chiave>$<base64url(pubblica effimera 32 B || nonce 12 B || cifrato+tag)>``.

Il contesto lega il sigillo al messaggio (ente, oggetto, destinatario): un
sigillo copiato in un'altra riga, o una riga a cui qualcuno cambia il
destinatario, non si apre (la posta NON parte verso un indirizzo diverso da
quello scelto da chi ha sigillato). L'id della chiave permette la rotazione
(chiavi private precedenti solo per aprire).
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMATO = "vdsig1"
_INFO = b"verumdoc-sigillo-v1"
_RAW = serialization.Encoding.Raw


class ErroreSigillo(RuntimeError):
    """Chiave assente o non valida, sigillo malformato o non apribile."""


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode("ascii").rstrip("=")


def _da_b64(testo: str, nome: str, lunghezza: int | None = None) -> bytes:
    pulito = (testo or "").strip()
    try:
        dati = base64.urlsafe_b64decode(pulito + "=" * (-len(pulito) % 4))
    except (binascii.Error, ValueError):
        raise ErroreSigillo(f"{nome}: non è base64") from None
    if lunghezza is not None and len(dati) != lunghezza:
        raise ErroreSigillo(f"{nome}: servono {lunghezza} byte, ricevuti {len(dati)}")
    return dati


def _pubblica_raw(chiave: X25519PublicKey) -> bytes:
    return chiave.public_bytes(_RAW, serialization.PublicFormat.Raw)


def genera_coppia() -> tuple[str, str]:
    """(privata, pubblica) X25519 in base64url: per i segreti di sviluppo e i test."""
    privata = X25519PrivateKey.generate()
    grezza = privata.private_bytes(_RAW, serialization.PrivateFormat.Raw,
                                   serialization.NoEncryption())
    return _b64(grezza), _b64(_pubblica_raw(privata.public_key()))


def _chiave_aead(condiviso: bytes, effimera: bytes, destinatario: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=_INFO + effimera + destinatario).derive(condiviso)


def sigilla(segreto: str, pubblica: str, contesto: str, *, id_chiave: str = "p1") -> str:
    """Sigilla ``segreto`` per il titolare della chiave privata di ``pubblica``."""
    if not id_chiave or "$" in id_chiave:
        raise ErroreSigillo("id della chiave vuoto o con «$»")
    destinatario = _da_b64(pubblica, "chiave pubblica del sigillo", 32)
    effimera = X25519PrivateKey.generate()
    effimera_pub = _pubblica_raw(effimera.public_key())
    condiviso = effimera.exchange(X25519PublicKey.from_public_bytes(destinatario))
    nonce = os.urandom(12)
    cifrato = AESGCM(_chiave_aead(condiviso, effimera_pub, destinatario)).encrypt(
        nonce, segreto.encode("utf-8"), contesto.encode("utf-8"))
    return f"{FORMATO}${id_chiave}${_b64(effimera_pub + nonce + cifrato)}"


def e_sigillo(valore: object) -> bool:
    return isinstance(valore, str) and valore.startswith(FORMATO + "$")


@dataclass(frozen=True)
class Apritore:
    """Chiavi private (corrente e precedenti) del servizio destinatario. Nessuna chiave nel repr."""

    chiavi: dict[str, X25519PrivateKey] = field(repr=False)

    @classmethod
    def da_impostazioni(cls, privata: str, id_chiave: str = "p1", precedenti: str = "", *,
                        nome: str = "POSTA_CHIAVE_PRIVATA") -> Apritore | None:
        """None se la chiave non è configurata; ``ErroreSigillo`` se non valida.
        ``precedenti``: JSON {"p0": "<privata base64url>"} delle chiavi ritirate."""
        if not (privata or "").strip():
            return None
        if not id_chiave or "$" in id_chiave:
            raise ErroreSigillo(f"{nome}_ID vuoto o con «$»")
        chiavi = {id_chiave: X25519PrivateKey.from_private_bytes(_da_b64(privata, nome, 32))}
        if (precedenti or "").strip():
            try:
                mappa = json.loads(precedenti)
            except json.JSONDecodeError:
                raise ErroreSigillo(f"{nome}_PRECEDENTI: JSON non valido") from None
            if not isinstance(mappa, dict):
                raise ErroreSigillo(f"{nome}_PRECEDENTI: serve un oggetto JSON")
            for kid, valore in mappa.items():
                if kid in chiavi or "$" in str(kid):
                    raise ErroreSigillo(f"chiave precedente {kid!r} duplicata o con «$»")
                chiavi[str(kid)] = X25519PrivateKey.from_private_bytes(
                    _da_b64(str(valore), f"chiave precedente {kid}", 32))
        return cls(chiavi=chiavi)

    def apri(self, sigillo: str, contesto: str) -> str:
        try:
            formato, kid, corpo = sigillo.split("$", 2)
        except ValueError:
            raise ErroreSigillo("sigillo malformato") from None
        if formato != FORMATO:
            raise ErroreSigillo(f"formato del sigillo non riconosciuto: {formato!r}")
        privata = self.chiavi.get(kid)
        if privata is None:
            raise ErroreSigillo(f"chiave del sigillo {kid!r} non configurata")
        dati = _da_b64(corpo, "sigillo")
        if len(dati) < 32 + 12 + 16:
            raise ErroreSigillo("sigillo troppo corto")
        effimera_pub, nonce, cifrato = dati[:32], dati[32:44], dati[44:]
        destinatario = _pubblica_raw(privata.public_key())
        condiviso = privata.exchange(X25519PublicKey.from_public_bytes(effimera_pub))
        try:
            chiaro = AESGCM(_chiave_aead(condiviso, effimera_pub, destinatario)).decrypt(
                nonce, cifrato, contesto.encode("utf-8"))
        except InvalidTag:
            raise ErroreSigillo("sigillo non apribile (chiave o contesto diversi)") from None
        return chiaro.decode("utf-8")


def contesto_posta(tenant_id: str, oggetto_id: str, destinatario: str) -> str:
    """Contesto di un segreto in un messaggio di posta: ente, oggetto e destinatario
    (normalizzato come la coda: spazi tolti, minuscolo)."""
    return f"posta|{tenant_id}|{oggetto_id}|{destinatario.strip().lower()}"


#: Segnaposto nel testo della posta sostituito dal servizio di posta all'invio
SEGNAPOSTO = "{{segreto:%s}}"


__all__ = ["FORMATO", "SEGNAPOSTO", "Apritore", "ErroreSigillo", "contesto_posta", "e_sigillo",
           "genera_coppia", "sigilla"]
