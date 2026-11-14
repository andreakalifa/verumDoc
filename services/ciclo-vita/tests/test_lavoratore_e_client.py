# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lavoratore con lease (REGOLE §12.9, §15.8) e client dei sistemi esterni con
trasporto finto (REGOLE §20.1): registro delle impronte (contratto Q3),
gateway (contratti C1/C2: sessione di caricamento e versamento per
riferimento con token di canale CICLO_VITA), distribution (migrazione).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.application import lavoratore as lv
from app.config import settings
from app.infrastructure.ambito import ambito_lavoratore
from app.infrastructure.distribution import ClientDistribution, ErroreDistribution
from app.infrastructure.gateway import ClientGateway, ErroreGateway, token_canale
from app.infrastructure.registro import ClientRegistro, ErroreRegistro
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from libs.shared.security.jwt_rs256 import verifica_token
from libs.shared.security.token_canale import PUBBLICO_TOKEN_CANALE
from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
)

# ── Registro (Q3) ──────────────────────────────────────────────────────

async def test_client_registro_contratto_q3() -> None:
    ricevute = []

    def risposta(richiesta: httpx.Request) -> httpx.Response:
        ricevute.append(richiesta)
        return httpx.Response(202, json={"foglia_id": "f-1"})

    client = ClientRegistro("http://hashchain", "tok", transport=httpx.MockTransport(risposta))
    foglia = await client.aggiungi_foglia(tipo="scarto", pda="urn:x",
                                          riferimento={"pratica": "p"},
                                          impronta_documento_pratica="a" * 64)
    assert foglia == "f-1"
    r = ricevute[0]
    assert r.url.path == "/interno/v1/registro/foglie"
    assert r.headers["authorization"] == "Bearer tok"
    assert json.loads(r.content) == {"tipo": "scarto", "pda": "urn:x",
                                     "riferimento": {"pratica": "p"},
                                     "impronta_documento_pratica": "a" * 64}


@pytest.mark.parametrize(("status", "ritentabile"), [(503, True), (429, True), (403, False),
                                                     (422, False)])
async def test_client_registro_errori(status, ritentabile) -> None:
    client = ClientRegistro("http://h", "tok", transport=httpx.MockTransport(
        lambda r: httpx.Response(status, json={"codice": "HC-X"})))
    with pytest.raises(ErroreRegistro) as e:
        await client.aggiungi_foglia(tipo="rettifica", pda="u", riferimento={"pratica": "p"},
                                     impronta_documento_pratica="b" * 64)
    assert e.value.ritentabile is ritentabile


async def test_client_registro_senza_token_ritentabile() -> None:
    with pytest.raises(ErroreRegistro) as e:
        await ClientRegistro("http://h", "").aggiungi_foglia(
            tipo="scarto", pda="u", riferimento={"pratica": "p"},
            impronta_documento_pratica="b" * 64)
    assert e.value.ritentabile and e.value.codice == "LC-503-REGISTRO"


# ── Gateway (C1/C2) ────────────────────────────────────────────────────

def _chiave_pem() -> tuple[str, str]:
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    privata = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()).decode()
    pubblica = chiave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return privata, pubblica


