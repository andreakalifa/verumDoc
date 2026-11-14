# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-001/VD-C-004 (R02, g2-auth) — distribution verifica i token del gateway con il modulo
condiviso libs/shared/security/jwt_rs256.py: firma RS256, iss, aud, exp, claim
tenant_id/ruolo; nessuna chiave effimera fuori da ENV=test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import jwt
import pytest
from app.auth.jwt_handler import chiavi_jwt, create_access_token, verify_access_token
from app.config import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

SERVIZIO = Path(__file__).resolve().parents[1]
RADICE = SERVIZIO.parents[1]


def _claims(**extra: object) -> dict[str, object]:
    ora = int(time.time())
    c: dict[str, object] = {
        "tenant_id": "00000000-0000-0000-0000-000000000001", "user_id": "u",
        "ruolo": "ADMIN_TENANT", "scopes": ["read"], "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience, "iat": ora, "exp": ora + 600,
    }
    c.update(extra)
    return {k: v for k, v in c.items() if v is not None}


def _firma(claims: dict[str, object], chiave: str | None = None) -> str:
    return jwt.encode(claims, chiave or chiavi_jwt().privata_pem, algorithm="RS256")


class TestVerifica:
    def test_token_come_quelli_del_gateway_accettato(self) -> None:
        payload = verify_access_token(create_access_token(
            tenant_id="t-1", user_id="u", ruolo="OPERATORE", scopes=["read"]))
        assert payload["aud"] == settings.jwt_audience and payload["iss"] == settings.jwt_issuer

    @pytest.mark.parametrize("modifica", [
        {"aud": None}, {"aud": "altro-pubblico"}, {"iss": None}, {"iss": "altro.it"},
        {"exp": int(time.time()) - 3600}, {"tenant_id": ""}, {"ruolo": None},
    ])
    def test_claim_mancanti_o_sbagliati_401(self, modifica: dict[str, object]) -> None:
        with pytest.raises(HTTPException) as exc:
            verify_access_token(_firma(_claims(**modifica)))
        assert exc.value.status_code == 401

    def test_firma_di_altra_chiave_401(self) -> None:
        altra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = altra.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
        with pytest.raises(HTTPException) as exc:
            verify_access_token(_firma(_claims(), pem))
        assert exc.value.status_code == 401

    def test_token_non_firmato_401(self) -> None:
        with pytest.raises(HTTPException):
            verify_access_token(jwt.encode(_claims(), key=None, algorithm="none"))


def _avvia(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items()
            if k.upper() not in {"ENV", "JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY"}}
    base["PYTHONPATH"] = os.pathsep.join([str(SERVIZIO), str(RADICE)])
    base.update(env)
    return subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [sys.executable, "-c", "import app.auth.jwt_handler"], cwd=SERVIZIO, env=base,
        capture_output=True, text=True, timeout=120,
    )


class TestAvvio:
    @pytest.mark.parametrize("env", ["development", "staging", "production"])
    def test_senza_chiave_pubblica_non_parte(self, env: str) -> None:
        esito = _avvia({"ENV": env})
        assert esito.returncode != 0
        assert "JWT_PUBLIC_KEY mancante" in esito.stderr

    def test_con_chiave_pubblica_da_ambiente_parte(self) -> None:
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = k.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        esito = _avvia({"ENV": "production", "JWT_PUBLIC_KEY": pub.replace("\n", "\\n")})
        assert esito.returncode == 0, esito.stderr[-2000:]
