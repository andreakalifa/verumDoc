# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""libs/shared/observability/prontezza*.py: controlli reali delle dipendenze (g4-chiusura)."""
from __future__ import annotations

import asyncio
import json
import socket

import pytest

from libs.shared.observability.prontezza import NonPronto, esegui_controlli, risposta_prontezza
from libs.shared.observability.prontezza_kafka import controlla_kafka, indirizzi
from libs.shared.observability.prontezza_s3 import controlla_s3
from libs.shared.observability.tests.broker_kafka_finto import broker_kafka_finto, porta_chiusa


def _corpo(risposta) -> dict:
    return json.loads(risposta.body)


# ── Schema comune ──────────────────────────────────────────────────

async def test_tutti_ok_200() -> None:
    async def ok():
        return None

    r = await risposta_prontezza("svc", {"database": ok, "kafka": ok})
    assert r.status_code == 200
    assert _corpo(r) == {"status": "ready", "service": "svc",
                         "checks": {"database": "ok", "kafka": "ok"}, "facoltativi": []}


async def test_un_guasto_503_senza_testo_eccezione(capsys) -> None:
    async def ok():
        return None

    async def guasto():
        raise OSError("connessione rifiutata a 10.1.2.3 utente=vd_user password=segreta")

    r = await risposta_prontezza("svc", {"database": ok, "s3": guasto})
    assert r.status_code == 503
    corpo = _corpo(r)
    assert corpo["status"] == "not_ready" and corpo["checks"] == {"database": "ok", "s3": "errore"}
    testo = r.body.decode()
    for vietato in ("10.1.2.3", "vd_user", "segreta", "OSError"):
        assert vietato not in testo


async def test_non_pronto_e_timeout() -> None:
    async def non_init():
        raise NonPronto("non_inizializzato")

    async def lento():
        await asyncio.sleep(5)

    checks = await esegui_controlli({"database": non_init, "kafka": lento}, timeout=0.2)
    assert checks == {"database": "non_inizializzato", "kafka": "timeout"}


async def test_facoltativi_riportati_ma_non_bloccanti() -> None:
    async def ok():
        return None

    async def guasto():
        raise RuntimeError("giù")

    r = await risposta_prontezza("svc", {"database": ok}, facoltativi={"kafka": guasto})
    assert r.status_code == 200
    assert _corpo(r)["checks"] == {"database": "ok", "kafka": "errore"}
    assert _corpo(r)["facoltativi"] == ["kafka"]


# ── Kafka (protocollo vero verso un broker minimo) ──────────────────

def test_indirizzi() -> None:
    assert indirizzi("a:9093, b , [::1]:9094") == [("a", 9093), ("b", 9092), ("::1", 9094)]
    assert indirizzi("") == []


async def test_kafka_che_risponde() -> None:
    async with broker_kafka_finto() as bootstrap:
        await controlla_kafka(bootstrap)
        # Primo broker giù, secondo su: basta uno
        await controlla_kafka(f"{porta_chiusa()},{bootstrap}")


async def test_kafka_giu_o_senza_broker() -> None:
    with pytest.raises(Exception):  # noqa: B017 — errore di connessione di aiokafka
        await controlla_kafka(porta_chiusa(), timeout_ms=500)
    async with broker_kafka_finto(senza_broker=True) as bootstrap:
        with pytest.raises(RuntimeError, match="senza broker"):
            await controlla_kafka(bootstrap)
    with pytest.raises(NonPronto):
        await controlla_kafka("")


async def test_kafka_che_non_parla_kafka() -> None:
    """Una porta aperta che non risponde al protocollo: timeout, non «ok»."""
    async def muto(reader, writer):
        await asyncio.sleep(3)
        writer.close()

    server = await asyncio.start_server(muto, "127.0.0.1", 0)
    porta = server.sockets[0].getsockname()[1]
    try:
        checks = await esegui_controlli(
            {"kafka": lambda: controlla_kafka(f"127.0.0.1:{porta}", timeout_ms=300)}, timeout=1.0)
    finally:
        server.close()
    assert checks["kafka"] in {"errore", "timeout"}


# ── S3 (moto) ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def s3_endpoint():
    from moto.server import ThreadedMotoServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=porta, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{porta}"
    server.stop()


def _parametri(endpoint: str) -> dict:
    return {"endpoint_url": endpoint, "access_key": "test", "secret_key": "test",
            "region": "us-east-1"}


async def test_s3_bucket_presente_assente_e_senza_bucket(s3_endpoint) -> None:
    import boto3

    boto3.client("s3", **{"endpoint_url": s3_endpoint, "aws_access_key_id": "test",
                          "aws_secret_access_key": "test", "region_name": "us-east-1"}
                 ).create_bucket(Bucket="prontezza-ok")
    await controlla_s3(**_parametri(s3_endpoint), bucket="prontezza-ok")
    await controlla_s3(**_parametri(s3_endpoint))
    with pytest.raises(NonPronto) as e:
        await controlla_s3(**_parametri(s3_endpoint), bucket="prontezza-assente")
    assert e.value.stato == "bucket_assente"


async def test_s3_irraggiungibile() -> None:
    endpoint = "http://" + porta_chiusa()
    checks = await esegui_controlli(
        {"s3": lambda: controlla_s3(**_parametri(endpoint), bucket="x", timeout=0.5)},
        timeout=3.0)
    assert checks == {"s3": "errore"}


async def test_s3_bucket_senza_listbucket_e_pronto(monkeypatch) -> None:
    """R04 quarto giro: HeadBucket richiede s3:ListBucket, che i ruoli con
    permessi minimi sul bucket dei PdA non hanno. S3 risponde 403 a un bucket
    esistente (404 a uno inesistente): il servizio è pronto, non «errore»."""
    from botocore.exceptions import ClientError

    from libs.shared.observability import prontezza_s3

    class SenzaListBucket:
        def __init__(self, codice: str) -> None:
            self.codice = codice

        def head_bucket(self, **_):
            raise ClientError({"Error": {"Code": self.codice}}, "HeadBucket")

    monkeypatch.setattr(prontezza_s3, "_client", lambda *a, **k: SenzaListBucket("403"))
    await controlla_s3(endpoint_url=None, access_key=None, secret_key=None, bucket="aip")
    monkeypatch.setattr(prontezza_s3, "_client", lambda *a, **k: SenzaListBucket("404"))
    with pytest.raises(NonPronto, match="bucket_assente"):
        await controlla_s3(endpoint_url=None, access_key=None, secret_key=None, bucket="aip")
    monkeypatch.setattr(prontezza_s3, "_client", lambda *a, **k: SenzaListBucket("500"))
    with pytest.raises(ClientError):
        await controlla_s3(endpoint_url=None, access_key=None, secret_key=None, bucket="aip")
