# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/tests/test_sprint0_jwt.py
Sprint 0 FIX-001: regression test per verifica firma JWT RS256.

Verifica che:
  - Token forgiato (base64 valido ma firma sbagliata) → 401
  - Token scaduto → 401
  - Token valido con firma RS256 → 200
  - Assenza token → 401
  - Token con ruolo insufficiente → 403
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from app.auth.jwt_handler import create_access_token, verify_access_token
from fastapi import HTTPException


class TestJWTVerification:
    """FIX-001: verifica firma JWT RS256 nel dashboard-api."""

    def test_valid_token_passes(self):
        """Token valido con firma RS256 deve essere accettato."""
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="ADMIN_TENANT", scopes=["read"],
        )
        payload = verify_access_token(token)
        assert payload["tenant_id"] == "t-001"
        assert payload["ruolo"] == "ADMIN_TENANT"

    def test_forged_token_rejected(self):
        """Token con payload base64 valido ma firma sbagliata → 401."""
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({
                "tenant_id": "hacker-tenant",
                "ruolo": "SUPER_ADMIN",
                "exp": int(time.time()) + 3600,
            }).encode()
        ).rstrip(b"=").decode()
        fake_sig = base64.urlsafe_b64encode(b"not-a-real-signature").rstrip(b"=").decode()
        forged_token = f"{header}.{payload}.{fake_sig}"

        with pytest.raises(HTTPException) as exc_info:
            verify_access_token(forged_token)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["type"] == "AUTH-001"

    def test_expired_token_rejected(self):
        """Token scaduto → 401 con codice AUTH-002."""
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="ADMIN_TENANT", scopes=["read"],
            expiry_seconds=-120,  # Già scaduto (oltre la tolleranza di 30 s)
        )
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token(token)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["type"] == "AUTH-002"

    def test_missing_tenant_id_rejected(self):
        """Token senza claim tenant_id → 401."""
        # Crea token manualmente senza tenant_id
        import jwt as pyjwt
        from app.auth.jwt_handler import chiavi_jwt
        from app.config import settings

        data = {
            "ruolo": "ADMIN_TENANT",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(data, chiavi_jwt().privata_pem, algorithm="RS256")
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token(token)
        assert exc_info.value.status_code == 401

    def test_garbage_token_rejected(self):
        """Token completamente invalido → 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token("not.a.jwt")
        assert exc_info.value.status_code == 401


class TestRequireRole:
    """FIX-001: _require_role nel dashboard usa verify_access_token."""

    def test_no_auth_header_returns_401(self):
        from app.routers.dashboard import _require_role
        with pytest.raises(HTTPException) as exc_info:
            _require_role(None)
        assert exc_info.value.status_code == 401

    def test_empty_bearer_returns_401(self):
        from app.routers.dashboard import _require_role
        with pytest.raises(HTTPException) as exc_info:
            _require_role("Bearer ")
        assert exc_info.value.status_code == 401

    def test_wrong_role_returns_403(self):
        from app.routers.dashboard import _require_role
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="VIEWER", scopes=["read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            _require_role(f"Bearer {token}", "SUPER_ADMIN")
        assert exc_info.value.status_code == 403

    def test_correct_role_passes(self):
        from app.routers.dashboard import _require_role
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="SUPER_ADMIN", scopes=["*"],
        )
        payload = _require_role(f"Bearer {token}", "SUPER_ADMIN")
        assert payload["ruolo"] == "SUPER_ADMIN"


class TestCheckTenantAccess:
    """Sprint 0: _check_tenant_access previene data leakage cross-tenant."""

    def test_same_tenant_passes(self):
        from app.routers.dashboard import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "ADMIN_TENANT"}
        _check_tenant_access(user, "t-001")  # Non deve lanciare

    def test_different_tenant_blocked(self):
        from app.routers.dashboard import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "ADMIN_TENANT"}
        with pytest.raises(HTTPException) as exc_info:
            _check_tenant_access(user, "t-002")
        assert exc_info.value.status_code == 403

    def test_super_admin_accesses_any(self):
        from app.routers.dashboard import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "SUPER_ADMIN"}
        _check_tenant_access(user, "t-999")  # Non deve lanciare

    def test_rsc_accesses_any(self):
        from app.routers.dashboard import _check_tenant_access
        user = {"tenant_id": "t-001", "ruolo": "RSC_CONSERVATORE"}
        _check_tenant_access(user, "t-999")  # Non deve lanciare
