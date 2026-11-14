# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/tests/test_bugfix_review.py
Code review 20/03/2026: BUG-004 migration export richiede auth.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException


class TestBUG004MigrationAuth:
    """BUG-004: endpoint migration devono richiedere auth RSC/SUPER_ADMIN."""

    # R05: l'autenticazione è la dipendenza ``accesso_migrazione`` (persona
    # RSC/SUPER_ADMIN oppure token di servizio del ciclo di vita).
    def test_export_aip_has_authorization_param(self):
        from app.routers.migration import accesso_migrazione, export_aip
        sig = inspect.signature(export_aip)
        assert any(getattr(p.default, "dependency", None) is accesso_migrazione
                   for p in sig.parameters.values()), "export_aip deve richiedere l'accesso"

    def test_export_batch_has_authorization_param(self):
        from app.routers.migration import accesso_migrazione, export_batch
        sig = inspect.signature(export_batch)
        assert any(getattr(p.default, "dependency", None) is accesso_migrazione
                   for p in sig.parameters.values()), "export_batch deve richiedere l'accesso"

    def test_require_migration_auth_no_token(self):
        from app.routers.migration import _require_migration_auth
        with pytest.raises(HTTPException) as exc_info:
            _require_migration_auth(None)
        assert exc_info.value.status_code == 401

    def test_require_migration_auth_empty_bearer(self):
        from app.routers.migration import _require_migration_auth
        with pytest.raises(HTTPException) as exc_info:
            _require_migration_auth("Bearer ")
        assert exc_info.value.status_code == 401

    def test_require_migration_auth_blocks_operatore(self):
        from app.auth.jwt_handler import create_access_token
        from app.routers.migration import _require_migration_auth
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="OPERATORE", scopes=["read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            _require_migration_auth(f"Bearer {token}")
        assert exc_info.value.status_code == 403

    def test_require_migration_auth_allows_rsc(self):
        from app.auth.jwt_handler import create_access_token
        from app.routers.migration import _require_migration_auth
        token = create_access_token(
            tenant_id="t-001", user_id="u-rsc",
            ruolo="RSC_CONSERVATORE", scopes=["*"],
        )
        payload = _require_migration_auth(f"Bearer {token}")
        assert payload["ruolo"] == "RSC_CONSERVATORE"

    def test_require_migration_auth_allows_super_admin(self):
        from app.auth.jwt_handler import create_access_token
        from app.routers.migration import _require_migration_auth
        token = create_access_token(
            tenant_id="t-001", user_id="u-sa",
            ruolo="SUPER_ADMIN", scopes=["*"],
        )
        payload = _require_migration_auth(f"Bearer {token}")
        assert payload["ruolo"] == "SUPER_ADMIN"
