# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/tests/test_deepreview.py
Code review finale 20/03/2026 — MED-005: classi GET devono richiedere auth.
"""
from __future__ import annotations

import inspect

import pytest


class TestMED005ClassiAuth:
    def test_lista_classi_has_authorization(self):
        from app.routers.classi import lista_classi
        sig = inspect.signature(lista_classi)
        assert "authorization" in sig.parameters

    def test_get_classe_has_authorization(self):
        from app.routers.classi import get_classe
        sig = inspect.signature(get_classe)
        assert "authorization" in sig.parameters

    def test_check_admin_rifiuta_token_non_firmato(self):
        """_check_admin verifica la firma: un token SUPER_ADMIN costruito a mano
        (base64, senza firma) è rifiutato con 401 (R03: prima si cercava una
        stringa nel sorgente)."""
        import base64
        import json

        import pytest
        from app.routers.classi import _check_admin
        from fastapi import HTTPException

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        falso = f"{b64({'alg': 'none'})}.{b64({'ruolo': 'SUPER_ADMIN', 'tenant_id': 'x'})}.x"
        with pytest.raises(HTTPException) as exc:
            _check_admin(f"Bearer {falso}")
        assert exc.value.status_code == 401

    def test_check_auth_rejects_none(self):
        from app.routers.classi import _check_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _check_auth(None)
        assert exc.value.status_code == 401

    def test_check_auth_accepts_valid(self):
        from app.auth.jwt_handler import create_access_token
        from app.routers.classi import _check_auth
        token = create_access_token(
            tenant_id="t-001", user_id="u-001",
            ruolo="VIEWER", scopes=["read"],
        )
        user = _check_auth(f"Bearer {token}")
        assert user["ruolo"] == "VIEWER"
