# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/main.py
Sprint 1 FIX-009: montato status.router e lifecycle.router.

PRIMA: solo system.router e versamento.router montati.
       status.py (con stato_pdv + tenant_id filter) non raggiungibile.
       lifecycle.py non montato.
DOPO:  4 router montati con ordine corretto.
       stato_pdv viene da status.py (ORM + tenant filter).
       Il duplicato in versamento.py è stato rimosso.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.accesso_locale import router as accesso_locale_router  # R10, D08
from app.auth.conferme_decisione import router as conferme_decisione_router  # R17
from app.auth.credenziali_utente import router as credenziali_utente_router  # R12b
from app.auth.sessione import router as sessione_router  # R05, VD-C-079
from app.auth.spid import router as spid_router
from app.config import settings
from app.middleware.logging import StructuredLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.routers import (  # R03 b1
    caricamenti,
    lifecycle,
    status,
    system,
    versamento,
)
from app.services import caricamenti as servizio_caricamenti
from app.services.corpo import registra_metriche_disco
from app.services.outbox import avvia_relay_servizio, ferma_relay_servizio
from app.services.pulizia_staging import registra_metriche as registra_metriche_pulizia
from libs.shared.db import close_db, init_db
from libs.shared.db.ambito import MiddlewareAmbitoRichiesta
from libs.shared.errori import installa_gestori_errori
from libs.shared.kafka import close_kafka, init_kafka_producer
from libs.shared.observability.metriche import installa_metriche
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)

# VD-C-002: il login di sviluppo esiste solo con VERUMDOC_DEV_AUTH=1 E ENV
# development/test; altrimenti il router non viene nemmeno importato.
_dev_router = None
if settings.login_sviluppo_abilitato():
    from app.routers.dev_auth import router as _dev_router

    logger.warning(
        "login_sviluppo_attivo",
        env=settings.env,
        nota="POST /api/v1/auth/dev-token attivo: mai in ambienti esposti",
    )

# Import opzionale — pdnd potrebbe non essere configurato
_pdnd_router = None
try:
    from app.routers.pdnd import router as _pdnd_router
except ImportError:
    logger.warning("pdnd_router_not_found — endpoint /api/pdnd/v1 non disponibile")


async def avvia_relay_outbox(app: FastAPI) -> None:
    """VD-C-015, VD-C-076: relay degli eventi del GATEWAY (origine «gateway»)
    come task del processo, con la manutenzione del database (partizioni e
    contatori). Gli altri servizi hanno il proprio relay. Con
    OUTBOX_RELAY_ATTIVO=false nessun task (relay come processo dedicato:
    ``python -m app.services.outbox``), ma origine e metriche restano."""
    app.state.relay_servizio = await avvia_relay_servizio("gateway", app=app)
    app.state.relay = app.state.relay_servizio.task


async def ferma_relay_outbox(app: FastAPI, *, attesa: float = 10.0) -> None:
    """Arresto pulito: segnala la fine, attende il giro in corso (che conclude
    la sua transazione), poi cancella se non termina entro ``attesa``."""
    await ferma_relay_servizio(getattr(app.state, "relay_servizio", None), attesa=attesa)
    app.state.relay_servizio = None
    app.state.relay = None


async def avvia_lavoratore_caricamenti(app: FastAPI) -> None:
    """R03 b1 (contratto C1): verifica delle sessioni di caricamento fuori dalla
    richiesta HTTP e pulizia dello staging, come task del processo (più repliche
    insieme: lease e SKIP LOCKED). Spegnibile con CARICAMENTI_LAVORATORE_ATTIVO=false
    per eseguirlo come processo dedicato (python -m app.services.caricamenti)."""
    app.state.caricamenti_fine = asyncio.Event()
    app.state.caricamenti = asyncio.create_task(
        servizio_caricamenti.lavoratore(app.state.caricamenti_fine), name="caricamenti")


