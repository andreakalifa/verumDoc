# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/main.py — servizio notifiche (VD-C-024, contratto C3).

Attività:
  - consumer Kafka della posta: accoda le notifiche (public.notifications); il
    ciclo di invio le spedisce via SMTP con ritentativi e DLQ. R12b (VD-C-161):
    l'invio gira solo con POSTA_INVIO_ATTIVO=true, in esercizio nel lavoratore
    dedicato ``app.lavoratore_posta`` (Deployment notification-posta, non esposto),
    l'unico con la chiave privata della posta e le credenziali SMTP;
  - consumer Kafka degli eventi per i clienti (gruppo proprio): registra gli
    eventi (public.eventi_cliente) e accoda le consegne webhook;
  - passo di pubblicazione degli eventi: assegna la posizione dell'elenco dopo
    il commit (seriale fra le repliche, R03 f2);
  - lavoratore delle consegne webhook (SKIP LOCKED: più repliche insieme).
API pubbliche: GET /api/v1/eventi e /api/v1/webhook (JWT utente o chiave API).
Stato onesto in /api/v1/info: posta e webhook «non configurati» finché non lo sono.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.campanella import router as router_campanella
from app.api.eventi import router as router_eventi
from app.api.pec import router as router_pec
from app.api.webhook import router as router_webhook
from app.application.configurazione import (
    cifratore,
    parametri_consegna,
    problemi_avvio,
    stato_webhook,
)
from app.application.consegne import ciclo_consegne
from app.application.eventi_consumer import (
    avvia_consumer_eventi,
    ciclo_consumer_eventi,
    ciclo_pubblicazione,
    ferma_consumer_eventi,
)
from app.config import settings
from app.infrastructure.consegna_http import InviatoreWebhook
from app.lavoratore_posta import ciclo_invio, ciclo_pec
from app.services.coda import CodaUscitaSQL
from app.services.consumer import (
    close_notification_consumer,
    consume_loop,
    init_notification_consumer,
)
from app.services.smtp import problemi_configurazione, sicurezza_effettiva
from libs.shared.db.ambito import MiddlewareAmbitoRichiesta
from libs.shared.db.manutenzione import avvia_manutenzione_db, ferma_manutenzione_db
from libs.shared.errori import installa_gestori_errori
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_kafka import controlla_kafka
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)


