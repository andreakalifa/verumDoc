# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05, VD-C-112: export di ``audit_global`` nel WORM con la foglia ``audit`` nel
registro, e verifica database ↔ WORM, su PostgreSQL vero e S3 (moto).

  - periodi chiusi (con margine) esportati in segmenti JSON Lines canonici con
    l'impronta del segmento precedente e la radice di Merkle delle righe; periodi
    vuoti uniti; nessun periodo ancora aperto;
  - l'impronta di ogni segmento entra nel registro come foglia ``audit`` di
    formato 2 (sequenziata, coperta dal checkpoint);
  - un SUPERUTENTE del database che cancella, modifica o aggiunge righe in un
    periodo già esportato (scavalcando trigger e RLS) è scoperto da ``verifica``;
  - un segmento WORM divergente è un allarme dell'audit, non un blocco del registro;
  - un nuovo tentativo dopo un errore ritrova il segmento identico.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.config import settings
from app.registro import (
    archivio,
    archivio_worm,
    audit_worm,
    checkpoint,
    operatore,
    sequenziatore,
)
from app.registro.ciclo import CicloCatena, ConfigCiclo
from app.registro.metriche import metriche
from app.services import block_sealer

from libs.catena.foglie import analizza_foglia_registro
from libs.catena.rfc6962 import hash_foglia, radice
from libs.shared.db import get_async_session
from tests.infra_reale import (  # noqa: F401 — fixture
    _asyncpg_dsn,
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.supporto_registro import FirmaFinta, identita

pytestmark = [pytest.mark.integration, richiede_pg]

INIZIO = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
ADESSO = datetime(2026, 10, 20, 13, 15, tzinfo=UTC)
IO = "detentore-audit"


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 30)
    monkeypatch.setattr(settings, "audit_export_inizio", INIZIO.isoformat())
    # R07 (D19): periodo dalla console; queste prove sono costruite su periodi di 1 ora
    monkeypatch.setattr(audit_worm, "periodo_secondi", _un_ora)
    monkeypatch.setattr(settings, "audit_export_margine_secondi", 600)
    monkeypatch.setattr(settings, "audit_export_righe_massime", 200000)
    block_sealer._client.cache_clear()
    yield client_s3(s3_server)
    block_sealer._client.cache_clear()


async def _un_ora() -> int:
    return 3600


async def _riga(url: str, istante: datetime, evento: str = "prova.evento",
                dettagli: dict | None = None) -> uuid.UUID:
    id_ = uuid.uuid4()
    await sql(url, "INSERT INTO public.audit_global (id, evento, dettagli, created_at, "
                   "ip_address) VALUES ($1, $2, CAST($3 AS jsonb), $4, '10.0.0.1')", id_, evento,
              json.dumps(dettagli or {"importo": 1.5, "testo": "à"}), istante)
    return id_


async def _come_superutente(url: str, *istruzioni: tuple[str, ...]) -> None:
    """L'amministratore del database: trigger «origin» spenti, RLS scavalcata.
    Ogni istruzione è (SQL con parametri $n, argomenti…)."""
    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        await conn.execute("SET session_replication_role = replica")
        for sql_, *argomenti in istruzioni:
            await conn.execute(sql_, *argomenti)
    finally:
        await conn.close()


async def _popola(url: str) -> list[uuid.UUID]:
    ids = [await _riga(url, INIZIO + timedelta(minutes=5 * i)) for i in range(5)]  # 10:00-10:20
    ids += [await _riga(url, INIZIO + timedelta(hours=2, minutes=m)) for m in (1, 30, 59)]
    await _riga(url, ADESSO - timedelta(minutes=2))  # periodo ancora aperto
    return ids


async def _registra(adesso: datetime = ADESSO) -> None:
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, adesso, adesso + timedelta(hours=1))
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=adesso, limite=1000)
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=adesso, identita=identita(),
                                       segmento=100)


