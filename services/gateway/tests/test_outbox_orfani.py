# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Outbox transazionale e pulizia dei file orfani (VD-C-015).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.services.outbox import accoda_evento, pubblica_in_sospeso
from app.services.pulizia_orfani import pulisci_orfani_pdv

from libs.shared.db import get_async_session
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_accoda_evento_transazione_annullata_nessun_evento(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    with pytest.raises(RuntimeError):
        async with get_async_session() as session:
            await accoda_evento(session, topic="t", chiave="k", payload={"a": 1}, tenant_id=tenant)
            raise RuntimeError("errore dopo la scrittura")
    assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi") == []


async def test_pubblica_in_sospeso_errore_ferma_solo_la_stessa_chiave(db_url):
    """VD-C-076 (R04): al primo errore si ferma la CHIAVE (il suo gruppo), non
    tutto il relay: gli eventi successivi della stessa chiave non sorpassano
    quello fallito, le altre chiavi partono. Prima (R02) si fermava tutto."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with get_async_session() as session:
        for i, chiave in enumerate(["a", "b", "a"]):
            await accoda_evento(session, topic="t", chiave=chiave, payload={"i": i},
                                tenant_id=tenant)

    inviati: list[int] = []
    guasto = {"attivo": True}

    async def invia(topic, chiave, payload):
        if chiave == "a" and guasto["attivo"]:
            raise ConnectionError("broker giù")
        inviati.append(payload["i"])

    assert await pubblica_in_sospeso(invia) == 1          # solo «b»
    assert inviati == [1]
    righe = await sql(db_url, "SELECT chiave, pubblicato_il, tentativi, ultimo_errore "
                              "FROM public.outbox_eventi ORDER BY sequenza")
    assert [r["pubblicato_il"] is not None for r in righe] == [False, True, False]
    # R04 correzioni (rev4 rilievo 2): un errore di CONNESSIONE non è colpa
    # dell'evento e non conta come tentativo (con Kafka giù nessun evento si blocca)
    assert righe[0]["tentativi"] == 0 and "broker giù" in righe[0]["ultimo_errore"]
    assert righe[2]["tentativi"] == 0                     # mai tentato: niente sorpasso
    guasto["attivo"] = False
    assert await pubblica_in_sospeso(invia) == 2          # ripresa nell'ordine della chiave
    assert inviati[-2:] == [0, 2]


async def test_pulisci_orfani_elimina_solo_file_senza_pdv_e_vecchi(db_url, s3_server):
    s3 = client_s3(s3_server)
    bucket = f"pdv-{uuid.uuid4().hex[:8]}"
    s3.create_bucket(Bucket=bucket)
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = uuid.uuid4()
    await sql(
        db_url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                      "VALUES ($1, 'x', 'RICEVUTO', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    orfano = f"{tenant}/{uuid.uuid4()}/0000-a.pdf"
    valido = f"{tenant}/{pdv}/0000-a.pdf"
    for chiave in (orfano, valido, "senza-struttura.txt"):
        s3.put_object(Bucket=bucket, Key=chiave, Body=b"x")

    adesso = datetime.now(UTC)
    # Troppo recenti: un versamento potrebbe essere in corso → nessuna eliminazione
    assert await pulisci_orfani_pdv(s3=s3, bucket=bucket, adesso=adesso) == []
    futuro = adesso + timedelta(hours=2)
    assert await pulisci_orfani_pdv(s3=s3, bucket=bucket, adesso=futuro, prova=True) == [orfano]
    assert await pulisci_orfani_pdv(s3=s3, bucket=bucket, adesso=futuro) == [orfano]
    rimasti = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=bucket)["Contents"])
    assert rimasti == sorted([valido, "senza-struttura.txt"])


async def test_pulisci_orfani_staging_r03_e_multipart_aperti(db_url, s3_server):
    """R03: oggetti `versamenti/{tenant}/{pdv}/…` senza PdV eliminati; multipart aperti sotto
    `versamenti/` annullati; `caricamenti/` (sessioni di caricamento) mai toccato."""
    s3 = client_s3(s3_server)
    bucket = f"pdv-{uuid.uuid4().hex[:8]}"
    s3.create_bucket(Bucket=bucket)
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = uuid.uuid4()
    await sql(
        db_url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                      "VALUES ($1, 'x', 'RICEVUTO', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    orfano = f"versamenti/{tenant}/{uuid.uuid4()}/0000-a.pdf"
    valido = f"versamenti/{tenant}/{pdv}/0000-a.pdf"
    sessione = f"caricamenti/{tenant}/{uuid.uuid4()}/a.pdf"
    for chiave in (orfano, valido, sessione):
        s3.put_object(Bucket=bucket, Key=chiave, Body=b"x")
    s3.create_multipart_upload(Bucket=bucket, Key=f"versamenti/{tenant}/{uuid.uuid4()}/0000-b")
    s3.create_multipart_upload(Bucket=bucket, Key=sessione + ".parti")

    futuro = datetime.now(UTC) + timedelta(hours=2)
    assert await pulisci_orfani_pdv(s3=s3, bucket=bucket, adesso=futuro) == [orfano]
    rimasti = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=bucket)["Contents"])
    assert rimasti == sorted([valido, sessione])
    aperti = [u["Key"] for u in s3.list_multipart_uploads(Bucket=bucket).get("Uploads", [])]
    assert aperti == [sessione + ".parti"]
