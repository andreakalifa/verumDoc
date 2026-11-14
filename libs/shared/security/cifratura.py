# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/cifratura.py — cifratura a riposo dei segreti (AES-256-GCM).

Una sola implementazione (REGOLE §9.1, §19.3) per i segreti dei webhook
(notification, contratto C3) e, da R10 (D08), per i segreti TOTP del gateway.
Chiave d'ambiente di 32 byte in base64; formato salvato:
    aesgcm$<id chiave>$<base64url(nonce 12 byte || testo cifrato || tag)>
Il dato associato (AAD) lega il segreto alla riga (per esempio
«<tenant_id>:<sottoscrizione_id>» o «totp:<user_id>»): un segreto copiato su
un'altra riga non si decifra. L'id della chiave permette la rotazione: si cifra
sempre con la chiave corrente e si decifra con quella indicata sulla riga.
Né la chiave né i segreti compaiono nei log o nel repr.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMATO = "aesgcm"
LUNGHEZZA_CHIAVE = 32
LUNGHEZZA_NONCE = 12


class ErroreCifratura(RuntimeError):
    """Configurazione della chiave assente o non valida, o segreto non decifrabile."""


def _decodifica_chiave(valore: str, nome: str) -> bytes:
    try:
        chiave = base64.b64decode(valore.strip(), validate=True)
    except (binascii.Error, ValueError):
        try:
            chiave = base64.urlsafe_b64decode(valore.strip())
        except (binascii.Error, ValueError):
            raise ErroreCifratura(f"{nome}: non è base64") from None
    if len(chiave) != LUNGHEZZA_CHIAVE:
        raise ErroreCifratura(f"{nome}: servono {LUNGHEZZA_CHIAVE} byte (AES-256), "
                              f"ricevuti {len(chiave)}")
    return chiave


@dataclass(frozen=True)
class Cifratore:
    """Chiave corrente e precedenti. Nessuna chiave nel repr."""

    id_corrente: str
    chiavi: dict[str, bytes] = field(repr=False)

    @classmethod
    def da_impostazioni(cls, chiave: str, id_chiave: str, precedenti: str = "", *,
                        nome: str = "WEBHOOK_CHIAVE_CIFRATURA",
                        nome_precedenti: str = "WEBHOOK_CHIAVI_CIFRATURA_PRECEDENTI",
                        ) -> Cifratore | None:
        """None se la chiave corrente non è configurata; ``ErroreCifratura`` se non valida.
        ``nome``/``nome_precedenti``: variabili d'ambiente citate nei messaggi."""
        if not (chiave or "").strip():
            return None
        if not id_chiave or "$" in id_chiave:
            raise ErroreCifratura(f"{nome}_ID vuoto o con «$»")
        chiavi = {id_chiave: _decodifica_chiave(chiave, nome)}
        if (precedenti or "").strip():
            try:
                mappa = json.loads(precedenti)
            except json.JSONDecodeError:
                raise ErroreCifratura(f"{nome_precedenti}: JSON non valido") from None
            if not isinstance(mappa, dict):
                raise ErroreCifratura(f"{nome_precedenti}: serve un oggetto JSON")
            for kid, valore in mappa.items():
                if kid in chiavi or "$" in str(kid):
                    raise ErroreCifratura(f"chiave precedente {kid!r} duplicata o con «$»")
                chiavi[str(kid)] = _decodifica_chiave(str(valore), f"chiave precedente {kid}")
        return cls(id_corrente=id_chiave, chiavi=chiavi)

    def cifra(self, segreto: str, contesto: str) -> str:
        nonce = os.urandom(LUNGHEZZA_NONCE)
        cifrato = AESGCM(self.chiavi[self.id_corrente]).encrypt(
            nonce, segreto.encode("utf-8"), contesto.encode("utf-8"))
        blob = base64.urlsafe_b64encode(nonce + cifrato).decode("ascii")
        return f"{FORMATO}${self.id_corrente}${blob}"

    def decifra(self, salvato: str, contesto: str) -> str:
        formato, _, resto = (salvato or "").partition("$")
        id_chiave, _, blob = resto.partition("$")
        if formato != FORMATO or not blob:
            raise ErroreCifratura("formato del segreto salvato non riconosciuto")
        chiave = self.chiavi.get(id_chiave)
        if chiave is None:
            raise ErroreCifratura(f"chiave {id_chiave!r} non configurata")
        try:
            dati = base64.urlsafe_b64decode(blob.encode("ascii"))
            testo = AESGCM(chiave).decrypt(dati[:LUNGHEZZA_NONCE], dati[LUNGHEZZA_NONCE:],
                                           contesto.encode("utf-8"))
        except (InvalidTag, binascii.Error, ValueError):
            raise ErroreCifratura("segreto non decifrabile con la chiave indicata") from None
        return testo.decode("utf-8")


def contesto_segreto(tenant_id: str, sottoscrizione_id: str) -> str:
    """Dato associato della cifratura: lega il segreto all'ente e alla riga."""
    return f"{tenant_id}:{sottoscrizione_id}"
