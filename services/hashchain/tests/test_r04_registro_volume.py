# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prova di volume del registro (R04, obiettivo 1 milione di documenti al giorno =
1.000-10.000 PdA al giorno secondo la politica dei PdA): 100.000 foglie (da 10 a
100 giorni di esercizio) aggiunte dal sequenziatore a lotti, checkpoint con
esportazione nel WORM, prove d'inclusione e di consistenza dai nodi salvati.

Misure riportate come proprietà del test (``record_property``) e, se è
impostata ``VD_TEMPI_VOLUME``, scritte in quel file JSON. Le soglie sono
larghe (la CI non è una macchina di riferimento): servono a fermare una
regressione di ordine di grandezza (per esempio una prova che torni a leggere
l'albero intero).
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.registro import archivio, checkpoint, prove, sequenziatore
from app.services import block_sealer

from libs.catena import albero, nota
from libs.catena.rfc6962 import (
    da_esadecimale,
    radice,
    verifica_consistenza,
    verifica_inclusione,
)
from libs.shared.db import get_async_session
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.supporto_registro import identita

pytestmark = [pytest.mark.integration, pytest.mark.slow, richiede_pg]

FOGLIE = int(os.environ.get("VD_VOLUME_FOGLIE", "100000"))
LOTTO = 1000


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 30)
    block_sealer._client.cache_clear()
    yield client_s3(s3_server)
    block_sealer._client.cache_clear()


async def _popola(url: str, n: int) -> None:
    """n PdV + PdA SU_STORAGE + albero K1 + coda, generati in SQL (pochi secondi)."""
    tenant, classe = await crea_tenant_e_classe(url)
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "SELECT gen_random_uuid(), 'volume', 'IN_BUILD', $1, gen_random_uuid(), $2 "
                   "FROM generate_series(1, $3)", classe, tenant, n)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) SELECT gen_random_uuid(), p.id, p.tenant_id, "
                   "'urn:volume:' || p.id, 'SU_STORAGE', 'k', 'b', "
                   "encode(sha256(p.id::text::bytea), 'hex') FROM public.pdv p "
                   "WHERE p.tenant_id = $1", tenant)
    await sql(url, "INSERT INTO public.catena_pda (aip_id, tenant_id, urn, radice, num_foglie, "
                   "foglie) SELECT a.id, a.tenant_id, a.urn, "
                   "encode(sha256(a.urn::bytea), 'hex'), 1, '[\"x\"]'::jsonb "
                   "FROM public.aip a WHERE a.tenant_id = $1", tenant)
    await sql(url, "INSERT INTO public.catena_coda (aip_id) SELECT id FROM public.aip "
                   "WHERE tenant_id = $1 ORDER BY urn", tenant)


async def test_volume_100000_foglie(db_url, s3, record_property):
    tempi: dict[str, float] = {"foglie": FOGLIE}
    t0 = time.perf_counter()
    await _popola(db_url, FOGLIE)
    tempi["popolamento_s"] = round(time.perf_counter() - t0, 2)

    # Sequenziatore: lotti da 1.000 foglie per transazione, lease rinnovato
    detentore = "volume"
    t0 = time.perf_counter()
    dimensione = 0
    while dimensione < FOGLIE:
        adesso = datetime.now(UTC)
        async with get_async_session() as session:
            assert await archivio.acquisisci_lease(session, detentore, adesso,
                                                   adesso + timedelta(seconds=60))
        esito = await sequenziatore.aggiungi_lotto(detentore=detentore, adesso=adesso,
                                                   limite=LOTTO)
        assert esito.aggiunte > 0
        dimensione = esito.dimensione
    durata = time.perf_counter() - t0
    tempi["sequenziatore_s"] = round(durata, 2)
    tempi["sequenziatore_foglie_al_secondo"] = round(FOGLIE / durata)

    # Checkpoint a 50.000 e a 100.000 (con esportazione delle foglie nel WORM)
    ident = identita()
    hash_foglie = [bytes(r["hash"]) for r in await sql(
        db_url, "SELECT hash FROM public.catena_registro ORDER BY indice")]
    assert len(hash_foglie) == FOGLIE
    meta = FOGLIE // 2
    t0 = time.perf_counter()
    async with get_async_session() as session:
        nodi = await archivio.leggi_nodi(session, albero.nodi_per_radice(meta))
    radice_meta = albero.radice_da_nodi(meta, nodi)
    tempi["radice_da_nodi_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    assert radice_meta == radice(hash_foglie[:meta])

    adesso = datetime.now(UTC)
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, detentore, adesso,
                                               adesso + timedelta(seconds=600))
    t0 = time.perf_counter()
    payload = await checkpoint.emetti_checkpoint(detentore=detentore, adesso=adesso,
                                                 identita=ident, segmento=10000)
    tempi["checkpoint_con_esportazione_worm_s"] = round(time.perf_counter() - t0, 2)
    assert payload is not None and payload["dimensione"] == FOGLIE
    cp = nota.analizza(payload["testo"])
    assert cp.radice == radice(hash_foglie)

    # Prove d'inclusione: 200 foglie a caso, O(log n) nodi ciascuna
    caso = random.Random(42)  # noqa: S311 — scelta delle foglie da provare, non crittografia
    indici = [caso.randrange(FOGLIE) for _ in range(200)]
    t0 = time.perf_counter()
    async with get_async_session() as session:
        for i in indici:
            prova = await prove.prova_nel_registro(session, i, FOGLIE)
            assert verifica_inclusione(hash_foglie[i], i, FOGLIE,
                                       da_esadecimale(prova["percorso"]), cp.radice)
    tempi["prova_inclusione_media_ms"] = round((time.perf_counter() - t0) * 1000 / len(indici), 2)
    tempi["nodi_letti_per_prova_max"] = max(len(albero.nodi_per_inclusione(i, FOGLIE))
                                            for i in indici)

    t0 = time.perf_counter()
    async with get_async_session() as session:
        cons = await prove.prova_consistenza(session, meta, FOGLIE)
    tempi["prova_consistenza_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    assert verifica_consistenza(meta, FOGLIE, radice_meta, cp.radice,
                                da_esadecimale(cons["percorso"]))

    for chiave, valore in tempi.items():
        record_property(chiave, valore)
    destinazione = os.environ.get("VD_TEMPI_VOLUME")
    if destinazione:
        with open(destinazione, "w", encoding="utf-8") as f:
            json.dump(tempi, f, indent=2)
    # Soglie larghe: 10.000 PdA/giorno richiedono 0,12 foglie/s di media
    assert tempi["sequenziatore_foglie_al_secondo"] > 500
    assert tempi["prova_inclusione_media_ms"] < 100
    assert tempi["nodi_letti_per_prova_max"] <= 2 * FOGLIE.bit_length()
