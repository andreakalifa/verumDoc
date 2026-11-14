# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test RBAC dashboard-api — F1-04 + B-03 regression.
Verifica che ogni endpoint protetto risponda correttamente a:
- Token mancante → 401
- Token scaduto → 401 AUTH-002
- Ruolo insufficiente → 403 AUTH-010
- Ruolo corretto → passa il guard (mock risposta 200)
"""
from __future__ import annotations

import time as _time
from unittest.mock import AsyncMock, patch

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient

TENANT_ID   = "00000000-0000-0000-0000-000000000001"
TENANT_ID_B = "bbbbbbbb-0000-0000-0000-000000000002"
USER_ID     = "00000000-0000-0000-0000-000000000010"


def _tok(ruolo: str, scopes: list[str] | None = None,
         tenant_id: str = TENANT_ID) -> str:
    return create_access_token(
        tenant_id=tenant_id, user_id=USER_ID,
        ruolo=ruolo, scopes=scopes or ["read"],
    )


def _expired_tok(ruolo: str = "SUPER_ADMIN") -> str:
    """Token già scaduto."""
    import jwt as pyjwt
    from app.auth.jwt_handler import chiavi_jwt
    from app.config import settings

    priv = chiavi_jwt().privata_pem  # coppia effimera di ENV=test (VD-C-004)
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(_time.time()) - 7200,
        "exp": int(_time.time()) - 3600,
        "tenant_id": TENANT_ID,
        "user_id": USER_ID,
        "ruolo": ruolo,
        "scopes": ["*"],
    }
    return pyjwt.encode(payload, priv, algorithm="RS256")


def _mock_http_200():
    """Mock _get che ritorna risposta vuota."""
    async def _fake_get(url, params=None, **_kw):
        return {"total": 0, "items": []}
    return patch("app.routers.dashboard._get", side_effect=_fake_get)


def _mock_db_session_empty():
    session = AsyncMock()
    # scalar_one per count
    count_r = AsyncMock()
    count_r.scalar_one.return_value = 0
    count_r.scalar_one_or_none = AsyncMock(return_value=0)
    # scalars per lista
    scalars_r = AsyncMock()
    scalars_r.all.return_value = []
    session.execute.side_effect = [count_r, scalars_r]
    session.commit = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__  = AsyncMock(return_value=False)
    return cm


# ══════════════════════════════════════════════════════════════════════
# Endpoint /tenants — solo SUPER_ADMIN
# ══════════════════════════════════════════════════════════════════════

class TestRBACTenants:
    """GET/POST /api/dashboard/tenants — solo SUPER_ADMIN."""

    @pytest.mark.asyncio
    async def test_tenants_senza_token_ritorna_401(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/api/dashboard/tenants")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_tenants_token_scaduto_ritorna_401(self):
        """B-03 regression: token scaduto non deve passare."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/tenants",
                headers={"Authorization": f"Bearer {_expired_tok('SUPER_ADMIN')}"},
            )
        assert r.status_code == 401
        assert r.json()["codice"] == "AUTH-002"

    @pytest.mark.asyncio
    async def test_tenants_viewer_ritorna_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/tenants",
                headers={"Authorization": f"Bearer {_tok('VIEWER')}"},
            )
        assert r.status_code == 403
        assert r.json()["codice"] == "AUTH-010"

    @pytest.mark.asyncio
    async def test_tenants_admin_tenant_ritorna_403(self):
        """ADMIN_TENANT non può vedere tenants di altri — solo SUPER_ADMIN."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/tenants",
                headers={"Authorization": f"Bearer {_tok('ADMIN_TENANT')}"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_tenants_operatore_ritorna_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/tenants",
                headers={"Authorization": f"Bearer {_tok('OPERATORE')}"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_tenants_super_admin_passa_guard(self):
        """SUPER_ADMIN supera il guard (può ricevere 502 senza onboarding, ma non 401/403)."""
        with _mock_http_200():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                r = await c.get(
                    "/api/dashboard/tenants",
                    headers={"Authorization": f"Bearer {_tok('SUPER_ADMIN', ['*'])}"},
                )
        # Il guard è passato — l'errore eventuale è di backend (502/200), non 401/403
        assert r.status_code not in (401, 403)

    @pytest.mark.asyncio
    async def test_rsc_conservatore_ritorna_403_su_tenants(self):
        """RSC_CONSERVATORE non ha accesso a gestione tenants."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/tenants",
                headers={"Authorization": f"Bearer {_tok('RSC_CONSERVATORE', ['*'])}"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_post_tenants_senza_token_401(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/dashboard/tenants", json={})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_post_tenants_viewer_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                "/api/dashboard/tenants",
                headers={"Authorization": f"Bearer {_tok('VIEWER')}"},
                json={"ragione_sociale": "Test"},
            )
        assert r.status_code == 403


# ══════════════════════════════════════════════════════════════════════
# Endpoint /classi-documentali — admin
# ══════════════════════════════════════════════════════════════════════

class TestRBACClassiDocumentali:
    """POST /api/dashboard/classi-documentali — richiede ruolo admin."""

    @pytest.mark.asyncio
    async def test_post_classi_senza_token_401(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/dashboard/classi-documentali", json={})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_post_classi_viewer_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                "/api/dashboard/classi-documentali",
                headers={"Authorization": f"Bearer {_tok('VIEWER')}"},
                json={"codice": "test", "nome": "Test"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_post_classi_operatore_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                "/api/dashboard/classi-documentali",
                headers={"Authorization": f"Bearer {_tok('OPERATORE', ['read', 'versamento:ud'])}"},
                json={"codice": "test", "nome": "Test"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_post_classi_admin_tenant_supera_guard(self):
        """ADMIN_TENANT può creare classi — supera il guard."""
        async def _fake_post(url, body, **_kw):
            return {"id": "test", "codice": "test"}

        with patch("app.routers.dashboard._post", side_effect=_fake_post):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                r = await c.post(
                    "/api/dashboard/classi-documentali",
                    headers={"Authorization": f"Bearer {_tok('ADMIN_TENANT', ['admin:tenant'])}"},
                    json={"codice": "test", "nome": "Test Classe"},
                )
        assert r.status_code not in (401, 403)

    @pytest.mark.asyncio
    async def test_post_classi_token_scaduto_401(self):
        """B-03: token scaduto su endpoint classi."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                "/api/dashboard/classi-documentali",
                headers={"Authorization": f"Bearer {_expired_tok('ADMIN_TENANT')}"},
                json={"codice": "test"},
            )
        assert r.status_code == 401
        assert r.json()["codice"] == "AUTH-002"


# ══════════════════════════════════════════════════════════════════════
# Endpoint /manuale-conservazione — solo SUPER_ADMIN e RSC
# ══════════════════════════════════════════════════════════════════════

class TestRBACManuale:
    """GET /api/dashboard/manuale-conservazione — solo RSC e SUPER_ADMIN."""

    @pytest.mark.asyncio
    async def test_manuale_senza_token_401(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/api/dashboard/manuale-conservazione")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_manuale_viewer_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/manuale-conservazione",
                headers={"Authorization": f"Bearer {_tok('VIEWER')}"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_manuale_operatore_403(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/manuale-conservazione",
                headers={"Authorization": f"Bearer {_tok('OPERATORE')}"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_manuale_admin_tenant_403(self):
        """ADMIN_TENANT non può generare il manuale — è responsabilità del RSC."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/manuale-conservazione",
                headers={"Authorization": f"Bearer {_tok('ADMIN_TENANT')}"},
            )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_manuale_rsc_conservatore_200(self):
        """RSC_CONSERVATORE può generare il manuale."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/manuale-conservazione",
                headers={"Authorization": f"Bearer {_tok('RSC_CONSERVATORE', ['*'])}"},
            )
        assert r.status_code == 200
        body = r.json()
        assert "sezioni" in body
        assert "meta" in body

    @pytest.mark.asyncio
    async def test_manuale_super_admin_200(self):
        """SUPER_ADMIN può generare il manuale."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/manuale-conservazione",
                headers={"Authorization": f"Bearer {_tok('SUPER_ADMIN', ['*'])}"},
            )
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# Test formato JWT malformato
# ══════════════════════════════════════════════════════════════════════

class TestRBACTokenMalformato:
    """Token malformato → sempre 401."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token", [
        "NON_UN_JWT",
        "Bearer",
        "Bearer   ",
        "Bearer NON.VALIDO",
        "Bearer A.B",          # solo 2 parti
        "Bearer A.B.C.D",      # 4 parti
    ])
    async def test_token_malformato_ritorna_401(self, token: str):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/dashboard/tenants",
                headers={"Authorization": token},
            )
        assert r.status_code == 401
