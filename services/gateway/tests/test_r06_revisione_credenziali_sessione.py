# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R06 — V1 (credenziali duplicate) e V4 (privilegi e lavoro sul DB di
``sessioni_attive``).

V1. Il middleware della sessione leggeva l'ULTIMA intestazione ``Authorization``
(dizionario), le dipendenze delle rotte la PRIMA: un token del portale scaduto
per inattività (o chiuso con «Esci») seguito da un secondo Bearer qualsiasi
passava, nel gateway e in ogni servizio protetto dal solo middleware. Ora
credenziali ambigue (intestazione ripetuta, più cookie di sessione diversi,
cookie e Bearer diversi) → 400 ``AUTH-041`` prima di ogni altro controllo.

V4. Il rinnovo (UPDATE) avveniva per qualunque JWT con ``ses=portale``, anche
non firmato; tutti i login di ``verumdoc_applicazione`` (compreso il server
SFTP) potevano cambiare limite e scadenza di ogni sessione. Ora: firma e
scadenza verificate prima di toccare il database, rinnovo al più ogni 30 s,
diritti solo al gruppo ``verumdoc_sessioni_portale`` con UPDATE della sola
``ultima_attivita`` (mai nel futuro).
"""
from __future__ import annotations

import time
import uuid

import asyncpg
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from libs.shared.db.ruolo_prova import url_con_utente
from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.sessione_cookie import COOKIE_CSRF, COOKIE_SESSIONE
from tests.infra_reale import _asyncpg_dsn, _con_database, db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_revisione_uscita import _altro_servizio
from tests.test_r05_sessione_cookie import _senza_rate_limit  # noqa: F401
from tests.test_r06_sessione_inattivita import ORIGINE, _accedi, _invecchia

pytestmark = richiede_pg

FALSO = "Bearer x.e30.x"


def _ambigua(r) -> None:
    assert r.status_code == 400, r.text
    assert r.json()["codice"] == "AUTH-041"


def _doppia(token: str) -> list[tuple[str, str]]:
    return [("Authorization", f"Bearer {token}"), ("Authorization", FALSO)]


# ── V1: credenziali duplicate ──────────────────────────────────────────

async def test_v1_doppia_authorization_dopo_l_inattivita(db_url, redis_finto,  # noqa: F811
                                                       login_sviluppo,
                                                       _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "SUPER_ADMIN")
        token = c.cookies.get(COOKIE_SESSIONE)
        await _invecchia(db_url, 120)  # oltre il limite di 15 minuti
        c.cookies.clear()
        r = await c.get("/api/v1/auth/sessione", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-004"
        _ambigua(await c.get("/api/v1/auth/sessione", headers=_doppia(token)))
        # anche nell'ordine inverso (il falso per primo)
        _ambigua(await c.get("/api/v1/auth/sessione", headers=list(reversed(_doppia(token)))))
    async with AsyncClient(transport=ASGITransport(app=_altro_servizio()),
                           base_url=ORIGINE) as a:
        _ambigua(await a.get("/api/v1/altro/eco", headers=_doppia(token)))


async def test_v1_doppia_authorization_dopo_esci(db_url, redis_finto, login_sviluppo,  # noqa: F811
                                               _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "ADMIN_TENANT")
        token = c.cookies.get(COOKIE_SESSIONE)
        csrf = c.cookies.get(COOKIE_CSRF)
        r = await c.delete("/api/v1/auth/sessione",
                           headers={"Origin": ORIGINE, "X-CSRF-Token": csrf})
        assert r.status_code == 204
        svuota_cache_revoche()
        c.cookies.clear()
        _ambigua(await c.get("/api/v1/auth/sessione", headers=_doppia(token)))
    async with AsyncClient(transport=ASGITransport(app=_altro_servizio()),
                           base_url=ORIGINE) as a:
        r = await a.get("/api/v1/altro/eco", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        _ambigua(await a.get("/api/v1/altro/eco", headers=_doppia(token)))


async def test_v1_cookie_e_bearer_diversi_e_cookie_ripetuti(db_url, redis_finto,  # noqa: F811
                                                          login_sviluppo,
                                                          _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "ADMIN_TENANT")
        token = c.cookies.get(COOKIE_SESSIONE)
        # cookie e Bearer dello STESSO token: ammesso
        r = await c.get("/api/v1/auth/sessione", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        # cookie della sessione e un Bearer diverso: ambiguo
        _ambigua(await c.get("/api/v1/auth/sessione", headers={"Authorization": FALSO}))
        c.cookies.clear()
        # due cookie di sessione diversi in due intestazioni Cookie
        _ambigua(await c.get("/api/v1/auth/sessione", headers=[
            ("Cookie", f"{COOKIE_SESSIONE}={token}"), ("Cookie", f"{COOKIE_SESSIONE}=altro")]))
        # due chiavi API
        _ambigua(await c.get("/api/v1/auth/sessione", headers=[
            ("X-Api-Key", "vd_a_b"), ("X-Api-Key", "vd_c_d")]))


# ── V4: firma prima del database, rinnovo al più ogni 30 s ─────────────

def _token_falso_portale() -> str:
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    ora = int(time.time())
    return jwt.encode({"ses": "portale", "ruolo": "SUPER_ADMIN", "tenant_id": str(uuid.uuid4()),
                       "iat": ora, "exp": ora + 600, "iss": "x", "aud": "x"}, pem,
                      algorithm="RS256")


async def test_v4_token_falso_nessun_lavoro_sul_database(db_url, redis_finto,  # noqa: F811
                                                        login_sviluppo, monkeypatch,
                                                        _senza_rate_limit):  # noqa: F811
    from app.main import app

    from libs.shared.security import inattivita, revoca

    chiamate: list[str] = []
    originale = inattivita.controlla_sessione
    originale_revoca = revoca.sessione_revocata

    async def spia(token: str, *, rinnova: bool = True) -> str:
        chiamate.append("inattivita")
        return await originale(token, rinnova=rinnova)

    async def spia_revoca(**argomenti):
        chiamate.append("revoca")
        return await originale_revoca(**argomenti)

    monkeypatch.setattr(inattivita, "controlla_sessione", spia)
    monkeypatch.setattr(revoca, "sessione_revocata", spia_revoca)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        r = await c.get("/api/v1/auth/sessione",
                        headers={"Authorization": f"Bearer {_token_falso_portale()}"})
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"
        assert chiamate == []
        await _accedi(c, db_url, "ADMIN_TENANT")
        assert (await c.get("/api/v1/auth/sessione")).status_code == 200
        assert "inattivita" in chiamate


async def test_v4_rinnovo_al_piu_ogni_30_secondi(db_url, redis_finto, login_sviluppo,  # noqa: F811
                                                _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "ADMIN_TENANT")
        await sql(db_url, "UPDATE public.sessioni_attive SET ultima_attivita = now() - "
                          "interval '10 seconds'")
        assert (await c.get("/api/v1/auth/sessione")).status_code == 200
        ferma = await sql(db_url, "SELECT now() - ultima_attivita > interval '9 seconds' AS ok "
                                  "FROM public.sessioni_attive")
        assert ferma[0]["ok"] is True  # nessuna scrittura entro 30 s
        await sql(db_url, "UPDATE public.sessioni_attive SET ultima_attivita = now() - "
                          "interval '40 seconds'")
        assert (await c.get("/api/v1/auth/sessione")).status_code == 200
        rinnovata = await sql(db_url, "SELECT now() - ultima_attivita < interval '5 seconds' "
                                      "AS ok FROM public.sessioni_attive")
        assert rinnovata[0]["ok"] is True


async def test_v4_privilegi_su_sessioni_attive(db_url, redis_finto, login_sviluppo,  # noqa: F811
                                              _senza_rate_limit):  # noqa: F811
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        await _accedi(c, db_url, "ADMIN_TENANT")
    # login come quello del server SFTP: solo verumdoc_applicazione → nessun diritto
    ruolo = f"vd_sftp_{uuid.uuid4().hex[:8]}"
    fuori = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
    try:
        await fuori.execute(f"CREATE ROLE {ruolo} LOGIN IN ROLE verumdoc_applicazione")
        sftp = await asyncpg.connect(_asyncpg_dsn(url_con_utente(db_url, ruolo)))
        try:
            for istruzione in ("SELECT 1 FROM public.sessioni_attive",
                               "UPDATE public.sessioni_attive SET ultima_attivita = now()",
                               "DELETE FROM public.sessioni_attive"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await sftp.execute(istruzione)
        finally:
            await sftp.close()
        # login dei servizi del portale: solo ultima_attivita, mai nel futuro
        await fuori.execute(f"GRANT verumdoc_sessioni_portale TO {ruolo}")
        portale = await asyncpg.connect(_asyncpg_dsn(url_con_utente(db_url, ruolo)))
        try:
            for istruzione in ("UPDATE public.sessioni_attive SET inattivita_secondi = 86400",
                               "UPDATE public.sessioni_attive SET scade_il = now() + "
                               "interval '30 days'"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await portale.execute(istruzione)
            await portale.execute("UPDATE public.sessioni_attive SET ultima_attivita = now() + "
                                  "interval '1 day'")
        finally:
            await portale.close()
        righe = await sql(db_url, "SELECT ultima_attivita <= now() AS ok FROM "
                                  "public.sessioni_attive")
        assert righe and all(r["ok"] for r in righe)
        with pytest.raises(asyncpg.CheckViolationError):
            await sql(db_url, "UPDATE public.sessioni_attive SET inattivita_secondi = 86400")
    finally:
        await fuori.execute(f"DROP OWNED BY {ruolo}")
        await fuori.execute(f"DROP ROLE IF EXISTS {ruolo}")
        await fuori.close()
