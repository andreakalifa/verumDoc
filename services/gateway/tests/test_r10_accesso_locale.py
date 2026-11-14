# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 (D08) — accesso locale e attivazione da invito, sul PostgreSQL vero.

  - invito: collegamento monouso, legato all'e-mail, scadenza; e-mail sbagliata
    contata (al limite l'invito è revocato) senza rivelare nulla;
  - password + codice a tempo: segreto cifrato, codici di recupero monouso,
    codice già usato rifiutato, blocco dopo troppi tentativi (con audit),
    stesso errore per e-mail sconosciuta, password errata e account bloccato;
  - passkey (autenticatore di prova): registrazione dall'invito, accesso,
    contatore che torna indietro rifiutato, origine diversa rifiutata;
  - metodi: l'ente sceglie fra quelli del conservatore (verso «più sicuro»);
  - credenziali leggibili solo dal gruppo del gateway;
  - ruolo personalizzato: token con permessi del ruolo, revocato al cambio.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

from libs.shared.parametri import svuota_cache
from libs.shared.security.revoca import svuota_cache_revoche
from tests.autenticatore_prova import Autenticatore
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_sessione_cookie import _senza_rate_limit  # noqa: F401

pytestmark = richiede_pg

ORIGINE = "http://localhost:5173"
EMAIL = "persona.prova@example.org"
PASSWORD = "una frase lunga di prova 42"


@pytest.fixture
def accesso_locale(monkeypatch: pytest.MonkeyPatch):
    from app.auth.fornitori import svuota_cache_fornitori
    from app.config import settings

    monkeypatch.setattr(settings, "credenziali_chiave_cifratura",
                        type(settings.credenziali_chiave_cifratura)(
                            base64.b64encode(secrets.token_bytes(32)).decode()))
    monkeypatch.setattr(settings, "base_url_console", ORIGINE)
    monkeypatch.setattr(settings, "env", "test")
    svuota_cache()
    svuota_cache_fornitori()
    svuota_cache_revoche()
    yield
    svuota_cache_fornitori()


async def _ente(url: str, metodi: list[str], *, metodi_ente: list[str] | None = None) -> uuid.UUID:
    ente = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, piano, email_referente) VALUES ($1, $2, 'Ente di prova', $3, 'ATTIVO', "
                   "'business', 'e@example.org')", ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ('CONSERVATORE', NULL, 'ACCESSO_METODI_AMMESSI', "
                   "CAST($1 AS jsonb), 'prova R10') ON CONFLICT ON CONSTRAINT "
                   "uq_impostazioni_valori_livello DO UPDATE SET valore = EXCLUDED.valore",
              json.dumps(metodi))
    if metodi_ente is not None:
        await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                       "valore, motivo) VALUES ('ENTE', $1, 'ACCESSO_METODI_AMMESSI', "
                       "CAST($2 AS jsonb), 'prova R10')", ente, json.dumps(metodi_ente))
    for tipo in ("PASSWORD_TOTP", "PASSKEY"):
        await sql(url, "INSERT INTO public.fornitori_identita (tipo, nome, attivo) VALUES "
                       "($1, $1, true) ON CONFLICT (tipo) DO NOTHING", tipo)
    svuota_cache()
    from app.auth.fornitori import svuota_cache_fornitori
    svuota_cache_fornitori()
    return ente


async def _invito(url: str, ente: uuid.UUID, *, email: str = EMAIL, ore: int = 72,
                  ruolo: str = "OPERATORE") -> tuple[uuid.UUID, str]:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, $3, 'Persona', 'Di Prova', $4, false)", uid, ente, email,
              ruolo)
    token = f"{ente.hex}.{secrets.token_urlsafe(32)}"
    await sql(url, "INSERT INTO public.inviti (tenant_id, user_id, email, impronta, scade_il) "
                   "VALUES ($1, $2, $3, $4, $5)", ente, uid, email,
              hashlib.sha256(token.encode()).hexdigest(),
              datetime.now(UTC) + timedelta(hours=ore))
    return uid, token


