# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Terzo giro delle correzioni R04 sul registro delle impronte (terza verifica):

  - T4: dopo un ripristino del database da backup il registro NON riordina foglie
    già pubblicate. All'avvio del ciclo (e quando si toglie
    ``CATENA_REGISTRO_SOSPESO``) confronta la propria dimensione ``D`` con l'ultimo
    checkpoint nel WORM (``N``), la fine dei segmenti esportati (``X``) e la
    dimensione controfirmata dal testimone (``T``). Se ``D < max(N, X, T)`` o i
    contenuti già pubblicati divergono, blocco PERSISTENTE e nessuna scrittura.
    Lo scenario del revisore: checkpoint 4 pubblicato e non ancora controfirmato
    (``T = 2 < N = 4``), database tornato a 2, un PdA nuovo in coda: prima il
    sequenziatore lo metteva in posizione 2 e firmava un checkpoint inconsistente
    con il 4 (biforcazione firmata); ora resta fermo con l'allarme;
  - T4: una scrittura WORM con lo stesso nome e contenuto diverso apre un blocco
    persistente invece di ritentare a ogni giro;
  - T5: registro sospeso = metrica, log a ogni giro, allarmi che continuano.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.config import settings
from app.registro import archivio_worm, checkpoint, operatore, sequenziatore
from app.registro.ciclo import CicloCatena, ConfigCiclo
from app.registro.metriche import metriche
from app.services import block_sealer
from structlog.testing import capture_logs

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
from tests.supporto_registro import FintoTestimone, FirmaFinta, crea_pda, identita

ORA = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
IO = "detentore-terzo-giro"
CONFIG = {"intervallo": 1, "checkpoint_secondi": 60, "controfirma_max_secondi": 600,
          "lease_secondi": 30, "lotto": 10, "completamento_lotto": 10, "segmento_worm": 10,
          "ripresa_secondi": 60, "sonda_testimone_secondi": 60}

pytestmark = [pytest.mark.integration, richiede_pg]


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


@pytest.fixture
async def copie(db_url):  # noqa: F811
    """Istantanee del database di prova («backup»), eliminate alla fine."""
    nomi: list[str] = []

    async def istantanea() -> str:
        await close_db()
        nome = f"vdt_bk_{uuid.uuid4().hex[:10]}"
        admin = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
        try:
            await admin.execute(f'CREATE DATABASE "{nome}" TEMPLATE "{db_url.rsplit("/", 1)[1]}"')
        finally:
            await admin.close()
        nomi.append(nome)
        await init_db(db_url)
        return _con_database(db_url, nome)

    yield istantanea
    await close_db()
    admin = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
    try:
        for nome in nomi:
            await admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
    finally:
        await admin.close()


async def _usa(url: str) -> None:
    """Il servizio riparte su un altro database (ripristino)."""
    await close_db()
    await init_db(url)


async def _cresci(url, tenant, classe, quanti: int, ident, adesso: datetime) -> None:
    for _ in range(quanti):
        await crea_pda(url, tenant, classe, n_doc=1)
    async with get_async_session() as session:
        from app.registro import archivio

        assert await archivio.acquisisci_lease(session, IO, adesso, adesso + timedelta(hours=1))
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=adesso, limite=1000)
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=adesso, identita=ident, segmento=10)
    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=adesso)


def _ciclo(ident, testimone, *, adesso: datetime, sospeso: bool = False) -> CicloCatena:
    tempo = {"t": 0.0}

    def orologio() -> float:
        tempo["t"] += 100.0  # ogni giro può emettere un checkpoint
        return tempo["t"]

    return CicloCatena(config=ConfigCiclo(**CONFIG, sospeso=sospeso), identita=ident,
                       servizio_firma=FirmaFinta(), testimone=testimone, detentore=IO,
                       adesso=lambda: adesso, orologio=orologio)


async def _stato(url: str) -> int:
    return int((await sql(url, "SELECT dimensione FROM public.catena_stato"))[0]["dimensione"])


async def _blocchi(url: str) -> list[dict]:
    return [dict(r) for r in await sql(
        url, "SELECT id, causa, occorrenze, dettagli::text AS dettagli FROM "
             "public.catena_blocchi_registro WHERE chiuso_il IS NULL ORDER BY id")]


