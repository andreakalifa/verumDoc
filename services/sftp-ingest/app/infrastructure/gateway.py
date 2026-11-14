# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Client HTTP del gateway per il canale SFTP: sessioni di caricamento (contratto
C1) e versamento per riferimento.

Servizio: sftp-ingest. Riferimenti: contratto C1 dell'ondata B R03; REGOLE
§12.11 e §20.5 (ritentativi), §18 (errori dal catalogo).

Ritentativi: ogni chiamata si ripete (attese ``ritentativi_secondi``, oppure
``Retry-After`` se più lunga) SOLO per errori ritentabili: errori di rete e
tempi scaduti, risposte problem+json con ``ritentabile: true``, 429 e 5xx senza
corpo del catalogo. R03 f2 (rapporto c2, difetto 1): l'attesa dentro la
chiamata ha un tetto (``retry_after_max_secondi``); un ``Retry-After`` più
lungo (es. QTA-002 fino a mezzanotte) non si aspetta qui: la chiamata finisce
subito con l'errore e ``retry_after``, e il lavoratore rilascia la cartella e
programma il nuovo tentativo. Tutte le chiamate di scrittura sono idempotenti
(``Idempotency-Key`` sulla creazione della sessione e sul versamento, parti
idempotenti per contratto), quindi ripeterle è sicuro. Gli altri errori
tornano subito al chiamante con il problem+json del gateway.

Nessun file in memoria: una parte si legge dal disco a blocchi due volte (prima
per l'impronta ``X-Impronta-Parte``, poi durante l'invio).
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import structlog

from libs.shared.errori import ErroreApi, cerca, codice_generico
from libs.shared.errori.problema import corpo_problema

logger = structlog.get_logger(__name__)

BLOCCO_LETTURA = 1024 * 1024
_STATI_RITENTABILI_SENZA_CORPO = frozenset({408, 425, 429, 500, 502, 503, 504})


class ErroreGateway(Exception):
    """Chiamata al gateway non riuscita: ``problema`` è un problem+json del catalogo."""

    def __init__(self, problema: dict[str, Any], *, status: int | None,
                 retry_after: float | None = None) -> None:
        super().__init__(f"{problema.get('codice')}: {problema.get('title')}")
        self.problema = problema
        self.status = status
        #: Secondi del Retry-After NON attesi dentro la chiamata (oltre il tetto):
        #: il nuovo tentativo della cartella non va programmato prima.
        self.retry_after = retry_after

    @property
    def ritentabile(self) -> bool:
        return self.problema.get("ritentabile") is True


def _problema_canale(codice: str, dettaglio: str, **dettagli: Any) -> dict[str, Any]:
    return ErroreApi(codice, dettaglio, dettagli=dettagli).corpo()


def _problema_da_risposta(risposta: httpx.Response) -> dict[str, Any]:
    """problem+json del gateway, o uno costruito dal catalogo sullo stato HTTP."""
    try:
        corpo = risposta.json()
    except ValueError:
        corpo = None
    if isinstance(corpo, dict) and isinstance(corpo.get("codice"), str):
        return corpo
    voce = cerca(codice_generico(risposta.status_code))
    if voce is None:  # stato non previsto dal catalogo
        return _problema_canale("SFTP-201", f"Risposta HTTP {risposta.status_code} non prevista")
    problema = corpo_problema(voce, status=risposta.status_code,
                              dettaglio="Risposta del gateway senza codice del catalogo")
    if risposta.status_code in _STATI_RITENTABILI_SENZA_CORPO:
        problema["ritentabile"] = True
    return problema


def _secondi_retry_after(risposta: httpx.Response | None) -> float:
    if risposta is None:
        return 0.0
    try:
        return max(0.0, float(risposta.headers.get("Retry-After", "0")))
    except ValueError:
        return 0.0


def impronta_parte(percorso: Path, inizio: int, lunghezza: int) -> str:
    """SHA-256 esadecimale di ``lunghezza`` byte dal byte ``inizio`` (a blocchi)."""
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        f.seek(inizio)
        resto = lunghezza
        while resto > 0:
            blocco = f.read(min(BLOCCO_LETTURA, resto))
            if not blocco:
                raise OSError(f"{percorso}: file più corto del previsto")
            h.update(blocco)
            resto -= len(blocco)
    return h.hexdigest()


async def _blocchi_parte(percorso: Path, inizio: int, lunghezza: int) -> AsyncIterator[bytes]:
    f = await asyncio.to_thread(open, percorso, "rb")
    try:
        await asyncio.to_thread(f.seek, inizio)
        resto = lunghezza
        while resto > 0:
            blocco = await asyncio.to_thread(f.read, min(BLOCCO_LETTURA, resto))
            if not blocco:
                raise OSError(f"{percorso}: file più corto del previsto")
            resto -= len(blocco)
            yield blocco
    finally:
        await asyncio.to_thread(f.close)


