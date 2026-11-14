# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b4 — identità di canale (contratto C2), chiave API e quote per piano (VD-C-099).

Su PostgreSQL vero (tests/infra_reale.py). Si monta una piccola app con la
dipendenza reale del gateway (``get_current_user``) e un Redis finto in memoria
con la semantica dei comandi usati. R04 (VD-C-105): la quota di PdV al giorno si
prova nel caso d'uso (``test_i3_quote_ente.py``, ``test_r03_quote_canali.py``):
``app.auth.quote`` non esiste più.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.auth import api_key, canali
from app.auth.canali import canale_effettivo
from app.auth.dependencies import get_current_user, get_redis, require_scope
from app.auth.jwt_handler import chiavi_jwt, create_access_token
from app.config import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from libs.shared.errori import ErroreApi, installa_gestori_errori
from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.quote import svuota_cache_piani
from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.token_canale import ServizioCanale, firma_token_canale
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.redis_finto import RedisQuote

PEPPER = b"pepper-di-prova-b4-0123456789abcdef0123456789"

_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIV_SFTP = _PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
PUB_SFTP = _PRIV.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _app(redis: RedisQuote | None) -> FastAPI:
    prova = FastAPI()

    @prova.get("/chi")
    async def chi(user: dict = Depends(get_current_user)) -> dict:  # noqa: B008
        return {k: user.get(k) for k in ("tenant_id", "user_id", "ruolo", "ruolo_utente",
                                         "scopes", "canale", "piano", "auth_method")}

    @prova.post("/versa")
    async def versa(user: dict = Depends(require_scope("versamento:ud"))) -> dict:  # noqa: B008
        return {"ok": True}

    prova.dependency_overrides[get_redis] = lambda: redis
    installa_gestori_errori(prova)
    return prova


async def _chiama(prova: FastAPI, metodo: str, percorso: str, intestazioni: dict) -> object:
    async with AsyncClient(transport=ASGITransport(app=prova), base_url="http://gw") as c:
        return await c.request(metodo, percorso, headers=intestazioni)


@pytest.fixture(autouse=True)
def _pulizia(monkeypatch):
    monkeypatch.setattr(api_key, "_PEPPER", PEPPER)
    monkeypatch.setattr(canali, "SERVIZI_CANALE", [
        ServizioCanale("sftp-ingest", frozenset({"SFTP"}), PUB_SFTP)])
    svuota_cache_piani()
    svuota_cache_revoche()
    yield
    svuota_cache_piani()
    svuota_cache_revoche()


async def _ente(url: str, *, piano: str = "starter", ruolo: str = "OPERATORE",
                stato: str = "ATTIVO", account_sftp: str | None = "ATTIVO",
                ) -> tuple[uuid.UUID, uuid.UUID]:
    """Ente con un utente; R03 f1 (REV-04): per default l'utente è il responsabile di
    un account SFTP ATTIVO dell'ente (``account_sftp=None``: nessun account)."""
    tid, uid = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, piano, email_referente) VALUES ($1, $2, 'Ente', $3, "
                   "CAST($4 AS public.tenant_stato), $5, 'e@example.org')",
              tid, tid.hex[:16], f"t_{tid.hex[:20]}", stato, piano)
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, 'u@example.org', 'Ugo', 'Rossi', "
                   "CAST($3 AS public.user_ruolo))", uid, tid, ruolo)
    if account_sftp is not None:
        await _account_sftp(url, tid, uid, account_sftp)
    return tid, uid


async def _account_sftp(url: str, tid, uid, stato: str = "ATTIVO") -> None:
    await sql(url, "INSERT INTO public.sftp_account (tenant_id, nome_utente, responsabile_id, "
                   "stato) VALUES ($1, $2, $3, $4)", tid, f"acc-{uuid.uuid4().hex[:12]}", uid,
              stato)


def _token_sftp(tid, uid, ruolo="OPERATORE", **kw) -> str:
    base = {"tenant_id": str(tid), "user_id": str(uid), "ruolo": ruolo, "canale": "SFTP",
                "chiave_privata_pem": PRIV_SFTP, "emittente": settings.jwt_issuer}
    base.update(kw)
    return firma_token_canale("sftp-ingest", **base)


