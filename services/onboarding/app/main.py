# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/main.py — Onboarding Service
Riferimento piano: F1.5
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
from app.routers.accesso import router as accesso_router  # R10 D08
from app.routers.classi import router as classi_router
from app.routers.configurazione import router as configurazione_router  # R05 Q1
from app.routers.firma_fornitori import router as firma_fornitori_router
from app.routers.inviti import router as inviti_router  # R10 D08
from app.routers.preferenze import router as preferenze_router  # R07 D31/D29
from app.routers.ruoli import router as ruoli_router  # R10 D30
from app.routers.sftp import router as sftp_router  # R03 b2: account SFTP
from app.routers.tenant import router as tenant_router
from app.routers.utenti import router as utenti_router
from libs.shared.db import close_db, init_db
from libs.shared.db.ambito import MiddlewareAmbitoRichiesta
from libs.shared.errori import installa_gestori_errori
from libs.shared.kafka import close_kafka, init_kafka_producer
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_kafka import controlla_kafka
from libs.shared.outbox import avvia_relay_servizio, ferma_relay_servizio
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("onboarding_starting", env=settings.env)
    # R12a (VD-C-152): in produzione gli inviti si sigillano per la posta: senza
    # una chiave pubblica valida il servizio non parte (fuori produzione: 503 sugli inviti)
    if settings.env.lower() == "production":
        from app.routers.inviti import chiave_posta

        chiave_posta()
    await init_db(settings.database_url)
    await init_kafka_producer(settings.kafka_bootstrap)
    # R04 (VD-C-076): relay proprio degli eventi di onboarding (prima li pubblicava il gateway)
    app.state.relay_outbox = await avvia_relay_servizio("onboarding", app=app)
    # R07 (D28, D17): lavoro periodico della configurazione
    fine = asyncio.Event()
    lavoro = None
    if settings.configurazione_lavoratore_attivo:
        from app.services.programmate import lavoratore

        lavoro = asyncio.create_task(
            lavoratore(fine, settings.configurazione_intervallo_secondi),
            name="configurazione-programmate")
    logger.info("onboarding_ready")
    yield
    fine.set()
    if lavoro is not None:
        try:
            await asyncio.wait_for(lavoro, timeout=10)
        except (TimeoutError, asyncio.CancelledError):
            lavoro.cancel()
    await ferma_relay_servizio(app.state.relay_outbox)
    await close_kafka()
    await close_db()

app = FastAPI(
    title="VerumDoc Onboarding Service",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)
installa_metriche(app, "onboarding")  # VD-C-033: GET /metrics
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.env.lower() == "development" else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],  # PATCH: bozze (R05 Q1)
    allow_headers=["*"],
)
app.include_router(tenant_router)
app.include_router(classi_router)
app.include_router(utenti_router)
app.include_router(sftp_router)
app.include_router(configurazione_router)
app.include_router(preferenze_router)
app.include_router(accesso_router)
app.include_router(inviti_router)
app.include_router(ruoli_router)
app.include_router(firma_fornitori_router)  # R13a: fornitori di firma e marca

@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}

@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto solo se il database risponde. Kafka è riportato ma non blocca:
    gli eventi si scrivono nell'outbox e il relay li pubblica quando torna.
    Testo delle eccezioni solo nel log (g4-chiusura)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
    }, facoltativi={
        "kafka": lambda: controlla_kafka(settings.kafka_bootstrap),
    })

@app.get("/api/v1/info")
async def info() -> dict:
    return {"service": settings.service_name, "version": settings.service_version,
            "env": settings.env}


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
