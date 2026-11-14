# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-001 — onboarding: token verificato (RS256, iss, aud, exp) su utenti e API key;
ruolo e tenant dal token verificato; ADMIN_TENANT solo sul proprio tenant.
VD-C-004 — onboarding non parte senza JWT_PUBLIC_KEY fuori da ENV=test.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
import pytest
from app.auth.jwt_handler import create_access_token
from app.config import settings
from app.main import app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

SERVIZIO = Path(__file__).resolve().parents[1]
RADICE = SERVIZIO.parents[1]
TENANT_A = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT_B = "bbbbbbbb-0000-0000-0000-000000000002"
UID = "11111111-0000-0000-0000-000000000001"

ENDPOINT: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/onboarding/tenant/{t}/utenti", None),
    ("POST", "/api/v1/onboarding/tenant/{t}/utenti",
     {"email": "x@example.com", "nome": "Nome", "ruolo": "OPERATORE"}),
    ("PUT", f"/api/v1/onboarding/tenant/{{t}}/utenti/{UID}", {"nome": "Nuovo"}),
    ("DELETE", f"/api/v1/onboarding/tenant/{{t}}/utenti/{UID}", None),
    ("GET", "/api/v1/onboarding/tenant/{t}/api-keys", None),
    ("POST", "/api/v1/onboarding/tenant/{t}/api-keys", {"descrizione": "x"}),
    ("DELETE", f"/api/v1/onboarding/tenant/{{t}}/api-keys/{UID}", None),
]


def _b64(d: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _pem_privata() -> str:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


def _claims(**extra: Any) -> dict[str, Any]:
    ora = int(time.time())
    c = {"tenant_id": TENANT_B, "user_id": "u", "ruolo": "SUPER_ADMIN", "scopes": ["*"],
         "iss": settings.jwt_issuer, "aud": settings.jwt_audience, "iat": ora,
         "exp": ora + 600}
    c.update(extra)
    return c


def _token_forgiati() -> dict[str, str]:
    """Token che il vecchio _get_caller (decodifica base64) avrebbe accettato."""
    from app.auth.jwt_handler import chiavi_jwt

    altra = _pem_privata()
    propria = chiavi_jwt().privata_pem
    valido_a = create_access_token(tenant_id=TENANT_A, user_id="u", ruolo="ADMIN_TENANT")
    h, _, s = valido_a.split(".")
    return {
        "senza_firma": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims())}.",
        "firma_finta": f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(_claims())}.ZmFrZQ",
        "altra_chiave": jwt.encode(_claims(), altra, algorithm="RS256"),
        # firmato con la chiave giusta ma destinato a un altro servizio
        "aud_errato": jwt.encode(_claims(aud="altro-servizio"), propria, algorithm="RS256"),
        "payload_manomesso": f"{h}.{_b64(_claims(tenant_id=TENANT_B))}.{s}",
    }


