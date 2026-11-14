# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b4 — pacchetti di distribuzione completi (VD-C-077) e chiave API su
distribution (VD-C-099), su PostgreSQL e S3 (moto) veri.

Prima: COMPLETO e MIGRAZIONE davano chiavi S3 interne, ESTRATTO chiavi
inventate dal nome del file, ESIBIZIONE solo manifesto e indice, MIGRAZIONE
valori fissi e nessun audit, chiave API rifiutata (solo Bearer).
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.pda_reale import BUCKET_AIP, crea_pda, s3  # noqa: F401

pytestmark = richiede_pg

URL = "/api/v2/distribution/richiedi"
PEPPER = b"pepper-di-prova-distribution-b4-0123456789abcdef"


def _h(tenant, ruolo: str = "ADMIN_TENANT", user_id: str | None = None) -> dict:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=user_id or str(uuid.uuid4()), ruolo=ruolo)}


async def _client() -> AsyncClient:
    from app.routers.distribution import router
    from app.routers.migration import router as migrazione

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(router)
    app.include_router(migrazione)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _scarica(url: str) -> bytes:
    risposta = httpx.get(url)
    assert risposta.status_code == 200, risposta.text
    assert risposta.headers["content-disposition"].startswith("attachment")
    return risposta.content


# ── Consegna LINK ─────────────────────────────────────────────────────

@pytest.mark.parametrize("tipo", ["COMPLETO", "ESIBIZIONE"])
async def test_pdd_link_file_reali_impronte_verificate(db_url, s3, tipo) -> None:
    pda = await crea_pda(db_url, s3, documenti=[("principale.pdf", b"%PDF uno"),
                                                ("allegato.xml", b"<a/>")])
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": tipo,
                                    "motivo": "verifica interna", "url_expiry_seconds": 600},
                         headers=_h(pda["tenant"]))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["stato"] == "PRONTO" and corpo["consegna"] == "LINK"
    per_nome = {d["nome_file"]: d for d in corpo["documenti"]}
    assert set(per_nome) == {"principale.pdf", "allegato.xml", "IndiceAIP_SInCRO.xml",
                             "manifest.json"}
    attesi = {"principale.pdf": b"%PDF uno", "allegato.xml": b"<a/>",
              "IndiceAIP_SInCRO.xml": pda["ipda"], "manifest.json": pda["manifesto"]}
    for nome, contenuto in attesi.items():
        d = per_nome[nome]
        assert d["impronta_verificata"] and d["s3_key"] is None
        assert d["hash_sha256"] == hashlib.sha256(contenuto).hexdigest()
        assert _scarica(d["signed_url"]) == contenuto
    attesa = hashlib.sha256("\n".join(sorted(
        f"{n}:{hashlib.sha256(c).hexdigest()}" for n, c in attesi.items())).encode()).hexdigest()
    assert corpo["hash_pdd"] == attesa
    riga = (await sql(db_url, "SELECT stato, consegna, durata_link_secondi, scade_il "
                              "FROM public.pacchetti_distribuzione WHERE id = $1",
                      uuid.UUID(corpo["pdd_id"])))[0]
    assert riga["stato"] == "PRONTO" and riga["consegna"] == "LINK"
    assert 590 <= riga["durata_link_secondi"] <= 600


async def test_pdd_estratto_chiavi_reali_e_nomi_assenti(db_url, s3) -> None:
    pda = await crea_pda(db_url, s3, documenti=[("a.pdf", b"A"), ("b.pdf", b"B")])
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "ESTRATTO",
                                    "motivo": "estratto per ufficio", "file_richiesti": ["b.pdf"]},
                         headers=_h(pda["tenant"]))
        assert r.status_code == 200, r.text
        nomi = [d["nome_file"] for d in r.json()["documenti"]]
        assert nomi == ["b.pdf", "IndiceAIP_SInCRO.xml"]
        assert _scarica(r.json()["documenti"][0]["signed_url"]) == b"B"
        no = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "ESTRATTO",
                                     "motivo": "estratto per ufficio",
                                     "file_richiesti": ["b.pdf", "zzz.pdf"]},
                          headers=_h(pda["tenant"]))
    assert no.status_code == 422 and no.json()["codice"] == "DST-103"
    assert no.json()["dettagli"]["mancanti"] == ["zzz.pdf"]
    assert no.json()["dettagli"]["disponibili"] == ["a.pdf", "b.pdf"]


async def test_pdd_link_file_alterato_502_nessun_collegamento(db_url, s3) -> None:
    pda = await crea_pda(db_url, s3)
    s3.put_object(Bucket=BUCKET_AIP, Key=pda["documenti"][0]["s3_key"], Body=b"alterato")
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                    "motivo": "verifica interna"}, headers=_h(pda["tenant"]))
    assert r.status_code == 502 and r.json()["codice"] == "DST-502"
    assert "documenti" not in r.json() and "s3" not in r.text.lower()
    assert await sql(db_url, "SELECT 1 FROM public.notifications") == []


