# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test: health endpoints
Servizio: signature
Riferimento piano: F0.1 — Skeleton health check

Test di base: verificano che il servizio risponda agli health check.
Devono essere SEMPRE verdi — sono la garanzia che il deploy è funzionante.
"""

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


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
    assert body["service"] == "signature"


@pytest.mark.unit
async def test_readiness_returns_200() -> None:
    """GET /health/ready deve ritornare 200."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


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
    assert body["service"] == "signature"
    assert "version" in body
    assert "env" in body
