# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (agente r05-sicurezza) — VD-C-079: sessione del portale in cookie HttpOnly.

  - middleware ``libs/shared/security/sessione_cookie``: il cookie diventa
    ``Authorization`` solo dopo i controlli anti-CSRF (doppio invio del token e
    origine); credenziali esplicite (Bearer, chiave API) lasciano il cookie fuori;
  - flusso vero del gateway: scambio SPID/CIE (mock di sviluppo) → cookie
    ``vd_sessione`` HttpOnly; Secure; SameSite=Strict e ``vd_csrf`` → ``GET
    /api/v1/auth/sessione`` senza token nel corpo → uscita con ``DELETE``.
"""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient

from libs.shared.errori import installa_gestori_errori
from libs.shared.security.sessione_cookie import (
    COOKIE_CSRF,
    COOKIE_SESSIONE,
    MiddlewareSessioneCookie,
)
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

# ── Middleware su un'applicazione minima ───────────────────────────────


def _app_prova(origini: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MiddlewareSessioneCookie, origini_ammesse=origini)
    installa_gestori_errori(app)

    @app.api_route("/api/eco", methods=["GET", "POST", "DELETE"])
    async def eco(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return {"authorization": authorization}

    return app


def _client(app: FastAPI, base: str = "https://portale.example.it") -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url=base)
    c.cookies.set(COOKIE_SESSIONE, "TOKEN-DEL-COOKIE", domain="portale.example.it", path="/api")
    c.cookies.set(COOKIE_CSRF, "csrf-123", domain="portale.example.it", path="/")
    return c


async def test_get_con_il_cookie_diventa_bearer():
    async with _client(_app_prova()) as c:
        r = await c.get("/api/eco")
    assert r.status_code == 200
    assert r.json() == {"authorization": "Bearer TOKEN-DEL-COOKIE"}


@pytest.mark.parametrize(("intestazioni", "motivo"), [
    ({"Origin": "https://portale.example.it"}, "senza token anti-CSRF"),
    ({"Origin": "https://portale.example.it", "X-CSRF-Token": "altro"}, "token diverso"),
    ({"Origin": "https://attaccante.example", "X-CSRF-Token": "csrf-123"}, "altra origine"),
    ({"X-CSRF-Token": "csrf-123"}, "né Origin né Referer"),
    ({"Referer": "https://attaccante.example/pagina", "X-CSRF-Token": "csrf-123"},
     "Referer di un'altra origine"),
])
async def test_modifica_con_il_cookie_senza_protezioni_rifiutata(intestazioni, motivo):
    async with _client(_app_prova()) as c:
        r = await c.post("/api/eco", headers=intestazioni)
    assert r.status_code == 403, motivo
    assert r.json()["codice"] == "AUTH-040"
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_modifica_dalla_stessa_origine_con_token_ammessa():
    async with _client(_app_prova()) as c:
        r = await c.post("/api/eco", headers={"Origin": "https://portale.example.it",
                                              "X-CSRF-Token": "csrf-123"})
        assert r.json() == {"authorization": "Bearer TOKEN-DEL-COOKIE"}
        # dietro un ingress che termina TLS: schema dall'intestazione inoltrata
    async with _client(_app_prova(), base="http://portale.example.it") as c:
        r = await c.delete("/api/eco", headers={"Referer": "https://portale.example.it/x",
                                                "X-Forwarded-Proto": "https",
                                                "X-CSRF-Token": "csrf-123"})
    assert r.status_code == 200 and r.json()["authorization"] == "Bearer TOKEN-DEL-COOKIE"


async def test_origine_configurata_ammessa():
    async with _client(_app_prova(("https://console.example.it",))) as c:
        r = await c.post("/api/eco", headers={"Origin": "https://console.example.it",
                                              "X-CSRF-Token": "csrf-123"})
    assert r.status_code == 200


async def test_percorsi_dell_accesso_non_usano_la_sessione():
    """Un nuovo accesso SPID con una sessione vecchia nel browser non è rifiutato e
    non riceve il token vecchio."""
    app = _app_prova()

    @app.post("/api/v1/auth/spid/scambio")
    async def scambio(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return {"authorization": authorization}

    async with _client(app) as c:
        r = await c.post("/api/v1/auth/spid/scambio")
    assert r.status_code == 200 and r.json() == {"authorization": None}


@pytest.mark.parametrize("credenziale", [
    {"Authorization": "Bearer TOKEN-DEL-COOKIE"},
    {"X-Api-Key": "vd_x_y"},
    {"X-Api-Key-Id": "x", "X-Api-Key-Secret": "y"},
])
async def test_credenziali_esplicite_ignorano_il_cookie(credenziale):
    """Sistemi versanti e SDK: il cookie non entra e il CSRF non si applica."""
    async with _client(_app_prova()) as c:
        r = await c.post("/api/eco", headers=credenziale)
    assert r.status_code == 200
    atteso = credenziale.get("Authorization")
    assert r.json()["authorization"] == atteso


async def test_cookie_e_bearer_diversi_rifiutati():
    """Revisione R06 (V1): cookie della sessione e un Bearer DIVERSO sono
    credenziali ambigue (400 AUTH-041), come due intestazioni Authorization."""
    async with _client(_app_prova()) as c:
        r = await c.get("/api/eco", headers={"Authorization": "Bearer ESPLICITO"})
        assert r.status_code == 400 and r.json()["codice"] == "AUTH-041"
        c.cookies.clear()
        r = await c.get("/api/eco", headers=[("Authorization", "Bearer A"),
                                             ("Authorization", "Bearer B")])
        assert r.status_code == 400 and r.json()["codice"] == "AUTH-041"


# ── Flusso vero del gateway ─────────────────────────────────────────────

CF = "FTZPRV00A01Z999V"


@pytest.fixture
def _senza_rate_limit():
    from app.auth.dependencies import get_redis
    from app.main import app

    app.dependency_overrides[get_redis] = lambda: None
    yield
    app.dependency_overrides.pop(get_redis, None)


@richiede_pg
async def test_scambio_spid_consegna_il_cookie_e_la_sessione_si_legge_e_si_chiude(
        db_url, redis_finto, login_sviluppo, _senza_rate_limit):  # noqa: F811
    from app.main import app

    ente, uid = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                      "schema_name, stato, piano, email_referente) VALUES ($1, $2, "
                      "'Comune di Prova', $3, 'ATTIVO', 'business', 'e@example.org')",
              ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                      "attivo, codice_fiscale) VALUES ($1, $2, 'm@example.org', 'Mario', "
                      "'Rossi', 'ADMIN_TENANT', true, $3)", uid, ente, CF)
    origine = "http://localhost:5173"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=origine) as c:
        r = await c.get("/api/v1/auth/spid/login",
                        params={"provider": "spid", "redirect_uri": f"{origine}/login"})
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "cf": CF})
        codice = parse_qs(urlparse(r.headers["location"]).fragment)["spid_codice"][0]
        s = await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})
        assert s.status_code == 200, s.text
        assert "access_token" not in s.json() and s.json()["sessione"] == "cookie"
        biscotti = {v.split("=", 1)[0]: v.lower() for v in s.headers.get_list("set-cookie")}
        assert "httponly" in biscotti[COOKIE_SESSIONE]
        assert "samesite=strict" in biscotti[COOKIE_SESSIONE]
        assert "path=/api" in biscotti[COOKIE_SESSIONE]
        assert "httponly" not in biscotti[COOKIE_CSRF]
        assert "samesite=strict" in biscotti[COOKIE_CSRF]

        # sessione letta con il solo cookie (nessun Authorization dal client)
        r = await c.get("/api/v1/auth/sessione")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["autenticazione"] == "cookie" and "token" not in str(corpo).lower()
        assert corpo["utente"]["tenant_id"] == str(ente)
        assert corpo["utente"]["ruolo"] == "ADMIN_TENANT"
        assert corpo["utente"]["ragione_sociale"] == "Comune di Prova"
        assert corpo["utente"]["nome"] == "Mario"
        # revisione R17: permessi effettivi del ruolo fisso per la console
        assert "esibizione:richiedi" in corpo["utente"]["permessi"]
        assert "scarto:esegui" not in corpo["utente"]["permessi"]

        # uscita senza token anti-CSRF: rifiutata; con il token: cookie cancellati
        r = await c.delete("/api/v1/auth/sessione", headers={"Origin": origine})
        assert r.status_code == 403 and r.json()["codice"] == "AUTH-040"
        csrf = c.cookies.get(COOKIE_CSRF)
        r = await c.delete("/api/v1/auth/sessione",
                           headers={"Origin": origine, "X-CSRF-Token": csrf})
        assert r.status_code == 204
        assert c.cookies.get(COOKIE_SESSIONE) is None
        r = await c.get("/api/v1/auth/sessione")
        assert r.status_code == 401


async def test_cookie_secure_fuori_da_sviluppo_su_http(monkeypatch):
    """In esercizio (https) il cookie è sempre Secure; in sviluppo su http no."""
    from app.auth.sessione import cookie_sicuro
    from app.config import settings
    from starlette.requests import Request

    def richiesta(schema: str) -> Request:
        return Request({"type": "http", "scheme": schema, "method": "GET", "path": "/",
                        "headers": [], "server": ("h", 443)})

    monkeypatch.setattr(settings, "env", "production")
    assert cookie_sicuro(richiesta("http")) and cookie_sicuro(richiesta("https"))
    monkeypatch.setattr(settings, "env", "development")
    assert not cookie_sicuro(richiesta("http")) and cookie_sicuro(richiesta("https"))