async def _attiva_password(c: AsyncClient, token: str) -> tuple[str, list[str]]:
    r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
    assert r.status_code == 200, r.text
    passaggio = r.json()["passaggio"]
    r = await c.post("/api/v1/auth/inviti/totp", json={"passaggio": passaggio})
    segreto = r.json()["segreto"]
    r = await c.post("/api/v1/auth/inviti/completa", json={
        "passaggio": passaggio, "metodo": "PASSWORD_TOTP", "password": PASSWORD,
        "codice": pyotp.TOTP(segreto).now()})
    assert r.status_code == 200, r.text
    assert r.json()["esito"] == "attivato_e_accesso"
    return segreto, r.json()["codici_recupero"]


def _corpo(r) -> dict:
    """Corpo dell'errore senza l'id della richiesta (che cambia sempre)."""
    return {k: v for k, v in r.json().items() if k not in ("id_richiesta", "instance")}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE)


async def test_invito_password_totp_e_accesso(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["SPID_CIE", "PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        # e-mail diversa: stesso errore di un collegamento inesistente, ma contata
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token,
                                                               "email": "altro@example.org"})
        assert r.status_code == 404 and r.json()["codice"] == "ACC-404-INVITO"
        r2 = await c.post("/api/v1/auth/inviti/verifica", json={
            "token": f"{ente.hex}.{secrets.token_urlsafe(32)}", "email": EMAIL})
        assert r2.status_code == 404 and _corpo(r2) == _corpo(r)
        assert (await sql(db_url, "SELECT tentativi_errati FROM public.inviti"))[0][0] == 1
        # password troppo corta: rifiutata prima di scrivere
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
        assert r.json()["metodi"] == ["PASSWORD_TOTP"]  # senza codice fiscale niente SPID
        passaggio = r.json()["passaggio"]
        segreto = (await c.post("/api/v1/auth/inviti/totp",
                                json={"passaggio": passaggio})).json()["segreto"]
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSWORD_TOTP", "password": "corta",
            "codice": pyotp.TOTP(segreto).now()})
        assert r.status_code == 422 and r.json()["codice"] == "ACC-422-PASSWORD"
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSWORD_TOTP", "password": PASSWORD,
            "codice": "000000" if pyotp.TOTP(segreto).now() != "000000" else "111111"})
        assert r.json()["codice"] == "ACC-422-CODICE"
        assert (await sql(db_url, "SELECT attivo FROM public.users WHERE id = $1", uid))[0][0] \
            is False
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSWORD_TOTP", "password": PASSWORD,
            "codice": pyotp.TOTP(segreto).now()})
        assert r.status_code == 200, r.text
        codici = r.json()["codici_recupero"]
        assert len(codici) == 10 and r.json()["esito"] == "attivato_e_accesso"
        assert (await c.get("/api/v1/auth/sessione")).json()["utente"]["user_id"] == str(uid)
        # invito consumato: il collegamento non vale più (monouso)
        c.cookies.clear()
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
        assert r.status_code == 404
    riga = (await sql(db_url, "SELECT stato, metodo_attivato FROM public.inviti"))[0]
    assert (riga["stato"], riga["metodo_attivato"]) == ("USATO", "PASSWORD_TOTP")
    salvato = (await sql(db_url, "SELECT segreto_cifrato FROM public.credenziali_totp"))[0][0]
    assert salvato.startswith("aesgcm$") and segreto not in salvato
    assert (await sql(db_url, "SELECT count(*) FROM public.audit_global WHERE evento = "
                              "'invito.attivato'"))[0][0] == 1


