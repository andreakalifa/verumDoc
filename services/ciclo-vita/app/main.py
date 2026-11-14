# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/ciclo-vita/app/main.py — servizio del ciclo di vita dei pacchetti (R05).

Riferimenti: contratto Q2 di R05, VD-C-072, decisione VD-P-06 (servizio
dedicato); REGOLE_SVILUPPO §7.1, §12.3 (outbox con relay proprio), §12.9
(lavoratore con lease), §18 (errori dal catalogo), §19.4, §27.2.

Attività del processo:
  - API ``/api/v1/ciclo-vita`` (pratiche: rettifica, annullamento, scarto,
    riversamento, restituzione);
  - relay dell'outbox con origine ``ciclo-vita`` (eventi per i clienti) e
    manutenzione del database;
  - lavoratore delle pratiche (``LAVORATORE_ATTIVO``; in esercizio è un
    Deployment dedicato ``ciclo-vita-lavoratore``: ``python -m app.lavoratore``).
Salute: ``/api/v1/health/live``, ``/api/v1/health/ready`` (database e Kafka),
metriche ``/metrics``.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.limiti import LIMITATORE
from app.api.pratiche import router as router_pratiche
from app.config import settings
from libs.shared.db.ambito import MiddlewareAmbitoRichiesta
from libs.shared.errori import installa_gestori_errori
from libs.shared.limiti_richieste import controlla_configurazione
from libs.shared.observability.metriche import installa_metriche
from libs.shared.observability.prontezza import risposta_prontezza
from libs.shared.observability.prontezza_db import controlla_database
from libs.shared.observability.prontezza_kafka import controlla_kafka
from libs.shared.outbox import avvia_relay_servizio, ferma_relay_servizio
from libs.shared.security.sessione_cookie import MiddlewareSessioneCookie

logger = structlog.get_logger(__name__)


def problemi_avvio(*, lavoratore: bool) -> list[str]:
    """Impostazioni che in produzione fermano l'avvio (REGOLE §19.4).

    Revisione 2 di R05 (S2): le credenziali verso gli altri servizi (token del
    registro, chiave dei token di canale, token di distribution) servono SOLO
    al lavoratore, che le riceve dal suo Secret (``ciclo-vita-lavoratore-secrets``).
    Il processo API, esposto all'ingress, non le ha e non le pretende: una
    compromissione del pod API non dà accesso al registro, al gateway (versamenti
    a nome di altri) né a distribution (PdD di altri enti)."""
    from libs.shared.storage.worm import problemi_configurazione

    problemi = list(problemi_configurazione(settings))
    problemi += controlla_configurazione(env=settings.env, redis_url=settings.redis_url)
    problemi += problemi_conferme()
    if not lavoratore:
        return problemi
    if not settings.hashchain_token_registro.get_secret_value():
        problemi.append("HASHCHAIN_TOKEN_REGISTRO non impostato: nessuna foglia del ciclo di "
                        "vita nel registro delle impronte")
    if not settings.ciclo_vita_token_chiave_privata.get_secret_value():
        problemi.append("CICLO_VITA_TOKEN_CHIAVE_PRIVATA non impostata: rettifiche e "
                        "riversamenti in ingresso non possono versare")
    if not settings.distribution_token_servizio.get_secret_value():
        problemi.append("DISTRIBUTION_TOKEN_SERVIZIO non impostato: riversamento in uscita e "
                        "restituzione non possono chiedere i PdD")
    return problemi


def problemi_conferme() -> list[str]:
    """R17 (VD-P-164): conferma firmata delle decisioni: in produzione non si spegne e
    servono le chiavi pubbliche del gateway (API e lavoratore)."""
    from app.application.conferme import chiavi

    problemi: list[str] = []
    valore = settings.conferma_decisioni.strip().upper()
    if valore not in ("", "ATTIVO", "DISATTIVO"):
        problemi.append("CONFERMA_DECISIONI: atteso ATTIVO o DISATTIVO")
    if settings.in_produzione and valore == "DISATTIVO":
        problemi.append("CONFERMA_DECISIONI=DISATTIVO non ammesso in produzione")
    if settings.conferma_decisioni_attiva:
        try:
            if not chiavi()[0]:
                problemi.append("CONFERME_CHIAVI_PUBBLICHE vuoto: nessuna decisione sensibile "
                                "si potrebbe verificare")
        except ValueError as exc:
            problemi.append(f"CONFERME_CHIAVI_PUBBLICHE non valido: {exc}")
    return problemi


def credenziali_lavoratore_presenti() -> list[str]:
    """Nomi delle credenziali del lavoratore presenti nel processo (nessun valore)."""
    return [nome for nome, valore in (
        ("HASHCHAIN_TOKEN_REGISTRO", settings.hashchain_token_registro),
        ("CICLO_VITA_TOKEN_CHIAVE_PRIVATA", settings.ciclo_vita_token_chiave_privata),
        ("DISTRIBUTION_TOKEN_SERVIZIO", settings.distribution_token_servizio),
    ) if valore.get_secret_value()]


def problemi_processo_api() -> list[str]:
    """Problemi d'avvio del processo API: quelli di ``problemi_avvio`` (con le
    credenziali solo se il lavoratore gira nel processo, come in sviluppo) e,
    se il lavoratore NON gira qui, la presenza di sue credenziali (S2: in
    produzione l'API con un token del registro non parte)."""
    problemi = problemi_avvio(lavoratore=settings.lavoratore_attivo)
    presenti = credenziali_lavoratore_presenti()
    if not settings.lavoratore_attivo and presenti:
        problemi.append("credenziali del lavoratore nel processo API (" + ", ".join(presenti)
                        + "): vanno solo nel Secret ciclo-vita-lavoratore-secrets")
    return problemi