def stato_posta() -> dict:
    if not settings.posta_invio_attivo:
        # R12b (VD-C-161): l'invio è del lavoratore dedicato (notification-posta)
        return {"stato": "inviata dal lavoratore dedicato",
                "lavoratore": "notification-posta (POSTA_INVIO_ATTIVO=true)"}
    problemi = problemi_configurazione(settings)
    if problemi:
        return {"stato": "posta non configurata", "problemi": problemi}
    return {
        "stato": "configurata",
        "host": urlparse(f"//{settings.smtp_host}").hostname,
        "porta": settings.smtp_port,
        "sicurezza": sicurezza_effettiva(settings.smtp_port, settings.smtp_sicurezza),
        "pec": "canali PEC dal registro della console (invio e ricevute nel lavoratore "
               "della posta)",
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from libs.shared.db import close_db, init_db

    logger.info("notification_starting", env=settings.env, posta=stato_posta()["stato"])
    sicurezza = sicurezza_effettiva(settings.smtp_port, settings.smtp_sicurezza)
    if settings.in_produzione and settings.posta_invio_attivo and settings.smtp_host \
            and sicurezza == "nessuna":
        raise RuntimeError("SMTP senza cifratura vietato in produzione")
    problemi = problemi_avvio(settings)
    if problemi:
        if settings.in_produzione:
            raise RuntimeError("configurazione dei webhook non valida: " + "; ".join(problemi))
        logger.warning("webhook_configurazione_incompleta", problemi=problemi)
    await init_db(settings.database_url)
    # R04 (VD-C-103): manutenzione del database (partizioni mensili di eventi e
    # consegne con retention RETENZIONE_EVENTI_CLIENTE_GIORNI, contatori)
    manutenzione = await avvia_manutenzione_db(app=app)
    coda = CodaUscitaSQL()
    await init_notification_consumer()
    attivita = [asyncio.create_task(consume_loop(coda))]
    if settings.posta_invio_attivo:
        # R12b (VD-C-161): in esercizio l'invio è SOLO nel lavoratore notification-posta
        attivita.append(asyncio.create_task(ciclo_invio(coda)))
        if settings.pec_attivo:
            # R13b: in sviluppo (un solo processo) anche il canale PEC; in esercizio
            # solo nel lavoratore notification-posta
            attivita.append(asyncio.create_task(ciclo_pec()))
    if settings.eventi_consumer_attivo:
        await avvia_consumer_eventi(bootstrap=settings.kafka_bootstrap,
                                    group_id=settings.eventi_kafka_group_id,
                                    topics=sorted(settings.eventi_topic))
        attivita.append(asyncio.create_task(ciclo_consumer_eventi(
            mappa_topic=settings.eventi_topic,
            ritardi=settings.kafka_ritardi_ritentativi_secondi,
            lotto=settings.eventi_consumer_lotto)))
    if settings.eventi_pubblicazione_attiva:
        # R03 f2 (REV-13): posizione assegnata dopo il commit, una replica alla volta
        attivita.append(asyncio.create_task(ciclo_pubblicazione(
            intervallo_secondi=settings.eventi_pubblicazione_intervallo_secondi,
            lotto=settings.eventi_pubblicazione_lotto)))
    inviatore: InviatoreWebhook | None = None
    cifratore_attivo = cifratore() if not problemi else None
    if settings.webhook_lavoratore_attivo and cifratore_attivo is not None:
        inviatore = InviatoreWebhook(
            timeout_connessione=settings.webhook_timeout_connessione_secondi,
            timeout=settings.webhook_timeout_secondi,
            consenti_privati=settings.webhook_consenti_indirizzi_privati,
            ca_pem=settings.webhook_ca_pem)
        attivita.append(asyncio.create_task(ciclo_consegne(
            inviatore=inviatore, cifratore=cifratore_attivo,
            parametri=parametri_consegna(settings),
            intervallo_secondi=settings.webhook_intervallo_secondi)))
    # R14 (VD-C-170): metriche del lavoratore della posta (senza HTTP) dal database
    fine_metriche = asyncio.Event()
    if settings.metriche_posta_attive:
        from app.application import metriche_posta

        attivita.append(asyncio.create_task(metriche_posta.ciclo(
            fine_metriche, settings.metriche_posta_intervallo_secondi)))
    logger.info("notification_ready", webhook=stato_webhook(settings)["stato"])
    yield
    fine_metriche.set()
    for t in attivita:
        t.cancel()
    for t in attivita:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    if inviatore is not None:
        await inviatore.chiudi()
    await ferma_consumer_eventi()
    await close_notification_consumer()
    await ferma_manutenzione_db(manutenzione)
    await close_db()


app = FastAPI(
    title="VerumDoc Notification Service",
    version=settings.service_version,
    docs_url="/docs" if not settings.in_produzione else None,
    redoc_url=None,
    lifespan=lifespan,
)
_metriche_http = installa_metriche(app, "notification")  # VD-C-033: GET /metrics


def _registra_metriche_posta() -> None:
    """R14 (VD-C-170): metriche del lavoratore della posta nel /metrics del servizio."""
    from app.application import metriche_posta

    metriche_posta.registra(_metriche_http.registro)


_registra_metriche_posta()
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.include_router(router_eventi)   # contratto C3: elenco degli eventi
app.include_router(router_campanella)  # R10 (D32): campanella e preferenze
app.include_router(router_webhook)  # contratto C3: webhook firmati
app.include_router(router_pec)  # R13b (VD-C-078): console dei canali PEC


@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}


@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto solo se rispondono database (coda di uscita) e Kafka (eventi da
    notificare). La posta non configurata è uno stato di /api/v1/info, non un guasto.
    Testo delle eccezioni solo nel log (g4-chiusura)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "kafka": lambda: controlla_kafka(settings.kafka_bootstrap),
    })


@app.get("/api/v1/info", summary="Metadati del servizio e stato della posta")
async def info() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "env": settings.env,
        "kafka_topic_in": settings.kafka_topic_in,
        "posta": stato_posta(),
        "webhook": stato_webhook(settings),
    }


# VD-C-100: ogni errore esce nel formato unico application/problem+json del
# catalogo libs/shared/errori (ultima riga: il middleware dell'id della
# richiesta è il più esterno).
installa_gestori_errori(app)
