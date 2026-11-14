# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/validation/app/main.py
Riferimento piano: F1.2 — Validation Service
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
from app.services.consumer import close_consumer, consume_loop, init_consumer
from app.services.metriche import METRICHE
from libs.shared.db import close_db, init_db
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_kafka import controlla_kafka
from libs.shared.observability.prontezza_s3 import controlla_s3

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("validation_starting", env=settings.env)
    await init_db(settings.database_url)
    await init_consumer()
    # Avvia il consumer loop in background
    consumer_task = asyncio.create_task(consume_loop())
    logger.info("validation_ready")
    yield
    logger.info("validation_stopping")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await close_consumer()
    await close_db()
    logger.info("validation_stopped")


app = FastAPI(
    title="VerumDoc Validation Service",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)
_metriche_http = installa_metriche(app, "validation")  # VD-C-033: GET /metrics
# Metriche del consumer (concorrenza, offset non confermati, esiti, byte letti)
_metriche_http.registro.register(METRICHE.registro)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.env.lower() == "development" else [],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}


@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto solo se rispondono database, Kafka (PdV da verificare) e S3 (file
    del versamento; il bucket arriva nel messaggio, si controlla il servizio S3).
    Testo delle eccezioni solo nel log (g4-chiusura)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "kafka": lambda: controlla_kafka(settings.kafka_bootstrap),
        "s3": lambda: controlla_s3(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, region=getattr(settings, "s3_region", None),),
    })


@app.get("/api/v1/info")
async def info() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "env": settings.env,
        "kafka_topic_in": settings.kafka_topic_in,
        "validation_steps": 9,
        "concorrenza_pdv": settings.concorrenza_pdv,
    }


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
