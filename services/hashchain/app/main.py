# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/hashchain/app/main.py — avvio del servizio della catena delle impronte.

R04 (contratti K1-K3): il consumer Kafka dei lotti di ``aip.conserved`` è
sostituito dal ciclo del registro a due livelli (``app.registro.ciclo``): il
sequenziatore legge ``catena_coda`` (scritta dall'aip-builder nella transazione
del PdA), emette i checkpoint, completa i PdA coperti e pubblica
``aip.conserved`` / ``catena.checkpoint`` tramite l'outbox, pubblicata dal relay
proprio del servizio (VD-C-076). Kafka non è nella prontezza: con Kafka giù gli
eventi restano durevoli in outbox e il registro continua.
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
from app.registro.chiavi import verifica_configurazione
from app.registro.ciclo import ciclo_predefinito
from app.registro.metriche import installa as installa_metriche_catena
from app.routers.catena import router as catena_router
from app.routers.interno import router as interno_router
from app.routers.registro import router as registro_router
from app.services.verifica_asincrona import arresta_verifiche, chiudi_verifiche_orfane
from app.verifica_continua.router import router as verifiche_router
from libs.shared.db import close_db, init_db
from libs.shared.db.ambito import (
    MiddlewareAmbitoRichiesta,
    ambito_conservatore,
    come_conservatore,
)
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_s3 import controlla_s3
from libs.shared.outbox import avvia_relay_servizio, ferma_relay_servizio
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("hashchain_starting", env=settings.env)
    # §19.4: chiave del registro e testimone coerenti, altrimenti nessun avvio
    verifica_configurazione(settings)
    await init_db(settings.database_url)
    # g4: le verifiche complete rimaste IN_CORSO (processo arrestato durante
    # l'esecuzione) si chiudono come INTERROTTA: la verità non resta appesa.
    try:
        with ambito_conservatore("avvio della hashchain: verifiche complete rimaste aperte"):
            chiuse = await chiudi_verifiche_orfane()
        if chiuse:
            logger.warning("verifiche_orfane_chiuse", quante=chiuse)
    except Exception as exc:  # noqa: BLE001 — la readiness segnala il DB
        logger.error("verifiche_orfane_non_chiuse", tipo=type(exc).__name__, errore=str(exc))
    # R04 (VD-C-076): relay proprio degli eventi della hashchain (aip.conserved,
    # catena.checkpoint); con Kafka giù gli eventi restano durevoli in outbox.
    app.state.relay_outbox = await avvia_relay_servizio(
        "hashchain", app=app, bootstrap=settings.kafka_bootstrap)
    fine = asyncio.Event()
    # R05 (VD-C-080): il registro è multi-ente per costruzione → ambito conservatore
    ciclo = asyncio.create_task(come_conservatore(
        "registro delle impronte: sequenziatore, checkpoint, conservazione, coerenza",
        ciclo_predefinito().esegui(fine)), name="registro-catena")
    logger.info("hashchain_ready")
    yield
    await arresta_verifiche()  # ogni verifica in corso registra INTERROTTA
    fine.set()
    try:
        await asyncio.wait_for(ciclo, timeout=30)
    except TimeoutError:
        ciclo.cancel()
        logger.warning("registro_ciclo_cancellato")
    except Exception as exc:  # noqa: BLE001 — arresto: si registra e si chiude
        logger.error("registro_ciclo_terminato_con_errore", errore=str(exc)[:300])
    await ferma_relay_servizio(app.state.relay_outbox)
    await close_db()


app = FastAPI(
    title="VerumDoc Hashchain Service",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
# VD-C-033: GET /metrics, con le metriche del registro (R04)
installa_metriche_catena(installa_metriche(app, "hashchain").registro)
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.add_middleware(CORSMiddleware,
    allow_origins=["*"] if settings.env.lower() == "development" else [],
    allow_credentials=True, allow_methods=["GET"], allow_headers=["*"])

@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}

@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto solo se rispondono database (registro) e S3 (checkpoint e RdV in
    WORM); altrimenti 503. Testo delle eccezioni solo nel log (g4). Da R04 la
    hashchain non usa più Kafka direttamente (eventi via outbox)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "s3": lambda: controlla_s3(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, region=settings.s3_region,
            bucket=settings.s3_bucket_aip),
    })

@app.get("/api/v1/info")
async def info() -> dict:
    return {"service": settings.service_name, "version": settings.service_version,
            "env": settings.env,
            "checkpoint_secondi": settings.catena_checkpoint_secondi,
            "testimone_configurato": bool(settings.testimone_url)}

# g4-chiusura (audit B-H11): eliminato GET /api/v1/hashchain/integrity, che
# eseguiva una scansione completa della catena SENZA autenticazione e senza
# registrare l'esito. Nessuno lo chiamava (codice e frontend). La verifica
# completa autenticata e registrata è POST /api/v1/hashchain/verifica-completa.

# G2: stato della catena (con stato di firma ed esito dell'ultima verifica
# completa) e avvio della verifica completa — app/routers/catena.py
app.include_router(catena_router)
# R04 (K1): checkpoint, prove d'inclusione e di consistenza, foglie del registro
app.include_router(registro_router)
# R05 (contratto Q3): API interna del registro per il ciclo di vita (scarto,
# annullamento, rettifica), solo token di servizio con scope registro:aggiungi
app.include_router(interno_router)
# R04 (verifica continua): richieste di verifica, registro delle verifiche,
# riepilogo di copertura. Il lavoratore è un processo a parte
# (``python -m app.verifica_continua``); i suoi eventi ``integrita.anomalia``
# escono dal relay ``hashchain`` avviato nel lifespan.
app.include_router(verifiche_router)


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