def _chiavi_checkpoint(client) -> list[str]:
    risposta = client.list_objects_v2(Bucket=settings.s3_bucket_aip,
                                      Prefix="registro/checkpoint/")
    return sorted(o["Key"] for o in risposta.get("Contents", []))


async def _scenario_del_revisore(db_url, copie, s3):  # noqa: F811
    """Checkpoint 2 controfirmato, backup a 2, checkpoint 4 pubblicato e non
    controfirmato, ripristino del backup. Restituisce (backup, ident, testimone)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    await _cresci(db_url, tenant, classe, 2, ident, ORA)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    backup = await copie()
    await _cresci(db_url, tenant, classe, 2, ident, ORA + timedelta(minutes=1))
    assert await _stato(db_url) == 4
    assert "registro/checkpoint/4.txt" in _chiavi_checkpoint(s3)  # pubblicato, T resta 2
    await _usa(backup)
    return backup, ident, testimone, tenant, classe


async def test_ripristino_da_backup_registro_indietro_non_sequenzia(db_url, copie, s3):
    backup, ident, testimone, tenant, classe = await _scenario_del_revisore(db_url, copie, s3)
    await crea_pda(backup, tenant, classe, n_doc=1)   # un PdA nuovo arriva in coda
    richieste_prima = len(testimone.richieste)
    ciclo = _ciclo(ident, testimone.client(), adesso=ORA + timedelta(hours=1))
    with capture_logs() as log:
        for _ in range(3):
            await ciclo.giro()
    # Nessuna foglia nuova, nessun checkpoint 3, nessun invio al testimone
    assert await _stato(backup) == 2
    assert "registro/checkpoint/3.txt" not in _chiavi_checkpoint(s3)
    assert len(testimone.richieste) == richieste_prima
    blocchi = await _blocchi(backup)
    # un controllo per giro (orologio di prova: +100 s), un solo blocco aperto
    assert [(b["causa"], b["occorrenze"]) for b in blocchi] == [("REGISTRO_INDIETRO", 3)]
    for chiave, valore in (('"dimensione_db": 2', None), ('"checkpoint_worm": 4', None),
                           ('"fine_segmenti_worm": 4', None), ('"testimone": 2', None),
                           ('"soglia": 4', None)):
        assert chiave in blocchi[0]["dettagli"], (chiave, valore)
    eventi = [r["event"] for r in log]
    assert eventi.count("registro_bloccato") == 1           # apertura
    assert eventi.count("registro_bloccato_aperto") == 3    # a ogni giro
    assert metriche().registro_bloccato._value.get() == 1
    assert metriche().registro_soglia._value.get() == 4
    # Comando del runbook: misure e codice d'uscita 1
    assert await operatore.esegui(operatore.analizza(["controlla-worm"])) == 1
    # Il blocco del controllo NON si chiude a mano
    assert await operatore.esegui(operatore.analizza([
        "sblocca-registro", "--blocco", str(blocchi[0]["id"]), "--operatore", "Op",
        "--motivo", "provo a far ripartire il registro a mano"])) == 1

    # Ripristino point-in-time più recente (D = 4): il controllo passa, si riparte
    await _usa(db_url)
    await crea_pda(db_url, tenant, classe, n_doc=1)
    ciclo = _ciclo(ident, testimone.client(), adesso=ORA + timedelta(hours=2))
    with capture_logs() as log:
        await ciclo.giro()
    assert "registro_coerente" in [r["event"] for r in log]
    assert await _stato(db_url) == 5
    assert await _blocchi(db_url) == []
    assert metriche().registro_bloccato._value.get() == 0
    assert await operatore.esegui(operatore.analizza(["controlla-worm"])) == 0


async def test_ripristino_con_storia_diversa_registro_divergente(db_url, copie, s3):
    """Il database ripristinato ha già rimesso in registro PdA diversi fino a 4: la
    dimensione basta, ma le foglie del segmento 2-4 e la radice del checkpoint 4
    pubblicati sono diverse. Blocco DIVERGENTE, nessun checkpoint."""
    backup, ident, testimone, tenant, classe = await _scenario_del_revisore(db_url, copie, s3)
    for _ in range(2):
        await crea_pda(backup, tenant, classe, n_doc=1)
    async with get_async_session() as session:
        from app.registro import archivio

        assert await archivio.acquisisci_lease(session, IO, ORA, ORA + timedelta(hours=1))
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=ORA, limite=1000)
    assert await _stato(backup) == 4
    checkpoint_prima = len(await sql(backup, "SELECT 1 FROM public.catena_checkpoint"))
    ciclo = _ciclo(ident, testimone.client(), adesso=ORA + timedelta(hours=1))
    await ciclo.giro()
    blocchi = await _blocchi(backup)
    assert [b["causa"] for b in blocchi] == ["REGISTRO_DIVERGENTE"]
    assert "segmento 2-4" in blocchi[0]["dettagli"] and "checkpoint 4" in blocchi[0]["dettagli"]
    assert len(await sql(backup, "SELECT 1 FROM public.catena_checkpoint")) == checkpoint_prima


async def test_worm_stesso_nome_contenuto_diverso_blocco_persistente(db_url, s3):
    """Prima: ContenutoWormDiverso a ogni giro sullo stesso nome, senza fine. Ora
    un blocco persistente, nessun nuovo tentativo finché l'operatore non lo chiude."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=False)
    await archivio_worm.scrivi(archivio_worm.chiave_checkpoint(1), b"altro contenuto\n",
                               "text/plain; charset=utf-8", ORA)
    await crea_pda(db_url, tenant, classe, n_doc=1)
    ciclo = _ciclo(ident, None, adesso=ORA)
    with capture_logs() as log:
        for _ in range(3):
            await ciclo.giro()
    assert [r["event"] for r in log].count("registro_worm_contenuto_diverso") == 1
    blocchi = await _blocchi(db_url)
    assert [(b["causa"], b["occorrenze"]) for b in blocchi] == [("WORM_CONTENUTO_DIVERSO", 1)]
    assert "registro/checkpoint/1.txt" in blocchi[0]["dettagli"]
    assert await operatore.esegui(operatore.analizza(["blocchi"])) == 0
    assert await operatore.esegui(operatore.analizza([
        "sblocca-registro", "--blocco", str(blocchi[0]["id"]), "--operatore", "Mario Rossi",
        "--motivo", "oggetto WORM di un'installazione di prova: indagato"])) == 0
    audit = await sql(db_url, "SELECT 1 FROM public.audit_global "
                              "WHERE evento = 'catena.blocco_registro_chiuso'")
    assert len(audit) == 1
    with capture_logs() as log:
        await ciclo.giro()   # nuovo tentativo dopo la chiusura: il blocco si riapre
    assert [r["event"] for r in log].count("registro_worm_contenuto_diverso") == 1
    assert [b["causa"] for b in await _blocchi(db_url)] == ["WORM_CONTENUTO_DIVERSO"]


async def test_registro_sospeso_metrica_log_e_allarmi_a_ogni_giro(db_url, s3):
    """T5: con il registro sospeso metrica a 1, un log a OGNI giro e i controlli
    degli allarmi che continuano; tolta la sospensione, controllo di coerenza."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=False)
    await crea_pda(db_url, tenant, classe, n_doc=1)
    ciclo = _ciclo(ident, None, adesso=ORA, sospeso=True)
    metriche().rifiutati_aperti.set(99)
    metriche().registro_bloccato.set(99)
    with capture_logs() as log:
        for _ in range(3):
            await ciclo.giro()
    assert [r["event"] for r in log].count("registro_sospeso") == 3
    assert metriche().registro_sospeso._value.get() == 1
    assert metriche().rifiutati_aperti._value.get() == 0     # allarme ricalcolato
    assert metriche().registro_bloccato._value.get() == 0
    assert await _stato(db_url) == 0
    ciclo.config = ConfigCiclo(**CONFIG)
    with capture_logs() as log:
        await ciclo.giro()
    assert "registro_coerente" in [r["event"] for r in log]
    assert metriche().registro_sospeso._value.get() == 0
    assert await _stato(db_url) == 1
