# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/infrastructure/gateway.py — versamenti del ciclo di vita tramite il gateway.

Riferimenti: contratto Q2 di R05 (la rettifica crea un PdV che segue la pipeline
normale; il riversamento in ingresso versa con le sessioni di caricamento);
contratti C1 (sessioni di caricamento) e C2 (token di canale) di R03; REGOLE
§9.1 (un solo caso d'uso di versamento: ciclo-vita NON versa da sé, usa il
gateway come ogni canale), §12.11 (effetti esterni dopo il commit, ripresa
idempotente con ``Idempotency-Key`` derivata dalla pratica).

Identità: token di canale ``CICLO_VITA`` (stessa forma del token SFTP,
``libs.shared.security.token_canale``) firmato con la chiave PROPRIA del
servizio (``CICLO_VITA_TOKEN_CHIAVE_PRIVATA``), per conto dell'utente che ha
avviato la pratica e con il suo ruolo attuale letto dal database. Il gateway
lo accetta (integrazione R05: canale CICLO_VITA in
``CANALI_CON_TOKEN_DI_SERVIZIO``, account ``public.pratiche_canale_versamento``)
se ha la chiave pubblica del servizio in ``CANALE_CHIAVI_PUBBLICHE``; senza,
risponde 401 e la pratica resta in attesa con l'errore registrato.
"""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

import httpx
import structlog

from app.config import settings
from libs.shared.security.token_canale import firma_token_canale

logger = structlog.get_logger(__name__)

NOME_SERVIZIO = "ciclo-vita"
CANALE = "CICLO_VITA"
DURATA_TOKEN_SECONDI = 600


class ErroreGateway(Exception):
    """Chiamata al gateway non riuscita; ``problema`` è il problem+json ricevuto."""

    def __init__(self, messaggio: str, *, status: int | None, problema: dict[str, Any] | None,
                 ritentabile: bool) -> None:
        super().__init__(messaggio)
        self.status = status
        self.problema = problema or {}
        self.ritentabile = ritentabile


class Versatore(Protocol):
    """Interfaccia usata dai casi d'uso (il gateway reale o un finto nei test)."""

    async def carica(self, per_conto: dict[str, str], *, nome_file: str, dimensione: int,
                     sha256: str, blocchi: Callable[[], AsyncIterator[bytes]],
                     chiave: str) -> str: ...

    async def versa(self, per_conto: dict[str, str], *, indice: dict[str, Any],
                    file: dict[str, str], chiave: str) -> dict[str, Any]: ...


def token_canale(per_conto: dict[str, str]) -> str:
    """Token di canale CICLO_VITA per l'utente ``per_conto`` (tenant_id, user_id, ruolo)."""
    chiave = settings.ciclo_vita_token_chiave_privata.get_secret_value()
    if not chiave:
        raise ErroreGateway("chiave del token di canale non configurata "
                            "(CICLO_VITA_TOKEN_CHIAVE_PRIVATA)", status=None,
                            problema={"codice": "LC-503-GATEWAY"}, ritentabile=True)
    return firma_token_canale(
        NOME_SERVIZIO, tenant_id=per_conto["tenant_id"], user_id=per_conto["user_id"],
        ruolo=per_conto["ruolo"], canale=CANALE, chiave_privata_pem=chiave,
        emittente=settings.jwt_issuer, durata_secondi=DURATA_TOKEN_SECONDI)


class ClientGateway:
    """Sessione di caricamento a una parte per file + versamento per riferimento."""

    def __init__(self, base_url: str | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 token: Callable[[dict[str, str]], str] = token_canale) -> None:
        self._base = (base_url or settings.gateway_url).rstrip("/")
        self._transport = transport
        self._token = token

    async def _richiesta(self, metodo: str, percorso: str, per_conto: dict[str, str], *,
                         operazione: str, **argomenti: Any) -> dict[str, Any]:
        intestazioni = {"Authorization": f"Bearer {self._token(per_conto)}",
                        **argomenti.pop("headers", {})}
        try:
            async with httpx.AsyncClient(base_url=self._base, transport=self._transport,
                                         timeout=settings.gateway_timeout_secondi) as c:
                r = await c.request(metodo, percorso, headers=intestazioni, **argomenti)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ErroreGateway(f"gateway non raggiungibile durante «{operazione}» "
                                f"({type(exc).__name__})", status=None, problema=None,
                                ritentabile=True) from None
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code >= 400:
            problema = corpo if isinstance(corpo, dict) else {}
            ritentabile = bool(problema.get("ritentabile")) or r.status_code >= 500 \
                or r.status_code == 429
            logger.info("ciclo_vita_gateway_errore", operazione=operazione,
                        status=r.status_code, codice=problema.get("codice"))
            raise ErroreGateway(f"«{operazione}»: {r.status_code} {problema.get('codice') or ''}",
                                status=r.status_code, problema=problema, ritentabile=ritentabile)
        return corpo if isinstance(corpo, dict) else {}

    async def carica(self, per_conto: dict[str, str], *, nome_file: str, dimensione: int,
                     sha256: str, blocchi: Callable[[], AsyncIterator[bytes]],
                     chiave: str) -> str:
        """Sessione di caricamento (contratto C1) a parte unica se il file sta in
        una parte, altrimenti a più parti dalla stessa sorgente a blocchi."""
        sessione = await self._richiesta(
            "POST", "/api/v1/caricamenti", per_conto, operazione="apertura caricamento",
            headers={"Idempotency-Key": chiave},
            json={"nome_file": nome_file, "dimensione": dimensione, "mime_dichiarato": None,
                  "impronta": {"algoritmo": "SHA-256", "valore": sha256}})
        caricamento_id = str(sessione["caricamento_id"])
        dimensione_parte = int(sessione.get("dimensione_parte") or dimensione or 1)
        numero = 1
        buffer = bytearray()
        async for blocco in blocchi():
            buffer.extend(blocco)
            while len(buffer) >= dimensione_parte:
                await self._parte(per_conto, caricamento_id, numero,
                                  bytes(buffer[:dimensione_parte]))
                del buffer[:dimensione_parte]
                numero += 1
        if buffer or numero == 1:
            await self._parte(per_conto, caricamento_id, numero, bytes(buffer))
        await self._richiesta("POST", f"/api/v1/caricamenti/{caricamento_id}/completa", per_conto,
                              operazione="completamento caricamento")
        return caricamento_id

    async def _parte(self, per_conto: dict[str, str], caricamento_id: str, numero: int,
                     dati: bytes) -> None:
        await self._richiesta(
            "PUT", f"/api/v1/caricamenti/{caricamento_id}/parti/{numero}", per_conto,
            operazione="invio parte", content=dati,
            headers={"Content-Type": "application/octet-stream",
                     "X-Impronta-Parte": f"sha256={hashlib.sha256(dati).hexdigest()}"})

    async def versa(self, per_conto: dict[str, str], *, indice: dict[str, Any],
                    file: dict[str, str], chiave: str) -> dict[str, Any]:
        return await self._richiesta(
            "POST", "/api/v1/versamento/ud", per_conto, operazione="versamento",
            headers={"Idempotency-Key": chiave}, json={"indice": indice, "file": file})


__all__ = ["CANALE", "ClientGateway", "ErroreGateway", "NOME_SERVIZIO", "Versatore",
           "token_canale"]
