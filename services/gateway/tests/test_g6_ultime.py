# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Ultime correzioni della revisione di sicurezza (g6-ultime), su PostgreSQL reale
dove serve (tests/infra_reale.py).

M5  seed: senza ENV si assume produzione: nessun ente dimostrativo, nessun
    bucket di sviluppo.
B5/N2 revoca: eccezione dedicata per il DB non inizializzato (nessuna decisione
    sul testo dell'eccezione); token revocato se il ruolo attuale dell'utente è
    diverso da quello del token (declassamento) o se l'utente ha cambiato ente;
    stessa cache di 30 s. I token delle chiavi API (API_SYSTEM) non confrontano
    il ruolo del responsabile.
R3  PDND: chiave presente ma audience/emittente mancanti → solo le rotte PDND
    rispondono 503 PDND-503; la readiness del gateway resta pronta.
B3  SPID: id_token senza `acr` → SPID-009 con messaggio esplicito nel log.
"""
from __future__ import annotations

import time
import uuid

import jwt
import pytest
from app.auth.dependencies import get_current_user, get_redis
from app.auth.jwt_handler import create_access_token
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from libs.shared.errori import installa_gestori_errori
from libs.shared.security.revoca import svuota_cache_revoche, token_revocato
from tests.infra_reale import _asyncpg_dsn, db_url, richiede_pg, sql  # noqa: F401
from tests.test_vdc006_spid_id_token import (  # noqa: F401 — fixture e aggregatore finto
    CLIENT_ID,
    ISS,
    KID,
    _frammento,
    _login_e_callback,
    aggregatore,
)


@pytest.fixture(autouse=True)
def _cache_pulita():
    svuota_cache_revoche()
    yield
    svuota_cache_revoche()


# ── M5: seed con ENV assente ─────────────────────────────────────────

def test_demo_abilitato_senza_env_e_produzione(monkeypatch) -> None:
    from app.seed import demo_abilitato

    monkeypatch.delenv("ENV", raising=False)
    assert demo_abilitato() is False
    for valore in ("production", "staging", "", "Produzione"):
        monkeypatch.setenv("ENV", valore)
        assert demo_abilitato() is False
    for valore in ("development", "test", " Development "):
        monkeypatch.setenv("ENV", valore)
        assert demo_abilitato() is True


@richiede_pg
async def test_seed_senza_env_non_crea_ente_demo_ne_bucket(db_url, monkeypatch) -> None:
    from app import seed

    monkeypatch.delenv("ENV", raising=False)

    def _bucket_vietati(*_a, **_k):
        raise AssertionError("bucket di sviluppo creati senza ENV (produzione)")

    monkeypatch.setattr(seed, "_crea_bucket_sviluppo", _bucket_vietati)
    esito = await seed.run_seed(dsn=_asyncpg_dsn(db_url))
    assert esito["demo"] is False and esito["tenant_demo_creato"] is False
    righe = await sql(db_url, "SELECT 1 FROM public.tenants WHERE id = $1",
                      uuid.UUID(seed.DEMO_TENANT_ID))
    assert righe == []
    # le classi di base (dati di riferimento) ci sono comunque
    classi = await sql(db_url, "SELECT count(*) AS n FROM public.classi_documentali "
                               "WHERE tenant_id IS NULL")
    assert classi[0]["n"] == len(seed.CLASSI_BASE)


# ── B5/N2: revoca ────────────────────────────────────────────────────

def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/io")
    async def io(user: dict = Depends(get_current_user)) -> dict:
        return {"user_id": user["user_id"], "ruolo": user["ruolo"]}

    app.dependency_overrides[get_redis] = lambda: None
    installa_gestori_errori(app)  # VD-C-100: errori nel formato problem+json
    return app


async def _ente(url: str) -> uuid.UUID:
    tid = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', 'e@x.it')",
              tid, tid.hex[:11], f"t_{tid.hex[:20]}")
    return tid


async def _utente(url: str, tid: uuid.UUID, ruolo: str) -> uuid.UUID:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, $3, 'N', 'C', CAST($4 AS public.user_ruolo), true)",
              uid, tid, f"{uid.hex[:8]}@x.it", ruolo)
    return uid


async def _io(tid, uid, ruolo: str, **extra) -> tuple[int, dict]:
    tok = create_access_token({"tenant_id": str(tid), "user_id": str(uid), "ruolo": ruolo,
                               "scopes": [], **extra})
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/io", headers={"Authorization": f"Bearer {tok}"})
    return r.status_code, r.json()


@richiede_pg
class TestRevocaRuoloEdEnte:
    async def test_declassamento_revoca_il_token_entro_la_cache(self, db_url) -> None:
        tid = await _ente(db_url)
        uid = await _utente(db_url, tid, "ADMIN_TENANT")
        assert (await _io(tid, uid, "ADMIN_TENANT"))[0] == 200
        await sql(db_url, "UPDATE public.users SET ruolo = 'VIEWER' WHERE id = $1", uid)
        # entro i 30 s della cache la risposta resta quella memorizzata ...
        assert (await _io(tid, uid, "ADMIN_TENANT"))[0] == 200
        # ... scaduta la voce il vecchio token (ADMIN_TENANT) è rifiutato
        svuota_cache_revoche()
        stato, corpo = await _io(tid, uid, "ADMIN_TENANT")
        assert stato == 401 and corpo["codice"] == "AUTH-003"
        # un nuovo accesso (token col ruolo attuale) funziona
        assert (await _io(tid, uid, "VIEWER"))[0] == 200

    async def test_token_con_ruolo_superiore_a_quello_in_db_rifiutato(self, db_url,
                                                                      monkeypatch) -> None:
        monkeypatch.setenv("VD_REVOCA_CACHE_SECONDI", "0")
        tid = await _ente(db_url)
        uid = await _utente(db_url, tid, "OPERATORE")
        stato, corpo = await _io(tid, uid, "SUPER_ADMIN")
        assert stato == 401 and corpo["codice"] == "AUTH-003"

    async def test_utente_spostato_in_altro_ente_revocato(self, db_url, monkeypatch) -> None:
        monkeypatch.setenv("VD_REVOCA_CACHE_SECONDI", "0")
        a, b = await _ente(db_url), await _ente(db_url)
        uid = await _utente(db_url, a, "OPERATORE")
        assert (await _io(a, uid, "OPERATORE"))[0] == 200
        await sql(db_url, "UPDATE public.users SET tenant_id = $1 WHERE id = $2", b, uid)
        stato, corpo = await _io(a, uid, "OPERATORE")
        assert stato == 401 and corpo["codice"] == "AUTH-003"

    async def test_token_chiave_api_non_confronta_il_ruolo_del_responsabile(
            self, db_url, monkeypatch) -> None:
        """Token API_SYSTEM: user_id è il responsabile della chiave (OPERATORE)."""
        monkeypatch.setenv("VD_REVOCA_CACHE_SECONDI", "0")
        tid = await _ente(db_url)
        uid = await _utente(db_url, tid, "OPERATORE")
        assert (await _io(tid, uid, "API_SYSTEM", auth_method="api_key"))[0] == 200
        # ma il responsabile spostato in un altro ente revoca anche la chiave
        altro = await _ente(db_url)
        await sql(db_url, "UPDATE public.users SET tenant_id = $1 WHERE id = $2", altro, uid)
        assert (await _io(tid, uid, "API_SYSTEM", auth_method="api_key"))[0] == 401

    async def test_cache_distinta_per_ruolo(self, db_url) -> None:
        """La voce di cache del vecchio ruolo non vale per un token col ruolo nuovo."""
        tid = await _ente(db_url)
        uid = await _utente(db_url, tid, "OPERATORE")
        assert await token_revocato({"user_id": str(uid), "tenant_id": str(tid),
                                     "ruolo": "ADMIN_TENANT"}) is True
        assert await token_revocato({"user_id": str(uid), "tenant_id": str(tid),
                                     "ruolo": "OPERATORE"}) is False

    async def test_utente_assente_non_revoca(self, db_url) -> None:
        tid = await _ente(db_url)
        assert await token_revocato({"user_id": str(uuid.uuid4()), "tenant_id": str(tid),
                                     "ruolo": "ADMIN_TENANT"}) is False


async def test_db_non_inizializzato_eccezione_dedicata() -> None:
    """Senza init_db la revoca non si applica: lo decide il TIPO dell'eccezione."""
    from libs.shared.db import DBNonInizializzato, get_async_session

    with pytest.raises(DBNonInizializzato):
        async with get_async_session():
            pass
    assert issubclass(DBNonInizializzato, RuntimeError)
    assert await token_revocato({"user_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4()),
                                 "ruolo": "OPERATORE"}) is False


async def test_altre_runtime_error_non_concedono_accesso(monkeypatch) -> None:
    """Una RuntimeError qualsiasi, anche col vecchio testo, non disattiva più il
    controllo: si propaga (mai accesso concesso)."""
    import libs.shared.db as db

    def _rotta():
        raise RuntimeError("DB non inizializzato — ma in realtà è un altro guasto")

    monkeypatch.setattr(db, "get_async_session", _rotta)
    with pytest.raises(RuntimeError):
        await token_revocato({"user_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4()),
                              "ruolo": "OPERATORE"})


# ── R3: PDND non configurato → solo le rotte PDND in 503 ─────────────

@pytest.fixture
def pdnd_incompleto(monkeypatch):
    from tests.test_pdnd import PUB_PDND

    monkeypatch.setenv("PDND_PUBLIC_KEY", PUB_PDND)
    monkeypatch.setenv("PDND_AUDIENCE", "DA_IMPOSTARE:audience_eservice_pdnd")
    monkeypatch.delenv("PDND_ISSUER", raising=False)


@pytest.mark.parametrize("metodo,percorso", [
    ("POST", "/api/pdnd/v1/versamento"),
    ("GET", f"/api/pdnd/v1/aip/{uuid.uuid4()}"),
])
async def test_rotte_pdnd_503_se_non_configurato(pdnd_incompleto, metodo, percorso) -> None:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.request(metodo, percorso, headers={"Authorization": "Bearer x"},
                            json={} if metodo == "POST" else None)
    assert r.status_code == 503
    corpo = r.json()
    assert (corpo["codice"], corpo["title"], corpo["detail"]) == (
        "PDND-503", "PDND non configurato",
        "Le API PDND non sono disponibili: configurazione incompleta.")


async def test_rotte_pdnd_con_configurazione_completa_invariate(monkeypatch) -> None:
    """Configurazione completa: nessun 503, si arriva alla verifica del voucher (401)."""
    from app.main import app

    from tests.test_pdnd import AUDIENCE, ISSUER, PUB_PDND

    monkeypatch.setenv("PDND_PUBLIC_KEY", PUB_PDND)
    monkeypatch.setenv("PDND_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PDND_ISSUER", ISSUER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/pdnd/v1/aip/{uuid.uuid4()}",
                        headers={"Authorization": "Bearer non-un-voucher"})
    assert r.status_code == 401


@richiede_pg
async def test_readiness_pronta_anche_con_pdnd_non_configurato(db_url, pdnd_incompleto) -> None:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pronto = await c.get("/api/v1/health/ready")
        pdnd = await c.get(f"/api/pdnd/v1/aip/{uuid.uuid4()}")
    assert pronto.status_code == 200, pronto.text
    assert pronto.json()["status"] == "ready"
    assert pdnd.status_code == 503 and pdnd.json()["codice"] == "PDND-503"


# ── B3: id_token SPID senza acr ──────────────────────────────────────

async def test_id_token_senza_acr_spid_009_con_log_esplicito(aggregatore, monkeypatch) -> None:
    def _id_token_senza_acr() -> str:
        ora = int(time.time())
        claims = {"iss": ISS, "aud": CLIENT_ID, "sub": "persona-1", "iat": ora,
                  "exp": ora + 300, "nonce": aggregatore.nonce,
                  "fiscal_code": "TINIT-FTZPRV00A01Z999V"}
        assert "acr" not in claims
        return jwt.encode(claims, aggregatore.chiave, algorithm="RS256",
                          headers={"kid": KID})

    monkeypatch.setattr(aggregatore, "id_token", _id_token_senza_acr)
    with capture_logs() as log:
        r = await _login_e_callback(aggregatore)
    assert _frammento(r) == {"spid_errore": "SPID-009"}
    assert aggregatore.cf_cercati == []
    [voce] = [v for v in log if v["event"] == "spid_acr_assente"]
    assert voce["log_level"] == "error"
    assert ("acr assente nell'id_token: verificare la configurazione dell'aggregatore"
            in voce["messaggio"])


async def test_acr_presente_ma_basso_nessun_messaggio_di_acr_assente(aggregatore) -> None:
    aggregatore.claims_extra = {"acr": "https://www.spid.gov.it/SpidL1"}
    with capture_logs() as log:
        r = await _login_e_callback(aggregatore)
    assert _frammento(r) == {"spid_errore": "SPID-009"}
    assert not [v for v in log if v["event"] == "spid_acr_assente"]
    assert [v for v in log if v["event"] == "spid_livello_insufficiente"]
