# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/personale.py — firma PERSONALE con OTP del responsabile
della conservazione (R16, VD-C-166, VD-P-140).

La persona ha una credenziale personale presso un fornitore CSC del registro
(``credenziali_firma_personali``: ``credentialID`` e impronta ATTESA del certificato, nessun
segreto). Il cliente OAuth2 è quello del fornitore (Secret del servizio di firma, legato agli
host, §31 R13a (2)); l'OTP (e il PIN, se la credenziale lo chiede) li dà la persona PER
QUESTA firma: non si conservano, non si registrano, non si ritentano (``idempotente=False``
in ``FornitoreFirmaCSC._autorizza``).

Controlli (§31 R13a (2), R13b (2)):
  - l'adattatore si costruisce SOLO da un fornitore CSC attivo, costruito senza problemi
    dal registro (stessi controlli di configurazione, segreti e host del sigillo);
  - l'identità attesa è il certificato della CREDENZIALE della persona (mai quello del
    sigillo del conservatore): ``IdentitaAttesa`` lo controlla sul materiale e
    ``ServizioFirma`` di nuovo sulla firma prodotta, che è VERIFICATA prima di restituirla;
  - l'adattatore firma UNA volta sola (OTP monouso): un secondo uso è un errore.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asn1crypto import x509

from app.services.csc_client import FornitoreFirmaCSC
from app.services.fornitori import (
    ErroreFornitore,
    FornitoreFirma,
    IdentitaAttesa,
    MaterialeFirma,
)


@dataclass(frozen=True)
class CredenzialePersonale:
    id: str
    user_id: str
    fornitore_codice: str
    credential_id: str
    certificato_sha256: str


class CredenzialeAssente(LookupError):
    """La persona non ha una credenziale personale attiva."""


SQL_CREDENZIALE = (
    "SELECT id::text, user_id::text, fornitore_codice, credential_id, certificato_sha256 "
    "FROM public.credenziali_firma_personali WHERE user_id = CAST(:u AS uuid) AND attiva")


async def leggi_credenziale(user_id: str) -> CredenzialePersonale:
    """Credenziale attiva della persona (login di sola lettura del servizio di firma)."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session

    async with get_async_session() as s:
        riga = (await s.execute(text(SQL_CREDENZIALE), {"u": user_id})).mappings().first()
    if riga is None:
        raise CredenzialeAssente(user_id)
    return CredenzialePersonale(str(riga["id"]), str(riga["user_id"]),
                                str(riga["fornitore_codice"]), str(riga["credential_id"]),
                                str(riga["certificato_sha256"]).strip().lower())


class FornitoreFirmaPersonale(FornitoreFirma):
    """Adattatore di UNA firma personale: delega a ``FornitoreFirmaCSC.firma_impronta_con_otp``
    con l'OTP (e il PIN) della persona."""

    nome = "csc-personale"
    di_sviluppo = False

    def __init__(self, base: FornitoreFirmaCSC, *, otp: str, pin: str,
                 certificato_sha256: str) -> None:
        self._base = base
        self._otp = otp
        self._pin = pin
        self._usato = False
        self.codice = base.codice
        self.radici_proprie: tuple[x509.Certificate, ...] = base.radici_proprie
        self.identita = IdentitaAttesa(frozenset({certificato_sha256}))
        base.identita = self.identita

    def __repr__(self) -> str:  # mai OTP né PIN
        return f"FornitoreFirmaPersonale(codice={self.codice!r})"

    @property
    def info(self) -> dict[str, Any]:
        return dict(self._base.info)

    async def materiale(self) -> MaterialeFirma:
        return await self._base.materiale()

    async def firma_impronta(self, impronta: bytes, algoritmo_digest: str) -> bytes:
        if self._usato:
            raise ErroreFornitore("OTP già usato per una firma", {"servizio": "csc",
                                                                 "motivo": "otp_usato"})
        self._usato = True
        return await self._base.firma_impronta_con_otp(impronta, algoritmo_digest,
                                                       otp=self._otp, pin=self._pin)

    async def invia_otp(self) -> None:
        await self._base.invia_otp()

    async def chiudi(self) -> None:
        await self._base.chiudi()


def errore_di_autorizzazione(exc: ErroreFornitore) -> bool:
    """True se il prestatore ha rifiutato OTP o PIN (errore della persona, non del servizio)."""
    d = exc.dettagli or {}
    return d.get("operazione") == "credentials/authorize" and d.get("errore") in (
        "invalid_otp", "invalid_pin", "invalid_request") or d.get("motivo") in (
        "otp_richiesto", "otp_usato")


__all__ = ["CredenzialeAssente", "CredenzialePersonale", "FornitoreFirmaPersonale",
           "errore_di_autorizzazione", "leggi_credenziale"]
