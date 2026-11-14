# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — connessione Kafka: PLAINTEXT in locale, SASL_SSL +
OAUTHBEARER IAM per MSK (KAFKA_AUTENTICAZIONE=iam). Nessun broker: si provano i
parametri, il fornitore del token (anche con la libreria AWS vera, offline: la
firma SigV4 non chiama la rete) e che aiokafka li accetti."""
from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from libs.shared.kafka import connessione
from libs.shared.kafka.connessione import (
    ConfigurazioneKafkaNonValida,
    ImpostazioniKafka,
    TokenMskIam,
    parametri_connessione,
)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    for nome in ("KAFKA_AUTENTICAZIONE", "KAFKA_REGIONE", "KAFKA_CA_FILE", "AWS_REGION",
                 "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(nome, raising=False)
    connessione._fornitori.clear()  # noqa: SLF001
    yield
    connessione._fornitori.clear()  # noqa: SLF001


def test_predefinito_plaintext_come_oggi() -> None:
    assert parametri_connessione() == {"security_protocol": "PLAINTEXT"}
    assert connessione.descrizione()["protocollo"] == "PLAINTEXT"


def test_iam_da_ambiente_sasl_ssl_oauthbearer(monkeypatch) -> None:
    monkeypatch.setenv("KAFKA_AUTENTICAZIONE", "iam")
    monkeypatch.setenv("AWS_REGION", "eu-south-1")
    p = parametri_connessione()
    assert p["security_protocol"] == "SASL_SSL"
    assert p["sasl_mechanism"] == "OAUTHBEARER"
    assert isinstance(p["sasl_oauth_token_provider"], TokenMskIam)
    assert p["sasl_oauth_token_provider"].regione == "eu-south-1"
    assert p["ssl_context"].check_hostname is True
    # un fornitore per processo (e regione): i token si riusano fra i client
    assert parametri_connessione()["sasl_oauth_token_provider"] is p["sasl_oauth_token_provider"]
    monkeypatch.setenv("KAFKA_REGIONE", "eu-west-1")  # KAFKA_REGIONE prevale
    assert parametri_connessione()["sasl_oauth_token_provider"].regione == "eu-west-1"
    assert connessione.descrizione() == {"autenticazione": "iam", "protocollo": "SASL_SSL",
                                         "regione": "eu-west-1"}


def test_iam_senza_regione_o_con_ca_inesistente_non_parte(monkeypatch) -> None:
    with pytest.raises(ConfigurazioneKafkaNonValida, match="regione"):
        parametri_connessione(ImpostazioniKafka(autenticazione="iam"))
    with pytest.raises(ConfigurazioneKafkaNonValida, match="KAFKA_CA_FILE"):
        parametri_connessione(ImpostazioniKafka(autenticazione="iam", regione="eu-south-1",
                                                ca_file="/non/esiste.pem"))
    monkeypatch.setenv("KAFKA_AUTENTICAZIONE", "sasl-plain")
    with pytest.raises(ValueError):
        ImpostazioniKafka()


async def test_token_riusato_fino_a_un_minuto_dalla_scadenza() -> None:
    adesso = [1_000.0]
    chiamate: list[str] = []

    def generatore(regione: str) -> tuple[str, int]:
        chiamate.append(regione)
        return f"token-{len(chiamate)}", int((adesso[0] + 900) * 1000)  # 15 minuti

    fornitore = TokenMskIam("eu-south-1", generatore=generatore, orologio=lambda: adesso[0])
    assert await fornitore.token() == "token-1"
    adesso[0] += 800  # 100 s alla scadenza: ancora valido
    assert await fornitore.token() == "token-1"
    adesso[0] += 50  # 50 s alla scadenza: si rinnova
    assert await fornitore.token() == "token-2"
    assert chiamate == ["eu-south-1", "eu-south-1"] and fornitore.extensions() == {}


async def test_libreria_aws_vera_genera_un_token_firmato_offline(monkeypatch) -> None:
    """Con credenziali (finte) nell'ambiente la libreria firma l'URL SigV4 dell'azione
    kafka-cluster:Connect senza rete: il token è quello che MSK verifica."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDPROVA")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "segreto-di-prova")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    fornitore = TokenMskIam("eu-south-1")
    token = await fornitore.token()
    url = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    parti = urlparse(url)
    assert parti.hostname == "kafka.eu-south-1.amazonaws.com"
    query = parse_qs(parti.query)
    assert query["Action"] == ["kafka-cluster:Connect"]
    assert query["X-Amz-Credential"][0].startswith("AKIDPROVA/")
    assert "X-Amz-Signature" in query


async def test_aiokafka_accetta_i_parametri_iam(monkeypatch) -> None:
    monkeypatch.setenv("KAFKA_AUTENTICAZIONE", "iam")
    monkeypatch.setenv("KAFKA_REGIONE", "eu-south-1")
    producer = AIOKafkaProducer(bootstrap_servers="b-1.msk:9098", **parametri_connessione())
    consumer = AIOKafkaConsumer("t", bootstrap_servers="b-1.msk:9098", group_id="g",
                                **parametri_connessione())
    for client in (producer.client, consumer._client):  # noqa: SLF001
        assert client._security_protocol == "SASL_SSL"  # noqa: SLF001
        assert client._sasl_mechanism == "OAUTHBEARER"  # noqa: SLF001
        assert isinstance(client._sasl_oauth_token_provider, TokenMskIam)  # noqa: SLF001
    await producer.stop()
    await consumer.stop()


async def test_probe_di_prontezza_usa_gli_stessi_parametri(monkeypatch) -> None:
    from libs.shared.observability import prontezza_kafka

    visti: list[dict] = []

    async def finta_conn(host, porta, **kw):
        visti.append(kw)
        raise ConnectionError("nessun broker nel test")

    monkeypatch.setattr(prontezza_kafka, "create_conn", finta_conn)
    monkeypatch.setenv("KAFKA_AUTENTICAZIONE", "iam")
    monkeypatch.setenv("KAFKA_REGIONE", "eu-south-1")
    with pytest.raises(ConnectionError):
        await prontezza_kafka.controlla_kafka("b-1.msk:9098")
    assert visti[0]["security_protocol"] == "SASL_SSL"
    assert visti[0]["sasl_mechanism"] == "OAUTHBEARER"
