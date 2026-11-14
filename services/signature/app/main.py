# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/main.py — servizio di firma, marca e verifica (VD-C-010).

All'avvio: controlli di configurazione puri (registro.problemi_configurazione);
in ENV=production il servizio non parte se sono presenti problemi, in
particolare se è configurato l'adattatore di sviluppo (REGOLE §19.4).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers.personale import router as personale_router
from app.routers.signature import gestore, get_servizio
from app.routers.signature import router as signature_router
from app.services.registro import problemi_configurazione
from app.services.registro_fornitori import leggi_registro, problemi_avvio
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import NonPronto, risposta_prontezza
from libs.shared.security.credenziali_uniche import MiddlewareCredenzialiUniche

logger = structlog.get_logger(__name__)


def verifica_audience_servizio(cfg=settings) -> None:
    """Fuori da ENV=test l'audience del token di servizio (JWT_AUDIENCE_SERVIZIO)
    deve essere DIVERSA da quella dei token utente (JWT_AUDIENCE): se coincidono
    la separazione della revisione M2 non vale più (un token utente varrebbe
    per /firma e /marca se avesse anche lo scope). Il servizio non parte."""
    if (cfg.env or "").strip().lower() == "test":
        return
    servizio = (cfg.jwt_audience_servizio or "").strip()
    utente = (cfg.jwt_audience or "").strip()
    if servizio == utente:
        logger.error("signature_audience_servizio_uguale", audience=servizio)
        raise RuntimeError(
            "JWT_AUDIENCE_SERVIZIO coincide con JWT_AUDIENCE: il token di servizio della "
            "firma deve avere un'audience dedicata (es. «verumdoc-signature»). "
            "Correggere la configurazione: il servizio non parte."
        )


def controlla_avvio() -> None:
    """Blocca l'avvio in produzione con configurazione pericolosa o incompleta;
    in ogni ambiente tranne test, con audience del token di servizio uguale a
    quella dei token utente."""
    verifica_audience_servizio(settings)
    problemi = problemi_configurazione(settings)
    if problemi:
        logger.error("signature_configurazione_problemi", problemi=problemi)
        if settings.in_produzione:
            raise RuntimeError(
                "Configurazione di firma non valida in produzione: " + "; ".join(problemi)
            )


async def controlla_registro() -> None:
    """R13a: in produzione il servizio NON parte senza DATABASE_URL, con il registro
    dei fornitori non leggibile o con un fornitore di SVILUPPO attivo (§19.4)."""
    fornitori = None
    if settings.database_url:
        from libs.shared.db import init_db

        await init_db(settings.database_url)
        try:
            fornitori = await leggi_registro()
        except Exception as exc:  # noqa: BLE001 — in produzione blocca l'avvio
            logger.error("signature_registro_non_leggibile", errore=type(exc).__name__)
            if settings.in_produzione:
                raise RuntimeError("registro dei fornitori di firma non leggibile: il "
                                   "servizio non parte") from exc
    problemi = problemi_avvio(settings, fornitori)
    if problemi:
        logger.error("signature_registro_problemi", problemi=problemi)
        raise RuntimeError("Configurazione di firma non valida in produzione: "
                           + "; ".join(problemi))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("signature_starting", env=settings.env)
    controlla_avvio()
    await controlla_registro()
    try:
        await gestore().esito()
    except Exception as exc:  # noqa: BLE001 — in produzione ha già bloccato l'avvio
        logger.error("signature_registro_non_caricato", errore=type(exc).__name__)
    yield
    if settings.database_url:
        from libs.shared.db import close_db

        await close_db()
    logger.info("signature_stopped")


app = FastAPI(
    title="VerumDoc Signature Service",
    version=settings.service_version,
    docs_url="/docs" if not settings.in_produzione else None,
    redoc_url=None,
    lifespan=lifespan,
)
installa_metriche(app, "signature")  # VD-C-033: GET /metrics
# Revisione R06 (V1): più intestazioni Authorization → 400 AUTH-041
app.add_middleware(MiddlewareCredenzialiUniche)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.env.lower() == "development" else [],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(signature_router)
app.include_router(personale_router)  # R16 (VD-C-166): firma personale con OTP


@app.get("/api/v1/health/live", include_in_schema=False)
async def live() -> dict:
    return {"status": "alive", "service": "signature"}


async def _servizio_di_firma() -> None:
    """Il servizio di firma si costruisce dal registro o dalla configurazione."""
    await get_servizio()


async def _configurazione_firma() -> None:
    if problemi_configurazione(settings):
        raise NonPronto("problemi_configurazione")


@app.get("/api/v1/health/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    """signature non usa database, Kafka né S3: è pronto se il servizio di firma
    è costruito dalla configurazione (503 altrimenti). I problemi di
    configurazione (fornitore non configurato) sono riportati ma non bloccano:
    le richieste ricevono un errore esplicito, e in produzione bloccano già
    l'avvio. I fornitori remoti (CSC, TSA) non si interrogano a ogni probe."""
    return await risposta_prontezza(
        "signature", {"servizio_firma": _servizio_di_firma},
        facoltativi={"configurazione_firma": _configurazione_firma},
    )


@app.get("/api/v1/info", summary="Metadati del servizio")
async def info() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "env": settings.env,
        "firma_fornitore": settings.firma_fornitore,
        "marca_fornitore": settings.marca_fornitore,
    }


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