def test_token_canale_ciclo_vita(monkeypatch) -> None:
    privata, pubblica = _chiave_pem()
    monkeypatch.setattr(settings, "ciclo_vita_token_chiave_privata", SecretStr(privata))
    conto = {"tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "ruolo": "OPERATORE"}
    payload = verifica_token(token_canale(conto), chiave_pubblica_pem=pubblica,
                             emittente=settings.jwt_issuer, pubblico=PUBBLICO_TOKEN_CANALE)
    assert payload["canale"] == "CICLO_VITA" and payload["servizio"] == "ciclo-vita"
    assert payload["sub"] == "servizio:ciclo-vita" and payload["tipo_token"] == "canale"
    assert {k: payload[k] for k in conto} == conto
    assert payload["exp"] - payload["iat"] <= 600


def test_token_canale_senza_chiave_ritentabile(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ciclo_vita_token_chiave_privata", SecretStr(""))
    with pytest.raises(ErroreGateway) as e:
        token_canale({"tenant_id": "t", "user_id": "u", "ruolo": "OPERATORE"})
    assert e.value.ritentabile


async def test_client_gateway_sessione_e_versamento() -> None:
    contenuto = b"x" * 25
    richieste: list[httpx.Request] = []

    def risposta(r: httpx.Request) -> httpx.Response:
        richieste.append(r)
        if r.method == "POST" and r.url.path == "/api/v1/caricamenti":
            return httpx.Response(201, json={"caricamento_id": "c1", "dimensione_parte": 10})
        if r.method == "PUT":
            assert r.headers["x-impronta-parte"] == \
                f"sha256={hashlib.sha256(r.content).hexdigest()}"
            return httpx.Response(200, json={"parte": 1})
        if r.url.path.endswith("/completa"):
            return httpx.Response(202, json={"stato": "IN_VERIFICA"})
        return httpx.Response(202, json={"pdv_id": "p1", "stato": "RICEVUTO"})

    client = ClientGateway("http://gw", transport=httpx.MockTransport(risposta),
                           token=lambda conto: "tok-canale")

    async def blocchi():
        for i in range(0, 25, 7):
            yield contenuto[i:i + 7]

    conto = {"tenant_id": "t", "user_id": "u", "ruolo": "OPERATORE"}
    cid = await client.carica(conto, nome_file="a.xml", dimensione=25,
                              sha256=hashlib.sha256(contenuto).hexdigest(), blocchi=blocchi,
                              chiave="k-1")
    esito = await client.versa(conto, indice={"documenti": []}, file={"a.xml": cid}, chiave="k-2")
    assert cid == "c1" and esito["pdv_id"] == "p1"
    parti = [r for r in richieste if r.method == "PUT"]
    assert [len(r.content) for r in parti] == [10, 10, 5]
    assert [r.url.path.rsplit("/", 1)[-1] for r in parti] == ["1", "2", "3"]
    apertura = json.loads(richieste[0].content)
    assert apertura["impronta"] == {"algoritmo": "SHA-256",
                                    "valore": hashlib.sha256(contenuto).hexdigest()}
    assert richieste[0].headers["idempotency-key"] == "k-1"
    assert richieste[-1].headers["idempotency-key"] == "k-2"
    assert all(r.headers["authorization"] == "Bearer tok-canale" for r in richieste)


async def test_client_gateway_errori() -> None:
    client = ClientGateway("http://gw", transport=httpx.MockTransport(
        lambda r: httpx.Response(422, json={"codice": "VRS-130", "ritentabile": False})),
        token=lambda c: "t")
    with pytest.raises(ErroreGateway) as e:
        await client.versa({}, indice={}, file={}, chiave="k")
    assert e.value.problema["codice"] == "VRS-130" and not e.value.ritentabile
    client = ClientGateway("http://gw", transport=httpx.MockTransport(
        lambda r: httpx.Response(503, json={"codice": "VRS-501"})), token=lambda c: "t")
    with pytest.raises(ErroreGateway) as e:
        await client.versa({}, indice={}, file={}, chiave="k")
    assert e.value.ritentabile


# ── Distribution ──────────────────────────────────────────────────────

async def test_client_distribution() -> None:
    richieste: list[httpx.Request] = []

    def risposta(r: httpx.Request) -> httpx.Response:
        richieste.append(r)
        if r.method == "POST":
            return httpx.Response(200, json={"results": [{"aip_id": "a", "pdd_id": "d"}]})
        return httpx.Response(200, json={"stato": "PRONTO"})

    client = ClientDistribution("http://dist", "tok-servizio",
                                transport=httpx.MockTransport(risposta))
    esito = await client.esporta(ente="e", aip_ids=["a"], destinatario="X", motivo="m")
    stato = await client.stato(ente="e", pdd_id="d")
    assert esito["results"][0]["pdd_id"] == "d" and stato["stato"] == "PRONTO"
    assert json.loads(richieste[0].content)["ente_richiedente"] == "e"
    assert richieste[1].url.params["ente"] == "e"
    assert richieste[0].headers["authorization"] == "Bearer tok-servizio"
    with pytest.raises(ErroreDistribution) as e:
        await ClientDistribution("http://dist", "").stato(ente="e", pdd_id="d")
    assert e.value.ritentabile


# ── Lavoratore ─────────────────────────────────────────────────────────

async def _annullamento(db_url) -> tuple[str, uuid.UUID]:
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, admin, stato="RICEVUTO")
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(tenant, "ADMIN_TENANT", admin),
                         json={"pdv_id": str(pdv), "motivo": "Versato per errore, prova"})
    return r.json()["id"], tenant


