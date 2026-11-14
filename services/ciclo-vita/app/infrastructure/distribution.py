# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/infrastructure/distribution.py — PdD MIGRAZIONE tramite l'API di distribution.

Riferimenti: contratto Q2 di R05 (riversamento in uscita e restituzione:
pacchetti di distribuzione MIGRAZIONE completi, SENZA duplicare la logica di
distribution); REGOLE_SVILUPPO §9.1, §20.1 (un client per sistema esterno).

Chiamate (API esistente di distribution, ``routers/migration.py``):
    POST /api/v2/migration/export-batch   {aip_ids ≤ 100, conservatore_destinatario,
                                           motivo, ente_richiedente}
         → {results: [{aip_id, pdd_id, stato}], errori: [{aip_id, errore}]}
    GET  /api/v2/migration/status/{pdd_id}?ente=<ente>
         → {stato: IN_PREPARAZIONE|PRONTO|ERRORE|SCADUTO, hash_pdd, zip?: {hash_sha256, ...}}
con ``Authorization: Bearer <token di servizio>`` (aud «verumdoc-distribution»,
scope «migrazione:servizio»). Oggi distribution accetta solo il JWT di una
persona RSC/SUPER_ADMIN: la modifica esatta è una richiesta al coordinatore
(rapporto R05). Fino ad allora la risposta è 401/403 e la pratica resta in
attesa con l'errore registrato (stato onesto, nessun pacchetto inventato).
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


class ErroreDistribution(Exception):
    def __init__(self, messaggio: str, *, status: int | None, ritentabile: bool,
                 codice: str | None = None) -> None:
        super().__init__(messaggio)
        self.status = status
        self.ritentabile = ritentabile
        self.codice = codice


class Esportatore(Protocol):
    async def esporta(self, *, ente: str, aip_ids: list[str], destinatario: str,
                      motivo: str) -> dict[str, Any]: ...

    async def stato(self, *, ente: str, pdd_id: str) -> dict[str, Any]: ...


class ClientDistribution:
    def __init__(self, base_url: str | None = None, token: str | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = (base_url or settings.distribution_url).rstrip("/")
        self._token = token if token is not None else \
            settings.distribution_token_servizio.get_secret_value()
        self._transport = transport

    async def _chiama(self, metodo: str, percorso: str, **argomenti: Any) -> dict[str, Any]:
        if not self._token:
            raise ErroreDistribution("token di servizio verso distribution non configurato "
                                     "(DISTRIBUTION_TOKEN_SERVIZIO)", status=None,
                                     ritentabile=True, codice="LC-503-DISTRIBUTION")
        try:
            async with httpx.AsyncClient(base_url=self._base, transport=self._transport,
                                         timeout=settings.distribution_timeout_secondi) as c:
                r = await c.request(metodo, percorso,
                                    headers={"Authorization": f"Bearer {self._token}"},
                                    **argomenti)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ErroreDistribution(f"distribution non raggiungibile ({type(exc).__name__})",
                                     status=None, ritentabile=True,
                                     codice="LC-503-DISTRIBUTION") from None
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code >= 400:
            codice = corpo.get("codice") if isinstance(corpo, dict) else None
            logger.warning("distribution_rifiuto", status=r.status_code, codice=codice,
                           percorso=percorso.split("?")[0])
            raise ErroreDistribution(f"distribution ha risposto {r.status_code} ({codice})",
                                     status=r.status_code, codice=codice,
                                     ritentabile=r.status_code >= 500 or r.status_code in (
                                         401, 403, 429))
        return corpo if isinstance(corpo, dict) else {}

    async def esporta(self, *, ente: str, aip_ids: list[str], destinatario: str,
                      motivo: str) -> dict[str, Any]:
        return await self._chiama("POST", "/api/v2/migration/export-batch", json={
            "aip_ids": aip_ids, "conservatore_destinatario": destinatario,
            "motivo": motivo, "ente_richiedente": ente})

    async def stato(self, *, ente: str, pdd_id: str) -> dict[str, Any]:
        return await self._chiama("GET", f"/api/v2/migration/status/{pdd_id}",
                                  params={"ente": ente})


__all__ = ["ClientDistribution", "ErroreDistribution", "Esportatore"]
