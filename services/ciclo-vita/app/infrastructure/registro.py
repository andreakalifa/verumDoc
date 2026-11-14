# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/infrastructure/registro.py — client dell'API interna del registro delle
impronte (contratto Q3, realizzato da r05-catena-residui sulla hashchain).

    POST /interno/v1/registro/foglie   (token di servizio, scope registro:aggiungi)
         {tipo: "scarto"|"annullamento"|"rettifica"|"chiusura_eccezioni"|
                "minimizzazione"|"rinnovo_retention" (R09), pda: urn,
          riferimento: {pratica, ...}, impronta_documento_pratica: hex}
         → 202 {foglia_id}     (idempotente per (tipo, pda, riferimento.pratica))
    GET  /interno/v1/registro/foglie/{id} → stato, indice e checkpoint
    (409 CAT-409-RISCONTRO se il fatto non risulta dal database: permanente)

Riferimenti: REGOLE_SVILUPPO §12.11 (effetti esterni dopo il commit, ripresa
idempotente: la richiesta si ripete con gli stessi dati), §20.1 (un client per
sistema esterno, trasporto iniettabile), §20.5 (le scritture idempotenti si
possono ripetere).

``RegistroImpronte`` è l'interfaccia usata dai casi d'uso; i test usano un
finto che rispetta il contratto (``tests/finti.py``).
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx
import structlog

from app.config import settings
from libs.catena.foglie import TIPI_CICLO_VITA

logger = structlog.get_logger(__name__)

#: Revisione 2 di R05 (S2): API interna fuori da ``/api/`` (nessuna rotta
#: dell'ingress la raggiunge); la chiama solo il lavoratore (NetworkPolicy).
PERCORSO_FOGLIE = "/interno/v1/registro/foglie"
#: Tipi del ciclo di vita (R09: anche chiusura con eccezioni, minimizzazione, rinnovo
#: del blocco WORM): una sola fonte, ``libs.catena.foglie``
TIPI_FOGLIA = TIPI_CICLO_VITA


class ErroreRegistro(Exception):
    """Il registro non ha accettato la foglia. ``ritentabile``: errore temporaneo
    (rete, 5xx, 429, servizio non configurato): il lavoratore ritenta più tardi."""

    def __init__(self, messaggio: str, *, ritentabile: bool, codice: str | None = None) -> None:
        super().__init__(messaggio)
        self.ritentabile = ritentabile
        self.codice = codice


class RegistroImpronte(Protocol):
    async def aggiungi_foglia(self, *, tipo: str, pda: str, riferimento: dict[str, Any],
                              impronta_documento_pratica: str) -> str: ...


class ClientRegistro:
    """Client HTTP del contratto Q3 (token di servizio da impostazione)."""

    def __init__(self, base_url: str | None = None, token: str | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = (base_url or settings.hashchain_url).rstrip("/")
        self._token = token if token is not None else \
            settings.hashchain_token_registro.get_secret_value()
        self._transport = transport

    async def aggiungi_foglia(self, *, tipo: str, pda: str, riferimento: dict[str, Any],
                              impronta_documento_pratica: str) -> str:
        if tipo not in TIPI_FOGLIA:
            raise ErroreRegistro(f"tipo di foglia non ammesso: {tipo!r}", ritentabile=False)
        if not self._token:
            raise ErroreRegistro("token di servizio del registro non configurato "
                                 "(HASHCHAIN_TOKEN_REGISTRO)", ritentabile=True,
                                 codice="LC-503-REGISTRO")
        corpo = {"tipo": tipo, "pda": pda, "riferimento": riferimento,
                 "impronta_documento_pratica": impronta_documento_pratica}
        try:
            async with httpx.AsyncClient(base_url=self._base, transport=self._transport,
                                         timeout=settings.hashchain_timeout_secondi) as c:
                r = await c.post(PERCORSO_FOGLIE, json=corpo,
                                 headers={"Authorization": f"Bearer {self._token}"})
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ErroreRegistro(f"registro non raggiungibile ({type(exc).__name__})",
                                 ritentabile=True, codice="LC-503-REGISTRO") from None
        if r.status_code in (200, 201, 202):
            try:
                foglia = r.json().get("foglia_id")
            except ValueError:
                foglia = None
            if not foglia:
                raise ErroreRegistro("risposta del registro senza foglia_id", ritentabile=True)
            return str(foglia)
        codice = None
        try:
            codice = r.json().get("codice")
        except ValueError:
            pass
        ritentabile = r.status_code >= 500 or r.status_code in (408, 425, 429)
        logger.warning("registro_foglia_rifiutata", status=r.status_code, codice=codice,
                       tipo=tipo)
        raise ErroreRegistro(f"il registro ha risposto {r.status_code} "
                             f"({codice or 'senza codice'})",
                             ritentabile=ritentabile, codice=codice)


__all__ = ["ClientRegistro", "ErroreRegistro", "PERCORSO_FOGLIE", "RegistroImpronte",
           "TIPI_FOGLIA"]
