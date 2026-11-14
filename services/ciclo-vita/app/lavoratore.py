# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/ciclo-vita/app/lavoratore.py — processo dedicato del lavoratore.

Uso: ``python -m app.lavoratore`` (Deployment ``ciclo-vita-lavoratore``, senza
porta HTTP). Esegue le pratiche con lavoro dovuto (lease in tabella, più
repliche insieme: REGOLE §12.9). Gli eventi che scrive in outbox (origine
``ciclo-vita``) li pubblica il relay delle repliche API del servizio.
"""
from __future__ import annotations

import asyncio
import signal

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


async def principale() -> None:
    from app.application.lavoratore import ciclo
    from app.main import controlla_login_db, problemi_avvio
    from libs.shared.db import close_db, init_db
    from libs.shared.outbox import configura_origine

    problemi = problemi_avvio(lavoratore=True)
    if problemi and settings.in_produzione:
        raise RuntimeError("configurazione del ciclo di vita non valida: " + "; ".join(problemi))
    configura_origine(settings.service_name)
    await init_db(settings.database_url)
    # Revisione 3 di R05 (T5): login del lavoratore, distinto da quello dell'API
    await controlla_login_db(lavoratore=True)
    fine = asyncio.Event()
    ciclo_eventi = asyncio.get_running_loop()
    for segnale in (signal.SIGTERM, signal.SIGINT):
        ciclo_eventi.add_signal_handler(segnale, fine.set)
    try:
        await ciclo(fine)
    finally:
        await close_db()
        logger.info("ciclo_vita_lavoratore_fermato")


if __name__ == "__main__":
    asyncio.run(principale())
