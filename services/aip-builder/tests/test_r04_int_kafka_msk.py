# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — consumer e producer del servizio si collegano a MSK con
IAM (KAFKA_AUTENTICAZIONE=iam: SASL_SSL + OAUTHBEARER). Le classi aiokafka sono
sostituite da un registratore che si ferma dopo la costruzione (nessun broker)."""
from __future__ import annotations

import pytest

from libs.shared.kafka import connessione


class Fermati(BaseException):
    """Interrompe l'avvio dopo la costruzione (non catturata da ``except Exception``)."""


def _registratore(visti: list[dict]):
    class Client:
        def __init__(self, *topic, **kw):
            visti.append(kw)

        def subscribe(self, *a, **k):
            return None

        async def start(self):
            raise Fermati()

        async def stop(self):
            return None

    return Client


@pytest.fixture
def iam(monkeypatch):
    connessione._fornitori.clear()  # noqa: SLF001
    monkeypatch.setenv("KAFKA_AUTENTICAZIONE", "iam")
    monkeypatch.setenv("KAFKA_REGIONE", "eu-south-1")
    yield
    connessione._fornitori.clear()  # noqa: SLF001


def _verifica(visti: list[dict], attesi: int) -> None:
    assert len(visti) >= attesi
    for kw in visti:
        assert kw["security_protocol"] == "SASL_SSL"
        assert kw["sasl_mechanism"] == "OAUTHBEARER"
        assert kw["sasl_oauth_token_provider"].regione == "eu-south-1"


async def test_consumer_e_producer_di_aip_builder(monkeypatch, iam) -> None:
    from app.services import aip_engine

    visti: list[dict] = []
    monkeypatch.setattr(aip_engine, "AIOKafkaConsumer", _registratore(visti))
    monkeypatch.setattr(aip_engine, "AIOKafkaProducer", _registratore(visti))
    with pytest.raises(Fermati):
        await aip_engine.init_aip_consumer()
    _verifica(visti, 2)
