# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/main.py — testimone indipendente della catena (R04, K3).

Riferimenti: PIANO_R03_R05 §5.3; contratto K3; REGOLE_SVILUPPO §19.4
(impostazioni pericolose fermano l'avvio), VD-C-033 (metriche), g4
(readiness vera).

All'avvio: controlli puri della configurazione (``app.avvio``), pool del
database PROPRIO del testimone, lettura dello stato per le metriche. Le
migrazioni NON girano qui ma nell'entrypoint (``alembic upgrade head``),
come nel gateway.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.checkpoint import router as checkpoint_router
from app.application.stato import aggiorna_metriche_stato
from app.avvio import ConfigurazioneAvvio, carica_contesto
from app.config import settings
from app.osservabilita import MetricheTestimone
from libs.shared.db import close_db, init_db
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import NonPronto, risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.security.credenziali_uniche import MiddlewareCredenzialiUniche

logger = structlog.get_logger(__name__)


async def _ciclo_metriche(app: FastAPI, intervallo: float) -> None:
    """Rilegge periodicamente lo stato dal database (più repliche, operatore)."""
    while True:
        await asyncio.sleep(intervallo)
        try:
            await aggiorna_metriche_stato(app.state.testimone.metriche, app.state.testimone.origini)
        except Exception as exc:  # noqa: BLE001 — la readiness segnala il DB
            logger.error("metriche_stato_non_aggiornate", tipo=type(exc).__name__, errore=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("testimone_avvio", env=settings.env)
    app.state.testimone = carica_contesto(
        ConfigurazioneAvvio.da_impostazioni(settings), app.state.metriche_testimone
    )
    await init_db(settings.database_url)
    try:
        await aggiorna_metriche_stato(app.state.testimone.metriche, app.state.testimone.origini)
    except Exception as exc:  # noqa: BLE001 — la readiness segnala il DB
        logger.error("metriche_stato_non_lette", tipo=type(exc).__name__, errore=str(exc))
    compito = asyncio.create_task(_ciclo_metriche(app, settings.testimone_intervallo_stato_secondi))
    logger.info(
        "testimone_pronto",
        origini=app.state.testimone.origini,
        chiave=app.state.testimone.chiave.verificatore,
    )
    yield
    compito.cancel()
    with suppress(asyncio.CancelledError):
        await compito
    await close_db()


app = FastAPI(
    title="VerumDoc Testimone",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
_metriche_http = installa_metriche(app, "testimone")  # GET /metrics
app.state.metriche_testimone = MetricheTestimone(_metriche_http.registro)
app.state.testimone = None
# Revisione R06 (V1): più intestazioni Authorization → 400 AUTH-041
app.add_middleware(MiddlewareCredenzialiUniche)


@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "alive", "service": settings.service_name}


async def _controlla_configurazione() -> None:
    if getattr(app.state, "testimone", None) is None:
        raise NonPronto("non_configurato")


@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto se configurazione caricata e database del testimone raggiungibile.
    Lo stato COMPROMESSO non toglie la prontezza: le letture pubbliche devono
    restare disponibili proprio durante un incidente."""
    return await risposta_prontezza(
        settings.service_name,
        {
            "configurazione": _controlla_configurazione,
            "database": controlla_database,
        },
    )


@app.get("/api/v1/info", include_in_schema=False)
async def info() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "env": settings.env,
    }


app.include_router(checkpoint_router)
installa_gestori_errori(app)