async def test_invito_scaduto_o_revocato_non_attiva(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    _uid, token = await _invito(db_url, ente)
    async with _client() as c:
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
        passaggio = r.json()["passaggio"]
        segreto = (await c.post("/api/v1/auth/inviti/totp",
                                json={"passaggio": passaggio})).json()["segreto"]
        # l'amministratore revoca l'invito durante l'attivazione: uscita pulita
        await sql(db_url, "UPDATE public.inviti SET stato = 'REVOCATO'")
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSWORD_TOTP", "password": PASSWORD,
            "codice": pyotp.TOTP(segreto).now()})
        assert r.status_code == 404
    assert (await sql(db_url, "SELECT count(*) FROM public.credenziali_password"))[0][0] == 0
    assert (await sql(db_url, "SELECT bool_or(attivo) FROM public.users "
                              "WHERE tenant_id = $1", ente))[0][0] is False
    # scaduto
    _uid2, token2 = await _invito(db_url, ente, email="due@example.org", ore=-1)
    async with _client() as c:
        r = await c.post("/api/v1/auth/inviti/verifica",
                         json={"token": token2, "email": "due@example.org"})
        assert r.status_code == 404


async def test_invito_troppi_tentativi_revoca(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    _uid, token = await _invito(db_url, ente)
    async with _client() as c:
        for _ in range(5):
            await c.post("/api/v1/auth/inviti/verifica", json={"token": token,
                                                               "email": "x@example.org"})
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
        assert r.status_code == 404
    assert (await sql(db_url, "SELECT stato FROM public.inviti"))[0][0] == "REVOCATO"


async def test_accesso_password_secondo_fattore_e_blocco(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, codici = await _attiva_password(c, token)
        c.cookies.clear()
        # e-mail sconosciuta e password errata: stesso errore
        r1 = await c.post("/api/v1/auth/locale/accesso",
                          json={"email": "nessuno@example.org", "password": PASSWORD})
        r2 = await c.post("/api/v1/auth/locale/accesso",
                          json={"email": EMAIL, "password": "sbagliata ma lunga 123"})
        assert r1.status_code == r2.status_code == 401
        assert _corpo(r1) == _corpo(r2)
        # codice di recupero: vale una volta sola
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                              "password": PASSWORD})
        assert r.json()["esito"] == "secondo_fattore"
        r = await c.post("/api/v1/auth/locale/secondo-fattore", json={
            "passaggio": r.json()["passaggio"], "codice_recupero": codici[0].lower()})
        assert r.status_code == 200, r.text
        assert r.json()["utente"]["user_id"] == str(uid)
        c.cookies.clear()
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                              "password": PASSWORD})
        r = await c.post("/api/v1/auth/locale/secondo-fattore", json={
            "passaggio": r.json()["passaggio"], "codice_recupero": codici[0]})
        assert r.status_code == 401
        # passaggio monouso e legato al browser
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                              "password": PASSWORD})
        passaggio = r.json()["passaggio"]
        c.cookies.clear()
        r = await c.post("/api/v1/auth/locale/secondo-fattore", json={
            "passaggio": passaggio, "codice": pyotp.TOTP(segreto).now()})
        assert r.json()["codice"] == "ACC-400-PASSAGGIO"
        # troppi tentativi: blocco, anche con la password giusta; audit
        for _ in range(4):  # più quello errato sopra = 5
            await c.post("/api/v1/auth/locale/accesso",
                         json={"email": EMAIL, "password": "sbagliata ma lunga 123"})
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                              "password": PASSWORD})
        assert r.status_code == 401 and _corpo(r) == _corpo(r1)
    assert (await sql(db_url, "SELECT count(*) FROM public.audit_global WHERE evento = "
                              "'accesso.bloccato'"))[0][0] == 1
    await sql(db_url, "UPDATE public.credenziali_password SET bloccato_fino_a = now() - "
                      "interval '1 second'")
    async with _client() as c:
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                              "password": PASSWORD})
        assert r.status_code == 200


