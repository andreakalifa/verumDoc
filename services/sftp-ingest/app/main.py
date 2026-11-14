# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/sftp-ingest/app/main.py — canale di versamento via SFTP.

Servizio: sftp-ingest (piano R03 §3.2). Due componenti nello stesso processo,
attivabili separatamente con SFTP_COMPONENTI (es. repliche del solo lavoratore):
  - «server»: server SFTP (asyncssh) sulla porta SFTP_PORTA, aree per account
    sul volume SFTP_RADICE;
  - «lavoratore»: prende le cartelle pronte con lease sul database e le versa
    sul gateway (sessioni di caricamento + versamento per riferimento).
HTTP (porta 8000): salute, prontezza, metriche Prometheus, informazioni.
Nessuna API pubblica: gli account si gestiscono da onboarding.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.application.lavoratore import ConfigLavoratore, Lavoratore
from app.config import problemi_avvio, settings
from app.infrastructure import repository
from app.infrastructure.gateway import ClientGateway
from app.infrastructure.segnalazioni import Segnalatore
from app.infrastructure.server_sftp import (
    ContestoServer,
    QuotaAccount,
    avvia_server,
    carica_chiave_host,
)
from app.infrastructure.token_canale import emettitore_da_impostazioni
from app.metriche import MetricheSftp
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import NonPronto, risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database

logger = structlog.get_logger(__name__)

_stato: dict[str, Any] = {"server": None, "lavoratore": None, "client": None}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from libs.shared.db import close_db, init_db

    problemi = problemi_avvio(settings)
    if problemi:
        for p in problemi:
            logger.error("sftp_avvio_bloccato", problema=p)
        raise RuntimeError("sftp-ingest non parte: " + "; ".join(problemi))
    logger.info("sftp_ingest_starting", env=settings.env, componenti=settings.sftp_componenti)
    Path(settings.sftp_radice).mkdir(parents=True, exist_ok=True)
    await init_db(settings.database_url)
    attivita: list[asyncio.Task[None]] = []
    if "server" in settings.sftp_componenti:
        # R03 f2 (REV-15): attività dei clienti segnalata al lavoratore a lotti
        segnalatore = Segnalatore(
            stabilita_secondi=settings.lavoratore_stabilita_secondi,
            intervallo_secondi=settings.sftp_segnalazioni_intervallo_secondi)
        contesto = ContestoServer(
            radice=settings.sftp_radice, cerca_account=repository.account_per_accesso,
            tipi_chiave_ammessi=tuple(settings.sftp_tipi_chiave_ammessi),
            rsa_bit_minimi=settings.sftp_rsa_bit_minimi,
            quota=QuotaAccount(settings.sftp_quota_account_byte,
                               settings.sftp_quota_ricalcolo_secondi),
            metriche=METRICHE_SFTP,
            segnalatore=segnalatore,
            registra_accesso=repository.registra_accesso)
        attivita.append(asyncio.create_task(segnalatore.esegui()))
        chiave = carica_chiave_host(
            valore=settings.sftp_chiave_host.get_secret_value(), ambiente=settings.ambiente,
            file_sviluppo=settings.sftp_chiave_host_file_sviluppo)
        _stato["server"] = await avvia_server(
            contesto, host=settings.sftp_host, porta=settings.sftp_porta, chiave_host=chiave,
            algoritmi_firma=settings.sftp_algoritmi_firma,
            login_timeout=settings.sftp_login_timeout_secondi,
            proxy_fidate=settings.sftp_proxy_sorgenti_fidate,
            proxy_attesa=settings.sftp_proxy_attesa_secondi)
        logger.info("sftp_server_in_ascolto", porta=settings.sftp_porta)
    if "lavoratore" in settings.sftp_componenti:
        emettitore = emettitore_da_impostazioni(
            env=settings.ambiente,
            chiave_privata=settings.sftp_token_chiave_privata.get_secret_value(),
            emittente=settings.jwt_issuer,
            durata_secondi=settings.token_canale_durata_secondi)
        client = ClientGateway(settings.gateway_url,
                               timeout_secondi=settings.gateway_timeout_secondi,
                               ritentativi_secondi=settings.gateway_ritentativi_secondi,
                               retry_after_max_secondi=settings.gateway_retry_after_max_secondi)
        _stato["client"] = client
        lavoratore = Lavoratore(ConfigLavoratore.da_impostazioni(settings), gateway=client,
                                emettitore=emettitore, metriche=METRICHE_SFTP)
        _stato["lavoratore"] = lavoratore
        attivita.append(asyncio.create_task(lavoratore.esegui()))
    logger.info("sftp_ingest_ready")
    try:
        yield
    finally:
        for t in attivita:
            t.cancel()
        for t in attivita:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if _stato["server"] is not None:
            _stato["server"].close()
            await _stato["server"].wait_closed()
        if _stato["client"] is not None:
            await _stato["client"].chiudi()
        _stato.update(server=None, lavoratore=None, client=None)
        await close_db()


app = FastAPI(
    title="VerumDoc SFTP Ingest",
    version=settings.service_version,
    docs_url=None if settings.in_produzione else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)
_METRICHE_HTTP = installa_metriche(app, "sftp-ingest")  # GET /metrics
METRICHE_SFTP = MetricheSftp(_METRICHE_HTTP.registro)


async def controlla_volume() -> None:
    """Il volume delle aree SFTP esiste ed è scrivibile."""
    radice = settings.sftp_radice
    if not os.path.isdir(radice) or not os.access(radice, os.W_OK | os.X_OK):
        raise NonPronto("volume_non_scrivibile")


async def controlla_server() -> None:
    if "server" in settings.sftp_componenti and _stato["server"] is None:
        raise NonPronto("server_non_avviato")


@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "alive", "service": settings.service_name}


@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto se rispondono database e volume e, se attivo, il server è in ascolto.
    Il gateway non blocca la prontezza: i versamenti si riprendono quando torna."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "volume": controlla_volume,
        "server_sftp": controlla_server,
    })


@app.get("/api/v1/info", include_in_schema=False)
async def info() -> dict[str, Any]:
    return {"service": settings.service_name, "version": settings.service_version,
            "env": settings.env, "componenti": settings.sftp_componenti,
            "porta_sftp": settings.sftp_porta if "server" in settings.sftp_componenti else None}


installa_gestori_errori(app)
