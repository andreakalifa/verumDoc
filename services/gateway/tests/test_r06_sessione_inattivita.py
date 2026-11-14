# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R06 — VD-C-127: la sessione del portale scade per inattività.

Prima della correzione la sessione (cookie ``vd_sessione``) valeva per tutta la
durata del token, senza scadenza per inattività né limite più breve per i ruoli
privilegiati, e l'uscita arrivava agli altri processi entro 30 s (cache). Ora:
  - all'accesso il gateway registra la sessione (``sessioni_attive``) con il
    limite del ruolo (R07, D14: 15 minuti per SUPER_ADMIN, RSC_CONSERVATORE e
    ADMIN_TENANT, 30 per gli altri, impostazione della console);
  - ogni richiesta, in OGNI servizio con ``MiddlewareSessioneCookie``, rinnova
    la sessione (a scorrimento) oppure la rifiuta con 401 ``AUTH-004`` se è
    rimasta inattiva oltre il limite; lo stesso token presentato come Bearer
    segue la stessa regola;
  - ``X-Sessione-Rinnovo: no`` controlla senza rinnovare;
  - «Esci» e la disattivazione dell'utente valgono subito in ogni servizio.
"""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.sessione_cookie import COOKIE_CSRF, COOKIE_SESSIONE
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_revisione_uscita import _altro_servizio
from tests.test_r05_sessione_cookie import CF, _senza_rate_limit  # noqa: F401

pytestmark = richiede_pg
ORIGINE = "http://localhost:5173"


async def _accedi(c: AsyncClient, db_url: str, ruolo: str) -> str:  # noqa: F811
    ente, uid = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                      "schema_name, stato, piano, email_referente) VALUES ($1, $2, "
                      "'Ente di prova', $3, 'ATTIVO', 'business', 'e@example.org')",
              ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                      "attivo, codice_fiscale) VALUES ($1, $2, 'p@example.org', 'Persona', "
                      "'Di Prova', $3, true, $4)", uid, ente, ruolo, CF)
    r = await c.get("/api/v1/auth/spid/login",
                    params={"provider": "spid", "redirect_uri": f"{ORIGINE}/login"})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "cf": CF})
    codice = parse_qs(urlparse(r.headers["location"]).fragment)["spid_codice"][0]
    assert (await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})).status_code == 200
    return str(uid)


async def _invecchia(db_url: str, minuti: int) -> None:  # noqa: F811
    await sql(db_url, "UPDATE public.sessioni_attive SET ultima_attivita = now() - "
                      "make_interval(mins => $1)", minuti)


def _scaduta(r: Any) -> None:
    assert r.status_code == 401, r.text
    assert r.json()["codice"] == "AUTH-004"


@pytest.mark.parametrize("ruolo,limite", [("OPERATORE", 30), ("ADMIN_TENANT", 15),
                                          ("SUPER_ADMIN", 15),
                                          ("RSC_CONSERVATORE", 15)])
async def test_inattivita_oltre_il_limite_del_ruolo(db_url, redis_finto, login_sviluppo,  # noqa: F811
                                                   _senza_rate_limit, ruolo, limite):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, ruolo)
        righe = await sql(db_url, "SELECT inattivita_secondi FROM public.sessioni_attive")
        assert [r["inattivita_secondi"] for r in righe] == [limite * 60]
        r = await c.get("/api/v1/auth/sessione")
        assert r.status_code == 200 and r.json()["inattivita_secondi"] == limite * 60
        # poco prima del limite: valida e rinnovata (a scorrimento)
        await _invecchia(db_url, limite - 1)
        assert (await c.get("/api/v1/auth/sessione")).status_code == 200
        rinnovata = await sql(db_url, "SELECT now() - ultima_attivita < interval '1 minute' "
                                      "AS ok FROM public.sessioni_attive")
        assert rinnovata[0]["ok"] is True
        # oltre il limite: rifiutata (cookie e stesso token come Bearer), anche dopo
        await _invecchia(db_url, limite + 1)
        token = c.cookies.get(COOKIE_SESSIONE)
        _scaduta(await c.get("/api/v1/auth/sessione"))
        c.cookies.clear()
        _scaduta(await c.get("/api/v1/auth/sessione",
                             headers={"Authorization": f"Bearer {token}"}))
    # e in ogni altro servizio che accetta il cookie
    async with AsyncClient(transport=ASGITransport(app=_altro_servizio()),
                           base_url=ORIGINE) as altro:
        altro.cookies.set(COOKIE_SESSIONE, token, path="/api")
        _scaduta(await altro.get("/api/v1/altro/eco"))


async def test_aggiornamento_automatico_non_rinnova(db_url, redis_finto, login_sviluppo,  # noqa: F811
                                                   _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "OPERATORE")
        await _invecchia(db_url, 20)
        r = await c.get("/api/v1/auth/sessione", headers={"X-Sessione-Rinnovo": "no"})
        assert r.status_code == 200
        ferma = await sql(db_url, "SELECT now() - ultima_attivita > interval '19 minutes' "
                                  "AS ok FROM public.sessioni_attive")
        assert ferma[0]["ok"] is True
        await _invecchia(db_url, 31)
        _scaduta(await c.get("/api/v1/auth/sessione", headers={"X-Sessione-Rinnovo": "no"}))


async def test_uscita_e_disattivazione_valgono_subito_ovunque(db_url, redis_finto,  # noqa: F811
                                                             login_sviluppo,
                                                             _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "ADMIN_TENANT")
        token = c.cookies.get(COOKIE_SESSIONE)
        csrf = c.cookies.get(COOKIE_CSRF)
        altro_app = _altro_servizio()
        async with AsyncClient(transport=ASGITransport(app=altro_app), base_url=ORIGINE) as a:
            a.cookies.set(COOKIE_SESSIONE, token, path="/api")
            # l'altro servizio ha già visto la sessione valida (cache della revoca piena)
            assert (await a.get("/api/v1/altro/eco")).status_code == 200
            r = await c.delete("/api/v1/auth/sessione",
                               headers={"Origin": ORIGINE, "X-CSRF-Token": csrf})
            assert r.status_code == 204
            assert await sql(db_url, "SELECT 1 FROM public.sessioni_attive") == []
            # nessuna attesa della cache: subito rifiutata
            r = await a.get("/api/v1/altro/eco")
            assert r.status_code == 401 and r.json()["codice"] in ("AUTH-003", "AUTH-004")
    # disattivazione dell'utente (onboarding): chiude le sue sessioni subito
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        svuota_cache_revoche()
        # (un solo utente attivo con lo stesso codice fiscale: niente scelta dell'ente)
        await sql(db_url, "UPDATE public.users SET attivo = false")
        uid = await _accedi(c, db_url, "OPERATORE")
        token = c.cookies.get(COOKIE_SESSIONE)
        assert token
        from libs.shared.db import get_async_session
        from libs.shared.security.inattivita import chiudi_sessioni_utente

        async with get_async_session() as session:
            await chiudi_sessioni_utente(session, uid)
        async with AsyncClient(transport=ASGITransport(app=_altro_servizio()),
                               base_url=ORIGINE) as a:
            a.cookies.set(COOKIE_SESSIONE, token, path="/api")
            _scaduta(await a.get("/api/v1/altro/eco"))


def test_limite_d_inattivita_per_ruolo() -> None:
    """R07 (D14): predefiniti del registro (ADMIN_TENANT passa a 15 minuti)."""
    from libs.shared.parametri import codice_inattivita_ruolo, risolvi
    from libs.shared.security.inattivita import payload_non_verificato

    def minuti(ruolo: str) -> int:
        codice = codice_inattivita_ruolo(ruolo)
        return risolvi({}, [codice])[codice]

    assert minuti("ADMIN_TENANT") == 15 and minuti("SUPER_ADMIN") == 15
    assert minuti("OPERATORE") == 30 and minuti("VIEWER") == 30
    assert minuti("RUOLO_NUOVO") == 15  # ruolo sconosciuto: il limite più breve
    assert payload_non_verificato("non.un-token") == {}
