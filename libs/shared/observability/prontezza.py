# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/observability/prontezza.py — readiness vera dei servizi (g4-chiusura).

Prima (VD-C-030, g3-test §4.3) 8 servizi su 9 rispondevano «ready» senza
controllare nulla. Qui lo schema comune, lo stesso del gateway
(services/gateway/app/routers/system.py):

- ogni servizio dichiara i controlli delle dipendenze che gli servono davvero
  (database, Kafka, S3: moduli prontezza_db, prontezza_kafka, prontezza_s3,
  separati perché non tutti i servizi hanno quelle librerie);
- i controlli girano in parallelo, ciascuno con un tempo massimo (la probe
  Kubernetes ha timeoutSeconds 3);
- un controllo bloccante fallito → 503 ``not_ready``; quelli facoltativi sono
  riportati in ``checks`` ma non bloccano;
- al client arrivano solo codici di stato brevi (ok, errore, timeout,
  non_inizializzato, ...): il testo delle eccezioni va SOLO nel log (§18).

Questo modulo usa soltanto la libreria standard, FastAPI e structlog.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import structlog
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

#: Un controllo: coroutine che termina se la dipendenza risponde, altrimenti solleva.
Controllo = Callable[[], Awaitable[Any]]

#: Tempo massimo di ciascun controllo (secondi): sotto il timeoutSeconds della probe.
TIMEOUT_PREDEFINITO = 2.0


class NonPronto(Exception):  # noqa: N818 — nome di dominio
    """Dipendenza non pronta con uno stato noto (es. «non_inizializzato»).

    ``stato`` va al client: deve essere un codice breve, mai un messaggio tecnico."""

    def __init__(self, stato: str) -> None:
        super().__init__(stato)
        self.stato = stato


async def _esegui(nome: str, controllo: Controllo, timeout: float) -> str:
    try:
        await asyncio.wait_for(controllo(), timeout=timeout)
    except NonPronto as exc:
        logger.warning("readiness_non_pronto", controllo=nome, stato=exc.stato)
        return exc.stato
    except TimeoutError:
        logger.error("readiness_timeout", controllo=nome, secondi=timeout)
        return "timeout"
    except Exception as exc:  # noqa: BLE001 — qualsiasi guasto = dipendenza non pronta
        logger.error("readiness_ko", controllo=nome, tipo=type(exc).__name__, errore=str(exc))
        return "errore"
    return "ok"


async def esegui_controlli(
    controlli: Mapping[str, Controllo], *, timeout: float = TIMEOUT_PREDEFINITO,
) -> dict[str, str]:
    """Esegue i controlli in parallelo: {nome: stato}."""
    nomi = list(controlli)
    esiti = await asyncio.gather(*(_esegui(n, controlli[n], timeout) for n in nomi))
    return dict(zip(nomi, esiti, strict=True))


async def risposta_prontezza(
    servizio: str,
    controlli: Mapping[str, Controllo],
    *,
    facoltativi: Mapping[str, Controllo] | None = None,
    timeout: float = TIMEOUT_PREDEFINITO,
) -> JSONResponse:
    """200 ``ready`` se tutti i controlli bloccanti sono «ok», altrimenti 503 ``not_ready``.

    I ``facoltativi`` compaiono in ``checks`` ma non cambiano l'esito."""
    tutti = {**controlli, **(facoltativi or {})}
    checks = await esegui_controlli(tutti, timeout=timeout)
    pronto = all(checks[n] == "ok" for n in controlli)
    return JSONResponse(
        status_code=200 if pronto else 503,
        content={
            "status": "ready" if pronto else "not_ready",
            "service": servizio,
            "checks": checks,
            "facoltativi": sorted(facoltativi or {}),
        },
    )
