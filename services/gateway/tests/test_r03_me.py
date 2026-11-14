# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 i1 — ``GET /api/v1/me`` con la chiave API (rapporto c1, questione 2).

Prima la rotta accettava solo il Bearer (``get_current_user_optional``): un
sistema versante con la chiave API non poteva sapere il proprio ente dall'API.
Ora usa la dipendenza completa e restituisce ente, piano e responsabile della
chiave. App reale del gateway su PostgreSQL vero.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import pytest
from app.auth import api_key
from app.auth.dependencies import get_redis
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient

from libs.shared.security.quote import svuota_cache_piani
from libs.shared.security.revoca import svuota_cache_revoche
from tests.infra_reale import db_url, richiede_pg  # noqa: F401
from tests.test_b4_accesso_canali_quote import PEPPER, _chiave, _ente

pytestmark = richiede_pg


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(api_key, "_PEPPER", PEPPER)
    app.dependency_overrides[get_redis] = lambda: None  # quote fuori dalla prova
    svuota_cache_piani()
    svuota_cache_revoche()
    yield
    app.dependency_overrides.pop(get_redis, None)
    svuota_cache_piani()
    svuota_cache_revoche()


async def _me(intestazioni: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gw") as c:
        return await c.get("/api/v1/me", headers=intestazioni)


async def test_me_con_chiave_api_ente_piano_e_responsabile(db_url):
    tid, uid = await _ente(db_url, piano="business")
    chiave = await _chiave(db_url, tid, uid)
    r = await _me({"X-Api-Key": chiave})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["tenant_id"] == str(tid) and corpo["user_id"] == str(uid)
    assert corpo["ruolo"] == "API_SYSTEM" and corpo["auth_method"] == "api_key"
    assert corpo["piano"] == "business" and corpo["canale"] == "REST"
    assert corpo["ente"] == {"id": str(tid), "ragione_sociale": "Ente", "stato": "ATTIVO"}
    assert corpo["chiave_api"]["responsabile"] == {"user_id": str(uid), "nome": "Ugo",
                                                   "cognome": "Rossi"}
    assert corpo["chiave_api"]["id"]
    assert "email" not in r.text


async def test_me_con_bearer_senza_chiave(db_url):
    tid, uid = await _ente(db_url, piano="enterprise")
    token = create_access_token(tenant_id=str(tid), user_id=str(uid), ruolo="OPERATORE",
                                scopes=["read"])
    r = await _me({"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["piano"] == "enterprise" and corpo["auth_method"] == "jwt"
    assert corpo["ente"]["id"] == str(tid) and corpo["chiave_api"] is None


async def test_me_senza_credenziali_o_chiave_errata_401(db_url):
    assert (await _me({})).json()["codice"] == "AUTH-000"
    r = await _me({"X-Api-Key": "vd_chiave_inventata_0000000000000000"})
    assert r.status_code == 401
