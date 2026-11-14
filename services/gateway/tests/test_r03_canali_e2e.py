# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
End-to-end dei canali R03 (prova d'insieme del coordinatore, R03 i1).

Percorso reale, con PostgreSQL migrato e S3 (moto), servizi veri:
  1. sessione di caricamento a più parti (parti in parallelo e fuori ordine),
     completamento e verifica del file (lavoratore del gateway, un giro);
  2. versamento JSON per riferimento alla sessione → 202; lo stesso documento
     rinviato con la stessa Idempotency-Key → 200 sullo stesso PdV, nessun
     duplicato;
  3. validation → aip-builder (impronte a blocchi, copia server-side nello
     storage del PdA) → hashchain → PdV CONSERVATO;
  4. notification: gli eventi pubblicati dai produttori (outbox del gateway e
     uscite di validation e aip-builder) diventano righe di ``eventi_cliente`` e
     si leggono con ``GET /api/v1/eventi``, senza nomi di file, chiavi di
     storage o link;
  5. distribution: il lavoratore d'indicizzazione consuma la coda scritta da
     aip-builder verso un OpenSearch finto (vero server HTTP), la ricerca trova
     il documento, e un PdD COMPLETO in consegna LINK porta al file vero (stessi
     byte versati).

Come ``test_e2e_pipeline.py`` ogni servizio diverso dal gateway gira nel proprio
processo (pacchetti ``app`` distinti); gli eventi Kafka si passano a mano fra i
passi: il broker non è parte di questa prova (i cicli Kafka hanno i loro test).
Non è in-process: OpenSearch vero (si usa il finto HTTP di distribution) e la
consegna webhook verso un destinatario esterno (provata nei test di b3).
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from app.auth.dependencies import get_current_user
from app.config import settings
from app.main import app
from app.services import caricamenti as car
from httpx import ASGITransport, AsyncClient

from libs.catena import nota
from tests.identita_prova import dipendenza_utente
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.supporto_canali import MB, MINIMI, pdf_di
from tests.test_e2e_pipeline import _AIP_BUILDER, _HASHCHAIN, _VALIDATION

pytestmark = [pytest.mark.integration, pytest.mark.e2e, richiede_pg]

RADICE = Path(__file__).resolve().parents[3]

_NOTIFICATION = """
import asyncio, json, os, sys
from libs.shared.db import init_db
from app.application.eventi_consumer import elabora_evento_cliente
from app.config import settings
async def main():
    await init_db(os.environ["DATABASE_URL"])
    ingresso = json.load(open(sys.argv[1]))
    for m in ingresso["messaggi"]:
        for _ in range(2):  # riconsegna Kafka: nessuna riga in più
            await elabora_evento_cliente(m["topic"], m["valore"], mappa_topic=settings.eventi_topic)
    # R03 f2 (REV-13): l'elenco mostra gli eventi dopo il passo di pubblicazione
    # (posizione assegnata dopo il commit); nel servizio lo fa il lavoratore
    from app.infrastructure.repo_eventi import pubblica_eventi
    await pubblica_eventi()
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.autenticazione import lettore_eventi
    from app.api.eventi import router
    from app.domain.identita import Identita
    from libs.shared.errori import installa_gestori_errori
    prova = FastAPI(); installa_gestori_errori(prova); prova.include_router(router)
    prova.dependency_overrides[lettore_eventi] = lambda: Identita(
        tenant_id=ingresso["tenant_id"], user_id=None, ruolo="OPERATORE", metodo="api_key")
    async with AsyncClient(transport=ASGITransport(app=prova), base_url="http://n") as c:
        r = await c.get("/api/v1/eventi", params={"limite": 50})
    json.dump({"stato": r.status_code, "corpo": r.json()}, open(sys.argv[2], "w"))
asyncio.run(main())
"""

_DISTRIBUTION = """
import asyncio, json, os, sys, uuid
from libs.shared.db import init_db
async def main():
    await init_db(os.environ["DATABASE_URL"])
    ingresso = json.load(open(sys.argv[1]))
    from app.services.indicizzazione import elabora_lotto
    from app.services.search_index import ensure_index
    await ensure_index()
    lotti = [await elabora_lotto(), await elabora_lotto()]
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.auth.jwt_handler import create_access_token
    from app.routers.distribution import router
    from libs.shared.errori import installa_gestori_errori
    prova = FastAPI(); installa_gestori_errori(prova); prova.include_router(router)
    def h(tenant):
        return {"Authorization": "Bearer " + create_access_token(
            tenant_id=tenant, user_id=str(uuid.uuid4()), ruolo="ADMIN_TENANT")}
    async with AsyncClient(transport=ASGITransport(app=prova), base_url="http://d") as c:
        ricerca = await c.post("/api/v2/distribution/search",
                               json={"query": ingresso["testo"]}, headers=h(ingresso["tenant_id"]))
        altro_ente = await c.post("/api/v2/distribution/search",
                                  json={"query": ingresso["testo"]},
                                  headers=h(ingresso["altro_tenant_id"]))
        pdd = await c.post("/api/v2/distribution/richiedi", json={
            "aip_id": ingresso["aip_id"], "tipo": "COMPLETO", "consegna": "LINK",
            "motivo": "prova end-to-end dei canali", "url_expiry_seconds": 600},
            headers=h(ingresso["tenant_id"]))
    json.dump({"lotti": lotti,
               "ricerca": {"stato": ricerca.status_code, "corpo": ricerca.json()},
               "altro_ente": {"stato": altro_ente.status_code, "corpo": altro_ente.json()},
               "pdd": {"stato": pdd.status_code, "corpo": pdd.json()}},
              open(sys.argv[2], "w"))
asyncio.run(main())
"""


def _esegui(servizio: str, codice: str, ingresso: dict, tmp: Path, ambiente: dict) -> object:
    file_in, file_out = tmp / f"{servizio}-in.json", tmp / f"{servizio}-out.json"
    file_in.write_text(json.dumps(ingresso))
    cartella = RADICE / "services" / servizio
    env = {**os.environ, **ambiente,
           "PYTHONPATH": os.pathsep.join([str(RADICE), str(cartella)])}
    esito = subprocess.run(  # noqa: S603 — interprete e script del test, nessun input esterno
        [sys.executable, "-c", codice, str(file_in), str(file_out)],
        cwd=cartella, env=env, capture_output=True, text=True, timeout=180,
    )
    assert esito.returncode == 0, f"{servizio}: {esito.stderr[-3000:]}"
    return json.loads(file_out.read_text())


def _opensearch_finto():
    """OpenSearch finto di distribution (vero server HTTP, solo libreria standard)."""
    percorso = RADICE / "services/distribution/tests/opensearch_finto.py"
    spec = importlib.util.spec_from_file_location("_opensearch_finto_e2e", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo.OpenSearchFinto().avvia()


async def _outbox(url: str, topic: str, chiave: str | None = None) -> list[dict]:
    righe = await sql(url, "SELECT chiave, payload FROM public.outbox_eventi WHERE topic = $1 "
                           "ORDER BY sequenza", topic)
    return [json.loads(r["payload"]) for r in righe if chiave is None or r["chiave"] == chiave]


async def test_canali_r03_sessione_per_riferimento_fino_a_pdd(db_url, s3_server, monkeypatch,
                                                              tmp_path):
    bucket_pdv, bucket_aip = f"pdv-{uuid.uuid4().hex[:8]}", f"aip-{uuid.uuid4().hex[:8]}"
    bucket_pdd = f"pdd-{uuid.uuid4().hex[:8]}"
    for nome, valore in {"s3_endpoint_url": s3_server, "s3_access_key": "test",
                         "s3_secret_key": "test", "s3_region": "us-east-1",
                         "s3_bucket_pdv": bucket_pdv, "caricamenti_dimensione_parte_mb": 5,
                         "caricamenti_lavoratore_attivo": False}.items():
        monkeypatch.setattr(settings, nome, valore)
    s3 = httpx.Client()  # solo per scaricare dai link firmati
    client = client_s3(s3_server)
    for bucket in (bucket_pdv, bucket_pdd):
        client.create_bucket(Bucket=bucket)
    opensearch = _opensearch_finto()
    ambiente = {
        "DATABASE_URL": db_url, "ENV": "development", "S3_ENDPOINT_URL": s3_server,
        "S3_ACCESS_KEY": "test", "S3_SECRET_KEY": "test", "S3_REGION": "us-east-1",
        "S3_BUCKET_AIP": bucket_aip, "S3_BUCKET_PDD": bucket_pdd,
        "OPENSEARCH_URL": opensearch.url,
        # R04: chiave del registro delle impronte e retention degli oggetti del registro
        "CATENA_CHIAVE_PRIVATA": nota.genera_chiave("verumdoc-registro")[0],
        "RETENTION_PERMANENTE_ORIZZONTE_ANNI": "30", "SIGNATURE_SERVICE_TOKEN": "",
        "TESTIMONE_URL": "",
    }
    tenant, _ = await crea_tenant_e_classe(db_url)
    altro_tenant, _ = await crea_tenant_e_classe(db_url)
    app.dependency_overrides[get_current_user] = dipendenza_utente({
        "tenant_id": str(tenant), "user_id": str(uuid.uuid4()), "ruolo": "OPERATORE",
        "scopes": ["versamento:ud", "read"], "auth_method": "api_key"})
    contenuto = pdf_di(11 * MB, seme=97)  # 3 parti da 5 MiB (l'ultima più corta)
    indice = {
        "classe_documentale": "fattura",
        "chiave": {"numero": "FT-E2E-R03", "anno": "2026", "registro": "FATTURE"},
        "documenti": [{
            "id": "doc-1", "tipo": "PRINCIPALE", "nome_file": "fattura-grande.pdf",
            "formato": "application/pdf",
            "hash": {"algoritmo": "SHA-256", "impronta": hashlib.sha256(contenuto).hexdigest()},
            "parametri": {**MINIMI, "oggetto": "Fornitura Zanzibar prova canali"},
        }],
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gw") as c:
            # ── 1. Sessione di caricamento a più parti ───────────────────
            r = await c.post("/api/v1/caricamenti", json={
                "nome_file": "fattura-grande.pdf", "dimensione": len(contenuto),
                "impronta": {"algoritmo": "SHA-256",
                             "valore": hashlib.sha256(contenuto).hexdigest()}},
                headers={"Idempotency-Key": "e2e-r03-sessione-01"})
            assert r.status_code == 201, r.text
            sessione = r.json()
            cid, parte = sessione["caricamento_id"], sessione["dimensione_parte"]
            assert sessione["numero_parti"] == 3
            fette = [contenuto[i:i + parte] for i in range(0, len(contenuto), parte)]
            risposte = await asyncio.gather(*(
                c.put(f"/api/v1/caricamenti/{cid}/parti/{n}", content=fette[n - 1],
                      headers={"Content-Type": "application/octet-stream",
                               "X-Impronta-Parte": "sha256="
                               + hashlib.sha256(fette[n - 1]).hexdigest()})
                for n in (3, 1, 2)))
            assert [x.status_code for x in risposte] == [200, 200, 200]
            assert (await c.post(f"/api/v1/caricamenti/{cid}/completa")).status_code == 202
            assert await car.verifica_in_sospeso() == 1
            stato_sessione = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
            assert stato_sessione["stato"] == "COMPLETO"
            assert stato_sessione["impronte"]["SHA-256"] == hashlib.sha256(contenuto).hexdigest()

            # ── 2. Versamento per riferimento; stesso documento, stessa chiave ──
            corpo = {"indice": indice, "file": {"fattura-grande.pdf": cid}}
            chiave = {"Idempotency-Key": "e2e-r03-versamento-01"}
            primo = await c.post("/api/v1/versamento/ud", json=corpo, headers=chiave)
            assert primo.status_code == 202, primo.text
            pdv_id = primo.json()["pdv_id"]
            ripetuto = await c.post("/api/v1/versamento/ud", json=corpo, headers=chiave)
            assert ripetuto.status_code == 200, ripetuto.text
            assert ripetuto.json()["pdv_id"] == pdv_id
            assert ripetuto.headers["Idempotent-Replayed"] == "true"
            assert len(await sql(db_url, "SELECT 1 FROM public.pdv WHERE tenant_id = $1",
                                 tenant)) == 1
            [ricevuto] = await _outbox(db_url, "pdv.received", pdv_id)
            assert ricevuto["canale"] == "REST"
            assert ricevuto["docs_s3_keys"][0]["caricamento_id"] == cid

            # ── 3. Verifica → PdA → catena: CONSERVATO ───────────────────
            verificato = _esegui("validation", _VALIDATION, ricevuto, tmp_path, ambiente)
            assert verificato[0]["topic"] == "pdv.validated", verificato
            costruito = _esegui("aip-builder", _AIP_BUILDER, verificato[0]["valore"], tmp_path,
                                ambiente)
            # R04 (VD-C-097): l'aip-builder non pubblica eventi; «aip.conserved» lo
            # scrive la hashchain in outbox quando il PdA è coperto da un checkpoint
            aip_id = costruito["aip_id"]
            _esegui("hashchain", _HASHCHAIN, {}, tmp_path, ambiente)
            [conservato] = await _outbox(db_url, "aip.conserved", aip_id)
            stato = (await c.get(f"/api/v1/versamento/{pdv_id}")).json()
            assert stato["stato"] == "CONSERVATO"
            # il file nel PdA è quello caricato (copia server-side, stessi byte)
            [doc] = await sql(db_url, "SELECT s3_key, hash_sha256, dimensione_bytes "
                                      "FROM public.documenti WHERE aip_id = $1",
                              uuid.UUID(aip_id))
            assert doc["hash_sha256"] == hashlib.sha256(contenuto).hexdigest()
            assert doc["dimensione_bytes"] == len(contenuto)

        # ── 4. Eventi per il cliente ─────────────────────────────────────
        [completato] = await _outbox(db_url, "caricamento.completato", cid)
        messaggi = [{"topic": "caricamento.completato", "valore": completato},
                    {"topic": "pdv.received", "valore": ricevuto},
                    {"topic": verificato[0]["topic"], "valore": verificato[0]["valore"]},
                    {"topic": "aip.conserved", "valore": conservato}]
        eventi = _esegui("notification", _NOTIFICATION,
                         {"messaggi": messaggi, "tenant_id": str(tenant)}, tmp_path,
                         {**ambiente, "ENV": "test"})  # chiavi JWT effimere del processo
        assert eventi["stato"] == 200, eventi
        per_tipo = {e["tipo"]: e for e in eventi["corpo"]["eventi"]}
        assert list(per_tipo) == ["caricamento.completato", "versamento.ricevuto",
                                  "versamento.validato", "versamento.conservato"]
        assert per_tipo["caricamento.completato"]["risorsa"] == {"tipo": "caricamento", "id": cid}
        for tipo in ("versamento.ricevuto", "versamento.validato", "versamento.conservato"):
            assert per_tipo[tipo]["risorsa"] == {"tipo": "versamento", "id": pdv_id}
        assert per_tipo["versamento.conservato"]["dati"]["aip_id"] == aip_id
        testo = json.dumps(eventi["corpo"])
        for vietato in ("fattura-grande.pdf", "caricamenti/", "versamenti/", bucket_aip,
                        "http://", "https://", "X-Amz"):
            assert vietato not in testo, vietato
        righe = await sql(db_url, "SELECT tipo FROM public.eventi_cliente WHERE tenant_id = $1",
                          tenant)
        assert len(righe) == 4  # riconsegnati due volte, registrati una

        # ── 5. Ricerca e PdD COMPLETO con link al file vero ──────────────
        uscita = _esegui("distribution", _DISTRIBUTION, {
            "tenant_id": str(tenant), "altro_tenant_id": str(altro_tenant),
            "aip_id": aip_id, "testo": "Zanzibar"}, tmp_path, {**ambiente, "ENV": "test"})
        assert uscita["lotti"][0]["indicizzati"] == 1, uscita
        assert uscita["ricerca"]["stato"] == 200, uscita["ricerca"]
        trovati = uscita["ricerca"]["corpo"]["items"]
        assert [t["aip_id"] for t in trovati] == [aip_id]
        assert uscita["altro_ente"]["stato"] == 200
        assert uscita["altro_ente"]["corpo"]["items"] == []
        assert uscita["pdd"]["stato"] == 200, uscita["pdd"]
        pdd = uscita["pdd"]["corpo"]
        assert pdd["stato"] == "PRONTO" and pdd["consegna"] == "LINK"
        per_nome = {d["nome_file"]: d for d in pdd["documenti"]}
        assert {"fattura-grande.pdf", "IndiceAIP_SInCRO.xml", "manifest.json"} <= set(per_nome)
        file_pdd = per_nome["fattura-grande.pdf"]
        assert file_pdd["impronta_verificata"] is True and file_pdd["s3_key"] is None
        scaricato = s3.get(file_pdd["signed_url"])
        assert scaricato.status_code == 200
        assert scaricato.headers["content-disposition"].startswith("attachment")
        assert hashlib.sha256(scaricato.content).hexdigest() == \
            hashlib.sha256(contenuto).hexdigest()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        opensearch.ferma()
        s3.close()
