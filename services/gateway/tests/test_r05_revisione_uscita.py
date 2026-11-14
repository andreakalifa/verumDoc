# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R12: «Esci» chiude la sessione LATO SERVER.

Prima della correzione ``DELETE /api/v1/auth/sessione`` cancellava solo i
cookie: un token sottratto (log di un proxy, browser condiviso) restava valido
fino a ``exp``. Ora l'uscita registra la sessione in ``sessioni_revocate``
(impronta del token e ``jti``) e il token è rifiutato con 401 AUTH-003:
  - dal gateway, come cookie e come Bearer;
  - da OGNI servizio che accetta il cookie (``MiddlewareSessioneCookie``, prima
    di qualunque rotta), anche in un altro processo (cache svuotata).
"""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest  # noqa: F401 — marcatori
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient

from libs.shared.errori import installa_gestori_errori
from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.sessione_cookie import (
    COOKIE_CSRF,
    COOKIE_SESSIONE,
    MiddlewareSessioneCookie,
)
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_sessione_cookie import CF, _senza_rate_limit  # noqa: F401

pytestmark = richiede_pg
ORIGINE = "http://localhost:5173"


async def _accedi(c: AsyncClient, db_url: str) -> None:  # noqa: F811
    ente, uid = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                      "schema_name, stato, piano, email_referente) VALUES ($1, $2, "
                      "'Ente di prova', $3, 'ATTIVO', 'business', 'e@example.org')",
              ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                      "attivo, codice_fiscale) VALUES ($1, $2, 'p@example.org', 'Persona', "
                      "'Di Prova', 'ADMIN_TENANT', true, $3)", uid, ente, CF)
    r = await c.get("/api/v1/auth/spid/login",
                    params={"provider": "spid", "redirect_uri": f"{ORIGINE}/login"})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "cf": CF})
    codice = parse_qs(urlparse(r.headers["location"]).fragment)["spid_codice"][0]
    assert (await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})).status_code == 200


def _altro_servizio() -> FastAPI:
    """Un servizio qualsiasi raggiunto dal portale: solo il middleware della
    sessione e una rotta che risponde a chi arriva (nessuna dipendenza di revoca)."""
    app = FastAPI()
    app.add_middleware(MiddlewareSessioneCookie)
    installa_gestori_errori(app)

    @app.get("/api/v1/altro/eco")
    async def eco(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return {"autenticato": bool(authorization)}

    return app


async def test_uscita_revoca_il_token_in_tutti_i_servizi(db_url, redis_finto,  # noqa: F811
                                                        login_sviluppo, _senza_rate_limit):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url)
        token = c.cookies.get(COOKIE_SESSIONE)
        csrf = c.cookies.get(COOKIE_CSRF)
        assert token and (await c.get("/api/v1/auth/sessione")).status_code == 200
        r = await c.delete("/api/v1/auth/sessione",
                           headers={"Origin": ORIGINE, "X-CSRF-Token": csrf})
        assert r.status_code == 204
    righe = await sql(db_url, "SELECT jti, scade_il > now() AS valida "
                              "FROM public.sessioni_revocate")
    assert len(righe) == 1 and righe[0]["jti"] and righe[0]["valida"]

    svuota_cache_revoche()  # come un altro processo: nessuna memoria dell'uscita
    # Il token sottratto non vale più: come cookie...
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as ladro:
        ladro.cookies.set(COOKIE_SESSIONE, token, path="/api")
        r = await ladro.get("/api/v1/auth/sessione")
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"
        # ...e come Bearer
        svuota_cache_revoche()
        r = await ladro.get("/api/v1/auth/sessione",
                            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"
    # ...né in un altro servizio che accetta il cookie
    svuota_cache_revoche()
    async with AsyncClient(transport=ASGITransport(app=_altro_servizio()),
                           base_url=ORIGINE) as ladro:
        ladro.cookies.set(COOKIE_SESSIONE, token, path="/api")
        r = await ladro.get("/api/v1/altro/eco")
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"


async def test_uscita_ripetuta_idempotente(db_url, redis_finto, login_sviluppo,  # noqa: F811
                                          _senza_rate_limit):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url)
        token, csrf = c.cookies.get(COOKIE_SESSIONE), c.cookies.get(COOKIE_CSRF)
        for _ in range(2):
            c.cookies.set(COOKIE_SESSIONE, token, path="/api")
            c.cookies.set(COOKIE_CSRF, csrf, path="/")
            r = await c.delete("/api/v1/auth/sessione",
                               headers={"Origin": ORIGINE, "X-CSRF-Token": csrf})
            assert r.status_code == 204
    assert len(await sql(db_url, "SELECT 1 FROM public.sessioni_revocate")) == 1


async def test_altri_token_dello_stesso_utente_restano_validi(db_url, redis_finto,  # noqa: F811
                                                              login_sviluppo,
                                                              _senza_rate_limit):
    """L'uscita chiude QUELLA sessione, non le altre dello stesso utente."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url)
        primo = c.cookies.get(COOKIE_SESSIONE)
        r = await c.get("/api/v1/auth/spid/login",
                        params={"provider": "spid", "redirect_uri": f"{ORIGINE}/login"})
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "cf": CF})
        codice = parse_qs(urlparse(r.headers["location"]).fragment)["spid_codice"][0]
        assert (await c.post("/api/v1/auth/spid/scambio",
                             json={"codice": codice})).status_code == 200
        secondo = c.cookies.get(COOKIE_SESSIONE)
        assert secondo != primo
        csrf = c.cookies.get(COOKIE_CSRF)
        # revisione R06 (V1): un solo cookie di sessione per richiesta
        c.cookies.clear()
        c.cookies.set(COOKIE_CSRF, csrf, path="/")
        c.cookies.set(COOKIE_SESSIONE, primo, path="/api")
        r = await c.delete("/api/v1/auth/sessione",
                           headers={"Origin": ORIGINE, "X-CSRF-Token": csrf})
        assert r.status_code == 204
        svuota_cache_revoche()
        c.cookies.clear()
        r = await c.get("/api/v1/auth/sessione", headers={"Authorization": f"Bearer {secondo}"})
    assert r.status_code == 200, r.text