async def test_codice_a_tempo_non_vale_due_volte(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    _uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _codici = await _attiva_password(c, token)
        # aspetta un passo nuovo: il codice dell'attivazione è già «usato»
        while int(time.time()) % 30 > 25:
            time.sleep(1)
        attuale = pyotp.TOTP(segreto).now()
        await sql(db_url, "UPDATE public.credenziali_totp SET ultimo_passo = 0")
        for atteso in (200, 401):
            c.cookies.clear()
            r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                                  "password": PASSWORD})
            r = await c.post("/api/v1/auth/locale/secondo-fattore", json={
                "passaggio": r.json()["passaggio"], "codice": attuale})
            assert r.status_code == atteso, r.text


async def test_metodo_non_ammesso_dall_ente(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    # il conservatore abilita la password, l'ente sceglie solo SPID/CIE
    ente = await _ente(db_url, ["SPID_CIE", "PASSWORD_TOTP"])
    _uid, token = await _invito(db_url, ente)
    async with _client() as c:
        await _attiva_password(c, token)
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('ENTE', $1, 'ACCESSO_METODI_AMMESSI', "
                          "'[\"SPID_CIE\"]'::jsonb, 'prova')", ente)
        svuota_cache()
        c.cookies.clear()
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                              "password": PASSWORD})
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-METODO"
    # l'ente non può allargare oltre il conservatore: PASSKEY ignorata
    from app.auth.fornitori import metodi_ammessi_ente, svuota_cache_fornitori
    await sql(db_url, "INSERT INTO public.fornitori_identita (tipo, nome, attivo) VALUES "
                      "('SPID_CIE', 'Aggregatore di prova', true)")
    svuota_cache_fornitori()
    await sql(db_url, "UPDATE public.impostazioni_valori SET valore = '[\"PASSKEY\", "
                      "\"SPID_CIE\"]'::jsonb WHERE livello = 'ENTE'")
    svuota_cache()
    assert await metodi_ammessi_ente(ente) == ("SPID_CIE",)


async def test_passkey_dall_invito_e_accesso(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSKEY"])
    uid, token = await _invito(db_url, ente)
    autenticatore = Autenticatore(rp_id="localhost", origine=ORIGINE)
    async with _client() as c:
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
        passaggio = r.json()["passaggio"]
        opzioni = (await c.post("/api/v1/auth/inviti/passkey/opzioni",
                                json={"passaggio": passaggio})).json()["opzioni"]
        # registrazione da un'altra origine: rifiutata
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSKEY",
            "credenziale": autenticatore.registra(opzioni, origine="https://altro.example")})
        assert r.json()["codice"] == "ACC-400-PASSKEY"
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSKEY",
            "credenziale": autenticatore.registra(opzioni)})
        assert r.status_code == 200, r.text
        c.cookies.clear()
        r = await c.post("/api/v1/auth/passkey/opzioni")
        corpo = r.json()
        r = await c.post("/api/v1/auth/passkey/accesso", json={
            "passaggio": corpo["passaggio"], "credenziale": autenticatore.firma(corpo["opzioni"])})
        assert r.status_code == 200, r.text
        assert r.json()["utente"]["user_id"] == str(uid)
        # contatore che torna indietro (copia della passkey): rifiutata
        c.cookies.clear()
        corpo = (await c.post("/api/v1/auth/passkey/opzioni")).json()
        r = await c.post("/api/v1/auth/passkey/accesso", json={
            "passaggio": corpo["passaggio"],
            "credenziale": autenticatore.firma(corpo["opzioni"], contatore=1)})
        assert r.json()["codice"] == "ACC-400-PASSKEY"
        # firma su una sfida diversa: rifiutata
        corpo = (await c.post("/api/v1/auth/passkey/opzioni")).json()
        altra = dict(corpo["opzioni"], challenge="QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE")
        r = await c.post("/api/v1/auth/passkey/accesso", json={
            "passaggio": corpo["passaggio"], "credenziale": autenticatore.firma(altra)})
        assert r.json()["codice"] == "ACC-400-PASSKEY"


