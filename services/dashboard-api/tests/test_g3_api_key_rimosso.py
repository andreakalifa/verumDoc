# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g3-backend — il login con chiave API della dashboard è eliminato.

Le chiavi API le genera onboarding e le verifica solo il gateway
(libs/shared/security/chiavi_api.py). Il vecchio POST /api/dashboard/auth/api-key
usava bcrypt su un formato di chiave non più emesso e firmava token propri.
"""
from __future__ import annotations

from app.main import app
from httpx import ASGITransport, AsyncClient


async def test_rotta_non_esiste_piu() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/dashboard/auth/api-key",
                         params={"client_id": "vd_abcdefgh", "client_secret": "x"})
    assert r.status_code == 404


def test_rotta_assente_da_openapi() -> None:
    percorsi = app.openapi()["paths"]
    assert not any("api-key" in p for p in percorsi)