async def test_export_segmenti_catena_e_foglie_nel_registro(db_url, s3):
    await _popola(db_url)
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    assert (esito.esportati, esito.problemi) == (3, [])
    righe = await sql(db_url, "SELECT periodo_da, periodo_a, righe, impronta_segmento, "
                              "impronta_precedente, worm_chiave FROM public.audit_esportazioni "
                              "ORDER BY periodo_da")
    assert [(r["periodo_da"], r["periodo_a"], r["righe"]) for r in righe] == [
        (INIZIO, INIZIO + timedelta(hours=1), 5),
        (INIZIO + timedelta(hours=1), INIZIO + timedelta(hours=2), 0),
        (INIZIO + timedelta(hours=2), INIZIO + timedelta(hours=3), 3)]
    assert righe[0]["impronta_precedente"] is None
    assert [r["impronta_precedente"] for r in righe[1:]] == \
        [r["impronta_segmento"] for r in righe[:-1]]
    # Segmento: intestazione + righe canoniche, radice di Merkle, impronta
    oggetto = s3.get_object(Bucket=settings.s3_bucket_aip, Key=righe[0]["worm_chiave"])
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    corpo = oggetto["Body"].read()
    linee = corpo.split(b"\n")[:-1]
    testa = json.loads(linee[0])
    assert testa["righe"] == 5 and testa["segmento_precedente"] is None
    assert testa["radice_righe"] == radice([hash_foglia(x) for x in linee[1:]]).hex()
    riga = json.loads(linee[1])
    assert riga["ip"] == "10.0.0.1/32" or riga["ip"] == "10.0.0.1"
    assert json.loads(riga["dettagli"]) == {"importo": 1.5, "testo": "à"}  # testo jsonb esatto
    import hashlib

    assert hashlib.sha256(corpo).hexdigest() == righe[0]["impronta_segmento"]
    # Un secondo giro allo stesso istante non esporta nulla (periodo 13-14 aperto)
    assert (await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)).esportati == 0
    assert metriche().audit_ritardo._value.get() == (ADESSO - (INIZIO + timedelta(hours=3))) \
        .total_seconds()

    # Foglie «audit» di formato 2 in coda → registro → checkpoint
    await _registra()
    foglie = await sql(db_url, "SELECT tipo, aip_id, tenant_id, dati FROM public.catena_registro "
                               "ORDER BY indice")
    assert [f["tipo"] for f in foglie] == ["AUDIT"] * 3
    assert {(f["aip_id"], f["tenant_id"]) for f in foglie} == {(None, None)}
    foglia = analizza_foglia_registro(foglie[2]["dati"])
    assert foglia.tipo == "audit" and foglia.dati["impronta_segmento"] == \
        righe[2]["impronta_segmento"]
    verifica = await audit_worm.verifica()
    assert verifica.coerente, verifica.righe()
    assert (verifica.segmenti, verifica.righe_worm, verifica.righe_db,
            verifica.non_esportate) == (3, 8, 8, 1)
    assert await operatore.esegui(operatore.analizza(["verifica-audit"])) == 0


async def test_superutente_che_altera_l_audit_e_scoperto(db_url, s3):
    ids = await _popola(db_url)
    await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    await _registra()
    assert (await audit_worm.verifica()).coerente
    # Il superutente cancella una riga, ne modifica un'altra, ne aggiunge una retrodatata
    await _come_superutente(
        db_url, ("DELETE FROM public.audit_global WHERE id = $1", ids[1]),
        ("UPDATE public.audit_global SET evento = 'innocuo' WHERE id = $1", ids[6]))
    aggiunta = await _riga(db_url, INIZIO + timedelta(minutes=30), evento="retrodatata")
    # (le difese dentro il database non lo fermano: è fuori dal loro perimetro)
    esito = await audit_worm.verifica()
    assert not esito.coerente
    assert (esito.mancanti_nel_db, esito.modificate, esito.aggiunte_nel_db) == (1, 1, 1)
    testo = "\n".join(esito.differenze)
    assert str(ids[1]) in testo and "cancellata" in testo
    assert str(ids[6]) in testo and "modificata" in testo
    assert str(aggiunta) in testo and "aggiunta dopo l'esportazione" in testo
    assert await operatore.esegui(operatore.analizza(["verifica-audit"])) == 1
    # Solo una finestra temporale
    parziale = await audit_worm.verifica(da=INIZIO + timedelta(hours=2))
    assert (parziale.segmenti, parziale.modificate, parziale.mancanti_nel_db) == (1, 1, 0)


async def test_segmento_non_ancora_nel_registro_segnalato(db_url, s3):
    await _popola(db_url)
    await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    esito = await audit_worm.verifica()
    assert not esito.coerente and esito.differenze == []
    assert any("non ancora (o non più) nel registro" in p for p in esito.problemi)
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, ADESSO, ADESSO + timedelta(hours=1))
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=ADESSO, limite=1000)
    esito = await audit_worm.verifica()
    assert any("non ancora coperta da un checkpoint" in p for p in esito.problemi)


async def test_nuovo_tentativo_ritrova_il_segmento_identico(db_url, s3):
    """Errore dopo la scrittura WORM e prima del database: il segmento si riscrive
    identico (contenuto deterministico) e l'export prosegue."""
    await _popola(db_url)
    da, a = INIZIO, INIZIO + timedelta(hours=1)
    corpo, _, _ = audit_worm.componi_segmento(da, a, await audit_worm.righe_periodo(da, a), None)
    await archivio_worm.scrivi(audit_worm.chiave_segmento(da, a), corpo,
                               "application/x-ndjson", ADESSO)
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    assert (esito.esportati, esito.problemi) == (3, [])