@pytest.fixture
def db_vietato(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import utenti

    def _vietato() -> None:
        raise AssertionError("il database non deve essere toccato")

    monkeypatch.setattr(utenti, "get_async_session", _vietato)


async def _chiama(metodo: str, url: str, corpo: Any, token: str | None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(metodo, url, json=corpo, headers=headers)


class TestTokenNonVerificatoRifiutato:
    @pytest.mark.parametrize("metodo,url,corpo", ENDPOINT)
    @pytest.mark.parametrize("tipo", ["senza_firma", "firma_finta", "altra_chiave",
                                      "aud_errato", "payload_manomesso"])
    async def test_token_forgiato_401(self, metodo, url, corpo, tipo, db_vietato) -> None:
        tok = _token_forgiati()[tipo]
        r = await _chiama(metodo, url.format(t=TENANT_B), corpo, tok)
        assert r.status_code == 401, (tipo, r.text)

    @pytest.mark.parametrize("metodo,url,corpo", ENDPOINT)
    async def test_senza_token_401(self, metodo, url, corpo, db_vietato) -> None:
        r = await _chiama(metodo, url.format(t=TENANT_A), corpo, None)
        assert r.status_code == 401

    async def test_emittente_errato_401(self, db_vietato) -> None:
        from app.auth.jwt_handler import chiavi_jwt

        tok = jwt.encode(_claims(iss="https://altro-emittente"), chiavi_jwt().privata_pem,
                         algorithm="RS256")
        r = await _chiama("GET", f"/api/v1/onboarding/tenant/{TENANT_B}/utenti", None, tok)
        assert r.status_code == 401

    async def test_scaduto_401(self, db_vietato) -> None:
        from app.auth.jwt_handler import chiavi_jwt

        ora = int(time.time())
        tok = jwt.encode(_claims(iat=ora - 7200, exp=ora - 3600), chiavi_jwt().privata_pem,
                         algorithm="RS256")
        r = await _chiama("GET", f"/api/v1/onboarding/tenant/{TENANT_B}/utenti", None, tok)
        assert r.status_code == 401
        assert r.json()["codice"] == "AUTH-002"

    async def test_tenant_router_rifiuta_token_non_firmato(self) -> None:
        tok = f"{_b64({'alg': 'none'})}.{_b64(_claims())}."
        r = await _chiama("GET", "/api/v1/onboarding/tenant", None, tok)
        assert r.status_code == 401


class TestPerimetroTenant:
    @pytest.mark.parametrize("metodo,url,corpo", ENDPOINT)
    @pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "RSC_CONSERVATORE"])
    async def test_admin_di_a_su_tenant_b_404(self, metodo, url, corpo, ruolo,
                                              db_vietato) -> None:
        tok = create_access_token(tenant_id=TENANT_A, user_id="u", ruolo=ruolo)
        r = await _chiama(metodo, url.format(t=TENANT_B), corpo, tok)
        assert r.status_code == 404

    @pytest.mark.parametrize("metodo,url,corpo", ENDPOINT)
    @pytest.mark.parametrize("ruolo", ["VIEWER", "OPERATORE", "API_SYSTEM"])
    async def test_ruoli_non_gestori_403(self, metodo, url, corpo, ruolo, db_vietato) -> None:
        tok = create_access_token(tenant_id=TENANT_A, user_id="u", ruolo=ruolo)
        r = await _chiama(metodo, url.format(t=TENANT_A), corpo, tok)
        assert r.status_code == 403

    @richiede_pg
    async def test_admin_sul_proprio_tenant_usa_il_tenant_dell_url(self, db_url) -> None:
        """La lettura usa il tenant autorizzato (uguale a quello del token): su
        PostgreSQL reale l'ADMIN_TENANT di A vede solo gli utenti di A."""
        for tenant, email in ((TENANT_A, "a@example.org"), (TENANT_B, "b@example.org")):
            await sql(db_url,
                      "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                      "schema_name, stato, email_referente) VALUES ($1, $2, 'Ente', $3, "
                      "'ATTIVO', 'e@example.org')",
                      uuid.UUID(tenant), tenant[:8] + tenant[-8:], f"t_{tenant[:8]}")
            await sql(db_url,
                      "INSERT INTO public.users (tenant_id, email, nome, cognome) "
                      "VALUES ($1, $2, 'Nome', 'Cognome')", uuid.UUID(tenant), email)
        tok = create_access_token(tenant_id=TENANT_A, user_id="u", ruolo="ADMIN_TENANT")
        r = await _chiama("GET", f"/api/v1/onboarding/tenant/{TENANT_A}/utenti", None, tok)
        assert r.status_code == 200
        assert [u["email"] for u in r.json()["items"]] == ["a@example.org"]


def _avvia(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items()
            if k.upper() not in {"ENV", "JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY", "API_KEY_PEPPER"}}
    base["PYTHONPATH"] = os.pathsep.join([str(SERVIZIO), str(RADICE)])
    base.update(env)
    return subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [sys.executable, "-c", "import app.main"], cwd=SERVIZIO, env=base,
        capture_output=True, text=True, timeout=120,
    )


class TestAvvio:
    @pytest.mark.parametrize("env", ["development", "production"])
    def test_senza_chiave_pubblica_non_parte(self, env: str) -> None:
        esito = _avvia({"ENV": env})
        assert esito.returncode != 0
        assert "JWT_PUBLIC_KEY mancante" in esito.stderr

    def test_con_sola_chiave_pubblica_parte(self) -> None:
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = k.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        esito = _avvia({"ENV": "production", "JWT_PUBLIC_KEY": pub.replace("\n", "\\n"),
                        "API_KEY_PEPPER": "p" * 64})
        assert esito.returncode == 0, esito.stderr[-2000:]

    def test_produzione_senza_pepper_chiavi_api_non_parte(self) -> None:
        """R02 g2-auth: senza API_KEY_PEPPER le chiavi API non si possono generare
        in modo verificabile dal gateway: fuori da development/test non parte."""
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = k.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        esito = _avvia({"ENV": "production", "JWT_PUBLIC_KEY": pub.replace("\n", "\\n")})
        assert esito.returncode != 0
        assert "API_KEY_PEPPER mancante" in esito.stderr