async def test_credenziali_solo_al_gruppo_del_gateway(db_url):  # noqa: F811
    """Un login del solo ``verumdoc_applicazione`` (es. il server SFTP) non legge le
    credenziali locali."""
    import asyncpg

    from libs.shared.db.ruolo_prova import url_ruolo_servizio
    from tests.infra_reale import _asyncpg_dsn

    url = await url_ruolo_servizio(db_url, ruolo="vd_prova_r10_solo_app")
    await sql(db_url, "DO $$ BEGIN IF pg_has_role('vd_prova_r10_solo_app', "
                      "'verumdoc_credenziali_accesso', 'MEMBER') THEN REVOKE "
                      "verumdoc_credenziali_accesso FROM vd_prova_r10_solo_app; END IF; END $$")
    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        for tabella in ("credenziali_password", "credenziali_totp", "codici_recupero",
                        "credenziali_passkey"):
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT 1 FROM public.{tabella} LIMIT 1")  # noqa: S608
    finally:
        await conn.close()


async def test_ruolo_personalizzato_nel_token_e_revoca(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente, ruolo="VIEWER")
    ruolo = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.ruoli_personalizzati (id, tenant_id, nome, ruolo_base, "
                      "permessi) VALUES ($1, $2, 'Lettore che versa', 'VIEWER', "
                      "ARRAY['archivio:consulta', 'versamento:esegui', 'esibizione:richiedi'])",
              ruolo, ente)
    # il conservatore non concede le esibizioni a questo ente: escluse dal token
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                      "valore, motivo) VALUES ('ENTE', $1, 'RUOLI_PERMESSI_CONCEDIBILI', "
                      "'[\"versamento:esegui\"]'::jsonb, 'prova')", ente)
    await sql(db_url, "UPDATE public.users SET ruolo_personalizzato_id = $1 WHERE id = $2",
              ruolo, uid)
    svuota_cache()
    async with _client() as c:
        await _attiva_password(c, token)
        r = await c.get("/api/v1/auth/sessione")
        assert r.status_code == 200
        assert sorted(r.json()["utente"]["scopes"]) == ["read", "versamento:doc",
                                                        "versamento:ud"]
        # vincolo del database: un permesso mai delegabile non entra in un ruolo
        import asyncpg
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await sql(db_url, "UPDATE public.ruoli_personalizzati SET permessi = "
                              "ARRAY['scarto:esegui'] WHERE id = $1", ruolo)
        # cambio dei permessi (versione) → il token precedente non vale più
        await sql(db_url, "UPDATE public.users SET permessi_versione = permessi_versione + 1 "
                          "WHERE id = $1", uid)
        svuota_cache_revoche()
        r = await c.get("/api/v1/auth/sessione")
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"


# ── Revisione R10 (V1): limite per IP reale, solo fallimenti, nessun blocco globale ──

def _dietro_alb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il client di prova (127.0.0.1) fa da ALB fidato: l'IP reale è in X-Forwarded-For."""
    from app.config import settings

    monkeypatch.setattr(settings, "proxy_fidati", ["127.0.0.1/32"])
    monkeypatch.setattr(settings, "env", "production")  # nessuna scorciatoia di sviluppo


@pytest.fixture
def dietro_alb(monkeypatch: pytest.MonkeyPatch):
    _dietro_alb(monkeypatch)


async def test_v1_regressione_nessun_blocco_globale(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, dietro_alb):  # noqa: F811
    """Prova della revisione (test_rev_ip.py) come regressione: molte sfide anonime da
    IP diversi non bloccano un'altra persona; le richieste riuscite non contano."""
    from app.config import settings

    await _ente(db_url, ["PASSKEY", "PASSWORD_TOTP"])
    async with _client() as c:
        for i in range(settings.accesso_fallimenti_ip_15_minuti + 20):
            r = await c.post("/api/v1/auth/passkey/opzioni",
                             headers={"X-Forwarded-For": f"198.51.100.{i}"})
            assert r.status_code == 200, (i, r.text)
        r = await c.post("/api/v1/auth/passkey/opzioni",
                         headers={"X-Forwarded-For": "203.0.113.7"})
        assert r.status_code == 200
        r = await c.post("/api/v1/auth/locale/accesso", headers={"X-Forwarded-For": "203.0.113.8"},
                         json={"email": "chiunque@example.org", "password": "x"})
        assert r.status_code == 401 and r.json()["codice"] == "ACC-401"


