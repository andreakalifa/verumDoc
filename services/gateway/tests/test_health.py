# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test: health endpoints
Servizio: gateway
Riferimento piano: F0.1 — Skeleton health check

Test di base: verificano che il servizio risponda agli health check.
Devono essere SEMPRE verdi — sono la garanzia che il deploy è funzionante.
"""
import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg  # noqa: F401


@pytest.mark.unit
async def test_liveness_returns_200() -> None:
    """GET /health/live deve ritornare 200 con status alive."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"
    assert body["service"] == "gateway"


async def _ready():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get("/api/v1/health/ready")


@pytest.mark.unit
async def test_readiness_senza_database_non_e_pronto() -> None:
    """Senza DB inizializzato il gateway NON è pronto: 503 not_ready (VD-C-030).

    Prima il test pretendeva 200 senza alcun database: un gateway che non può
    registrare versamenti non deve ricevere traffico dal bilanciatore.
    """
    from libs.shared.db import close_db

    await close_db()
    response = await _ready()
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "non_inizializzato"


@richiede_pg
async def test_readiness_con_database_reale_e_pronto(db_url) -> None:
    """Con un PostgreSQL reale raggiungibile: 200 ready, database ok."""
    response = await _ready()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    # Redis non blocca e non espone testo di eccezioni; R07 (D11): nessun controllo di OPA
    assert body["checks"]["redis"] in {"ok", "errore"}
    assert "opa" not in body["checks"]


@pytest.mark.unit
async def test_readiness_database_irraggiungibile_503_senza_dettagli() -> None:
    """DB configurato ma irraggiungibile: 503, nessun testo dell'eccezione al client."""
    from libs.shared.db import close_db, init_db

    # porta 1: rifiuto immediato della connessione
    await init_db("postgresql+asyncpg://utente_inesistente:pw@127.0.0.1:1/nessuno")
    try:
        response = await _ready()
    finally:
        await close_db()
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"] == "errore"
    testo = response.text
    assert "127.0.0.1:1" not in testo and "utente_inesistente" not in testo
    assert "Errno" not in testo and "refused" not in testo.lower()


@pytest.mark.unit
async def test_info_returns_service_metadata() -> None:
    """GET /info deve ritornare nome, versione e env del servizio."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/info")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "gateway"
    assert "version" in body
    assert "env" in body