async def test_get_pdd_link_rigenera_collegamenti_e_scadenza(db_url, s3) -> None:
    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                    "motivo": "verifica interna"}, headers=_h(pda["tenant"]))
        pdd = r.json()["pdd_id"]
        stato = await c.get(f"/api/v2/distribution/pdd/{pdd}", headers=_h(pda["tenant"]))
        assert stato.status_code == 200 and stato.json()["stato"] == "PRONTO"
        assert _scarica(stato.json()["documenti"][0]["signed_url"]) == \
            pda["documenti"][0]["contenuto"]
        # altro ente: come inesistente
        altro = await c.get(f"/api/v2/distribution/pdd/{pdd}", headers=_h(uuid.uuid4()))
        assert altro.status_code == 404 and altro.json()["codice"] == "DST-404"
        await sql(db_url, "UPDATE public.pacchetti_distribuzione SET scade_il = now() - "
                          "interval '1 minute' WHERE id = $1", uuid.UUID(pdd))
        scaduto = await c.get(f"/api/v2/distribution/pdd/{pdd}", headers=_h(pda["tenant"]))
    assert scaduto.status_code == 410 and scaduto.json()["codice"] == "DST-410"


# ── Consegna ZIP fuori dalla richiesta ────────────────────────────────

async def test_pdd_zip_in_streaming_verificato_ed_evento(db_url, s3, monkeypatch) -> None:
    from app.config import settings
    from app.services.pdd_zip import elabora_un_pdd

    monkeypatch.setattr(settings, "pdd_sincrono_max_mb", 0)  # sopra soglia: ZIP automatico
    pda = await crea_pda(db_url, s3, documenti=[("grande.pdf", b"x" * 300_000),
                                                ("b.txt", b"ciao")])
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                    "motivo": "copia completa"}, headers=_h(pda["tenant"]))
        assert r.status_code == 200, r.text
        assert r.json()["stato"] == "IN_PREPARAZIONE" and r.json()["consegna"] == "ZIP"
        pdd = r.json()["pdd_id"]
        assert all(d["signed_url"] is None for d in r.json()["documenti"])
        attesa = await c.get(f"/api/v2/distribution/pdd/{pdd}", headers=_h(pda["tenant"]))
        assert attesa.json()["stato"] == "IN_PREPARAZIONE"

        assert await elabora_un_pdd() == "PRONTO"
        assert await elabora_un_pdd() is None
        pronto = await c.get(f"/api/v2/distribution/pdd/{pdd}", headers=_h(pda["tenant"]))
    corpo = pronto.json()
    assert corpo["stato"] == "PRONTO" and corpo["zip"]["url"]
    dati = _scarica(corpo["zip"]["url"])
    assert hashlib.sha256(dati).hexdigest() == corpo["zip"]["hash_sha256"]
    assert len(dati) == corpo["zip"]["dimensione_bytes"]
    archivio = zipfile.ZipFile(io.BytesIO(dati))
    assert archivio.read("documenti/0000-grande.pdf") == b"x" * 300_000
    assert archivio.read("documenti/0001-b.txt") == b"ciao"
    assert archivio.read("IndiceAIP_SInCRO.xml") == pda["ipda"]
    assert archivio.read("manifest.json") == pda["manifesto"]
    contenuto = json.loads(archivio.read("contenuto_pdd.json"))
    assert all(f["impronta_verificata"] for f in contenuto["file"])
    assert contenuto["hash_pdd"] == corpo["hash_pdd"]
    eventi = await sql(db_url, "SELECT topic, payload FROM public.outbox_eventi "
                               "WHERE topic = 'pdd.pronto'")
    assert len(eventi) == 1
    payload = json.loads(eventi[0]["payload"])
    assert payload["pdd_id"] == pdd and payload["tenant_id"] == str(pda["tenant"])
    assert payload["scade_il"]


async def test_pdd_zip_file_alterato_errore_definitivo(db_url, s3) -> None:
    from app.services.pdd_zip import elabora_un_pdd

    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                    "motivo": "copia completa", "consegna": "ZIP"},
                         headers=_h(pda["tenant"]))
        s3.put_object(Bucket=BUCKET_AIP, Key=pda["documenti"][0]["s3_key"], Body=b"alterato")
        assert await elabora_un_pdd() == "ERRORE"
        stato = await c.get(f"/api/v2/distribution/pdd/{r.json()['pdd_id']}",
                            headers=_h(pda["tenant"]))
    assert stato.json()["stato"] == "ERRORE"
    assert stato.json()["errore"]["codice"] == "DST-502"
    from app.config import settings

    assert s3.list_objects_v2(Bucket=settings.s3_bucket_pdd).get("KeyCount", 0) == 0


