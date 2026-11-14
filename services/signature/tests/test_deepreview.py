# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/tests/test_deepreview.py
Code review finale 20/03/2026 — MED-004: firma+verifica devono richiedere auth.
"""

from __future__ import annotations

import inspect

import pytest


class TestMED004SignatureAuth:
    def test_firma_has_authorization(self):
        from app.routers.signature import firma

        sig = inspect.signature(firma)
        assert "authorization" in sig.parameters

    def test_verifica_has_authorization(self):
        from app.routers.signature import verifica

        sig = inspect.signature(verifica)
        assert "authorization" in sig.parameters

    def test_sig_auth_rejects_none(self):
        from app.routers.signature import _sig_auth
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _sig_auth(None)
        assert exc.value.status_code == 401

    def test_sig_auth_accepts_valid(self):
        from app.auth.jwt_handler import create_access_token
        from app.routers.signature import _sig_auth

        token = create_access_token(
            tenant_id="t-001",
            user_id="u-001",
            ruolo="OPERATORE",
            scopes=["versamento:ud"],
        )
        user = _sig_auth(f"Bearer {token}")
        assert user["ruolo"] == "OPERATORE"
