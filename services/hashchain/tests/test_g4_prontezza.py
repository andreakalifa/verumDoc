# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""g4-chiusura: readiness vera di hashchain (database e S3; da R04 senza Kafka, eventi
via outbox) e eliminazione di GET /api/v1/hashchain/integrity (non autenticato)."""
from __future__ import annotations

import pytest
from app.config import settings
from app.main import app

from libs.shared.db import close_db, init_db
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

PRONTO = "/api/v1/health/ready"


@pytest.fixture
def dipendenze_giu(monkeypatch):
    imposta_s3(monkeypatch, settings, f"http://127.0.0.1:{porta_chiusa()}", "x")


async def test_senza_database_inizializzato_503(dipendenze_giu) -> None:
    r = await chiedi(app, PRONTO)
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert r.json()["checks"]["database"] == "non_inizializzato"
    senza_dettagli_tecnici(r)


async def test_database_irraggiungibile_503_senza_credenziali(dipendenze_giu) -> None:
    await init_db(url_db_irraggiungibile())
    try:
        r = await chiedi(app, PRONTO)
    finally:
        await close_db()
    assert r.status_code == 503
    # R04: Kafka non è più una dipendenza della hashchain (eventi via outbox)
    assert r.json()["checks"] == {"database": "errore", "s3": "errore"}
    senza_dettagli_tecnici(r)


@richiede_pg
async def test_tutto_raggiungibile_200(db_url, s3_server, monkeypatch) -> None:
    imposta_s3(monkeypatch, settings, s3_server, crea_bucket(s3_server))
    r = await chiedi(app, PRONTO)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ready", "service": "hashchain", "facoltativi": [],
                        "checks": {"database": "ok", "s3": "ok"}}


@richiede_pg
@pytest.mark.parametrize("giu", ["s3", "bucket"])
async def test_una_dipendenza_giu_503(giu, db_url, s3_server, monkeypatch) -> None:
    imposta_s3(monkeypatch, settings, s3_server, crea_bucket(s3_server))
    if giu == "s3":
        imposta_s3(monkeypatch, settings, f"http://127.0.0.1:{porta_chiusa()}", None)
    if giu == "bucket":
        monkeypatch.setattr(settings, "s3_bucket_aip", "bucket-che-non-esiste")
    r = await chiedi(app, PRONTO)
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["database"] == "ok"
    atteso = {"s3": ("s3", "errore"), "bucket": ("s3", "bucket_assente")}[giu]
    assert checks[atteso[0]] == atteso[1]
    assert [k for k, v in checks.items() if v != "ok"] == [atteso[0]]
    senza_dettagli_tecnici(r, "bucket-che-non-esiste")


async def test_integrity_non_autenticato_eliminato() -> None:
    r = await chiedi(app, "/api/v1/hashchain/integrity")
    assert r.status_code == 404
    assert "/api/v1/hashchain/integrity" not in app.openapi()["paths"]


async def test_nessuna_api_della_catena_senza_token() -> None:
    """Tutte le rotte /api/v1/hashchain/* rispondono 401 senza token."""
    rotte = [(m.upper(), p) for p, op in app.openapi()["paths"].items()
             if p.startswith("/api/v1/hashchain") for m in op]
    assert rotte, "nessuna rotta della catena trovata"
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for metodo, percorso in rotte:
            r = await c.request(metodo, percorso)
            assert r.status_code == 401, (metodo, percorso, r.status_code)
