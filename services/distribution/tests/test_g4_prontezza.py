# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""g4-chiusura: readiness vera di distribution.

Bloccanti: database, s3. Facoltativi (riportati, non bloccanti): kafka.
DB = PostgreSQL di test, S3 = moto, Kafka = broker minimo che parla il protocollo
(libs/shared/observability/tests/broker_kafka_finto.py)."""
from __future__ import annotations

import pytest
from app.config import settings
from app.main import app

from libs.shared.db import close_db, init_db
from libs.shared.observability.tests.broker_kafka_finto import broker_kafka_finto
from libs.shared.observability.tests.prova_prontezza import (
    chiedi,
    crea_bucket,
    imposta_s3,
    porta_chiusa,
    senza_dettagli_tecnici,
    url_db_irraggiungibile,
)
from tests import infra_reale
from tests.infra_reale import richiede_pg

# Fixture condivise (tests/infra_reale.py), registrate in questo modulo
db_url = infra_reale.db_url
s3_server = infra_reale.s3_server

PRONTO = "/api/v2/health/ready"
BLOCCANTI = ['database', 's3']
FACOLTATIVI = ['kafka']
BUCKET = 's3_bucket_aip'  # campo del bucket nelle Settings (None: si controlla solo il servizio S3)
USA_KAFKA = "kafka" in BLOCCANTI + FACOLTATIVI
USA_S3 = "s3" in BLOCCANTI


def _dipendenze(monkeypatch, *, kafka: str, s3: str | None, bucket: str | None) -> None:
    if USA_KAFKA:
        monkeypatch.setattr(settings, "kafka_bootstrap", kafka)
    if USA_S3:
        imposta_s3(monkeypatch, settings, s3 or f"http://127.0.0.1:{porta_chiusa()}",
                   bucket if BUCKET else None, campo_bucket=BUCKET or "s3_bucket_aip")


async def test_senza_database_inizializzato_503(monkeypatch) -> None:
    _dipendenze(monkeypatch, kafka=f"127.0.0.1:{porta_chiusa()}", s3=None, bucket="x")
    r = await chiedi(app, PRONTO)
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert r.json()["checks"]["database"] == "non_inizializzato"
    senza_dettagli_tecnici(r)


async def test_database_irraggiungibile_503_senza_credenziali(monkeypatch) -> None:
    _dipendenze(monkeypatch, kafka=f"127.0.0.1:{porta_chiusa()}", s3=None, bucket="x")
    await init_db(url_db_irraggiungibile())
    try:
        r = await chiedi(app, PRONTO)
    finally:
        await close_db()
    assert r.status_code == 503
    assert set(r.json()["checks"]) == set(BLOCCANTI + FACOLTATIVI)
    assert all(v == "errore" for v in r.json()["checks"].values())
    senza_dettagli_tecnici(r)


@richiede_pg
async def test_tutto_raggiungibile_200(db_url, s3_server, monkeypatch) -> None:
    async with broker_kafka_finto() as bootstrap:
        _dipendenze(monkeypatch, kafka=bootstrap, s3=s3_server, bucket=crea_bucket(s3_server))
        r = await chiedi(app, PRONTO)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"
    assert r.json()["checks"] == dict.fromkeys(BLOCCANTI + FACOLTATIVI, "ok")
    assert r.json()["facoltativi"] == sorted(FACOLTATIVI)


@richiede_pg
@pytest.mark.parametrize("giu", [d for d in ("kafka", "s3", "bucket")
                                 if (d == "bucket" and BUCKET and USA_S3) or
                                 (d != "bucket" and d in BLOCCANTI + FACOLTATIVI)])
async def test_una_dipendenza_giu(giu, db_url, s3_server, monkeypatch) -> None:
    async with broker_kafka_finto() as bootstrap:
        _dipendenze(monkeypatch,
                    kafka=f"127.0.0.1:{porta_chiusa()}" if giu == "kafka" else bootstrap,
                    s3=None if giu == "s3" else s3_server,
                    bucket="bucket-che-non-esiste" if giu == "bucket" else crea_bucket(s3_server))
        r = await chiedi(app, PRONTO)
    nome, stato = {"kafka": ("kafka", "errore"), "s3": ("s3", "errore"),
                   "bucket": ("s3", "bucket_assente")}[giu]
    checks = r.json()["checks"]
    assert checks[nome] == stato
    assert [k for k, v in checks.items() if v != "ok"] == [nome]
    # Bloccante → 503; facoltativo → resta 200 (riportato nei checks)
    assert r.status_code == (503 if nome in BLOCCANTI else 200)
    senza_dettagli_tecnici(r, "bucket-che-non-esiste")
