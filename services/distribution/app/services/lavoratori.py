# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/lavoratori.py — lavoratori come processo dedicato.

R03 b4: indicizzazione (VD-C-093) e PdD ZIP (VD-C-077) girano di norma come
task del processo API (``LAVORATORI_ATTIVI=true``). Per scalare a parte:
``LAVORATORI_ATTIVI=false`` sulle repliche API e, in un Deployment dedicato,

    python -m app.services.lavoratori

Più repliche sono sicure: la coda d'indicizzazione usa ``FOR UPDATE SKIP
LOCKED`` e i PdD un lease con scadenza.
"""
from __future__ import annotations

import asyncio
import signal

from app.config import settings
from app.services.indicizzazione import ciclo_indicizzazione
from app.services.pdd_zip import ciclo_pdd
from libs.shared.db import close_db, init_db


async def principale() -> None:
    await init_db(settings.database_url)
    fine = asyncio.Event()
    ciclo = asyncio.get_running_loop()
    for segnale in (signal.SIGINT, signal.SIGTERM):
        ciclo.add_signal_handler(segnale, fine.set)
    try:
        await asyncio.gather(ciclo_indicizzazione(fine), ciclo_pdd(fine))
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(principale())
