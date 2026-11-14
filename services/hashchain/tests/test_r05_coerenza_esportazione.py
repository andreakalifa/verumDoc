# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05, VD-C-118: il controllo di coerenza verifica che ``E`` (foglie esportate nel
WORM secondo il database) corrisponda ai segmenti DAVVERO presenti nel WORM.

Prima il controllo elencava solo i segmenti da ``E`` in poi: un ``E`` più grande
del vero (database di un altro ambiente o bucket, esportazione registrata e mai
scritta) passava, e il checkpoint successivo pubblicava foglie mai esportate
(il WORM non avrebbe più permesso la ricostruzione).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.registro import archivio, archivio_worm, checkpoint, coerenza, sequenziatore
from app.registro.ciclo import CicloCatena, ConfigCiclo
from app.services import block_sealer

from libs.shared.db import get_async_session
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.supporto_registro import FirmaFinta, crea_pda, identita

ORA = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
IO = "detentore-118"


def test_divergenze_esportazione_pura():
    f = coerenza.divergenze_esportazione
    assert f(0, 0, []) == []
    assert f(0, 5, [(0, 3, b""), (3, 5, b"")]) == []
    assert f(3, 5, [(3, 5, b"")]) == []
    # segmento riscritto dopo un errore dell'aggiornamento di E: [0,3) e [0,5)
    assert f(0, 5, [(0, 3, b""), (0, 5, b"")]) == []
    assert "finiscono in 3" in f(0, 5, [(0, 3, b"")])[0]
    assert "inizia da 4" in f(0, 8, [(0, 3, b""), (4, 8, b"")])[0]
    assert "finiscono in 0" in f(0, 5, [])[0]


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


async def _cresci(url, tenant, classe, quanti: int, *, emetti: bool, ident) -> None:
    for _ in range(quanti):
        await crea_pda(url, tenant, classe, n_doc=1)
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, ORA, ORA + timedelta(hours=1))
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=ORA, limite=1000)
    if emetti:
        await checkpoint.emetti_checkpoint(detentore=IO, adesso=ORA, identita=ident, segmento=3)
        await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=ORA)


def _ciclo(ident) -> CicloCatena:
    tempo = {"t": 0.0}

    def orologio() -> float:
        tempo["t"] += 100.0
        return tempo["t"]

    return CicloCatena(config=ConfigCiclo(
        intervallo=1, checkpoint_secondi=60, controfirma_max_secondi=600, lease_secondi=30,
        lotto=10, completamento_lotto=10, segmento_worm=3, ripresa_secondi=60),
        identita=ident, servizio_firma=FirmaFinta(), testimone=None, detentore=IO,
        adesso=lambda: ORA, orologio=orologio)


@pytest.mark.integration
@richiede_pg
async def test_e_dichiarato_oltre_il_worm_blocca_il_registro(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita()
    await _cresci(db_url, tenant, classe, 5, emetti=True, ident=ident)   # E = 5 nel WORM
    await _cresci(db_url, tenant, classe, 3, emetti=False, ident=ident)  # D = 8
    assert (await coerenza.verifica()).coerente
    # Il database dichiara esportate anche le foglie 5-8, che nel WORM non ci sono
    await sql(db_url, "UPDATE public.catena_stato SET worm_foglie_esportate = 8")
    esito = await coerenza.verifica()
    assert not esito.coerente
    assert any("dichiara 8 foglie esportate" in d for d in esito.divergenze), esito.divergenze
    await _ciclo(ident).giro()
    blocchi = await sql(db_url, "SELECT causa FROM public.catena_blocchi_registro "
                                "WHERE chiuso_il IS NULL")
    assert [b["causa"] for b in blocchi] == ["REGISTRO_DIVERGENTE"]
    # nessun checkpoint a 8: avrebbe pubblicato foglie mai esportate nel WORM
    assert [r["dimensione"] for r in await sql(
        db_url, "SELECT dimensione FROM public.catena_checkpoint")] == [5]


@pytest.mark.integration
@richiede_pg
async def test_bucket_senza_i_segmenti_dichiarati_divergente(db_url, s3, monkeypatch):
    """Database che punta a un bucket (o ambiente) diverso da quello dei segmenti."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita()
    await _cresci(db_url, tenant, classe, 5, emetti=True, ident=ident)
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    block_sealer._client.cache_clear()
    s3.create_bucket(Bucket=settings.s3_bucket_aip, ObjectLockEnabledForBucket=True)
    esito = await coerenza.verifica()
    assert not esito.coerente
    assert any("finiscono in 0" in d for d in esito.divergenze), esito.divergenze


@pytest.mark.integration
@richiede_pg
async def test_segmento_riscritto_dopo_un_errore_resta_coerente(db_url, s3):
    """Un segmento [5,7) rimasto da un tentativo interrotto accanto a [5,8): coerente."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita()
    await _cresci(db_url, tenant, classe, 5, emetti=True, ident=ident)
    await _cresci(db_url, tenant, classe, 3, emetti=False, ident=ident)
    righe = await sql(db_url, "SELECT dati FROM public.catena_registro WHERE indice >= 5 "
                              "AND indice < 7 ORDER BY indice")
    await archivio_worm.scrivi(archivio_worm.chiave_segmento(5, 7),
                               "".join(r["dati"] + "\n" for r in righe).encode(),
                               "application/x-ndjson", ORA)
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, ORA, ORA + timedelta(hours=1))
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=ORA, identita=ident, segmento=10)
    esito = await coerenza.verifica()
    assert esito.coerente, esito.come_dict()