async def problemi_login_db(*, lavoratore: bool) -> list[str]:
    """Revisione 3 di R05 (T5): login del database distinti per API e
    lavoratore. Gli esiti di esecuzione (``esiti_esecuzione``, su cui il
    registro delle impronte riscontra lo scarto prima di firmarne il tombstone)
    li scrive SOLO il ruolo ``verumdoc_ciclo_vita_lavoratore``:
      - il lavoratore senza quel privilegio non potrebbe chiudere uno scarto;
      - l'API (esposta all'ingress) con quel privilegio vanificherebbe il
        riscontro: un pod API compromesso potrebbe fabbricare il fatto.
    Fuori da sviluppo/test entrambi i casi fermano l'avvio."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        scrive = bool((await session.execute(text(
            "SELECT has_table_privilege(current_user, 'public.esiti_esecuzione', 'INSERT')"
        ))).scalar_one())
    if lavoratore and not scrive:
        return ["il login del database del lavoratore non può scrivere gli esiti di "
                "esecuzione: serve un login membro di verumdoc_ciclo_vita_lavoratore "
                "(DATABASE_URL del Secret ciclo-vita-lavoratore-secrets)"]
    if not lavoratore and scrive:
        return ["il login del database dell'API può scrivere gli esiti di esecuzione "
                "(membro di verumdoc_ciclo_vita_lavoratore): l'API deve entrare con un login "
                "distinto da quello del lavoratore (DATABASE_URL di ciclo-vita-secrets)"]
    return []


async def controlla_login_db(*, lavoratore: bool) -> None:
    problemi = await problemi_login_db(lavoratore=lavoratore)
    if not problemi:
        return
    if settings.in_produzione:
        raise RuntimeError("login del database di ciclo-vita non valido: " + "; ".join(problemi))
    logger.warning("ciclo_vita_login_db_non_separato", problemi=problemi)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from libs.shared.db import close_db, init_db

    problemi = problemi_processo_api()
    if problemi:
        if settings.in_produzione:
            raise RuntimeError("configurazione del ciclo di vita non valida: "
                               + "; ".join(problemi))
        logger.warning("ciclo_vita_configurazione_incompleta", problemi=problemi)
    await init_db(settings.database_url)
    await controlla_login_db(lavoratore=settings.lavoratore_attivo)
    app.state.relay_outbox = await avvia_relay_servizio(
        settings.service_name, app=app, bootstrap=settings.kafka_bootstrap)
    fine = asyncio.Event()
    # Revisione 2 di R05 (S3): metriche (allarmi) dello scarto e del lavoratore
    from app.application import metriche as metriche_cv

    aggiornamento_metriche = asyncio.create_task(metriche_cv.ciclo(fine),
                                                 name="ciclo-vita-metriche")
    lavoratore = None
    if settings.lavoratore_attivo:
        from app.application.lavoratore import ciclo

        lavoratore = asyncio.create_task(ciclo(fine), name="ciclo-vita-lavoratore")
    logger.info("ciclo_vita_pronto", lavoratore=lavoratore is not None)
    yield
    fine.set()
    aggiornamento_metriche.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await aggiornamento_metriche
    if lavoratore is not None:
        try:
            await asyncio.wait_for(lavoratore, timeout=15)
        except TimeoutError:
            lavoratore.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lavoratore
    await ferma_relay_servizio(app.state.relay_outbox)
    await LIMITATORE.chiudi()
    await close_db()


app = FastAPI(
    title="VerumDoc Ciclo di vita",
    version=settings.service_version,
    docs_url="/docs" if not settings.in_produzione else None,
    redoc_url=None,
    lifespan=lifespan,
)
_metriche_http = installa_metriche(app, "ciclo-vita")


def _registra_metriche_ciclo_vita() -> None:
    from app.application import metriche as metriche_cv

    metriche_cv.registra(_metriche_http.registro)


_registra_metriche_ciclo_vita()
# R05 (VD-C-079): sessione del portale in cookie HttpOnly → Authorization, con CSRF
app.add_middleware(MiddlewareSessioneCookie)
# R05 (VD-C-080): ogni richiesta parte senza ambito d'ente (lo imposta l'identità verificata)
app.add_middleware(MiddlewareAmbitoRichiesta)
app.include_router(router_pratiche)


@app.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}


@app.get("/api/v1/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Pronto se rispondono il database (pratiche, outbox) e Kafka (relay)."""
    return await risposta_prontezza(settings.service_name, {
        "database": controlla_database,
        "kafka": lambda: controlla_kafka(settings.kafka_bootstrap),
    })


@app.get("/api/v1/ciclo-vita/info", include_in_schema=False)
async def info() -> dict:
    """Stato onesto del processo (nessun segreto). Revisione 2 di R05 (S2): le
    dipendenze verso registro, gateway e distribution le usa SOLO il
    lavoratore dedicato, che ha le credenziali; il processo API non le ha."""
    return {
        "service": settings.service_name, "version": settings.service_version,
        "env": settings.env,
        "lavoratore_nel_processo": settings.lavoratore_attivo,
        "dipendenze": "nel lavoratore ciclo-vita-lavoratore" if not settings.lavoratore_attivo
        else dict.fromkeys(credenziali_lavoratore_presenti(), "configurato"),
    }


# Ultima riga: il middleware dell'id della richiesta è il più esterno (VD-C-100)
installa_gestori_errori(app)
