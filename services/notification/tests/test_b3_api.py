# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API pubbliche GET /api/v1/eventi e /api/v1/webhook sull'app reale e su
PostgreSQL vero — contratto C3 (agente R03 b3), REGOLE §14, §18, §19.

Copre: 401 senza credenziali o con token firmato da un'altra chiave; ruoli
(gestione solo ADMIN_TENANT, lettura eventi per ogni ruolo noto); chiave API
per gli eventi ma non per i webhook; revoca (utente disattivato); 404 fra enti
su ogni rotta con id; segreto mostrato una sola volta e cifrato nel database;
audit; errori del catalogo (WH-001/002/003/004/010/011/030/404, EVT-001,
VAL-422); cursore degli eventi; coda di scarto e riprova.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.api import autenticazione
from app.application import configurazione
from app.config import settings
from app.domain.eventi import EventoCliente
from app.infrastructure.cifratura import Cifratore
from app.infrastructure.repo_eventi import pubblica_eventi, registra_evento
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.revoca import svuota_cache_revoche
from tests.b3_supporto import token_utente
from tests.infra_reale import (
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]

PEPPER = b"p" * 40
URL_OK = "https://hook.cliente-prova.it/eventi"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    configurazione.imposta_cifratore(
        Cifratore.da_impostazioni(base64.b64encode(os.urandom(32)).decode(), "k1"))
    autenticazione.imposta_pepper(PEPPER)
    svuota_cache_revoche()
    yield
    configurazione.imposta_cifratore(None)
    autenticazione.imposta_pepper(None)
    svuota_cache_revoche()


@pytest.fixture
async def client(db_url):
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://notification") as c:
        yield c


async def crea_ente(url: str) -> dict:
    """Ente con un utente per ruolo; restituisce {tenant, ruolo: user_id}."""
    t = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', 'a@example.org')",
              t, t.hex[:16], f"t_{t.hex[:20]}")
    ente: dict = {"tenant": t}
    for ruolo in ("ADMIN_TENANT", "OPERATORE", "VIEWER"):
        u = uuid.uuid4()
        await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                       "VALUES ($1, $2, $3, 'N', 'C', CAST($4 AS user_ruolo))",
                  u, t, f"{u.hex[:8]}@example.org", ruolo)
        ente[ruolo] = u
    return ente


def h(ente: dict, ruolo: str = "ADMIN_TENANT") -> dict:
    return {"Authorization": f"Bearer {token_utente(ente['tenant'], ruolo, ente[ruolo])}"}


async def crea_webhook(client, ente, **kw) -> dict:
    corpo = {"url": URL_OK, "tipi": ["versamento.ricevuto"]} | kw
    r = await client.post("/api/v1/webhook", json=corpo, headers=h(ente))
    assert r.status_code == 201, r.text
    return r.json()


# ── Autenticazione e ruoli ──────────────────────────────────────────

ROTTE = [
    ("GET", "/api/v1/eventi"), ("GET", "/api/v1/webhook"), ("POST", "/api/v1/webhook"),
    ("GET", f"/api/v1/webhook/{uuid.uuid4()}"), ("PATCH", f"/api/v1/webhook/{uuid.uuid4()}"),
    ("DELETE", f"/api/v1/webhook/{uuid.uuid4()}"),
    ("GET", f"/api/v1/webhook/{uuid.uuid4()}/consegne"),
    ("POST", f"/api/v1/webhook/{uuid.uuid4()}/consegne/1/riprova"),
]


@pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
async def test_rotte_401_senza_credenziali_e_con_chiave_estranea(client, metodo, percorso):
    r = await client.request(metodo, percorso, json={})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-000"
    assert r.headers["content-type"] == "application/problem+json"
    estranea = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    token = token_utente(uuid.uuid4(), "ADMIN_TENANT", chiave_privata=estranea)
    r = await client.request(metodo, percorso, json={},
                             headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"


async def test_gestione_webhook_solo_amministratore(client, db_url):
    ente = await crea_ente(db_url)
    for ruolo in ("OPERATORE", "VIEWER"):
        r = await client.get("/api/v1/webhook", headers=h(ente, ruolo))
        assert r.status_code == 403 and r.json()["codice"] == "AUTH-010"
        r = await client.get("/api/v1/eventi", headers=h(ente, ruolo))
        assert r.status_code == 200


async def test_chiave_api_legge_eventi_ma_non_gestisce_webhook(client, db_url):
    ente = await crea_ente(db_url)
    chiave = genera_chiave_api(PEPPER)
    await sql(db_url, "INSERT INTO public.api_keys (tenant_id, user_id, nome, key_hash, "
                      "key_prefix, "
                      "permessi) VALUES ($1, $2, 'sistema', $3, $4, '[\"read\"]')",
              ente["tenant"], ente["ADMIN_TENANT"], chiave.impronta, chiave.identificativo)
    r = await client.get("/api/v1/eventi", headers={"X-Api-Key": chiave.chiave})
    assert r.status_code == 200, r.text
    r = await client.get("/api/v1/eventi", headers={
        "X-Api-Key-Id": f"vd_{chiave.identificativo}",
        "X-Api-Key-Secret": chiave.chiave.split("_", 2)[2]})
    assert r.status_code == 200, r.text
    r = await client.get("/api/v1/webhook", headers={"X-Api-Key": chiave.chiave})
    assert r.status_code == 403
    r = await client.get("/api/v1/eventi", headers={"X-Api-Key": chiave.chiave[:-2] + "xx"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"
    await sql(db_url, "UPDATE public.api_keys SET scadenza = now() - interval '1 day'")
    r = await client.get("/api/v1/eventi", headers={"X-Api-Key": chiave.chiave})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-002"


async def test_utente_disattivato_revocato(client, db_url):
    ente = await crea_ente(db_url)
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", ente["ADMIN_TENANT"])
    r = await client.get("/api/v1/webhook", headers=h(ente))
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"


# ── Webhook: creazione, segreto, errori ─────────────────────────────

async def test_crea_webhook_segreto_una_volta_cifrato_e_audit(client, db_url):
    ente = await crea_ente(db_url)
    creato = await crea_webhook(client, ente, tipi=["pdd.pronto", "versamento.ricevuto",
                                                    "pdd.pronto"], descrizione="ERP")
    segreto = creato["segreto"]
    assert segreto.startswith("whsec_") and creato["segreto_suffisso"] == segreto[-4:]
    assert creato["tipi"] == ["pdd.pronto", "versamento.ricevuto"] and creato["attiva"] is True
    letto = (await client.get(f"/api/v1/webhook/{creato['id']}", headers=h(ente))).json()
    elenco = (await client.get("/api/v1/webhook", headers=h(ente))).json()
    assert "segreto" not in letto and segreto not in json.dumps(elenco) + json.dumps(letto)
    assert [s["id"] for s in elenco["sottoscrizioni"]] == [creato["id"]]
    riga = (await sql(db_url, "SELECT segreto_cifrato FROM public.webhook_sottoscrizioni"))[0]
    assert segreto not in riga["segreto_cifrato"] and riga["segreto_cifrato"].startswith("aesgcm$")
    audit = await sql(db_url, "SELECT evento, dettagli FROM public.audit_global "
                              "WHERE evento LIKE 'webhook.%'")
    assert [a["evento"] for a in audit] == ["webhook.creato"]
    assert segreto not in audit[0]["dettagli"] and "hook.cliente-prova.it" in audit[0]["dettagli"]


@pytest.mark.parametrize(("corpo", "stato", "codice"), [
    ({"url": "http://hook.cliente-prova.it/x", "tipi": ["pdd.pronto"]}, 422, "WH-001"),
    ({"url": "https://u:p@hook.cliente-prova.it/x", "tipi": ["pdd.pronto"]}, 422, "WH-001"),
    ({"url": "https://10.0.0.5/x", "tipi": ["pdd.pronto"]}, 422, "WH-002"),
    ({"url": "https://169.254.169.254/latest", "tipi": ["pdd.pronto"]}, 422, "WH-002"),
    ({"url": "https://localhost:8443/x", "tipi": ["pdd.pronto"]}, 422, "WH-002"),
    ({"url": URL_OK, "tipi": ["pdd.pronto", "aip.conserved"]}, 422, "WH-003"),
    ({"url": URL_OK, "tipi": []}, 422, "VAL-422"),
    ({"url": URL_OK, "tipi": ["pdd.pronto"], "segreto": "mio"}, 422, "VAL-422"),
    ({"url": URL_OK, "tipi": ["pdd.pronto"], "attiva": "si"}, 422, "VAL-422"),
])
async def test_crea_webhook_rifiuti(client, db_url, corpo, stato, codice):
    ente = await crea_ente(db_url)
    r = await client.post("/api/v1/webhook", json=corpo, headers=h(ente))
    assert (r.status_code, r.json()["codice"]) == (stato, codice), r.text
    if codice == "WH-003":
        assert r.json()["dettagli"]["sconosciuti"] == ["aip.conserved"]
    assert await sql(db_url, "SELECT 1 FROM public.webhook_sottoscrizioni") == []


async def test_crea_webhook_limite_per_ente(client, db_url, monkeypatch):
    monkeypatch.setattr(settings, "webhook_max_sottoscrizioni_per_ente", 2)
    ente, altro = await crea_ente(db_url), await crea_ente(db_url)
    await crea_webhook(client, ente)
    eliminato = await crea_webhook(client, ente)
    r = await client.post("/api/v1/webhook", json={"url": URL_OK, "tipi": ["pdd.pronto"]},
                          headers=h(ente))
    assert r.status_code == 409 and r.json()["codice"] == "WH-004"
    await crea_webhook(client, altro)  # il limite è per ente
    r = await client.delete(f"/api/v1/webhook/{eliminato['id']}", headers=h(ente))
    assert r.status_code == 204
    await crea_webhook(client, ente)   # un eliminato libera il posto


async def test_crea_webhook_senza_chiave_di_cifratura_503(client, db_url):
    configurazione.imposta_cifratore(None)
    ente = await crea_ente(db_url)
    r = await client.post("/api/v1/webhook", json={"url": URL_OK, "tipi": ["pdd.pronto"]},
                          headers=h(ente))
    assert r.status_code == 503 and r.json()["codice"] == "WH-030"


async def test_modifica_ed_elimina_webhook(client, db_url):
    ente = await crea_ente(db_url)
    w = await crea_webhook(client, ente)
    r = await client.patch(f"/api/v1/webhook/{w['id']}", headers=h(ente),
                           json={"tipi": ["pdd.pronto"], "attiva": False})
    assert r.status_code == 200 and r.json()["tipi"] == ["pdd.pronto"] and not r.json()["attiva"]
    r = await client.patch(f"/api/v1/webhook/{w['id']}", headers=h(ente),
                           json={"url": "https://192.168.0.1/x"})
    assert r.status_code == 422 and r.json()["codice"] == "WH-002"
    for corpo in ({}, {"url": None}, {"segreto_cifrato": "x"}):
        r = await client.patch(f"/api/v1/webhook/{w['id']}", headers=h(ente), json=corpo)
        assert r.status_code == 422 and r.json()["codice"] == "VAL-422"
    assert (await client.delete(f"/api/v1/webhook/{w['id']}", headers=h(ente))).status_code == 204
    r = await client.get(f"/api/v1/webhook/{w['id']}", headers=h(ente))
    assert r.status_code == 404 and r.json()["codice"] == "WH-404"
    eventi = [a["evento"] for a in await sql(
        db_url, "SELECT evento FROM public.audit_global WHERE evento LIKE 'webhook.%' "
                "ORDER BY created_at")]
    assert eventi == ["webhook.creato", "webhook.modificato", "webhook.eliminato"]


async def test_isolamento_fra_enti_404_su_ogni_rotta(client, db_url):
    """REGOLE §14.4: la risorsa di un altro ente risponde 404, anche in GET."""
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    w = await crea_webhook(client, a)
    wid = w["id"]
    for metodo, percorso, corpo in (
        ("GET", f"/api/v1/webhook/{wid}", None),
        ("PATCH", f"/api/v1/webhook/{wid}", {"attiva": False}),
        ("DELETE", f"/api/v1/webhook/{wid}", None),
        ("GET", f"/api/v1/webhook/{wid}/consegne", None),
    ):
        r = await client.request(metodo, percorso, json=corpo, headers=h(b))
        assert r.status_code == 404 and r.json()["codice"] == "WH-404", (metodo, percorso)
    r = await client.post(f"/api/v1/webhook/{wid}/consegne/1/riprova", headers=h(b))
    assert r.status_code == 404 and r.json()["codice"] == "WH-010"
    assert (await client.get("/api/v1/webhook", headers=h(b))).json() == {"sottoscrizioni": []}
    # la sottoscrizione di A è intatta
    assert (await client.get(f"/api/v1/webhook/{wid}", headers=h(a))).json()["attiva"] is True


@pytest.mark.parametrize("percorso", [
    "/api/v1/webhook/abc", "/api/v1/webhook/-1/consegne",
    "/api/v1/webhook/00000000-0000-0000-0000-000000000000",
])
async def test_id_non_validi_404_mai_500(client, db_url, percorso):
    ente = await crea_ente(db_url)
    r = await client.get(percorso, headers=h(ente))
    assert r.status_code == 404 and r.json()["codice"] == "WH-404"


async def test_consegne_coda_di_scarto_e_riprova(client, db_url):
    ente = await crea_ente(db_url)
    w = await crea_webhook(client, ente)
    for _ in range(3):
        await registra_evento(EventoCliente(str(ente["tenant"]), "versamento.ricevuto",
                                            "versamento", str(uuid.uuid4()), {}), origine="x")
    righe = await sql(db_url, "SELECT id FROM public.webhook_consegne ORDER BY id")
    fallita, in_coda = righe[0]["id"], righe[1]["id"]
    await sql(db_url, "UPDATE public.webhook_consegne SET stato = 'FALLITA', tentativi = 5, "
                      "ultimo_codice = 'WH-103', ultimo_stato_http = 500, "
                      "prossimo_tentativo = NULL "
                      "WHERE id = $1", fallita)
    base = f"/api/v1/webhook/{w['id']}/consegne"
    r = await client.get(f"{base}?stato=FALLITA", headers=h(ente))
    assert r.status_code == 200
    corpo = r.json()
    assert [c["id"] for c in corpo["consegne"]] == [fallita] and not corpo["altri"]
    assert corpo["consegne"][0]["ultimo_codice"] == "WH-103"
    assert corpo["consegne"][0]["tipo_evento"] == "versamento.ricevuto"
    pagina = (await client.get(f"{base}?limite=2", headers=h(ente))).json()
    assert len(pagina["consegne"]) == 2 and pagina["altri"]
    resto = (await client.get(f"{base}?limite=2&dopo={pagina['cursore']}", headers=h(ente))).json()
    assert len(resto["consegne"]) == 1 and not resto["altri"]
    r = await client.get(f"{base}?stato=PERSA", headers=h(ente))
    assert r.status_code == 422

    r = await client.post(f"{base}/{fallita}/riprova", headers=h(ente))
    assert r.status_code == 202 and r.json()["stato"] == "IN_CODA" and r.json()["riprove"] == 1
    r = await client.post(f"{base}/{in_coda}/riprova", headers=h(ente))
    assert r.status_code == 409 and r.json()["codice"] == "WH-011"
    assert r.json()["dettagli"]["stato"] == "IN_CODA"
    for cid in (999999, 2**70):
        r = await client.post(f"{base}/{cid}/riprova", headers=h(ente))
        assert r.status_code in (404, 422) and r.json()["codice"] in ("WH-010", "VAL-422")


# ── Eventi ──────────────────────────────────────────────────────────

async def test_eventi_cursore_filtro_e_isolamento(client, db_url):
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    for ente, n in ((a, 3), (b, 2)):
        for _ in range(n):
            await registra_evento(EventoCliente(str(ente["tenant"]), "versamento.ricevuto",
                                                "versamento", str(uuid.uuid4()), {"x": 1}),
                                  origine="x")
    await registra_evento(EventoCliente(str(a["tenant"]), "pdd.pronto", "pdd",
                                        str(uuid.uuid4()), {}), origine="x")
    await pubblica_eventi()  # R03 f2 (REV-13): visibili dopo la pubblicazione
    r = await client.get("/api/v1/eventi?limite=2", headers=h(a, "VIEWER"))
    p1 = r.json()
    assert r.status_code == 200 and len(p1["eventi"]) == 2 and p1["altri"]
    assert p1["cursore"] == p1["eventi"][-1]["posizione"]
    assert set(p1["eventi"][0]) == {"id", "tipo", "creato_il", "risorsa", "dati", "posizione"}
    p2 = (await client.get(f"/api/v1/eventi?limite=2&dopo={p1['cursore']}",
                           headers=h(a, "VIEWER"))).json()
    assert len(p2["eventi"]) == 2 and not p2["altri"]
    vuoto = (await client.get(f"/api/v1/eventi?dopo={p2['cursore']}", headers=h(a))).json()
    assert vuoto == {"eventi": [], "cursore": p2["cursore"], "altri": False}
    solo_pdd = (await client.get("/api/v1/eventi?tipo=pdd.pronto", headers=h(a))).json()
    assert [e["tipo"] for e in solo_pdd["eventi"]] == ["pdd.pronto"]
    assert len((await client.get("/api/v1/eventi", headers=h(b))).json()["eventi"]) == 2
    r = await client.get("/api/v1/eventi?tipo=pdd.pronto&tipo=inventato", headers=h(a))
    assert r.status_code == 422 and r.json()["codice"] == "EVT-001"
    assert "pdd.pronto" in r.json()["dettagli"]["ammessi"]
    for parametri in ("limite=0", "limite=501", "dopo=-1", f"dopo={2**64}", "dopo=abc"):
        r = await client.get(f"/api/v1/eventi?{parametri}", headers=h(a))
        assert r.status_code == 422 and r.json()["codice"] == "VAL-422", parametri


async def test_eventi_utente_senza_ente_negato(client, db_url):
    """REGOLE §14.3: un token senza cliente valido non vede nulla."""
    from app.api.autenticazione import chiavi_jwt

    from libs.shared.security.jwt_rs256 import firma_token

    token = firma_token({"sub": "x", "tenant_id": "non-uuid", "ruolo": "VIEWER"},
                        chiave_privata_pem=chiavi_jwt().privata_pem,
                        emittente=settings.jwt_issuer, pubblico=settings.jwt_audience,
                        durata_secondi=60)
    r = await client.get("/api/v1/eventi", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["codice"] == "AUTH-021"


async def test_eventi_token_scaduto(client, db_url):
    ente = await crea_ente(db_url)
    from app.api.autenticazione import chiavi_jwt

    from libs.shared.security.jwt_rs256 import firma_token

    passato = int((datetime.now(UTC) - timedelta(hours=2)).timestamp())
    token = firma_token({"sub": "x", "tenant_id": str(ente["tenant"]), "ruolo": "VIEWER",
                         "user_id": str(ente["VIEWER"])},
                        chiave_privata_pem=chiavi_jwt().privata_pem,
                        emittente=settings.jwt_issuer, pubblico=settings.jwt_audience,
                        durata_secondi=60, adesso=passato)
    r = await client.get("/api/v1/eventi", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-002"


async def test_riprova_consegna_oltre_retention_422_wh_012(client, db_url):
    """R04 correzioni (rev4 rilievo 11): la riprova di una consegna il cui evento è
    oltre la retention è rifiutata con WH-012 (422) e la consegna resta FALLITA."""
    from datetime import UTC, datetime, timedelta

    from libs.shared.db import engine_manutenzione  # R05: ruolo proprietario
    from libs.shared.db.partizioni import POLITICHE, esegui_manutenzione_partizioni

    ente = await crea_ente(db_url)
    w = await crea_webhook(client, ente)
    vecchio = datetime.now(UTC) - timedelta(days=95)
    await esegui_manutenzione_partizioni(engine_manutenzione(), vecchio, [
        p for p in POLITICHE if p.tabella in ("eventi_cliente", "webhook_consegne")])
    ev = await sql(db_url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                           "risorsa_id, creato_il) VALUES ($1, 'versamento.ricevuto', "
                           "'versamento', 'v', $2) RETURNING id, creato_il", ente["tenant"],
                   vecchio)
    [c] = await sql(db_url, "INSERT INTO public.webhook_consegne (tenant_id, sottoscrizione_id, "
                            "evento_id, evento_creato_il, stato) VALUES ($1, $2, $3, $4, "
                            "'FALLITA') RETURNING id", ente["tenant"], uuid.UUID(w["id"]),
                    ev[0]["id"], ev[0]["creato_il"])
    r = await client.post(f"/api/v1/webhook/{w['id']}/consegne/{c['id']}/riprova", headers=h(ente))
    assert r.status_code == 422 and r.json()["codice"] == "WH-012", r.text
    assert (await sql(db_url, "SELECT stato FROM public.webhook_consegne WHERE id = $1",
                      c["id"]))[0]["stato"] == "FALLITA"
