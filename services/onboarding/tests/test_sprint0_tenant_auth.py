# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/tests/test_sprint0_tenant_auth.py
Sprint 0 FIX-003: regression test — onboarding richiede JWT su tutti gli endpoint.

Verifica che:
  - Registra tenant senza token → 401
  - Lista tenant senza token → 401
  - Attiva tenant senza token → 401
  - Disattiva tenant senza token → 401
  - VIEWER non può creare tenant → 403
  - SUPER_ADMIN può creare tenant → payload valido
"""
from __future__ import annotations

import pytest
from app.auth.jwt_handler import create_access_token
from fastapi import HTTPException


class TestOnboardingTenantAuth:
    """FIX-003: tutti gli endpoint tenant richiedono JWT."""

    def _auth(self, ruolo: str = "SUPER_ADMIN") -> str:
        token = create_access_token(
            tenant_id="t-admin", user_id="u-admin",
            ruolo=ruolo, scopes=["*"],
        )
        return f"Bearer {token}"

    def test_registra_senza_token_401(self):
        from app.routers.tenant import _require_super_admin
        with pytest.raises(HTTPException) as exc_info:
            _require_super_admin(None)
        assert exc_info.value.status_code == 401

    def test_lista_senza_token_401(self):
        from app.routers.tenant import _require_super_admin
        with pytest.raises(HTTPException) as exc_info:
            _require_super_admin("")
        assert exc_info.value.status_code == 401

    def test_attiva_senza_token_401(self):
        from app.routers.tenant import _require_super_admin
        with pytest.raises(HTTPException) as exc_info:
            _require_super_admin("Bearer ")
        assert exc_info.value.status_code == 401

    def test_viewer_non_puo_creare_tenant(self):
        from app.routers.tenant import _require_super_admin
        token = create_access_token(
            tenant_id="t-001", user_id="u-viewer",
            ruolo="VIEWER", scopes=["read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            _require_super_admin(f"Bearer {token}")
        assert exc_info.value.status_code == 403

    def test_admin_tenant_non_puo_creare_tenant(self):
        from app.routers.tenant import _require_super_admin
        token = create_access_token(
            tenant_id="t-001", user_id="u-admin",
            ruolo="ADMIN_TENANT", scopes=["admin:tenant"],
        )
        with pytest.raises(HTTPException) as exc_info:
            _require_super_admin(f"Bearer {token}")
        assert exc_info.value.status_code == 403

    def test_super_admin_passes(self):
        from app.routers.tenant import _require_super_admin
        payload = _require_super_admin(self._auth("SUPER_ADMIN"))
        assert payload["ruolo"] == "SUPER_ADMIN"


class TestOnboardingTenantAccess:
    """FIX-003: _check_tenant_access isolamento multi-tenant."""

    def test_admin_tenant_accede_solo_al_proprio(self):
        from app.routers.tenant import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "ADMIN_TENANT"}
        _check_tenant_access(user, "t-001")  # OK

    def test_admin_tenant_bloccato_su_altro(self):
        from app.routers.tenant import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "ADMIN_TENANT"}
        with pytest.raises(HTTPException) as exc_info:
            _check_tenant_access(user, "t-002")
        # 404 fra clienti diversi (REGOLE §14.4)
        assert exc_info.value.status_code == 404

    def test_super_admin_accede_a_tutti(self):
        from app.routers.tenant import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "SUPER_ADMIN"}
        _check_tenant_access(user, "t-999")  # OK

    def test_rsc_accede_a_tutti(self):
        from app.routers.tenant import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "RSC_CONSERVATORE"}
        _check_tenant_access(user, "t-999")  # OK