async def test_pdd_zip_errore_tecnico_ritentativi_poi_errore(db_url, s3, monkeypatch) -> None:
    from app.config import settings
    from app.services.pdd_zip import elabora_un_pdd

    pda = await crea_pda(db_url, s3)
    monkeypatch.setattr(settings, "pdd_ritardi_secondi", [0])
    monkeypatch.setattr(settings, "s3_bucket_pdd", "bucket-inesistente")
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                    "motivo": "copia completa", "consegna": "ZIP"},
                         headers=_h(pda["tenant"]))
    pdd = uuid.UUID(r.json()["pdd_id"])
    assert await elabora_un_pdd() == "RIPROVA"
    riga = (await sql(db_url, "SELECT stato, tentativi, lease_fino_al, ultimo_errore "
                              "FROM public.pacchetti_distribuzione WHERE id = $1", pdd))[0]
    assert riga["stato"] == "IN_PREPARAZIONE" and riga["tentativi"] == 1
    assert riga["lease_fino_al"] is None and riga["ultimo_errore"]
    assert await elabora_un_pdd() == "ERRORE"
    riga = (await sql(db_url, "SELECT stato, errore FROM public.pacchetti_distribuzione "
                              "WHERE id = $1", pdd))[0]
    assert riga["stato"] == "ERRORE" and json.loads(riga["errore"])["codice"] == "DST-503"


async def test_pdd_lease_un_solo_lavoratore(db_url, s3) -> None:
    """Un PdD preso in carico (lease valido) non è preso da un'altra replica."""
    from app.services.pdd_zip import _prendi_in_carico

    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                "motivo": "copia completa", "consegna": "ZIP"},
                     headers=_h(pda["tenant"]))
    adesso = datetime.now(UTC)
    assert await _prendi_in_carico(adesso) is not None
    assert await _prendi_in_carico(adesso) is None
    # lease scaduto (crash della replica): di nuovo disponibile
    assert await _prendi_in_carico(adesso + timedelta(hours=2)) is not None


async def test_pulisci_scaduti_elimina_zip(db_url, s3) -> None:
    from app.config import settings
    from app.services.pdd_zip import elabora_un_pdd, pulisci_scaduti

    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                    "motivo": "copia completa", "consegna": "ZIP"},
                         headers=_h(pda["tenant"]))
    await elabora_un_pdd()
    assert s3.list_objects_v2(Bucket=settings.s3_bucket_pdd)["KeyCount"] == 1
    assert await pulisci_scaduti(adesso=datetime.now(UTC) + timedelta(days=30)) == 1
    assert s3.list_objects_v2(Bucket=settings.s3_bucket_pdd)["KeyCount"] == 0
    stato = (await sql(db_url, "SELECT stato FROM public.pacchetti_distribuzione WHERE id = $1",
                       uuid.UUID(r.json()["pdd_id"])))[0]["stato"]
    assert stato == "SCADUTO"


# ── Migrazione ────────────────────────────────────────────────────────

async def test_migrazione_zip_con_dati_da_impostazione_e_audit(db_url, s3, monkeypatch) -> None:
    from app.config import settings
    from app.services.pdd_zip import elabora_un_pdd

    monkeypatch.setattr(settings, "conservatore_denominazione", "Conservatore di Prova S.p.A.")
    monkeypatch.setattr(settings, "conservatore_codice_fiscale", "01234567890")
    pda = await crea_pda(db_url, s3)
    ente_rsc = str(uuid.uuid4())
    rsc = str(uuid.uuid4())
    async with await _client() as c:
        r = await c.post(f"/api/v2/migration/export/{pda['aip']}", json={
            "conservatore_destinatario": "Nuovo Conservatore", "motivo": "fine del contratto",
            "richiedente_id": "ignorato"}, headers=_h(ente_rsc, "RSC_CONSERVATORE", rsc))
        assert r.status_code == 200, r.text
        job = r.json()["export_id"]
        assert r.json()["stato"] == "IN_PREPARAZIONE"
        assert await elabora_un_pdd() == "PRONTO"
        stato = await c.get(f"/api/v2/migration/status/{job}",
                            headers=_h(ente_rsc, "RSC_CONSERVATORE"))
    assert stato.status_code == 200 and stato.json()["stato"] == "PRONTO"
    archivio = zipfile.ZipFile(io.BytesIO(_scarica(stato.json()["zip"]["url"])))
    migrazione = json.loads(archivio.read("migrazione.json"))
    assert migrazione["conservatore_origine"] == {
        "denominazione": "Conservatore di Prova S.p.A.", "codice_fiscale": "01234567890"}
    assert migrazione["conservatore_destinatario"] == "Nuovo Conservatore"
    assert migrazione["versione_formato_ipda"] == "2020"
    assert migrazione["aip"]["urn"] == f"urn:prova:{pda['aip']}"
    audit = await sql(db_url, "SELECT user_id, tenant_id, dettagli FROM public.audit_global "
                              "WHERE evento = 'migrazione.richiesta'")
    assert len(audit) == 1 and str(audit[0]["user_id"]) == rsc
    assert audit[0]["tenant_id"] == pda["tenant"]
    assert json.loads(audit[0]["dettagli"])["pdd_id"] == job


