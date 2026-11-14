# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b4 — PdA reali per i test di distribution: righe vere su PostgreSQL
(pdv, aip, documenti, metadati_allegato5) e oggetti veri su S3 (server moto),
con le chiavi e le impronte che scrive aip-builder
(``{tenant}/{aip}/files/{ordine:04d}-{nome}``, manifesto con l'impronta
dell'IPdA in ``sincro_hash_sha256``).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.infra_reale import client_s3, crea_tenant_e_classe, sql

BUCKET_AIP = "aip-test"


@pytest.fixture
def s3(s3_server, monkeypatch):
    """S3 (moto) per distribution: bucket del PdA e bucket temporaneo dei PdD."""
    from app.config import settings

    for nome, valore in (("s3_endpoint_url", s3_server), ("s3_access_key", "test"),
                         ("s3_secret_key", "test"), ("s3_region", "us-east-1"),
                         ("s3_bucket_pdd", f"pdd-{uuid.uuid4().hex[:8]}")):
        monkeypatch.setattr(settings, nome, valore)
    client = client_s3(s3_server)
    for bucket in (BUCKET_AIP, settings.s3_bucket_pdd):
        try:
            client.create_bucket(Bucket=bucket)
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
    return client


async def crea_pda(url: str, s3: Any, *, tenant: uuid.UUID | None = None,
                   documenti: list[tuple[str, bytes]] | None = None,
                   stato: str = "CONSERVATO", oggetto: str = "Delibera di giunta",
                   metadati: dict[str, Any] | None = None,
                   chiave: dict[str, str] | None = None,
                   data_versamento: datetime | None = None,
                   regole_applicate: dict | None = None) -> dict[str, Any]:
    """Crea un PdA completo; restituisce identificativi, chiavi e contenuti."""
    if tenant is None:
        tenant, classe = await crea_tenant_e_classe(url)
    else:
        righe = await sql(url, "SELECT id FROM public.classi_documentali WHERE codice = 'fattura' "
                               "AND tenant_id IS NULL")
        classe = righe[0]["id"]
    documenti = documenti or [("doc.pdf", b"%PDF-1.7 prova\n%%EOF\n")]
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id, "
                   "metadata_json, regole_applicate) VALUES ($1, $2, $3, $4, $5, "
                   "CAST($6 AS jsonb), CAST($7 AS jsonb))",
              pdv, oggetto, classe, uuid.uuid4(), tenant,
              json.dumps({"chiave": chiave or {"numero": "42", "anno": "2026",
                                               "registro": "DELIBERE"}}),
              json.dumps(regole_applicate) if regole_applicate else None)
    voci = []
    for ordine, (nome, contenuto) in enumerate(documenti):
        k = f"{tenant}/{aip}/files/{ordine:04d}-{nome}"
        s3.put_object(Bucket=BUCKET_AIP, Key=k, Body=contenuto)
        voci.append({"nome_file": nome, "s3_key": k, "ordine": ordine,
                     "hash_sha256": hashlib.sha256(contenuto).hexdigest(),
                     "dimensione_bytes": len(contenuto), "contenuto": contenuto})
    ipda = f"<SInCRO aip='{aip}'/>".encode()
    chiave_ipda = f"{tenant}/{aip}/IndiceAIP_SInCRO.xml"
    s3.put_object(Bucket=BUCKET_AIP, Key=chiave_ipda, Body=ipda)
    manifesto = json.dumps({"aip_id": str(aip), "sincro_hash_sha256":
                            hashlib.sha256(ipda).hexdigest(), "versione_sincro": "2020",
                            "documenti": [{k: v for k, v in d.items() if k != "contenuto"}
                                          for d in voci]}).encode()
    chiave_manifesto = f"{tenant}/{aip}/manifest.json"
    s3.put_object(Bucket=BUCKET_AIP, Key=chiave_manifesto, Body=manifesto)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "sincro_xml_key, hash_sha256, dimensione_bytes, data_versamento) VALUES "
                   "($1, $2, $3, $4, CAST($5 AS public.aip_stato), $6, $7, $8, $9, $10, $11)",
              aip, pdv, tenant, f"urn:prova:{aip}", stato, chiave_manifesto, BUCKET_AIP,
              chiave_ipda, hashlib.sha256(manifesto).hexdigest(), len(manifesto),
              data_versamento or datetime.now(UTC))
    for d in voci:
        did = uuid.uuid4()
        d["documento_id"] = did
        await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                       "dimensione_bytes, hash_sha256, s3_key, ordine) VALUES "
                       "($1, $2, $3, $4, 'application/pdf', $5, $6, $7, $8)",
                  did, aip, d["nome_file"], "PRINCIPALE" if d["ordine"] == 0 else "ALLEGATO",
                  d["dimensione_bytes"], d["hash_sha256"], d["s3_key"], d["ordine"])
        if d["ordine"] == 0 and metadati is not False:
            m = {"identificativo_documento": "DEL-42", "tipologia_documentale": "delibera",
                 "soggetto_produttore_id": "80000000000",
                 "soggetto_produttore_nome": "Comune di Prova", "oggetto": oggetto,
                 "numero_registrazione": "PG/2026/42", "metadati_extra": {}, **(metadati or {})}
            await sql(url, "INSERT INTO public.metadati_allegato5 (documento_id, "
                           "identificativo_documento, modalita_formazione, tipologia_documentale, "
                           "data_documento, soggetto_produttore_id, soggetto_produttore_nome, "
                           "oggetto, formato, numero_registrazione, metadati_extra) VALUES "
                           "($1, $2, 'a', $3, DATE '2026-03-19', $4, $5, $6, 'application/pdf', "
                           "$7, CAST($8 AS jsonb))",
                      did, m["identificativo_documento"], m["tipologia_documentale"],
                      m["soggetto_produttore_id"], m["soggetto_produttore_nome"], m["oggetto"],
                      m["numero_registrazione"], json.dumps(m["metadati_extra"]))
    return {"tenant": tenant, "aip": aip, "pdv": pdv, "documenti": voci,
            "ipda": ipda, "manifesto": manifesto, "chiave_manifesto": chiave_manifesto}
