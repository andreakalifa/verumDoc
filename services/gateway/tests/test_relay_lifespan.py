# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Relay dell'outbox nel ciclo di vita del gateway (VD-C-015) e ``kafka_produce``
che non scarta più in silenzio.

Prove su PostgreSQL reale: un evento rimasto in outbox (Kafka giù al momento
del versamento) viene pubblicato dal relay avviato nel lifespan; all'arresto il
relay si ferma senza attendere l'intervallo e prima della chiusura di producer
e DB.
"""
from __future__ import annotations

import asyncio
import time

import app.main as modulo_main
import pytest
from app.config import settings
from app.services.outbox import KafkaNonDisponibile, accoda_evento, invia_kafka

from libs.shared import kafka as kafka_condiviso
from libs.shared.db import get_async_session
from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)


class ProducerFinto:
    """Sostituisce AIOKafkaProducer: registra gli invii confermati."""

    def __init__(self) -> None:
        self.inviati: list[tuple[str, str | None, dict]] = []
        self.fermato = False

    async def send_and_wait(self, topic, value=None, key=None):
        if self.fermato:
            raise RuntimeError("producer già fermato")
        self.inviati.append((topic, key, value))

    async def stop(self):
        self.fermato = True


async def test_kafka_produce_senza_producer_solleva(monkeypatch):
    monkeypatch.setattr(kafka_condiviso, "_producer", None)
    with pytest.raises(KafkaNonDisponibile):
        await kafka_condiviso.kafka_produce("pdv.received", {"a": 1}, key="k")
    # L'invio dell'outbox lo propaga: l'evento non risulta pubblicato
    with pytest.raises(KafkaNonDisponibile):
        await invia_kafka("pdv.received", "k", {"a": 1})


async def test_kafka_produce_errore_broker_si_propaga(monkeypatch):
    class Rotto(ProducerFinto):
        async def send_and_wait(self, topic, value=None, key=None):
            raise ConnectionError("broker giù")

    monkeypatch.setattr(kafka_condiviso, "_producer", Rotto())
    with pytest.raises(ConnectionError):
        await kafka_condiviso.kafka_produce("pdv.received", {"a": 1})


@richiede_pg
@pytest.mark.integration
async def test_lifespan_avvia_relay_e_lo_ferma_pulito(db_url, monkeypatch):
    tenant, _ = await crea_tenant_e_classe(db_url)
    # Evento rimasto in sospeso (es. Kafka giù durante il versamento)
    async with get_async_session() as session:
        await accoda_evento(session, topic="pdv.received", chiave="p1",
                            payload={"pdv_id": "p1"}, tenant_id=tenant)

    producer = ProducerFinto()

    async def init_kafka_finto(_bootstrap: str) -> None:
        kafka_condiviso._producer = producer

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(modulo_main, "init_kafka_producer", init_kafka_finto)
    monkeypatch.setenv("OUTBOX_INTERVALLO_SECONDI", "5")

    app = modulo_main.app
    async with modulo_main.lifespan(app):
        task = app.state.relay
        assert task is not None and not task.done()
        for _ in range(100):
            if producer.inviati:
                break
            await asyncio.sleep(0.05)
        assert producer.inviati == [("pdv.received", "p1", {"pdv_id": "p1"})]
        inizio = time.monotonic()
    # Arresto: il relay termina (non viene cancellato a metà) senza attendere i 5 s
    assert time.monotonic() - inizio < 2.0
    assert task.done() and not task.cancelled() and task.exception() is None
    assert app.state.relay is None
    assert producer.fermato  # producer chiuso DOPO il relay
    righe = await sql(db_url, "SELECT pubblicato_il FROM public.outbox_eventi")
    assert [r["pubblicato_il"] is not None for r in righe] == [True]


@richiede_pg
@pytest.mark.integration
async def test_relay_disattivabile_per_processo_dedicato(db_url, monkeypatch):
    async def init_kafka_finto(_bootstrap: str) -> None:
        kafka_condiviso._producer = ProducerFinto()

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(modulo_main, "init_kafka_producer", init_kafka_finto)
    monkeypatch.setenv("OUTBOX_RELAY_ATTIVO", "false")
    app = modulo_main.app
    app.state.relay = None
    async with modulo_main.lifespan(app):
        assert app.state.relay is None