async def test_migrazione_audit_non_scritto_nessuna_richiesta(db_url, s3) -> None:
    """REGOLE §15.3: se l'audit non si scrive, la richiesta non passa."""
    pda = await crea_pda(db_url, s3)
    # rinominare un registro protetto è rifiutato dall'event trigger (migrazione
    # 20261023_033): qui sospeso SOLO nella copia del database di questo test
    await sql(db_url, "ALTER EVENT TRIGGER vd_protezione_registri_stato DISABLE")
    await sql(db_url, "ALTER TABLE public.audit_global RENAME TO audit_altrove")
    async with await _client() as c:
        r = await c.post(f"/api/v2/migration/export/{pda['aip']}", json={
            "conservatore_destinatario": "Nuovo Conservatore", "motivo": "fine del contratto"},
            headers=_h(uuid.uuid4(), "SUPER_ADMIN"))
    assert r.status_code == 503 and r.json()["codice"] == "MIG-503"
    assert await sql(db_url, "SELECT 1 FROM public.pacchetti_distribuzione") == []


# ── Chiave API (VD-C-099) ─────────────────────────────────────────────

async def _chiave(url: str, tenant: uuid.UUID) -> str:
    from libs.shared.security.chiavi_api import genera_chiave_api

    utente = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, 'sys@example.org', 'Sistema', 'Protocollo', 'OPERATORE')",
              utente, tenant)
    generata = genera_chiave_api(PEPPER)
    await sql(url, "INSERT INTO public.api_keys (id, tenant_id, user_id, nome, key_hash, "
                   "key_prefix, permessi) VALUES ($1, $2, $3, 'Protocollo', $4, $5, "
                   "CAST('[\"read\"]' AS jsonb))",
              uuid.uuid4(), tenant, utente, generata.impronta, generata.identificativo)
    return generata.chiave


async def test_chiave_api_chiede_pdd_e_cerca(db_url, s3, monkeypatch) -> None:
    from app.auth import dipendenze
    from app.config import settings

    from libs.shared.security.revoca import svuota_cache_revoche
    from tests.opensearch_finto import OpenSearchFinto

    monkeypatch.setattr(dipendenze, "_PEPPER", PEPPER)
    svuota_cache_revoche()
    finto = OpenSearchFinto().avvia()
    monkeypatch.setattr(settings, "opensearch_url", finto.url)
    try:
        pda = await crea_pda(db_url, s3)
        chiave = await _chiave(db_url, pda["tenant"])
        async with await _client() as c:
            r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "ESIBIZIONE",
                                        "motivo": "richiesta del protocollo"},
                             headers={"X-Api-Key": chiave})
            assert r.status_code == 200, r.text
            cerca = await c.post("/api/v2/distribution/search", json={"query": "x"},
                                 headers={"X-Api-Key-Id": chiave.split("_")[1],
                                          "X-Api-Key-Secret": chiave})
            assert cerca.status_code == 200, cerca.text
            assert finto.ricerche[0][1] == {"routing": str(pda["tenant"])}
            # registro: richiedente = responsabile della chiave
            riga = (await sql(db_url, "SELECT destinatario, canale FROM public.notifications"))[0]
            assert riga["canale"] == "API" and uuid.UUID(riga["destinatario"])
            # revocata → 401; ente sospeso → 401
            await sql(db_url, "UPDATE public.api_keys SET attiva = false")
            no = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "ESIBIZIONE",
                                         "motivo": "richiesta del protocollo"},
                              headers={"X-Api-Key": chiave})
            assert no.status_code == 401 and no.json()["codice"] == "AUTH-001"
            senza = await c.post("/api/v2/distribution/search", json={})
            assert senza.status_code == 401 and senza.json()["codice"] == "AUTH-000"
    finally:
        finto.ferma()
        svuota_cache_revoche()


async def test_chiave_api_senza_pepper_503(db_url, monkeypatch) -> None:
    from app.auth import dipendenze

    monkeypatch.setattr(dipendenze, "_PEPPER", None)
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/search", json={},
                         headers={"X-Api-Key": "vd_abcdefghij_" + "a" * 43})
    assert r.status_code == 503 and r.json()["codice"] == "AUTH-030"
