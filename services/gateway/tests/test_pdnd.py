# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-021 — e-service PDND su PostgreSQL reale (tests/infra_reale.py).

Verifica:
  - voucher firmato con la chiave PDND configurata in PdndSettings
    (PDND_PUBLIC_KEY, PDND_AUDIENCE, PDND_ISSUER): accettato; firma di altra
    chiave, audience sbagliata, voucher non firmato: 401;
  - produzione senza chiave PDND: 503 esplicito;
  - il client_id del voucher si risolve nel cliente del fruitore
    (public.pdnd_fruitori); fruitore non registrato o disattivato: 403;
  - consultazione e verifica: l'AIP del proprio cliente si legge (la query
    con CAST(:id AS uuid) funziona), quello di un altro cliente risponde 404;
  - versamento (R03, VD-C-092): nel cliente del fruitore, sul caso d'uso unico
    (nessuna chiamata HTTP interna), con identificativo e chiave UD dal
    fruitore, Idempotency-Key come il REST, formato dal contenuto, base64 con
    limite e decodifica a blocchi;
  - errori dal catalogo (R03, VD-C-100): problem+json, ``WWW-Authenticate``
    sulle 401 (RFC 6750). Cambio voluto: le funzioni sollevano ``ErroreApi``
    invece di ``HTTPException`` con il corpo in stile OAuth.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import base64
import json
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from libs.shared.errori import ErroreApi, installa_gestori_errori
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

AUDIENCE = "verumdoc-eservice-test"
ISSUER = "uat.interop.pagopa.it"


