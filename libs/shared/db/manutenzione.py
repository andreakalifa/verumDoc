# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/db/manutenzione.py — lavori periodici sul database (R04).

  - partizioni: creazione anticipata e distacco a fine retention
    (``libs.shared.db.partizioni``), ogni ``DB_MANUTENZIONE_PARTIZIONI_SECONDI``
    (predefinito 3600) e all'avvio;
  - contatori per ente: consolidamento delle variazioni
    (``libs.shared.db.contatori``), ogni ``DB_MANUTENZIONE_CONTATORI_SECONDI``
    (predefinito 30).

Gira come task in ogni servizio con un relay dell'outbox e in notification
(retention degli eventi): più processi insieme sono sicuri (lease in tabella
per le partizioni, SKIP LOCKED per i contatori), e basta che UNO sia vivo
perché le partizioni future esistano. ``DB_MANUTENZIONE_ATTIVA=false`` lo spegne
nel processo.

R04 correzioni (rev4 rilievi 5 e 6): le partizioni le gestisce il ruolo
PROPRIETARIO delle tabelle (``DATABASE_URL_MANUTENZIONE``, connessione diretta),
non quello dei servizi. ``DB_MANUTENZIONE_PARTIZIONI`` decide se il processo le
mantiene: ``auto`` (predefinito) = sì se ``DATABASE_URL_MANUTENZIONE`` è
impostata o l'ambiente (``ENV``) è ``test`` (suite di prova, ruolo unico);
``true``/``false`` forzano. Dal secondo giro anche lo sviluppo (compose) ha i
ruoli separati: lì la manutenzione la fa il gateway con
``DATABASE_URL_MANUTENZIONE`` (``vd_manutenzione``), non ogni servizio. In
esercizio le mantiene il CronJob ``gateway-db-manutenzione`` (``python -m
libs.shared.db.manutenzione``, ruolo della manutenzione, Secret
``db-manutenzione``); i servizi consolidano solo i contatori (DML).

R05 (VD-C-080): il consolidamento dei contatori e le partizioni operano su
TUTTI gli enti: il ciclo e il CronJob girano nell'ambito conservatore esplicito
della Row Level Security (``libs.shared.db.ambito``); il CronJob entra con
``init_db(uso="manutenzione")`` (VD-C-113: il ruolo della manutenzione possiede
le tabelle partizionate operative, ma non può essere superutente).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.shared.db import get_async_session
from libs.shared.db import metriche as metriche_db
from libs.shared.db.ambito import ambito_conservatore, con_ambito_conservatore

logger = structlog.get_logger(__name__)


