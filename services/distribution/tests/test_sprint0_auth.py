# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/tests/test_sprint0_auth.py
Sprint 0 FIX-002: regression test — distribution richiede JWT.

Verifica che:
  - Richiesta senza token → 401 (non più fallback anonimo)
  - Token forgiato → 401
  - Token valido → accesso consentito
"""
from __future__ import annotations

import pytest
from app.auth.jwt_handler import create_access_token
from fastapi import HTTPException


class TestDistributionAuth:
    """FIX-002: _get_user non accetta più richieste anonime."""

    def test_no_token_returns_401(self):
        from app.routers.distribution import _get_user
        with pytest.raises(HTTPException) as exc_info:
            _get_user(None)
        assert exc_info.value.status_code == 401

    def test_empty_bearer_returns_401(self):
        from app.routers.distribution import _get_user
        with pytest.raises(HTTPException) as exc_info:
            _get_user("Bearer ")
        assert exc_info.value.status_code == 401

    def test_no_auth_header_returns_401(self):
        """PRIMA: ritornava {"tenant_id": "00000...", "ruolo": "ADMIN_TENANT"}."""
        from app.routers.distribution import _get_user
        with pytest.raises(HTTPException) as exc_info:
            _get_user("")
        assert exc_info.value.status_code == 401

    def test_valid_token_passes(self):
        from app.routers.distribution import _get_user
        token = create_access_token(
            tenant_id="t-test-002",
            user_id="u-002",
            ruolo="ADMIN_TENANT",
            scopes=["distribution"],
        )
        payload = _get_user(f"Bearer {token}")
        assert payload["tenant_id"] == "t-test-002"
        assert payload["ruolo"] == "ADMIN_TENANT"

    def test_forged_token_returns_401(self):
        from app.routers.distribution import _get_user
        with pytest.raises(HTTPException) as exc_info:
            _get_user("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake.fake")
        assert exc_info.value.status_code == 401


class TestTenantOwnership:
    """FIX-002: verifica che AIP appartengano al tenant richiedente."""

    def test_same_tenant_allowed(self):
        from app.routers.distribution import _check_tenant_owns_aip
        user = {"tenant_id": "t-001", "ruolo": "ADMIN_TENANT"}
        _check_tenant_owns_aip(user, "t-001")

    def test_different_tenant_blocked(self):
        from app.routers.distribution import _check_tenant_owns_aip
        user = {"tenant_id": "t-001", "ruolo": "ADMIN_TENANT"}
        with pytest.raises(HTTPException) as exc_info:
            _check_tenant_owns_aip(user, "t-002")
        # 404 fra clienti diversi (REGOLE §14.4, VD-C-005)
        assert exc_info.value.status_code == 404

    def test_super_admin_sees_all(self):
        from app.routers.distribution import _check_tenant_owns_aip
        user = {"tenant_id": "t-001", "ruolo": "SUPER_ADMIN"}
        _check_tenant_owns_aip(user, "t-999")
