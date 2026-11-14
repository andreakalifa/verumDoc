# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test health dashboard-api."""
import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.unit
async def test_liveness_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/health/live")
    assert r.status_code == 200
    assert r.json()["service"] == "dashboard-api"

@pytest.mark.unit
async def test_readiness_senza_database_503():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/health/ready")
    assert r.status_code == 503  # g4: DB non inizializzato (test_g4_prontezza.py)

@pytest.mark.unit
async def test_info_dichiara_servizio_e_versione():
    """/api/info risponde con nome del servizio e versione configurata.

    g3-test: sostituisce ``test_info_has_moduli``, che pretendeva un campo
    ``moduli`` (overview/aip/hashchain) mai esistito nel codice (assente anche
    nel commit base) e non letto da alcun client (frontend, SDK): l'elenco
    delle funzioni pubblicate è l'OpenAPI, provato in test_configurazione.py.
    """
    from app.config import settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/info")
    assert r.status_code == 200
    assert r.json() == {"service": "dashboard-api", "version": settings.service_version}
