# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f1 (REV-08) — pulizia dello staging dopo la conservazione.

Con PostgreSQL migrato e S3 (moto): i PdV versati davvero dal gateway (multipart
e sessione di caricamento), il PdA simulato come lo scrive aip-builder (riga
``aip`` SU_STORAGE, righe ``documenti`` con impronte e copia nel bucket del PdA).
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.config import settings
from app.services.pulizia_staging import pulisci_staging

from tests.infra_reale import (  # noqa: F401
    _asyncpg_dsn,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.supporto_canali import (  # noqa: F401 — fixture
    PDF,
    chiavi_staging,
    client_http,
    indice,
    s3,
    senza_lavoratore,
    utente_di,
)
from tests.test_caricamenti import _carica_completo

pytestmark = [richiede_pg, pytest.mark.integration]


async def _versa(c, numero: str, contenuto: bytes = PDF) -> str:
    ud = indice(numero=numero, contenuto=contenuto)
    r = await c.post("/api/v1/versamento/ud", data={"indice": json.dumps(ud)},
                     files=[("files", ("fattura.pdf", contenuto, "application/pdf"))],
                     headers={"Idempotency-Key": f"pulizia-{numero}-0001"})
    assert r.status_code == 202, r.text
    return r.json()["pdv_id"]


async def _pda(url: str, s3, pdv_id: str, *, quando: datetime, copia: bool = True,
               stato: str = "SU_STORAGE", altera_impronta: bool = False,
               bucket: str | None = None) -> str:
    """Il PdA come lo lascia aip-builder: copia nel bucket del PdA + righe aip e documenti."""
    [pdv] = await sql(url, "SELECT tenant_id, metadata_json FROM public.pdv WHERE id = $1",
                      uuid.UUID(pdv_id))
    bucket = bucket or f"aip-{uuid.uuid4().hex[:8]}"
    if bucket not in {b["Name"] for b in s3.list_buckets()["Buckets"]}:
        s3.create_bucket(Bucket=bucket)
    aip_id = uuid.uuid4()
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256, created_at) VALUES ($1, $2, $3, $4, CAST($5 AS public.aip_stato), "
                   "$6, $7, $8, $9)", aip_id, uuid.UUID(pdv_id), pdv["tenant_id"],
              f"urn:prova:{aip_id}", stato, f"{aip_id}/manifest.json", bucket, "0" * 64, quando)
    for ordine, d in enumerate(json.loads(pdv["metadata_json"])["docs_s3_keys"]):
        chiave = f"{pdv['tenant_id']}/{aip_id}/files/{ordine:04d}-{d['nome_file']}"
        if copia:
            corpo = s3.get_object(Bucket=d["s3_bucket"], Key=d["s3_key"])["Body"].read()
            s3.put_object(Bucket=bucket, Key=chiave, Body=corpo)
        impronta = "f" * 64 if altera_impronta else d["hash_sha256"]
        await sql(url, "INSERT INTO public.documenti (aip_id, nome_file, tipo, formato_mime, "
                       "dimensione_bytes, hash_sha256, s3_key, ordine) VALUES ($1, $2, "
                       "'PRINCIPALE', 'application/pdf', $3, $4, $5, $6)", aip_id,
                  d["nome_file"], d["size_bytes"], impronta, chiave, ordine)
    await sql(url, "UPDATE public.pdv SET stato = 'IN_BUILD' WHERE id = $1", uuid.UUID(pdv_id))
    return bucket


async def _staging_eliminato(url: str, pdv_id: str):
    [riga] = await sql(url, "SELECT staging_eliminato_il FROM public.pdv WHERE id = $1",
                       uuid.UUID(pdv_id))
    return riga["staging_eliminato_il"]


async def test_copie_verificate_eliminate_una_volta_sola(db_url, s3, utente_di,
                                                         senza_lavoratore):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        multipart = await _versa(c, "PS-1")
        cid = await _carica_completo(c, PDF + b"%sessione")
        r = await c.post("/api/v1/versamento/ud", json={
            "indice": indice("fattura.pdf", PDF + b"%sessione", numero="PS-2"),
            "file": {"fattura.pdf": cid}}, headers={"Idempotency-Key": "pulizia-sessione-0001"})
        assert r.status_code == 202, r.text
        per_riferimento = r.json()["pdv_id"]
    bucket_pda = await _pda(db_url, s3, multipart, quando=adesso - timedelta(hours=2))
    await _pda(db_url, s3, per_riferimento, quando=adesso - timedelta(hours=2), bucket=bucket_pda)
    assert len(chiavi_staging(s3)) == 2

    prova = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso,
                                  attesa=timedelta(hours=1), prova=True)
    assert sorted(prova.puliti) == sorted([multipart, per_riferimento])
    assert len(chiavi_staging(s3)) == 2          # in prova nulla si elimina

    esito = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso,
                                  attesa=timedelta(hours=1))
    assert sorted(esito.puliti) == sorted([multipart, per_riferimento]) and not esito.rinviati
    assert chiavi_staging(s3) == []
    assert any(k.startswith(f"caricamenti/{tenant}/") for k in esito.oggetti)
    assert await _staging_eliminato(db_url, multipart) is not None
    [sessione] = await sql(db_url, "SELECT storage_eliminato_il, pdv_id FROM public.caricamenti")
    assert sessione["storage_eliminato_il"] is not None and sessione["pdv_id"] is not None
    # le copie nel PdA non si toccano
    assert len(s3.list_objects_v2(Bucket=bucket_pda)["Contents"]) == 2

    # idempotente: il giro successivo non trova nulla
    di_nuovo = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv,
                                     adesso=adesso, attesa=timedelta(hours=1))
    assert di_nuovo.puliti == [] and di_nuovo.oggetti == []


