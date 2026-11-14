# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/main.py — Distribution Service
Riferimento piano: F2.1
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers.distribution import router as dist_router
from app.routers.fascicoli import router as fascicoli_router
from app.routers.migration import router as migration_router
from app.services.indicizzazione import ciclo_indicizzazione
from app.services.pdd_zip import ciclo_pdd
from app.services.search_index import ensure_index
from libs.shared.db import close_db, init_db
from libs.shared.db.ambito import MiddlewareAmbitoRichiesta
from libs.shared.errori import installa_gestori_errori
from libs.shared.kafka import close_kafka, init_kafka_producer
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_kafka import controlla_kafka
from libs.shared.observability.prontezza_s3 import controlla_s3
from libs.shared.outbox import avvia_relay_servizio, ferma_relay_servizio
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("distribution_starting", env=settings.env)
    await init_db(settings.database_url)
    await init_kafka_producer(settings.kafka_bootstrap)
    # R04 (VD-C-076): relay proprio degli eventi di distribution (prima li pubblicava il gateway)
    app.state.relay_outbox = await avvia_relay_servizio("distribution", app=app)
    await ensure_index()
    # R03 b4: lavoratori dell'indicizzazione (VD-C-093) e dei PdD ZIP (VD-C-077)
    fine = asyncio.Event()
    lavoratori = []
    if settings.lavoratori_attivi:
        lavoratori = [asyncio.create_task(ciclo_indicizzazione(fine), name="indicizzazione"),
                      asyncio.create_task(ciclo_pdd(fine), name="pdd-zip")]
    logger.info("distribution_ready", lavoratori=len(lavoratori))
    yield
    fine.set()
    for task in lavoratori:
        try:
            await asyncio.wait_for(task, timeout=10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
    await ferma_relay_servizio(app.state.relay_outbox)
    await close_kafka()
    await close_db()

app = FastAPI(
    title="VerumDoc Distribution Service",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    openapi_url="/api/v2/openapi.json",
    lifespan=lifespan,
)
installa_metriche(app, "distribution")  # VD-C-033: GET /metrics
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.env.lower() == "development" else [],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    # R03 b4: le chiavi API passano anche da qui (X-Api-Key)
    allow_headers=["*"],
)
app.include_router(dist_router)
app.include_router(fascicoli_router)    # HIGH-001 FIX: was not mounted
app.include_router(migration_router)    # HIGH-001 FIX: was not mounted

@app.get("/api/v2/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}

@app.get("/api/v2/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto solo se rispondono database (registro delle esibizioni, outbox) e
    S3 (pacchetti da esibire). Kafka riportato ma non bloccante (outbox).
    Testo delle eccezioni solo nel log (g4-chiusura)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "s3": lambda: controlla_s3(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, region=getattr(settings, "s3_region", None),
            bucket=settings.s3_bucket_aip),
    }, facoltativi={
        "kafka": lambda: controlla_kafka(settings.kafka_bootstrap),
    })

@app.get("/api/v2/info")
async def info() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "env": settings.env,
        "tipi_pdd": ["COMPLETO", "ESTRATTO", "ESIBIZIONE", "MIGRAZIONE"],
        "opensearch_index": settings.opensearch_index,
    }


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