async def ferma_lavoratore_caricamenti(app: FastAPI, *, attesa: float = 10.0) -> None:
    task: asyncio.Task | None = getattr(app.state, "caricamenti", None)
    if task is None:
        return
    app.state.caricamenti_fine.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=attesa)
    except TimeoutError:
        task.cancel()  # una verifica interrotta riprende con il lease scaduto
        try:
            await task
        except asyncio.CancelledError:
            pass
    except Exception as exc:  # noqa: BLE001 — già terminato con errore
        logger.error("caricamenti_lavoratore_terminato_con_errore", errore=str(exc)[:300])
    app.state.caricamenti = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("gateway_starting", env=settings.env)
    from app.auth.accesso_locale import limite_ip_attivo

    if not limite_ip_attivo():
        # Revisione R10 (V1): senza PROXY_FIDATI l'IP della connessione è quello
        # dell'ALB: il limite per IP non si applica (sarebbe un blocco globale)
        logger.error("accesso_limite_ip_disattivato",
                     motivo="PROXY_FIDATI vuoto: impostare le sottoreti dell'ALB")
    from app.auth.oidc import problemi_avvio as problemi_spid

    problemi = problemi_spid(settings)
    if problemi:
        # R13b (VD-C-069): segreti del cliente OIDC legati agli host dell'aggregatore
        if settings.env.lower() == "production":
            raise RuntimeError("configurazione SPID/CIE non valida: " + "; ".join(problemi))
        logger.warning("spid_configurazione_incompleta", problemi=problemi)
    from app.auth.conferme_decisione import problema_avvio as problema_conferme

    problema = problema_conferme()
    if problema:
        # R17 (VD-P-164): senza la chiave delle conferme nessuna decisione sensibile passa
        if settings.env.lower() == "production":
            raise RuntimeError("conferma delle decisioni non configurata: " + problema)
        logger.warning("conferme_decisione_non_configurate", motivo=problema)
    await init_db(settings.database_url)
    await init_kafka_producer(settings.kafka_bootstrap)
    await avvia_relay_outbox(app)
    if settings.caricamenti_lavoratore_attivo:
        await avvia_lavoratore_caricamenti(app)
    logger.info("gateway_ready")
    try:
        yield
    finally:
        # Prima i lavoratori (usano producer e DB), poi producer e DB
        await ferma_lavoratore_caricamenti(app)
        await ferma_relay_outbox(app)
        await close_kafka()
        await close_db()


app = FastAPI(
    title="VerumDoc Gateway API",
    version=settings.service_version,
    docs_url="/docs" if settings.env.lower() != "production" else None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)
_metriche = installa_metriche(app, "gateway")  # VD-C-033: /metrics
# R03 f3: PdV bloccati dalla pulizia dello staging (REV-N1), disco temporaneo (REV-N2)
metriche_pulizia = registra_metriche_pulizia(_metriche.registro)
registra_metriche_disco(_metriche.registro)
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(RequestIdMiddleware)

# Sprint 1 FIX: CORS whitelist esplicita in dev (non wildcard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        ["http://localhost:5173", "http://localhost:3000"]
        if settings.env.lower() == "development"
        else settings.cors_origins
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# ── Router mounting — Sprint 1 FIX-009 ──────────────────────────────
# ORDINE IMPORTANTE:
#   1. system.router — health checks (no prefix)
#   2. versamento.router — POST versamento/ud (prefix /api/v1)
#   3. status.router — GET versamento/{pdv_id} + rdv (prefix /api/v1)
#   4. lifecycle.router — lifecycle endpoints (ha prefix proprio)
#   5. spid_router — auth SPID/CIE
#   6. dev_router — solo con VERUMDOC_DEV_AUTH=1 e ENV development/test
#   7. pdnd_router — e-service PDND
#   8. R03 b1: sessioni di caricamento (/api/v1/caricamenti)

app.include_router(system.router)
app.include_router(versamento.router, prefix="/api/v1")
app.include_router(status.router, prefix="/api/v1")       # Sprint 1 FIX-009
app.include_router(lifecycle.router)                        # Sprint 1 FIX-009
app.include_router(spid_router)
app.include_router(sessione_router)                         # R05, VD-C-079
app.include_router(accesso_locale_router)                   # R10, D08
app.include_router(credenziali_utente_router)               # R12b, VD-C-156
app.include_router(conferme_decisione_router)               # R17, VD-P-164
if _dev_router:
    app.include_router(_dev_router)
if _pdnd_router:
    app.include_router(_pdnd_router)
app.include_router(caricamenti.router, prefix="/api/v1")


# VD-C-100: ogni errore (ErroreApi, HTTPException, validazione, eccezioni non
# gestite) esce come application/problem+json del catalogo libs/shared/errori.
# Dopo gli add_middleware: l'id della richiesta è assegnato dal più esterno.
installa_gestori_errori(app)