@pytest.mark.integration
@richiede_pg
async def test_lease_una_replica_alla_volta(db_url, s3_ciclo) -> None:
    pid, _ = await _annullamento(db_url)
    adesso = datetime.now(UTC)
    # R05 (RLS, migrazione 20261023_040): fuori dall'ambito del lavoratore
    # (``giro``) le pratiche non si vedono: nessuna pratica presa per errore.
    assert await lv.prendi_pratica("replica-0", adesso) is None
    with ambito_lavoratore("test: lease del lavoratore"):
        prima = await lv.prendi_pratica("replica-1", adesso)
        assert prima is not None and str(prima["id"]) == pid
        assert await lv.prendi_pratica("replica-2", adesso) is None
        assert await lv.rinnova_lease(prima["id"], "replica-1", adesso)
        assert not await lv.rinnova_lease(prima["id"], "replica-2", adesso)
        # lease scaduto: un'altra replica lo prende e conta un tentativo fallito
        dopo = adesso + timedelta(seconds=settings.lavoratore_lease_secondi + 1)
        seconda = await lv.prendi_pratica("replica-2", dopo)
        assert seconda is not None and seconda["tentativi"] == prima["tentativi"] + 1
        assert not await lv.rinnova_lease(prima["id"], "replica-1", dopo)


@pytest.mark.integration
@richiede_pg
async def test_errore_temporaneo_rinvia_con_ritardo_e_riprende(db_url, s3_ciclo) -> None:
    bucket_worm(s3_ciclo)
    pid, _ = await _annullamento(db_url)
    # il PdV non è conservato: senza registro serve solo il WORM; storage giù → rinvio
    dip = dipendenze(db_url)
    originale = settings.s3_endpoint_url
    settings.s3_endpoint_url = "http://127.0.0.1:9"
    from libs.shared.storage import worm
    from libs.shared.storage.client import svuota_cache

    svuota_cache()
    worm._bucket_verificati.clear()
    try:
        esiti = await lavora(dip, giri=1)
    finally:
        settings.s3_endpoint_url = originale
        svuota_cache()
    assert esiti == ["RINVIATO"]
    riga = (await sql(db_url, "SELECT lavoro, tentativi, prossimo_tentativo, ultimo_errore, "
                              "lease_detentore FROM public.pratiche WHERE id = $1::uuid", pid))[0]
    assert riga["lavoro"] == "REGISTRO" and riga["tentativi"] == 1
    assert riga["lease_detentore"] is None
    assert riga["prossimo_tentativo"] > datetime.now(UTC) + timedelta(seconds=30)
    assert json.loads(riga["ultimo_errore"])["codice"]
    esecuzioni = await sql(db_url, "SELECT esito, errore FROM public.pratiche_esecuzioni "
                                   "WHERE pratica_id = $1::uuid", pid)
    assert [e["esito"] for e in esecuzioni] == ["RINVIATO"]
    # registro delle esecuzioni append-only anche per il proprietario
    with pytest.raises(Exception, match="append-only"):
        await sql(db_url, "DELETE FROM public.pratiche_esecuzioni WHERE pratica_id = $1::uuid",
                  pid)
    # alla scadenza del ritardo il lavoro riprende e si conclude
    await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() WHERE id = $1::uuid",
              pid)
    assert await lavora(dip) == ["COMPLETATO"]
    riga = (await sql(db_url, "SELECT lavoro FROM public.pratiche WHERE id = $1::uuid", pid))[0]
    assert riga["lavoro"] is None
