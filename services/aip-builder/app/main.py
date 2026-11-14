# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/aip-builder/app/main.py — AIP Builder Service
Riferimento piano: F1.3
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
from app.services.aip_engine import close_aip_consumer, consume_loop, init_aip_consumer
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
    logger.info("aip_builder_starting", env=settings.env)
    await init_db(settings.database_url)
    await init_aip_consumer()
    task = asyncio.create_task(consume_loop())
    logger.info("aip_builder_ready")
    yield
    logger.info("aip_builder_stopping")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await close_aip_consumer()
    await close_db()

app = FastAPI(
    title="VerumDoc AIP Builder",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
installa_metriche(app, "aip-builder")  # VD-C-033: GET /metrics
app.add_middleware(
    CORSMiddleware, allow_origins=["*"] if settings.env.lower() == "development" else [],
                   allow_credentials=True, allow_methods=["GET"], allow_headers=["*"])

@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}

@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto solo se rispondono database, Kafka (PdV da impacchettare) e S3
    (PdA in WORM, bucket esistente); altrimenti 503.
    Testo delle eccezioni solo nel log (g4-chiusura)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "kafka": lambda: controlla_kafka(settings.kafka_bootstrap),
        "s3": lambda: controlla_s3(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, region=getattr(settings, "s3_region", None),
            bucket=settings.s3_bucket_aip),
    })

@app.get("/api/v1/info")
async def info() -> dict:
    return {"service": settings.service_name, "version": settings.service_version,
            "env": settings.env, "kafka_topic_in": settings.kafka_topic_in}


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
