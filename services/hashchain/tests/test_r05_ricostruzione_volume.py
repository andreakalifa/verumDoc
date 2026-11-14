# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prova di volume della ricostruzione dal WORM (R05, VD-C-111): ``VD_VOLUME_FOGLIE``
foglie (predefinite 100.000) con un checkpoint ogni 10.000 e segmenti da
10.000, registro perso (database tornato a prima del sequenziamento), poi
``ricostruisci`` in prova e per davvero a lotti da 10.000.

Misure (``record_property`` e, con ``VD_TEMPI_VOLUME_RICOSTRUZIONE``, un file
JSON): foglie al secondo per lettura, memoria massima del processo durante la
ricostruzione. La memoria non deve crescere con il numero di foglie (frontiera
O(log n), un lotto alla volta, finestra di letture limitata): è ciò che rende
praticabili 10 milioni di foglie, che costano in tempo ~ 10.000.000 / (foglie al
secondo) per lettura.
"""
from __future__ import annotations

import json
import os
import resource
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.config import settings
from app.registro import archivio, checkpoint, coerenza, ricostruzione, sequenziatore
from app.registro.chiavi import firmatario_registro, origine_registro
from app.services import block_sealer

from libs.shared.db import close_db, get_async_session, init_db
from tests.infra_reale import (  # noqa: F401 — fixture
    _asyncpg_dsn,
    _con_database,
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.test_r04_registro_volume import _popola
from tests.test_r05_ricostruzione_worm import testimone  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, pytest.mark.slow, richiede_pg]

FOGLIE = int(os.environ.get("VD_VOLUME_FOGLIE", "100000"))
OGNI = 10000


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


def _rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def test_volume_ricostruzione_dal_worm(db_url, s3, record_property, testimone):  # noqa: F811
    """Ogni checkpoint è controfirmato dal testimone: la ricostruzione accetta solo
    foglie coperte da checkpoint firmati e controfirmati (revisione R05, R5)."""
    tempi: dict[str, float] = {"foglie": FOGLIE}
    await _popola(db_url, FOGLIE)
    # «backup»: PdA presenti, registro vuoto
    await close_db()
    backup = f"vdt_bk_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
    await admin.execute(f'CREATE DATABASE "{backup}" TEMPLATE "{db_url.rsplit("/", 1)[1]}"')
    await admin.close()
    await init_db(db_url)
    try:
        ident = checkpoint.Identita(firmatario_registro(settings), origine_registro(settings),
                                    True)
        dimensione = 0
        while dimensione < FOGLIE:
            adesso = datetime.now(UTC)
            async with get_async_session() as session:
                assert await archivio.acquisisci_lease(session, "volume", adesso,
                                                       adesso + timedelta(seconds=600))
            esito = await sequenziatore.aggiungi_lotto(detentore="volume", adesso=adesso,
                                                       limite=OGNI)
            dimensione = esito.dimensione
            await checkpoint.emetti_checkpoint(detentore="volume", adesso=adesso,
                                               identita=ident, segmento=OGNI)
            await checkpoint.invia_al_testimone(testimone.client(), detentore="volume",
                                                adesso=adesso)
        radici = [tuple(r) for r in await sql(db_url, "SELECT dimensione, radice FROM "
                                                      "public.catena_checkpoint ORDER BY 1")]
        assert len(radici) == FOGLIE // OGNI

        await close_db()
        await init_db(_con_database(db_url, backup))
        rss_prima = _rss_mib()
        t0 = time.perf_counter()
        r = await ricostruzione.ricostruisci(fino_a=FOGLIE, prova=True)
        tempi["prova_s"] = round(time.perf_counter() - t0, 2)
        assert r.riuscito, r.problemi
        assert r.checkpoint_verificati == FOGLIE // OGNI
        t0 = time.perf_counter()
        r = await ricostruzione.ricostruisci(fino_a=FOGLIE, prova=False, operatore="volume",
                                             motivo="prova di volume della ricostruzione")
        tempi["ricostruzione_s"] = round(time.perf_counter() - t0, 2)
        assert r.riuscito, r.problemi
        tempi["foglie_al_secondo_prova"] = round(FOGLIE / tempi["prova_s"])
        tempi["foglie_al_secondo_ricostruzione"] = round(FOGLIE / tempi["ricostruzione_s"])
        tempi["memoria_massima_mib"] = round(_rss_mib(), 1)
        tempi["memoria_aggiunta_mib"] = round(_rss_mib() - rss_prima, 1)
        tempi["stima_10_milioni_ore"] = round(
            10_000_000 / tempi["foglie_al_secondo_ricostruzione"] / 3600, 2)
        url_backup = _con_database(db_url, backup)
        assert [tuple(x) for x in await sql(url_backup, "SELECT dimensione, radice FROM "
                                                        "public.catena_checkpoint ORDER BY 1")] \
            == radici
        assert (await coerenza.verifica()).coerente
    finally:
        await close_db()
        admin = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
        await admin.execute(f'DROP DATABASE IF EXISTS "{backup}" WITH (FORCE)')
        await admin.close()
        await init_db(db_url)
    for chiave, valore in tempi.items():
        record_property(chiave, valore)
    destinazione = os.environ.get("VD_TEMPI_VOLUME_RICOSTRUZIONE")
    if destinazione:
        with open(destinazione, "w", encoding="utf-8") as f:
            json.dump(tempi, f, indent=2)
    # Soglie larghe: fermano una regressione d'ordine di grandezza
    assert tempi["foglie_al_secondo_ricostruzione"] > 500
    assert tempi["memoria_aggiunta_mib"] < 400
