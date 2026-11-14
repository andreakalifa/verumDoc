# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Aiuti comuni ai test di prontezza dei servizi (g4-chiusura): ogni servizio ha
il suo tests/test_g4_prontezza.py con l'app vera, il PostgreSQL di test
(tests/infra_reale.py), S3 moto e il broker Kafka minimo (broker_kafka_finto).
"""
from __future__ import annotations

import socket
import uuid

from httpx import ASGITransport, AsyncClient, Response

#: Credenziali «segrete» messe nell'URL di un DB irraggiungibile: non devono
#: mai comparire nella risposta della probe.
UTENTE_SEGRETO = "utente_prontezza"
PASSWORD_SEGRETA = "pw_prontezza_segreta"


def porta_chiusa() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def url_db_irraggiungibile() -> str:
    return (f"postgresql+asyncpg://{UTENTE_SEGRETO}:{PASSWORD_SEGRETA}"
            f"@127.0.0.1:{porta_chiusa()}/nessuno")


async def chiedi(app, percorso: str) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(percorso)


def senza_dettagli_tecnici(r: Response, *altri: str) -> None:
    """La risposta della probe non contiene host, porte, credenziali, eccezioni."""
    testo = r.text
    for vietato in (UTENTE_SEGRETO, PASSWORD_SEGRETA, "127.0.0.1", "Traceback",
                    "Error", "refused", *altri):
        assert vietato not in testo, f"«{vietato}» nella risposta: {testo}"


def crea_bucket(endpoint: str) -> str:
    import boto3

    nome = f"prontezza-{uuid.uuid4().hex[:10]}"
    boto3.client("s3", endpoint_url=endpoint, aws_access_key_id="test",
                 aws_secret_access_key="test", region_name="us-east-1").create_bucket(Bucket=nome)
    return nome


def imposta_s3(monkeypatch, settings, endpoint: str, bucket: str | None,
               campo_bucket: str = "s3_bucket_aip") -> None:
    monkeypatch.setattr(settings, "s3_endpoint_url", endpoint)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    if hasattr(settings, "s3_region"):
        monkeypatch.setattr(settings, "s3_region", "us-east-1")
    if bucket is not None:
        monkeypatch.setattr(settings, campo_bucket, bucket)