# ── Contratto C2: token di canale ─────────────────────────────────────

@richiede_pg
async def test_token_canale_identita_con_canale_e_piano_dal_db(db_url):
    tid, uid = await _ente(db_url, piano="business")
    r = await _chiama(_app(None), "GET", "/chi",
                      {"Authorization": f"Bearer {_token_sftp(tid, uid)}"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["tenant_id"] == str(tid) and corpo["user_id"] == str(uid)
    assert corpo["canale"] == "SFTP" and corpo["piano"] == "business"
    assert corpo["ruolo"] == "API_SYSTEM" and corpo["ruolo_utente"] == "OPERATORE"
    assert corpo["auth_method"] == "token_canale"
    assert set(corpo["scopes"]) <= {"versamento:ud", "versamento:doc", "read"}
    assert "versamento:ud" in corpo["scopes"]


@richiede_pg
async def test_token_canale_utente_viewer_non_puo_versare(db_url):
    tid, uid = await _ente(db_url, ruolo="VIEWER")
    token = _token_sftp(tid, uid, ruolo="VIEWER")
    r = await _chiama(_app(None), "POST", "/versa", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["codice"] == "AUTH-011"


@richiede_pg
@pytest.mark.parametrize("caso", ["disattivato", "altro_ente", "ruolo_cambiato",
                                  "ente_sospeso", "utente_inesistente", "ente_senza_account",
                                  "account_sospeso", "non_responsabile",
                                  "account_di_altro_ente"])
async def test_token_canale_utente_non_valido_rifiutato(db_url, caso):
    """REV-04 (R03 f1): oltre a utente ed ente, serve un account SFTP ATTIVO dell'ente
    di cui l'utente sia il responsabile."""
    tid, uid = await _ente(db_url, account_sftp=None if caso in (
        "ente_senza_account", "non_responsabile", "account_di_altro_ente") else "ATTIVO")
    altro_tid, altro_uid = await _ente(db_url)
    token = _token_sftp(tid, uid)
    if caso == "disattivato":
        await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", uid)
    elif caso == "altro_ente":
        token = _token_sftp(altro_tid, uid)
    elif caso == "ruolo_cambiato":
        await sql(db_url, "UPDATE public.users SET ruolo = 'VIEWER' WHERE id = $1", uid)
    elif caso == "ente_sospeso":
        await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", tid)
    elif caso == "utente_inesistente":
        token = _token_sftp(tid, uuid.uuid4())
    elif caso == "account_sospeso":
        await sql(db_url, "UPDATE public.sftp_account SET stato = 'SOSPESO' "
                          "WHERE responsabile_id = $1", uid)
    elif caso == "non_responsabile":
        # l'ente ha un account attivo, ma il responsabile è un altro utente
        secondo = uuid.uuid4()
        await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, "
                          "ruolo) VALUES ($1, $2, 'v@example.org', 'Vera', 'Bianchi', "
                          "'OPERATORE')", secondo, tid)
        await _account_sftp(db_url, tid, secondo)
    elif caso == "account_di_altro_ente":
        # l'utente risulta responsabile solo di un account di un ALTRO ente
        await _account_sftp(db_url, altro_tid, uid)
    # «ente_senza_account»: nessun account SFTP (prima il token era accettato)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-003", r.text


@richiede_pg
async def test_token_canale_firma_di_servizio_non_ammesso_rifiutato(db_url, monkeypatch):
    tid, uid = await _ente(db_url)
    monkeypatch.setattr(canali, "SERVIZI_CANALE", [])  # nessun servizio ammesso
    r = await _chiama(_app(None), "GET", "/chi",
                      {"Authorization": f"Bearer {_token_sftp(tid, uid)}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"


@richiede_pg
async def test_token_canale_scaduto_auth_002(db_url):
    tid, uid = await _ente(db_url)
    token = _token_sftp(
        tid, uid, durata_secondi=60, adesso=int(datetime.now(UTC).timestamp()) - 600)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-002"


@richiede_pg
async def test_token_canale_firmato_dal_gateway_senza_ammissione_rifiutato(db_url):
    """Token con la chiave del gateway ma servizio ammesso con chiave dedicata: rifiutato."""
    tid, uid = await _ente(db_url)
    token = _token_sftp(tid, uid, chiave_privata_pem=chiavi_jwt().privata_pem)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_canale_effettivo_token_canale_solo_rest_o_suo_canale():
    sftp = {"auth_method": "token_canale", "canale": "SFTP"}
    assert canale_effettivo(sftp, "REST") == "SFTP"
    assert canale_effettivo(sftp, "SFTP") == "SFTP"
    for altro in ("PDND", "CICLO_VITA", "PORTALE"):
        with pytest.raises(ErroreApi) as errore:
            canale_effettivo(sftp, altro)
        assert errore.value.codice == "AUTH-010"
    utente = {"auth_method": "jwt", "canale": "REST"}
    assert canale_effettivo(utente, "PDND") == "PDND"
    assert canale_effettivo(utente, "REST") == "REST"


def test_canali_sviluppo_usano_chiave_gateway_produzione_esclude():
    imp = canali.ImpostazioniCanali(servizi_ammessi={"sftp-ingest": ["SFTP", "PDND"]})
    sviluppo = canali.carica_servizi(imp, "test")
    assert [s.nome for s in sviluppo] == ["sftp-ingest"]
    assert sviluppo[0].canali == frozenset({"SFTP"})  # PDND non ha token di servizio
    assert sviluppo[0].chiave_pubblica_pem == chiavi_jwt().pubblica_pem
    assert canali.carica_servizi(imp, "production") == []
    dedicata = canali.ImpostazioniCanali(chiavi_pubbliche={"sftp-ingest": PUB_SFTP})
    assert canali.carica_servizi(dedicata, "production")[0].chiave_pubblica_pem == PUB_SFTP.strip()


# ── Chiave API: piano dal database, quote ─────────────────────────────

async def _chiave(url: str, tid, uid) -> str:
    generata = genera_chiave_api(PEPPER)
    await sql(url, "INSERT INTO public.api_keys (id, tenant_id, user_id, nome, key_hash, "
                   "key_prefix, permessi) VALUES ($1, $2, $3, 'Protocollo', $4, $5, "
                   "CAST($6 AS jsonb))",
              uuid.uuid4(), tid, uid, generata.impronta, generata.identificativo,
              '["versamento:ud", "read", "admin:tenant"]')
    return generata.chiave


@richiede_pg
async def test_chiave_api_piano_enterprise_dal_db(db_url):
    """VD-C-099: prima i sistemi con chiave API avevano sempre il piano starter."""
    tid, uid = await _ente(db_url, piano="enterprise")
    chiave = await _chiave(db_url, tid, uid)
    r = await _chiama(_app(None), "GET", "/chi", {"X-Api-Key": chiave})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["piano"] == "enterprise" and corpo["canale"] == "REST"
    assert corpo["auth_method"] == "api_key"
    assert sorted(corpo["scopes"]) == ["read", "versamento:ud"]  # mai oltre API_SYSTEM


@richiede_pg
async def test_jwt_piano_del_token_ignorato(db_url):
    tid, uid = await _ente(db_url, piano="starter")
    token = create_access_token(tenant_id=str(tid), user_id=str(uid), ruolo="OPERATORE",
                                scopes=["read"], piano="enterprise")
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["piano"] == "starter"


@richiede_pg
async def test_rate_limit_per_piano_letto_dal_db(db_url, monkeypatch):
    from app.auth import rate_limiter

    monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "richieste_minuto",
                        {"starter": 2, "business": 4})
    redis = RedisQuote()
    tid, uid = await _ente(db_url, piano="business")
    chiave = await _chiave(db_url, tid, uid)
    prova = _app(redis)
    stati = [(await _chiama(prova, "GET", "/chi", {"X-Api-Key": chiave})).status_code
             for _ in range(5)]
    assert stati == [200, 200, 200, 200, 429]
    r = await _chiama(prova, "GET", "/chi", {"X-Api-Key": chiave})
    assert r.json()["codice"] == "QTA-001" and int(r.headers["Retry-After"]) >= 1


@richiede_pg
async def test_chiave_api_utente_responsabile_disattivato_401(db_url):
    tid, uid = await _ente(db_url)
    chiave = await _chiave(db_url, tid, uid)
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", uid)
    r = await _chiama(_app(None), "GET", "/chi", {"X-Api-Key": chiave})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"


async def test_senza_credenziali_auth_000():
    r = await _chiama(_app(None), "GET", "/chi", {})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-000"


# ── Allineamento con i token emessi da sftp-ingest (R03 b2, i1) ──
# R03 i1: sftp-ingest firma con ``firma_token_canale`` (aud «verumdoc-canale»,
# tipo_token «canale», come ``_token_sftp``); il vecchio formato di b2 (aud
# «verumdoc-api», il pubblico dei token utente) non è più accettato.

def _token_formato_b2(tid, uid, *, chiave: str, ruolo: str = "OPERATORE") -> str:
    """VECCHIO formato di sftp-ingest (fino a R03 b2): aud verumdoc-api, scopes
    nel token, nessun tipo_token."""
    from libs.shared.security.jwt_rs256 import firma_token

    return firma_token(
        {"sub": "servizio:sftp-ingest", "servizio": "sftp-ingest", "tenant_id": str(tid),
         "user_id": str(uid), "canale": "SFTP", "ruolo": ruolo,
         "scopes": ["versamento:ud", "read"]},
        chiave_privata_pem=chiave, emittente=settings.jwt_issuer,
        pubblico=settings.jwt_audience, durata_secondi=300)


@richiede_pg
async def test_token_formato_sftp_ingest_con_chiave_dedicata_accettato(db_url):
    tid, uid = await _ente(db_url)
    token = _token_sftp(tid, uid)  # formato di sftp-ingest da R03 i1
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["canale"] == "SFTP" and r.json()["auth_method"] == "token_canale"
    assert r.json()["ruolo"] == "API_SYSTEM"


@richiede_pg
async def test_token_canale_con_pubblico_dei_token_utente_rifiutato(db_url):
    """R03 i1: un token di canale con aud «verumdoc-api» non vale più, nemmeno
    firmato con la chiave dedicata del servizio."""
    tid, uid = await _ente(db_url)
    token = _token_formato_b2(tid, uid, chiave=PRIV_SFTP)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"


@richiede_pg
async def test_token_di_servizio_con_chiave_gateway_non_e_un_utente(db_url):
    """Firmato con la chiave del gateway e aud verumdoc-api passerebbe la verifica
    utente: si tratta comunque come token di canale e, senza ammissione di
    quella chiave (esercizio), si rifiuta."""
    tid, uid = await _ente(db_url)
    token = _token_formato_b2(tid, uid, chiave=chiavi_jwt().privata_pem)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"


@richiede_pg
async def test_token_di_servizio_con_chiave_gateway_in_sviluppo(db_url, monkeypatch):
    """In sviluppo/test il ripiego sulla chiave del gateway è ammesso, ma con le
    regole del canale (utente stretto, ruolo API_SYSTEM)."""
    monkeypatch.setattr(canali, "SERVIZI_CANALE",
                        canali.carica_servizi(canali.ImpostazioniCanali(), "test"))
    tid, uid = await _ente(db_url)
    token = _token_sftp(tid, uid, chiave_privata_pem=chiavi_jwt().privata_pem)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["auth_method"] == "token_canale"
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", uid)
    r = await _chiama(_app(None), "GET", "/chi", {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"


def test_chiave_sftp_da_variabile_dedicata():
    servizi = canali.carica_servizi(canali.ImpostazioniCanali(), "production", PUB_SFTP)
    assert [(s.nome, s.chiave_pubblica_pem) for s in servizi] == [("sftp-ingest", PUB_SFTP.strip())]
