# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Controllo di prontezza del database (pool di libs/shared/db): SELECT 1."""
from __future__ import annotations

from sqlalchemy import text

from libs.shared.db import get_engine
from libs.shared.observability.prontezza import NonPronto


async def controlla_database() -> None:
    """Termina se il database risponde; NonPronto se il pool non è inizializzato
    (lifespan non eseguito o fallito); altrimenti l'eccezione del driver."""
    try:
        engine = get_engine()
    except RuntimeError:
        engine = None
    if engine is None:
        raise NonPronto("non_inizializzato")
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
