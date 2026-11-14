# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test health endpoints validation service."""
import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.unit
async def test_liveness_returns_200() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert response.json()["service"] == "validation"

@pytest.mark.unit
async def test_readiness_senza_database_503() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")
    assert response.status_code == 503  # g4: DB non inizializzato (test_g4_prontezza.py)
    assert response.json()["status"] == "not_ready"

@pytest.mark.unit
async def test_info_returns_service_metadata() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/info")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "validation"
    assert "version" in body
    assert "env" in body