class ConfigManutenzione(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_MANUTENZIONE_", case_sensitive=False)
    attiva: bool = True
    #: auto | true | false (vedi docstring del modulo)
    partizioni: str = "auto"
    partizioni_secondi: float = 3600.0
    contatori_secondi: float = 30.0
    contatori_lotto: int = 50_000


#: Ambienti con un solo ruolo del database (le partizioni le mantiene il servizio):
#: solo la suite di prova. Sviluppo ed esercizio hanno i ruoli separati.
AMBIENTI_RUOLO_UNICO = frozenset({"test"})


def partizioni_nel_processo(cfg: ConfigManutenzione | None = None) -> bool:
    """True se questo processo deve mantenere le partizioni (vedi modulo)."""
    import os

    from libs.shared.db import manutenzione_configurata

    valore = (cfg or ConfigManutenzione()).partizioni.strip().lower()
    if valore in ("true", "1", "si", "sì", "yes"):
        return True
    if valore in ("false", "0", "no"):
        return False
    return manutenzione_configurata() or \
        os.environ.get("ENV", "").strip().lower() in AMBIENTI_RUOLO_UNICO


async def giro_partizioni(adesso: datetime | None = None) -> Any:
    from libs.shared.db import engine_manutenzione
    from libs.shared.db.partizioni import esegui_manutenzione_partizioni

    return await esegui_manutenzione_partizioni(engine_manutenzione(), adesso)


async def giro_contatori(lotto: int = 50_000) -> int:
    """Consolida finché ci sono variazioni (lotti da ``lotto``); restituisce il totale."""
    from libs.shared.db.contatori import consolida, in_attesa

    totale = 0
    while True:
        async with get_async_session() as session:
            n = await consolida(session, lotto=lotto)
        totale += n
        if n < lotto:
            break
    async with get_async_session() as session:
        await in_attesa(session)
    return totale


@con_ambito_conservatore("manutenzione del database: contatori e partizioni di tutti gli enti")
async def ciclo_manutenzione(fine: asyncio.Event, cfg: ConfigManutenzione | None = None) -> None:
    cfg = cfg or ConfigManutenzione()
    ultima_partizioni = -1e18
    partizioni = partizioni_nel_processo(cfg)
    if not partizioni:
        logger.info("manutenzione_partizioni_affidata_al_ruolo_dedicato",
                    motivo="DATABASE_URL_MANUTENZIONE non impostata fuori da sviluppo")
    while not fine.is_set():
        if partizioni and time.monotonic() - ultima_partizioni >= cfg.partizioni_secondi:
            ultima_partizioni = time.monotonic()
            try:
                esito = await giro_partizioni(datetime.now(UTC))
                if esito is not None and (any(esito.create.values())
                                          or any(esito.staccate.values())):
                    logger.info("manutenzione_partizioni", create=esito.create,
                                staccate=esito.staccate, bloccate=esito.bloccate)
            except Exception as exc:  # noqa: BLE001 — si riprova al giro successivo
                logger.error("manutenzione_partizioni_errore", errore=str(exc)[:300])
                ultima_partizioni = time.monotonic() - cfg.partizioni_secondi + 60
        try:
            await giro_contatori(cfg.contatori_lotto)
        except Exception as exc:  # noqa: BLE001 — si riprova al giro successivo
            logger.error("manutenzione_contatori_errore", errore=str(exc)[:300])
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(fine.wait(), timeout=cfg.contatori_secondi)


@dataclass
class LavoroManutenzione:
    fine: asyncio.Event
    task: asyncio.Task | None


async def avvia_manutenzione_db(*, app: Any = None,
                                cfg: ConfigManutenzione | None = None) -> LavoroManutenzione:
    """Avvia la manutenzione come task (nessun task con DB_MANUTENZIONE_ATTIVA=false)."""
    cfg = cfg or ConfigManutenzione()
    if app is not None:
        metriche_db.registra_metriche_db(metriche_db.registro_da_app(app))
    fine = asyncio.Event()
    task = asyncio.create_task(ciclo_manutenzione(fine, cfg), name="manutenzione-db") \
        if cfg.attiva else None
    return LavoroManutenzione(fine, task)


async def ferma_manutenzione_db(lavoro: LavoroManutenzione | None, *, attesa: float = 10.0) -> None:
    if lavoro is None or lavoro.task is None:
        return
    lavoro.fine.set()
    try:
        await asyncio.wait_for(asyncio.shield(lavoro.task), timeout=attesa)
    except TimeoutError:
        lavoro.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lavoro.task
    except Exception as exc:  # noqa: BLE001 — già terminato con errore
        logger.error("manutenzione_db_terminata_con_errore", errore=str(exc)[:300])


async def _main() -> None:  # pragma: no cover — esecuzione come CronJob
    import os

    from libs.shared.db import close_db, init_db

    # Ruolo proprietario, connessione diretta (CronJob gateway-db-manutenzione)
    await init_db(os.environ.get("DATABASE_URL_MANUTENZIONE") or os.environ["DATABASE_URL"],
                  uso="manutenzione")
    try:
        with ambito_conservatore("CronJob della manutenzione: partizioni e contatori"):
            esito = await giro_partizioni()
            logger.info("manutenzione_cronjob", partizioni=str(esito),
                        contatori_consolidati=await giro_contatori())
    finally:
        await close_db()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
