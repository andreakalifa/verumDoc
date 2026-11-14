# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — un producer Kafka non partito all'avvio si ritenta, al più
una volta per pausa (2 s che raddoppiano fino a 60 s), senza attese interne."""
from __future__ import annotations

import pytest

from libs.shared import kafka


@pytest.fixture(autouse=True)
def _stato(monkeypatch):
    monkeypatch.setattr(kafka, "_producer", None)
    monkeypatch.setattr(kafka, "_bootstrap", None)
    monkeypatch.setattr(kafka, "_prossima_riprova", 0.0)
    monkeypatch.setattr(kafka, "_pausa_riprova", 0.0)


async def test_senza_bootstrap_nessun_tentativo(monkeypatch) -> None:
    async def mai(*_a, **_k):
        raise AssertionError("non deve tentare")

    monkeypatch.setattr(kafka, "init_kafka_producer", mai)
    assert await kafka.riprova_producer(adesso=0.0) is False


async def test_riprova_a_intervalli_crescenti_poi_riesce(monkeypatch) -> None:
    tentativi: list[tuple[str, int]] = []
    esito = {"su": False}

    async def finto_init(bootstrap, retries=5):
        tentativi.append((bootstrap, retries))
        kafka._producer = object() if esito["su"] else None

    monkeypatch.setattr(kafka, "_bootstrap", "kafka:9092")
    monkeypatch.setattr(kafka, "init_kafka_producer", finto_init)
    assert await kafka.riprova_producer(adesso=100.0) is False
    assert tentativi == [("kafka:9092", 1)]  # un solo tentativo, nessun ciclo interno
    assert await kafka.riprova_producer(adesso=101.0) is False  # entro la pausa (2 s)
    assert len(tentativi) == 1
    assert await kafka.riprova_producer(adesso=102.1) is False  # nuovo tentativo, pausa 4 s
    assert len(tentativi) == 2 and kafka._pausa_riprova == 4.0
    for _ in range(10):
        await kafka.riprova_producer(adesso=kafka._prossima_riprova)
    assert kafka._pausa_riprova == kafka.PAUSA_RIPROVA_MASSIMA_SECONDI
    esito["su"] = True
    assert await kafka.riprova_producer(adesso=kafka._prossima_riprova) is True
    assert kafka.producer_attivo() and kafka._pausa_riprova == 0.0
    n = len(tentativi)
    assert await kafka.riprova_producer(adesso=0.0) is True
    assert len(tentativi) == n  # attivo: nessun tentativo
