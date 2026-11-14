# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers.dashboard import router as dashboard_router
from app.routers.documentazione_servizio import GENERAZIONI as _GENERAZIONI_DOCUMENTAZIONE
from app.routers.documentazione_servizio import router as documentazione_servizio_router
from app.routers.documenti_conservatore import router as documenti_conservatore_router
from app.routers.download import router as download_router
from app.routers.firme_responsabile import router as firme_responsabile_router
from app.routers.rielabora_ente import router as rielabora_ente_router
from app.routers.sla import router as sla_router  # T-02: SLA router ora montato
from libs.shared.db import close_db, init_db
from libs.shared.db.ambito import MiddlewareAmbitoRichiesta
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_s3 import controlla_s3
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("dashboard_api_starting")
    await init_db(settings.database_url)
    logger.info("dashboard_api_ready")
    yield
    await close_db()

app = FastAPI(title="VerumDoc Dashboard API",
              version=settings.service_version,
              docs_url="/docs", lifespan=lifespan)
_metriche_http = installa_metriche(app, "dashboard-api")  # VD-C-033: GET /metrics
# R15 (VD-C-177): esiti delle generazioni della documentazione (allarme DocumentazioneNonGenerata)
_metriche_http.registro.register(_GENERAZIONI_DOCUMENTAZIONE)
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.add_middleware(CORSMiddleware,
    allow_origins=["*"] if settings.env.lower() == "development" else settings.cors_origins,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(dashboard_router)
app.include_router(download_router)
app.include_router(sla_router)  # T-02: /sla/report, /sla/throughput, /admin/tenants-stats
app.include_router(rielabora_ente_router)  # R07: «Rielabora» dei versamenti rifiutati dall'ente
app.include_router(documenti_conservatore_router)  # R13a: manuale e piano sigillati
app.include_router(firme_responsabile_router)  # R16 (VD-C-166): firma personale con OTP
app.include_router(documentazione_servizio_router)  # R15: documentazione generata (VD-C-177)
# R04 servizi: code di scarto (VD-C-075), fruitori PDND (VD-C-084)
app.include_router(__import__("app.routers.scarti", fromlist=["router"]).router)
app.include_router(__import__("app.routers.pdnd_fruitori", fromlist=["router"]).router)

@app.get("/api/health/live", include_in_schema=False)
async def live(): return {"status": "alive", "service": "dashboard-api"}

@app.get("/api/health/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    """Pronto solo se rispondono database e S3 (scaricamento dei pacchetti);
    altrimenti 503. I servizi a valle del proxy non sono controllati qui.
    Testo delle eccezioni solo nel log (g4-chiusura)."""
    return await risposta_prontezza("dashboard-api", {
        "database": controlla_database,
        "s3": lambda: controlla_s3(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, region=getattr(settings, "s3_region", "eu-south-1"),
            bucket=settings.s3_bucket_aip),
    })

@app.get("/api/info")
async def info(): return {"service": "dashboard-api", "version": settings.service_version}


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
