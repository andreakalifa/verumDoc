# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test health distribution."""
import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.unit
async def test_liveness_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v2/health/live")
    assert r.status_code == 200
    assert r.json()["service"] == "distribution"

@pytest.mark.unit
async def test_readiness_senza_database_503():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v2/health/ready")
    assert r.status_code == 503  # g4: DB non inizializzato (test_g4_prontezza.py)

@pytest.mark.unit
async def test_info_ha_tipi_pdd():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v2/info")
    assert r.status_code == 200
    body = r.json()
    assert "tipi_pdd" in body
    assert len(body["tipi_pdd"]) == 4