class ClientGateway:
    """Chiamate al gateway con timeout e ritentativi sugli errori ritentabili."""

    def __init__(self, base_url: str, *, timeout_secondi: float,
                 ritentativi_secondi: Sequence[float],
                 transport: httpx.AsyncBaseTransport | None = None,
                 attendi: Callable[[float], Any] = asyncio.sleep,
                 retry_after_max_secondi: float = 60.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout_secondi),
            transport=transport, follow_redirects=False,
        )
        self._ritentativi = list(ritentativi_secondi)
        self._attendi = attendi
        self._retry_after_max = float(retry_after_max_secondi)

    async def chiudi(self) -> None:
        await self._client.aclose()

    async def _richiesta(self, metodo: str, percorso: str, token: str, *,
                         operazione: str, headers: dict[str, str] | None = None,
                         json: Any = None,
                         contenuto: Callable[[], AsyncIterator[bytes]] | None = None,
                         ) -> httpx.Response:
        intestazioni = {"Authorization": f"Bearer {token}", **(headers or {})}
        ultimo: ErroreGateway | None = None
        for tentativo in range(len(self._ritentativi) + 1):
            risposta: httpx.Response | None = None
            try:
                risposta = await self._client.request(
                    metodo, percorso, headers=intestazioni, json=json,
                    content=contenuto() if contenuto is not None else None,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning("sftp_gateway_rete", operazione=operazione, tentativo=tentativo,
                               errore=type(exc).__name__)
                ultimo = ErroreGateway(_problema_canale(
                    "SFTP-201", f"Gateway non raggiungibile durante «{operazione}» "
                    f"({type(exc).__name__})"), status=None)
            else:
                if risposta.status_code < 400:
                    return risposta
                ultimo = ErroreGateway(_problema_da_risposta(risposta),
                                       status=risposta.status_code)
                logger.info("sftp_gateway_errore", operazione=operazione,
                            status=risposta.status_code, codice=ultimo.problema.get("codice"),
                            ritentabile=ultimo.ritentabile, tentativo=tentativo)
                if not ultimo.ritentabile:
                    raise ultimo
            attesa_server = _secondi_retry_after(risposta)
            if attesa_server > self._retry_after_max:
                ultimo.retry_after = attesa_server
                # attesa troppo lunga per tenere occupati lavoratore e lease
                logger.info("sftp_gateway_retry_after_lungo", operazione=operazione,
                            secondi=attesa_server, codice=ultimo.problema.get("codice"))
                raise ultimo
            if tentativo < len(self._ritentativi):
                await self._attendi(max(self._ritentativi[tentativo], attesa_server))
        assert ultimo is not None
        raise ultimo

    @staticmethod
    def _json(risposta: httpx.Response, operazione: str) -> dict[str, Any]:
        try:
            corpo = risposta.json()
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            raise ErroreGateway(_problema_canale(
                "SFTP-201", f"Risposta non interpretabile durante «{operazione}»"),
                status=risposta.status_code)
        return corpo

    # ── Sessioni di caricamento (contratto C1) ─────────────────────────
    async def apri_caricamento(self, token: str, *, nome_file: str, dimensione: int,
                               impronta: dict[str, str] | None, chiave: str) -> dict[str, Any]:
        r = await self._richiesta(
            "POST", "/api/v1/caricamenti", token, operazione="apertura caricamento",
            headers={"Idempotency-Key": chiave},
            json={"nome_file": nome_file, "dimensione": dimensione,
                  "mime_dichiarato": None, "impronta": impronta})
        return self._json(r, "apertura caricamento")

    async def stato_caricamento(self, token: str, caricamento_id: str) -> dict[str, Any]:
        r = await self._richiesta("GET", f"/api/v1/caricamenti/{caricamento_id}", token,
                                  operazione="stato caricamento")
        return self._json(r, "stato caricamento")

    async def carica_parte(self, token: str, caricamento_id: str, numero: int, *,
                           percorso: Path, inizio: int, lunghezza: int) -> dict[str, Any]:
        impronta = await asyncio.to_thread(impronta_parte, percorso, inizio, lunghezza)
        r = await self._richiesta(
            "PUT", f"/api/v1/caricamenti/{caricamento_id}/parti/{numero}", token,
            operazione="invio parte",
            headers={"Content-Type": "application/octet-stream",
                     "Content-Length": str(lunghezza),
                     "X-Impronta-Parte": f"sha256={impronta}"},
            contenuto=lambda: _blocchi_parte(percorso, inizio, lunghezza))
        return self._json(r, "invio parte")

    async def completa(self, token: str, caricamento_id: str) -> dict[str, Any]:
        r = await self._richiesta("POST", f"/api/v1/caricamenti/{caricamento_id}/completa",
                                  token, operazione="completamento caricamento")
        return self._json(r, "completamento caricamento")

    # ── Stato di un PdV ────────────────────────────────────────────────
    async def stato_pdv(self, token: str, pdv_id: str) -> dict[str, Any]:
        """``GET /api/v1/versamento/{pdv_id}``: stato attuale del PdV (R03 i3)."""
        r = await self._richiesta("GET", f"/api/v1/versamento/{pdv_id}", token,
                                  operazione="stato del PdV")
        return self._json(r, "stato del PdV")

    # ── Versamento per riferimento ─────────────────────────────────────
    async def versa(self, token: str, *, indice: dict[str, Any], file: dict[str, str],
                    chiave: str) -> tuple[int, dict[str, Any]]:
        r = await self._richiesta(
            "POST", "/api/v1/versamento/ud", token, operazione="versamento",
            headers={"Idempotency-Key": chiave}, json={"indice": indice, "file": file})
        return r.status_code, self._json(r, "versamento")
