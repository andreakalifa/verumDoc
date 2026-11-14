# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/tests/test_deepreview.py
Code review finale 20/03/2026 — regression per CRIT-003, HIGH-001.
"""
from __future__ import annotations

import inspect

import pytest

# ── CRIT-003: fascicoli.py must require auth on all endpoints ────────

class TestCRIT003FascicoliAuth:
    def test_crea_fascicolo_has_auth(self):
        from app.routers.fascicoli import crea_fascicolo
        sig = inspect.signature(crea_fascicolo)
        assert "authorization" in sig.parameters

    def test_get_fascicolo_has_auth(self):
        from app.routers.fascicoli import get_fascicolo
        sig = inspect.signature(get_fascicolo)
        assert "authorization" in sig.parameters

    def test_aggiungi_aip_has_auth(self):
        from app.routers.fascicoli import aggiungi_aip
        sig = inspect.signature(aggiungi_aip)
        assert "authorization" in sig.parameters

    def test_lista_aip_fascicolo_has_auth(self):
        from app.routers.fascicoli import lista_aip_fascicolo
        sig = inspect.signature(lista_aip_fascicolo)
        assert "authorization" in sig.parameters

    def test_fascicoli_auth_rejects_none(self):
        from app.routers.fascicoli import _fascicoli_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _fascicoli_auth(None)
        assert exc.value.status_code == 401

    def test_fascicoli_auth_rejects_invalid(self):
        from app.routers.fascicoli import _fascicoli_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _fascicoli_auth("Bearer bad.token.here")
        assert exc.value.status_code == 401

    def test_fascicoli_auth_accepts_valid_token(self):
        from app.auth.jwt_handler import create_access_token
        from app.routers.fascicoli import _fascicoli_auth
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="ADMIN_TENANT", scopes=["read"],
        )
        user = _fascicoli_auth(f"Bearer {token}")
        assert user["ruolo"] == "ADMIN_TENANT"


# ── HIGH-001: distribution main.py must mount all 3 routers ──────────

class TestHIGH001RouterMounting:
    def test_fascicoli_router_mounted(self):
        from app.main import app
        # Le rotte pubblicate (OpenAPI): con questa versione di FastAPI app.routes
        # contiene anche oggetti _IncludedRouter senza attributo path
        routes = list(app.openapi()["paths"])
        # At least one /api/v2/fascicoli path
        assert any("/fascicoli" in r for r in routes), \
            f"fascicoli router not mounted. Routes: {[r for r in routes if 'v2' in r]}"

    def test_migration_router_mounted(self):
        from app.main import app
        # Le rotte pubblicate (OpenAPI): con questa versione di FastAPI app.routes
        # contiene anche oggetti _IncludedRouter senza attributo path
        routes = list(app.openapi()["paths"])
        assert any("/migration" in r for r in routes), \
            f"migration router not mounted. Routes: {[r for r in routes if 'v2' in r]}"

    def test_distribution_router_still_mounted(self):
        from app.main import app
        # Le rotte pubblicate (OpenAPI): con questa versione di FastAPI app.routes
        # contiene anche oggetti _IncludedRouter senza attributo path
        routes = list(app.openapi()["paths"])
        assert any("/distribution" in r for r in routes)
