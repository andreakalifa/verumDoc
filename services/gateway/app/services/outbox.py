# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Outbox transazionale del gateway: eventi scritti nella stessa transazione del
cambio di stato e pubblicati su Kafka da un relay.

Servizio: gateway. Riferimenti: VD-C-015 (R02), VD-C-076 (R04); REGOLE_SVILUPPO §12.3.
La logica è in ``libs/shared/outbox.py``, condivisa da tutti i servizi che
scrivono eventi; questo modulo la riesporta e resta il punto d'avvio del relay
del gateway come processo dedicato: ``python -m app.services.outbox``. Dal R04
il relay del gateway pubblica SOLO gli eventi del gateway (origine «gateway»):
onboarding, distribution e gli altri hanno il proprio relay.
"""
from __future__ import annotations

import asyncio

from libs.shared.outbox import (
    ConfigRelay,
    Invio,
    KafkaNonDisponibile,
    accoda_evento,
    avvia_relay_servizio,
    configura_origine,
    ferma_relay_servizio,
    invia_kafka,
    misura_arretrato,
    pubblica_dopo_commit,
    pubblica_in_sospeso,
    relay_outbox,
)

__all__ = ["ConfigRelay", "Invio", "KafkaNonDisponibile", "accoda_evento",
           "avvia_relay_servizio", "configura_origine", "ferma_relay_servizio", "invia_kafka",
           "misura_arretrato", "pubblica_dopo_commit", "pubblica_in_sospeso", "relay_outbox"]


async def _main() -> None:  # pragma: no cover — avvio come processo dedicato
    from app.config import settings
    from libs.shared.db import init_db
    from libs.shared.kafka import init_kafka_producer

    configura_origine("gateway")
    await init_db(settings.database_url)
    await init_kafka_producer(settings.kafka_bootstrap)
    await relay_outbox(origine="gateway")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