async def test_v1_fallimenti_contati_per_ip_reale(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, dietro_alb, monkeypatch):  # noqa: F811
    from app.config import settings

    monkeypatch.setattr(settings, "accesso_fallimenti_ip_15_minuti", 5)
    await _ente(db_url, ["PASSWORD_TOTP"])
    corpo = {"email": "nessuno@example.org", "password": "sbagliata lunga 123"}
    async with _client() as c:
        # l'attaccante scrive voci a sinistra in X-Forwarded-For: contano solo gli hop
        # aggiunti dall'ALB (l'ultimo non fidato)
        for i in range(5):
            r = await c.post("/api/v1/auth/locale/accesso", json=corpo,
                             headers={"X-Forwarded-For": f"10.{i}.0.1, 192.0.2.66"})
            assert r.status_code == 401
        r = await c.post("/api/v1/auth/locale/accesso", json=corpo,
                         headers={"X-Forwarded-For": "8.8.8.8, 192.0.2.66"})
        assert r.status_code == 429 and r.json()["codice"] == "ACC-429"
        # un'altra persona non è bloccata
        r = await c.post("/api/v1/auth/locale/accesso", json=corpo,
                         headers={"X-Forwarded-For": "192.0.2.77"})
        assert r.status_code == 401


async def test_v1_intestazione_da_sorgente_non_fidata_ignorata(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, monkeypatch):  # noqa: F811
    from app.config import settings

    monkeypatch.setattr(settings, "proxy_fidati", ["10.9.0.0/16"])  # il client non è l'ALB
    monkeypatch.setattr(settings, "accesso_fallimenti_ip_15_minuti", 5)
    await _ente(db_url, ["PASSWORD_TOTP"])
    corpo = {"email": "nessuno@example.org", "password": "sbagliata lunga 123"}
    async with _client() as c:
        for i in range(5):  # X-Forwarded-For diversi non cambiano l'IP (127.0.0.1)
            await c.post("/api/v1/auth/locale/accesso", json=corpo,
                         headers={"X-Forwarded-For": f"192.0.2.{i}"})
        r = await c.post("/api/v1/auth/locale/accesso", json=corpo,
                         headers={"X-Forwarded-For": "192.0.2.200"})
        assert r.status_code == 429


def test_v1_senza_proxy_fidati_in_produzione_nessun_limite_ip(monkeypatch) -> None:
    from app.auth.accesso_locale import limite_ip_attivo
    from app.config import Settings, settings

    monkeypatch.setattr(settings, "proxy_fidati", [])
    monkeypatch.setattr(settings, "env", "production")
    assert limite_ip_attivo() is False
    with pytest.raises(ValueError):
        Settings(proxy_fidati=["0.0.0.0/0"])


def test_v1_ip_cliente() -> None:
    from libs.shared.security.ip_cliente import ip_cliente

    fidati = ["10.0.0.0/20"]
    assert ip_cliente("10.0.1.5", {"x-forwarded-for": "1.1.1.1, 203.0.113.9"}, fidati) \
        == "203.0.113.9"
    assert ip_cliente("10.0.1.5", {"x-forwarded-for": "203.0.113.9, 10.0.2.2"}, fidati) \
        == "203.0.113.9"
    assert ip_cliente("198.51.100.1", {"x-forwarded-for": "203.0.113.9"}, fidati) \
        == "198.51.100.1"
    assert ip_cliente("10.0.1.5", {"x-forwarded-for": "non-ip"}, fidati) == "10.0.1.5"
    assert ip_cliente("10.0.1.5", {}, []) == "10.0.1.5"


