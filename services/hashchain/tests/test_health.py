# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test health hashchain."""
import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.unit
async def test_liveness_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.json()["service"] == "hashchain"

@pytest.mark.unit
async def test_readiness_senza_database_503():
    """Senza lifespan (DB non inizializzato) il servizio NON è pronto: 503.
    Casi completi (DB, Kafka, S3 reali o giù): test_g4_prontezza.py."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["database"] == "non_inizializzato"

@pytest.mark.unit
async def test_info_returns_metadata():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/info")
    assert r.status_code == 200
    assert "checkpoint_secondi" in r.json() and "testimone_configurato" in r.json()