def _coppia() -> tuple[str, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


PRIV_PDND, PUB_PDND = _coppia()
PRIV_ALTRA, _ = _coppia()


def _voucher(client_id: str, *, chiave: str = PRIV_PDND, aud: str = AUDIENCE,
             scadenza: int = 600, iss: str = ISSUER, senza: tuple[str, ...] = ()) -> str:
    ora = int(time.time())
    claim = {"iss": iss, "aud": aud, "sub": client_id, "client_id": client_id,
             "purposeId": str(uuid.uuid4()), "iat": ora, "nbf": ora, "exp": ora + scadenza,
             "jti": str(uuid.uuid4())}
    for nome in senza:
        claim.pop(nome)
    return jwt.encode(claim, chiave, algorithm="RS256")


@pytest.fixture
def chiave_pdnd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDND_PUBLIC_KEY", PUB_PDND)
    monkeypatch.setenv("PDND_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PDND_ISSUER", ISSUER)


def _app() -> FastAPI:
    from app.auth.dependencies import get_redis
    from app.routers.pdnd import router

    app = FastAPI()
    app.include_router(router)
    # R04 (VD-C-084): senza Redis il controllo del jti monouso è fail-closed (503);
    # qui si provano voucher, fruitori e versamento: Redis assente come nei test
    # delle quote (jti monouso provato in test_r04_pdnd_jti.py)
    app.dependency_overrides[get_redis] = lambda: None
    installa_gestori_errori(app)
    return app


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t")


# ── Voucher (senza database) ─────────────────────────────────────────

def test_voucher_firmato_pdnd_accettato(chiave_pdnd) -> None:
    from app.routers.pdnd import _validate_pdnd_voucher

    payload = _validate_pdnd_voucher(f"Bearer {_voucher('client-a')}")
    assert payload["client_id"] == "client-a"


@pytest.mark.parametrize("token", [
    pytest.param(lambda: _voucher("client-a", chiave=PRIV_ALTRA), id="altra-chiave"),
    pytest.param(lambda: _voucher("client-a", aud="altro-eservice"), id="audience-errata"),
    pytest.param(lambda: _voucher("client-a", scadenza=-60), id="scaduto"),
    # revisione M1: con python-jose un voucher SENZA aud era accettato
    pytest.param(lambda: _voucher("client-a", senza=("aud",)), id="senza-aud"),
    pytest.param(lambda: _voucher("client-a", senza=("iss",)), id="senza-iss"),
    pytest.param(lambda: _voucher("client-a", senza=("iat",)), id="senza-iat"),
    pytest.param(lambda: _voucher("client-a", senza=("exp",)), id="senza-exp"),
    pytest.param(lambda: _voucher("client-a", iss="interop.pagopa.it"), id="emittente-errato"),
])
def test_voucher_non_valido_401(chiave_pdnd, token) -> None:
    from app.routers.pdnd import _validate_pdnd_voucher

    with pytest.raises(ErroreApi) as exc:
        _validate_pdnd_voucher(f"Bearer {token()}")
    assert exc.value.status == 401
    assert exc.value.codice in ("PDND-401", "PDND-401-SCADUTO")
    assert exc.value.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    # nessun messaggio della libreria JWT al client (M1/B2)
    assert exc.value.dettaglio in ("Voucher PDND non valido", "Voucher PDND scaduto")


@pytest.mark.parametrize("variabile", ["PDND_AUDIENCE", "PDND_ISSUER"])
@pytest.mark.parametrize("valore", ["", "DA_IMPOSTARE:audience_eservice_pdnd"])
def test_chiave_senza_audience_o_emittente(chiave_pdnd, monkeypatch, variabile, valore) -> None:
    """A runtime nessun voucher passa senza i due controlli (503 PDND-503);
    all'avvio, fuori dai test, il controllo NON ferma il gateway: restituisce
    (e scrive nel log) il motivo, con il nome della variabile mancante."""
    from app.routers.pdnd import _validate_pdnd_voucher, verifica_configurazione_pdnd
    from structlog.testing import capture_logs

    monkeypatch.setenv(variabile, valore)
    with pytest.raises(ErroreApi) as exc:
        _validate_pdnd_voucher(f"Bearer {_voucher('client-a')}")
    assert exc.value.status == 503 and exc.value.codice == "PDND-503"
    for env in ("production", "staging", "development"):
        with capture_logs() as log:
            motivo = verifica_configurazione_pdnd(env)
        assert motivo is not None and variabile in motivo
        [voce] = [v for v in log if v["event"] == "pdnd_non_configurato"]
        assert voce["log_level"] == "error" and variabile in voce["errore"]
    assert verifica_configurazione_pdnd("test") is None   # nei test nessun log


def test_avvio_con_configurazione_completa_o_senza_chiave(chiave_pdnd, monkeypatch) -> None:
    from app.routers.pdnd import verifica_configurazione_pdnd

    assert verifica_configurazione_pdnd("production") is None
    monkeypatch.delenv("PDND_PUBLIC_KEY")
    monkeypatch.setenv("PDND_AUDIENCE", "")
    assert verifica_configurazione_pdnd("production") is None  # PDND spento: 503 alle chiamate


def _chiavi_gateway() -> dict[str, str]:
    priv, pub = _coppia()
    return {"JWT_PUBLIC_KEY": pub, "JWT_PRIVATE_KEY": priv, "API_KEY_PEPPER": "p" * 64}


def _importa_gateway(variabili: dict[str, str]):
    """Avvio reale del gateway (import di app.main) in un processo separato."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    servizio = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("PDND_", "ENV", "JWT_", "API_KEY_PEPPER"))}
    env.update({**_chiavi_gateway(), **variabili,
                "PYTHONPATH": os.pathsep.join([str(servizio), str(servizio.parents[1])])})
    return subprocess.run(  # noqa: S603 - comando fisso
        [sys.executable, "-c", "import app.main; print('GATEWAY_AVVIATO')"], cwd=servizio,
        env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("audience", ["", "DA_IMPOSTARE:audience_eservice_pdnd"])
def test_gateway_parte_con_chiave_senza_audience_ed_emittente(audience: str) -> None:
    """Revisione R3: con ENV=production, chiave PDND e audience/emittente
    mancanti o segnaposto il gateway PARTE e scrive un errore chiaro nel log."""
    esito = _importa_gateway({"ENV": "production", "PDND_PUBLIC_KEY": PUB_PDND,
                              "PDND_AUDIENCE": audience})
    uscita = esito.stdout + esito.stderr
    assert esito.returncode == 0, uscita
    assert "GATEWAY_AVVIATO" in esito.stdout
    assert "pdnd_non_configurato" in uscita
    assert "PDND_ISSUER" in uscita and "503 PDND-503" in uscita


def test_gateway_con_configurazione_pdnd_completa_nessun_errore() -> None:
    esito = _importa_gateway({"ENV": "production", "PDND_PUBLIC_KEY": PUB_PDND,
                              "PDND_AUDIENCE": AUDIENCE, "PDND_ISSUER": ISSUER})
    assert esito.returncode == 0, esito.stderr
    assert "pdnd_non_configurato" not in esito.stdout + esito.stderr


def test_staging_senza_chiave_non_accetta_token_del_gateway(monkeypatch) -> None:
    from app.auth.jwt_handler import create_access_token
    from app.config import settings
    from app.routers.pdnd import _validate_pdnd_voucher

    monkeypatch.delenv("PDND_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(settings, "env", "staging")
    tok = create_access_token(tenant_id=str(uuid.uuid4()), user_id="x", ruolo="API_SYSTEM")
    with pytest.raises(ErroreApi) as exc:
        _validate_pdnd_voucher(f"Bearer {tok}")
    assert exc.value.status == 503 and exc.value.codice == "PDND-503"


def _non_firmato(payload: dict) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}.firma-finta"


@pytest.mark.parametrize("con_chiave", [True, False])
def test_voucher_non_firmato_sempre_rifiutato(monkeypatch, con_chiave: bool) -> None:
    """Il vecchio ripiego accettava `sub=dev-pdnd-test` senza firma fuori produzione."""
    from app.routers.pdnd import _validate_pdnd_voucher

    if con_chiave:
        monkeypatch.setenv("PDND_PUBLIC_KEY", PUB_PDND)
        monkeypatch.setenv("PDND_AUDIENCE", AUDIENCE)
        monkeypatch.setenv("PDND_ISSUER", ISSUER)
    else:
        monkeypatch.delenv("PDND_PUBLIC_KEY", raising=False)
    token = _non_firmato({"sub": "dev-pdnd-test", "client_id": "x",
                          "exp": int(time.time()) + 3600})
    with pytest.raises(ErroreApi) as exc:
        _validate_pdnd_voucher(f"Bearer {token}")
    assert exc.value.status == 401


def test_produzione_senza_chiave_503(monkeypatch) -> None:
    from app.config import settings
    from app.routers.pdnd import _validate_pdnd_voucher

    monkeypatch.delenv("PDND_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(ErroreApi) as exc:
        _validate_pdnd_voucher(f"Bearer {_voucher('client-a')}")
    assert exc.value.status == 503


def test_chiave_da_settings_con_a_capo_escapati(monkeypatch) -> None:
    from app.routers.pdnd import pdnd_settings

    monkeypatch.setenv("PDND_PUBLIC_KEY", PUB_PDND.replace("\n", "\\n"))
    assert pdnd_settings().public_key == PUB_PDND


# ── Con database reale ───────────────────────────────────────────────

async def _registra_fruitore(url: str, client_id: str, tenant: uuid.UUID,
                             attivo: bool = True) -> None:
    await sql(url, "INSERT INTO public.pdnd_fruitori (client_id, tenant_id, attivo) "
                   "VALUES ($1, $2, $3)", client_id, tenant, attivo)


async def _crea_aip(url: str, tenant: uuid.UUID, classe: uuid.UUID) -> uuid.UUID:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, "
                   "s3_bucket, hash_sha256) VALUES ($1, $2, $3, $4, 'SU_STORAGE', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    return aip


@richiede_pg
async def test_consultazione_solo_aip_del_fruitore(db_url, chiave_pdnd) -> None:
    t1, classe = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-t1", t1)
    mio = await _crea_aip(db_url, t1, classe)
    altrui = await _crea_aip(db_url, t2, classe)
    voucher = {"Authorization": f"Bearer {_voucher('client-t1')}"}

    async with await _client() as c:
        r = await c.get(f"/api/pdnd/v1/aip/{mio}", headers=voucher)
        assert r.status_code == 200, r.text
        assert r.json()["aip_id"] == str(mio)
        assert r.json()["ente_richiedente"] == "client-t1"

        assert (await c.get(f"/api/pdnd/v1/aip/{altrui}", headers=voucher)).status_code == 404
        assert (await c.get("/api/pdnd/v1/aip/non-un-uuid", headers=voucher)).status_code == 404

        v = await c.get(f"/api/pdnd/v1/hashchain/verify/{mio}", headers=voucher)
        assert v.status_code == 200, v.text
        assert v.json()["in_catena"] is False
        # dichiara onestamente cosa verifica (e cosa no)
        assert v.json()["verifiche_eseguite"] == ["registrazione_nella_catena"]
        assert "ricalcolo_delle_impronte_sui_byte_conservati" in v.json()["verifiche_non_eseguite"]
        assert "standard" not in v.json()
        assert (await c.get(f"/api/pdnd/v1/hashchain/verify/{altrui}",
                            headers=voucher)).status_code == 404


@richiede_pg
async def test_fruitore_non_registrato_o_disattivato_403(db_url, chiave_pdnd) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-spento", t1, attivo=False)
    async with await _client() as c:
        for client_id in ("client-ignoto", "client-spento"):
            r = await c.get(f"/api/pdnd/v1/aip/{uuid.uuid4()}",
                            headers={"Authorization": f"Bearer {_voucher(client_id)}"})
            assert r.status_code == 403, (client_id, r.text)
            assert r.headers["content-type"].startswith("application/problem+json")
            assert r.json()["codice"] == "PDND-403"


@richiede_pg
async def test_cliente_sospeso_403(db_url, chiave_pdnd) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", t1)
    await _registra_fruitore(db_url, "client-sospeso", t1)
    async with await _client() as c:
        r = await c.get(f"/api/pdnd/v1/aip/{uuid.uuid4()}",
                        headers={"Authorization": f"Bearer {_voucher('client-sospeso')}"})
    assert r.status_code == 403




# ── E-service 1: versamento sul caso d'uso unico (R03, VD-C-092) ────

from app.config import settings  # noqa: E402

from tests.infra_reale import s3_server  # noqa: E402,F401 — fixture
from tests.supporto_canali import MB, MINIMI, PDF, chiavi_staging, s3, sha256  # noqa: E402,F401

P7M = b"-----BEGIN PKCS7-----\nMIIBprova\n-----END PKCS7-----\n"


def _corpo(contenuto: bytes = PDF, **modifiche) -> dict:
    corpo = {
        "classe_documentale": "fattura", "registro": "PROTOCOLLO", "anno": "2026",
        "numero": "123", "id_documento": "doc-123", "nome_file": "f.pdf",
        "hash_sha256": sha256(contenuto), "file_b64": base64.b64encode(contenuto).decode(),
        "metadati": dict(MINIMI),
    }
    corpo.update(modifiche)
    return corpo


async def _versa_pdnd(c, client_id: str, corpo: dict, chiave: str | None = "pdnd-chiave-0001"):
    h = {"Authorization": f"Bearer {_voucher(client_id)}"}
    if chiave:
        h["Idempotency-Key"] = chiave
    return await c.post("/api/pdnd/v1/versamento", json=corpo, headers=h)


async def _fruitore_id(url: str, client_id: str) -> uuid.UUID:
    return (await sql(url, "SELECT id FROM public.pdnd_fruitori WHERE client_id = $1",
                      client_id))[0]["id"]


@richiede_pg
async def test_versamento_nel_cliente_del_fruitore_sul_caso_d_uso(db_url, chiave_pdnd, s3,
                                                                  monkeypatch) -> None:
    """Nessuna chiamata HTTP interna; PdV nel cliente del fruitore con la chiave UD e
    l'identificativo del fruitore; canale PDND; utente = registrazione del fruitore."""
    def vietato(*args, **kwargs):
        raise AssertionError("il versamento PDND non deve chiamare il gateway via HTTP")

    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-versa", t1)
    async with await _client() as c:
        monkeypatch.setattr(httpx, "AsyncClient", vietato)
        r = await _versa_pdnd(c, "client-versa", _corpo())
    assert r.status_code == 202, r.text
    pdv_id = r.json()["pdv_id"]
    riga = (await sql(db_url, "SELECT tenant_id, user_id, metadata_json FROM public.pdv "
                              "WHERE id = $1", uuid.UUID(pdv_id)))[0]
    assert riga["tenant_id"] == t1
    assert riga["user_id"] == await _fruitore_id(db_url, "client-versa")
    meta = json.loads(riga["metadata_json"])
    assert meta["canale"] == "PDND"
    assert meta["chiave"] == {"registro": "PROTOCOLLO", "anno": "2026", "numero": "123"}
    assert meta["indice"]["documenti"][0]["id"] == "doc-123"
    assert meta["idempotency_key"] == "pdnd-chiave-0001"


@richiede_pg
async def test_versamento_ritentato_stessa_chiave_stesso_pdv(db_url, chiave_pdnd, s3) -> None:
    """VD-C-092: il ritentativo non crea un secondo PdV (200, Idempotent-Replayed)."""
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-ritenta", t1)
    async with await _client() as c:
        prima = await _versa_pdnd(c, "client-ritenta", _corpo())
        seconda = await _versa_pdnd(c, "client-ritenta", _corpo())
        diverso = await _versa_pdnd(c, "client-ritenta", _corpo(PDF + b"%altro"))
    assert prima.status_code == 202 and seconda.status_code == 200
    assert seconda.json()["pdv_id"] == prima.json()["pdv_id"]
    assert seconda.headers["Idempotent-Replayed"] == "true"
    assert diverso.status_code == 409 and diverso.json()["codice"] == "VRS-121"
    assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 1


@richiede_pg
async def test_versamento_senza_chiave_transizione_come_il_rest(db_url, chiave_pdnd, s3,
                                                                monkeypatch) -> None:
    from datetime import date

    monkeypatch.setattr(settings, "idempotenza_transizione_fino_al", date(2999, 12, 31))
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-senza", t1)
    async with await _client() as c:
        prima = await _versa_pdnd(c, "client-senza", _corpo(), chiave=None)
        seconda = await _versa_pdnd(c, "client-senza", _corpo(), chiave=None)
    assert prima.status_code == 202 and "Deprecation" in prima.headers
    assert [a["codice"] for a in prima.json()["avvisi"]] == ["VRS-126"]
    assert seconda.status_code == 200 and seconda.json()["pdv_id"] == prima.json()["pdv_id"]
    monkeypatch.setattr(settings, "idempotenza_transizione_fino_al", date(2026, 1, 1))
    async with await _client() as c:
        dopo = await _versa_pdnd(c, "client-senza", _corpo(numero="124"), chiave=None)
    assert dopo.status_code == 428 and dopo.json()["codice"] == "VRS-125"


@richiede_pg
async def test_versamento_formato_dal_contenuto_p7m_non_forzato_a_pdf(db_url, chiave_pdnd,
                                                                       s3) -> None:
    """VD-C-092: un p7m senza formato dichiarato si riconosce dal contenuto."""
    t1, _ = await crea_tenant_e_classe(
        db_url, formati='["application/pdf", "application/pkcs7-mime"]')
    await _registra_fruitore(db_url, "client-p7m", t1)
    async with await _client() as c:
        r = await _versa_pdnd(c, "client-p7m", _corpo(P7M, nome_file="atto.pdf.p7m",
                                                      formato=None))
    assert r.status_code == 202, r.text
    meta = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv"))[0][0])
    assert meta["docs_s3_keys"][0]["formato_rilevato"] == "application/pkcs7-mime"


@richiede_pg
async def test_versamento_campi_del_fruitore_obbligatori_422(db_url, chiave_pdnd) -> None:
    """Identificativo e numero non si inventano più dall'orario: mancano → 422."""
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-campi", t1)
    corpo = _corpo()
    del corpo["id_documento"], corpo["numero"]
    async with await _client() as c:
        r = await _versa_pdnd(c, "client-campi", corpo)
    assert r.status_code == 422 and r.json()["codice"] == "VAL-422"
    assert {e["campo"] for e in r.json()["campi"]} == {"id_documento", "numero"}


@richiede_pg
@pytest.mark.parametrize("file_b64", ["%%%non-base64%%%", "QUJD\nREVG", "QQ==QUJD", "QUJ"])
async def test_versamento_file_b64_non_valido_422(db_url, chiave_pdnd, file_b64) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-b64", t1)
    async with await _client() as c:
        r = await _versa_pdnd(c, "client-b64", _corpo(file_b64=file_b64))
    assert r.status_code == 422 and r.json()["codice"] == "PDND-422"


@richiede_pg
async def test_versamento_oltre_il_limite_413_senza_staging(db_url, chiave_pdnd, s3,
                                                            monkeypatch) -> None:
    monkeypatch.setattr(settings, "pdnd_max_file_mb", 1)
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-grande", t1)
    grande = PDF + b"0" * (2 * MB)
    async with await _client() as c:
        r = await _versa_pdnd(c, "client-grande", _corpo(grande))
        # corpo oltre il limite: rifiutato durante la lettura, prima del JSON
        enorme = await c.post("/api/pdnd/v1/versamento",
                              content=b'{"file_b64": "' + b"A" * (6 * MB) + b'"}',
                              headers={"Authorization": f"Bearer {_voucher('client-grande')}",
                                       "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["codice"] == "PDND-413"
    assert enorme.status_code == 413 and enorme.json()["codice"] == "PDND-413"
    assert chiavi_staging(s3) == []


@richiede_pg
async def test_versamento_impronta_errata_400_e_nessun_file(db_url, chiave_pdnd, s3) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-hash", t1)
    async with await _client() as c:
        r = await _versa_pdnd(c, "client-hash", _corpo(hash_sha256="a" * 64))
    assert r.status_code == 400 and r.json()["codice"] == "VRS-102"
    assert chiavi_staging(s3) == []


@richiede_pg
async def test_versamento_senza_voucher_401_con_www_authenticate(db_url, chiave_pdnd) -> None:
    async with await _client() as c:
        r = await c.post("/api/pdnd/v1/versamento", json=_corpo())
    assert r.status_code == 401 and r.json()["codice"] == "PDND-401"
    assert r.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
