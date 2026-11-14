# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test F1.1 — Autenticazione JWT e API Key.
Riferimento piano: F1.1.2
"""
from __future__ import annotations

import pytest
from app.auth.jwt_handler import create_access_token, verify_access_token
from fastapi import HTTPException


class TestJwtHandler:
    """Test creazione e validazione JWT RS256."""

    def test_create_token_restituisce_stringa(self):
        token = create_access_token(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            ruolo="OPERATORE",
        )
        assert isinstance(token, str)
        assert len(token) > 50  # JWT ha sempre una lunghezza significativa

    def test_verify_token_valido(self):
        token = create_access_token(
            tenant_id="aaa-bbb-ccc",
            user_id="ddd-eee-fff",
            ruolo="ADMIN_TENANT",
            scopes=["versamento:ud"],
        )
        payload = verify_access_token(token)
        assert payload["tenant_id"] == "aaa-bbb-ccc"
        assert payload["user_id"] == "ddd-eee-fff"
        assert payload["ruolo"] == "ADMIN_TENANT"
        assert "versamento:ud" in payload["scopes"]

    def test_verify_token_scaduto_lancia_401(self, expired_token):
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token(expired_token)
        assert exc_info.value.status_code == 401
        # Token scaduto = AUTH-002 (B-R01); AUTH-001 resta per i token non validi
        assert exc_info.value.detail["type"] == "AUTH-002"

    def test_verify_token_invalido_lancia_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token("questo.non.e.un.jwt")
        assert exc_info.value.status_code == 401

    def test_payload_contiene_iss_e_exp(self):
        import jwt as pyjwt
        from app.auth.jwt_handler import chiavi_jwt
        from app.config import settings

        token = create_access_token("t1", "u1", "VIEWER")
        payload = pyjwt.decode(
            token, chiavi_jwt().pubblica_pem, algorithms=["RS256"],
            audience=settings.jwt_audience,
        )
        assert payload["iss"] == "verumdoc.it"
        assert payload["aud"] == settings.jwt_audience
        assert payload["jti"]
        assert "exp" in payload
        assert "iat" in payload

    def test_scopes_vuoti_se_non_forniti(self):
        token = create_access_token("t1", "u1", "VIEWER")
        payload = verify_access_token(token)
        assert payload["scopes"] == []

    def test_extra_fields_nel_payload(self):
        token = create_access_token(
            "t1", "u1", "API_SYSTEM",
            extra={"api_key_id": "key-123"}
        )
        payload = verify_access_token(token)
        assert payload["api_key_id"] == "key-123"