async def test_v1_riusciti_e_sfide_passkey_non_consumano_i_fallimenti(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, monkeypatch):  # noqa: F811
    """Dallo stesso IP reale: molte sfide passkey (contatore separato), molti primi
    passi riusciti e rifiuti che non sono tentativi sbagliati (metodo non ammesso)
    non portano al 429; i fallimenti sì."""
    from app.config import settings

    monkeypatch.setattr(settings, "accesso_fallimenti_ip_15_minuti", 5)
    ente = await _ente(db_url, ["PASSKEY", "PASSWORD_TOTP"])
    _uid, token = await _invito(db_url, ente)
    ip = {"X-Forwarded-For": "203.0.113.50"}
    async with _client() as c:
        await _attiva_password(c, token)   # prima: in produzione i cookie sono Secure
        c.cookies.clear()
        _dietro_alb(monkeypatch)
        for _ in range(8):
            assert (await c.post("/api/v1/auth/passkey/opzioni", headers=ip)).status_code == 200
            r = await c.post("/api/v1/auth/locale/accesso", headers=ip,
                             json={"email": EMAIL, "password": PASSWORD})
            assert r.status_code == 200 and r.json()["esito"] == "secondo_fattore", r.text
        # password giusta ma metodo non più ammesso dall'ente: non è un tentativo sbagliato
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('ENTE', $1, 'ACCESSO_METODI_AMMESSI', "
                          "'[\"PASSKEY\"]'::jsonb, 'prova R10')", ente)
        svuota_cache()
        for _ in range(8):
            r = await c.post("/api/v1/auth/locale/accesso", headers=ip,
                             json={"email": EMAIL, "password": PASSWORD})
            assert r.status_code == 403 and r.json()["codice"] == "ACC-403-METODO", r.text
        await sql(db_url, "DELETE FROM public.impostazioni_valori WHERE tenant_id = $1", ente)
        svuota_cache()
        for _ in range(5):
            r = await c.post("/api/v1/auth/locale/accesso", headers=ip,
                             json={"email": "nessuno@example.org", "password": PASSWORD})
            assert r.status_code == 401
        r = await c.post("/api/v1/auth/locale/accesso", headers=ip,
                         json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 429


async def test_u1_fallimenti_in_parallelo_non_aggirano_il_limite(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, dietro_alb, monkeypatch):  # noqa: F811
    """Prova del secondo giro di revisione (test_rev2_corsa.py) come regressione: il
    tentativo si riserva prima della rotta, quindi 60 richieste in parallelo dallo
    stesso IP reale con limite 5 danno al più 5 risposte 401, le altre 429; le
    riserve restituite (429) non restano nel contatore."""
    import asyncio

    from app.config import settings

    monkeypatch.setattr(settings, "accesso_fallimenti_ip_15_minuti", 5)
    await _ente(db_url, ["PASSWORD_TOTP"])
    async with _client() as c:
        async def uno(i: int) -> int:
            r = await c.post("/api/v1/auth/locale/accesso",
                             headers={"X-Forwarded-For": "192.0.2.66"},
                             json={"email": f"vittima{i}@example.org",
                                   "password": "Primavera2026!"})
            return r.status_code
        esiti = await asyncio.gather(*[uno(i) for i in range(60)])
        assert esiti.count(401) == 5 and esiti.count(429) == 55, esiti
        contatori = [v for k, v in redis_finto.dati.items() if ":ip:fallimenti:" in k]
        assert contatori == ["5"]
        # un altro IP reale non è toccato
        r = await c.post("/api/v1/auth/locale/accesso", headers={"X-Forwarded-For": "192.0.2.77"},
                         json={"email": "vittima@example.org", "password": "Primavera2026!"})
        assert r.status_code == 401