async def test_segmento_divergente_allarme_dell_audit_non_blocco_del_registro(db_url, s3):
    """Il database è tornato indietro (esportazione persa) e una riga del periodo è
    cambiata: il segmento nel WORM differisce. Allarme dell'audit, registro libero."""
    ids = await _popola(db_url)
    await audit_worm.esporta_periodi_chiusi(adesso=ADESSO, limite_periodi=1)
    await _come_superutente(db_url, ("DELETE FROM public.audit_esportazioni",),
                            ("DELETE FROM public.audit_global WHERE id = $1", ids[0]))
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    assert esito.esportati == 0 and "contenuto diverso" in esito.problemi[0]
    assert metriche().audit_divergente._value.get() == 1
    assert await sql(db_url, "SELECT 1 FROM public.catena_blocchi_registro") == []
    assert await operatore.esegui(operatore.analizza(["esporta-audit"])) == 1


async def test_periodo_troppo_pieno_diviso(db_url, s3, monkeypatch):
    monkeypatch.setattr(settings, "audit_export_righe_massime", 3)
    await _popola(db_url)
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    assert esito.problemi == []
    righe = await sql(db_url, "SELECT periodo_da, periodo_a, righe FROM "
                              "public.audit_esportazioni ORDER BY periodo_da")
    assert all(r["righe"] <= 3 for r in righe)
    assert sum(r["righe"] for r in righe) == 8
    assert righe[0]["periodo_a"] - righe[0]["periodo_da"] < timedelta(hours=1)
    for prima, dopo in zip(righe, righe[1:], strict=False):
        assert prima["periodo_a"] == dopo["periodo_da"]  # nessun buco
    assert (await audit_worm.verifica()).differenze == []


async def test_ciclo_esporta_l_audit_e_registra_le_foglie(db_url, s3):
    await _popola(db_url)
    tempo = {"t": 0.0}

    def orologio() -> float:
        tempo["t"] += 100.0
        return tempo["t"]

    ciclo = CicloCatena(
        config=ConfigCiclo(intervallo=1, checkpoint_secondi=60, controfirma_max_secondi=600,
                           lease_secondi=30, lotto=10, completamento_lotto=10,
                           segmento_worm=10, ripresa_secondi=60, audit_attivo=True),
        identita=identita(), servizio_firma=FirmaFinta(), testimone=None, detentore=IO,
        adesso=lambda: ADESSO, orologio=orologio, ammetti_senza_testimone=True)
    await ciclo.giro()   # coerenza, export (foglie in coda)
    await ciclo.giro()   # sequenziatore e checkpoint
    assert len(await sql(db_url, "SELECT 1 FROM public.catena_registro "
                                 "WHERE tipo = 'AUDIT'")) == 3
    assert (await audit_worm.verifica()).coerente


async def test_segmento_divergente_adottato_dall_operatore(db_url, s3):
    """Dopo l'allarme l'operatore registra il segmento già nel WORM (senza
    riscriverlo): l'export riprende e la riga persa resta visibile come cancellata."""
    ids = await _popola(db_url)
    await audit_worm.esporta_periodi_chiusi(adesso=ADESSO, limite_periodi=1)
    chiave = audit_worm.chiave_segmento(INIZIO, INIZIO + timedelta(hours=1))
    await _come_superutente(db_url, ("DELETE FROM public.audit_esportazioni",),
                            ("DELETE FROM public.audit_global WHERE id = $1", ids[0]))
    assert (await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)).problemi
    # un segmento che non è il successivo della catena non si adotta
    assert await operatore.esegui(operatore.analizza([
        "adotta-segmento-audit", "--segmento",
        audit_worm.chiave_segmento(INIZIO + timedelta(hours=2), INIZIO + timedelta(hours=3)),
        "--operatore", "Mario Rossi",
        "--motivo", "prova di adozione di un segmento sbagliato"])) == 1
    assert await operatore.esegui(operatore.analizza([
        "adotta-segmento-audit", "--segmento", chiave, "--operatore", "Mario Rossi",
        "--motivo", "ripristino del database: riga persa, segmento WORM di riferimento"])) == 0
    assert metriche().audit_divergente._value.get() == 0
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    assert (esito.esportati, esito.problemi) == (2, [])
    await _registra()
    verifica = await audit_worm.verifica()
    assert verifica.problemi == [] and verifica.mancanti_nel_db == 1
    assert str(ids[0]) in verifica.differenze[0]
    assert len(await sql(db_url, "SELECT 1 FROM public.audit_global "
                                 "WHERE evento = 'catena.audit_segmento_adottato'")) == 1