async def test_senza_copia_verificata_o_troppo_presto_nulla_si_elimina(db_url, s3, utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        senza_copia = await _versa(c, "PN-1", PDF + b"1")
        impronta_diversa = await _versa(c, "PN-2", PDF + b"2")
        recente = await _versa(c, "PN-3", PDF + b"3")
        rifiutato = await _versa(c, "PN-4", PDF + b"4")
    vecchio = adesso - timedelta(hours=30)
    await _pda(db_url, s3, senza_copia, quando=vecchio, copia=False)
    await _pda(db_url, s3, impronta_diversa, quando=vecchio, altera_impronta=True)
    await _pda(db_url, s3, recente, quando=adesso - timedelta(minutes=5))
    await sql(db_url, "UPDATE public.pdv SET stato = 'ERRORE' WHERE id = $1",
              uuid.UUID(rifiutato))
    esito = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso)
    assert esito.puliti == []
    assert set(esito.rinviati) == {senza_copia, impronta_diversa}
    assert "non leggibile" in esito.rinviati[senza_copia]
    assert "nessuna copia" in esito.rinviati[impronta_diversa]
    assert len(chiavi_staging(s3)) == 4
    for pdv_id in (senza_copia, impronta_diversa, recente, rifiutato):
        assert await _staging_eliminato(db_url, pdv_id) is None


async def test_dimensione_della_copia_diversa_rinviata(db_url, s3, utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        pdv_id = await _versa(c, "PD-1")
    bucket = await _pda(db_url, s3, pdv_id, quando=adesso - timedelta(days=2))
    [doc] = await sql(db_url, "SELECT s3_key FROM public.documenti")
    s3.put_object(Bucket=bucket, Key=doc["s3_key"], Body=b"troncato")
    esito = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso)
    assert esito.rinviati == {pdv_id: "dimensione della copia nel PdA diversa"}
    assert len(chiavi_staging(s3)) == 1


async def test_a_lotti_e_un_solo_giro_alla_volta(db_url, s3, utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        ids = [await _versa(c, f"PL-{i}", PDF + str(i).encode()) for i in range(5)]
    bucket = None
    for pdv_id in ids:
        bucket = await _pda(db_url, s3, pdv_id, quando=adesso - timedelta(days=2), bucket=bucket)

    # un'altra replica sta già pulendo: questo giro salta
    conn = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        transazione = conn.transaction()
        await transazione.start()
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('pulizia_staging', 0))")
        occupato = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv,
                                         adesso=adesso)
        await transazione.rollback()
    finally:
        await conn.close()
    assert occupato.occupato and occupato.puliti == []

    # lotti da 2, al più 2 lotti per giro: 4 PdV ora, l'ultimo al giro dopo
    primo = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso,
                                  lotto=2, lotti=2)
    assert len(primo.puliti) == 4
    secondo = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv,
                                    adesso=adesso, lotto=2, lotti=2)
    assert len(secondo.puliti) == 1 and chiavi_staging(s3) == []


async def test_chiave_fuori_dallo_staging_dell_ente_mai_eliminata(db_url, s3, utente_di):
    """Difesa: una chiave registrata nel PdV fuori da ``versamenti/{ente}/`` o
    ``caricamenti/{ente}/`` (o in un altro bucket) non si elimina."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        pdv_id = await _versa(c, "PF-1")
    await _pda(db_url, s3, pdv_id, quando=adesso - timedelta(days=2))
    await sql(db_url, "UPDATE public.pdv SET metadata_json = jsonb_set(metadata_json, "
                      "'{docs_s3_keys,0,s3_key}', to_jsonb('versamenti/' || gen_random_uuid() "
                      "|| '/x/0000-fattura.pdf')) WHERE id = $1", uuid.UUID(pdv_id))
    esito = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso)
    assert "fuori dallo staging" in esito.rinviati[pdv_id]
    assert len(chiavi_staging(s3)) == 1
