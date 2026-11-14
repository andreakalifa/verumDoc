# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05, VD-C-111: ricostruzione del registro dal WORM (``ricostruisci-da-worm``) su
PostgreSQL vero e S3 (moto con Object Lock).

Scenario: i PdA esistono nel database, il registro cresce a lotti con checkpoint
(alcuni controfirmati dal testimone), una foglia di scarto (Q3); poi il database
torna a un backup in cui il registro è VUOTO (o indietro). La ricostruzione:
  - con ``--prova`` verifica tutto e non scrive nulla (nemmeno il lease);
  - scrive foglie, nodi, checkpoint (con controfirma), richiesta interna e
    collegamenti: stesse radici, stesse prove d'inclusione e di consistenza,
    controllo di coerenza superato e registro che riparte;
  - non procede mai se qualcosa non torna: checkpoint con firma falsa nel WORM,
    segmento sovrapposto con contenuto diverso, PdA assente dal database,
    ``--fino-a`` oltre il WORM.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.config import settings
from app.registro import (
    archivio,
    archivio_worm,
    checkpoint,
    coerenza,
    interne,
    operatore,
    prove,
    ricostruzione,
    sequenziatore,
)
from app.registro.chiavi import firmatario_registro, origine_registro
from app.services import block_sealer

from libs.catena import nota
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
from tests.supporto_registro import FintoTestimone, FirmaFinta, crea_pda, fatto_ciclo_vita

pytestmark = [pytest.mark.integration, richiede_pg]

ORA = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
IO = "detentore-ricostruzione"
SEGMENTO = 4


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
    """Istantanee del database («backup»), eliminate alla fine."""
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
    await close_db()
    await init_db(url)


@pytest.fixture
def testimone(monkeypatch):
    """Testimone finto; la sua chiave pubblica è quella configurata nel servizio."""
    finto = FintoTestimone(firmatario_registro(settings).verificatore)
    monkeypatch.setattr(settings, "testimone_url", "http://testimone")
    monkeypatch.setattr(settings, "testimone_verificatore", finto.firmatario.verificatore.testo)
    return finto


def _identita() -> checkpoint.Identita:
    return checkpoint.Identita(firmatario_registro(settings), origine_registro(settings), True)


async def _giro(adesso: datetime, testimone: FintoTestimone | None) -> None:
    """Sequenziatore, checkpoint (segmenti da 4) e, se c'è, testimone."""
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, adesso, adesso + timedelta(hours=1))
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=adesso, limite=1000)
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=adesso, identita=_identita(),
                                       segmento=SEGMENTO)
    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=adesso)
    if testimone is not None:
        await checkpoint.invia_al_testimone(testimone.client(), detentore=IO, adesso=adesso)
    async with get_async_session() as session:
        await archivio.rilascia_lease(session, IO)


async def _registro(url: str) -> dict:
    """Istantanea del registro per il confronto."""
    return {
        "foglie": [tuple(r) for r in await sql(url, "SELECT indice, aip_id, tenant_id, tipo, "
                                                    "dati, hash FROM public.catena_registro "
                                                    "ORDER BY indice")],
        "nodi": [tuple(r) for r in await sql(url, "SELECT livello, indice, hash FROM "
                                                  "public.catena_nodi ORDER BY 1, 2")],
        "checkpoint": [tuple(r) for r in await sql(url, "SELECT dimensione, radice, testo, "
                                                        "controfirma FROM public.catena_checkpoint "
                                                        "ORDER BY dimensione")],
        "interne": [tuple(r) for r in await sql(url, "SELECT id, tipo, chiave, aip_id, dati, "
                                                     "indice FROM public.catena_foglie_interne "
                                                     "ORDER BY id")],
        "stato": tuple((await sql(url, "SELECT dimensione, worm_foglie_esportate, detentore, "
                                       "testimone_dimensione FROM public.catena_stato"))[0]),
    }


async def _prove(pda: list, n: int) -> list[dict]:
    async with get_async_session() as session:
        risultato = []
        for p in pda:
            trovato = await prove.pda_per_urn(session, p.urn)
            risultato.append(await prove.prova_inclusione_documento(session, trovato, "doc-0", n))
        risultato.append(await prove.prova_consistenza(session, 3, n))
    return risultato


async def _scenario(db_url, copie, testimone, *, backup_dopo_giri: int = 0,  # noqa: F811
                    controfirmati: frozenset[int] = frozenset({0, 1, 3})):
    """23 PdA (creati prima del backup), registro cresciuto in 4 giri, una foglia di
    scarto al terzo giro; il backup si prende dopo ``backup_dopo_giri`` giri. Il
    testimone controfirma i giri in ``controfirmati`` (predefinito: tace al terzo
    e controfirma l'ultimo, quindi tutte le foglie sono coperte da un checkpoint
    firmato e controfirmato: revisione R05, R5)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = [await crea_pda(db_url, tenant, classe, n_doc=1, in_coda=False) for _ in range(23)]
    lotti = [pda[:3], pda[3:10], pda[10:17], pda[17:]]
    backup = None
    for giro, lotto in enumerate(lotti):
        if giro == backup_dopo_giri:
            backup = await copie()
        for p in lotto:
            await sql(db_url, "INSERT INTO public.catena_coda (aip_id) VALUES ($1)", p.aip_id)
        if giro == 2:
            # revisione 2 di R05 (S2): il fatto dello scarto nel database
            async with fatto_ciclo_vita(db_url, pda[1], "scarto") as pratica, \
                    get_async_session() as session:
                await interne.richiedi_foglia_ciclo_vita(
                    session, tipo="scarto", pda=pda[1].urn, riferimento={"pratica": pratica},
                    impronta=hashlib.sha256(b"verbale").hexdigest(), richiesta_da="prova",
                    adesso=ORA)
        await _giro(ORA + timedelta(minutes=giro), testimone if giro in controfirmati else None)
    if backup is None:
        backup = await copie()
    return backup, pda


async def test_registro_distrutto_ricostruito_stesse_radici_e_prove(db_url, copie, s3,
                                                                    testimone):
    backup, pda = await _scenario(db_url, copie, testimone)
    originale = await _registro(db_url)
    n = originale["stato"][0]
    assert n == 24 and originale["stato"][1] == 24
    prove_originali = await _prove([pda[0], pda[1], pda[22]], n)

    # Il database torna al backup: registro VUOTO, PdA presenti (in coda)
    await _usa(backup)
    assert (await coerenza.verifica()).indietro

    # Prova: tutti i controlli, nessuna scrittura (nemmeno il lease)
    prima = await _registro(backup)
    r = await ricostruzione.ricostruisci(fino_a=n, prova=True)
    assert r.riuscito, r.problemi
    assert (r.foglie_nuove, r.checkpoint_verificati, r.controfirme_verificate) == (24, 4, 3)
    assert r.ultimo_checkpoint_controfirmato == 24
    assert r.collegamenti == {"pda": 23, "scarto": 1}
    assert await _registro(backup) == prima
    assert await sql(backup, "SELECT 1 FROM public.audit_global "
                             "WHERE evento LIKE 'catena.ricostruzione%'") == []

    # Ricostruzione (lotti piccoli: più transazioni)
    r = await ricostruzione.ricostruisci(fino_a=n, prova=False, operatore="Mario Rossi",
                                         motivo="ripristino di prova senza PITR utile", lotto=5)
    assert r.riuscito, r.problemi
    assert (r.foglie_scritte, r.checkpoint_ricostruiti, r.interne_ricreate) == (24, 4, 1)
    ricostruito = await _registro(backup)
    assert ricostruito["foglie"] == originale["foglie"]
    assert ricostruito["nodi"] == originale["nodi"]
    assert ricostruito["checkpoint"] == originale["checkpoint"]
    assert ricostruito["interne"] == originale["interne"]          # stesso id della foglia
    assert ricostruito["stato"][0] == n and ricostruito["stato"][1] == n
    assert ricostruito["stato"][2] is None                       # lease rilasciato
    assert ricostruito["stato"][3] == originale["stato"][3]      # dimensione controfirmata
    assert await _prove([pda[0], pda[1], pda[22]], n) == prove_originali
    # stati del testimone: controfirmati come prima; quello saltato dal testimone
    # (coperto dal successivo controfirmato) SUPERATO
    stati = [r["stato_testimone"] for r in await sql(
        backup, "SELECT stato_testimone FROM public.catena_checkpoint ORDER BY dimensione")]
    assert stati == ["CONTROFIRMATO", "CONTROFIRMATO", "SUPERATO", "CONTROFIRMATO"]
    assert await sql(backup, "SELECT 1 FROM public.catena_coda") == []
    assert {r["s"] for r in await sql(backup, "SELECT stato::text AS s FROM public.aip")} == \
        {"IN_CATENA"}
    voci = await sql(backup, "SELECT evento FROM public.audit_global "
                             "WHERE evento LIKE 'catena.ricostruzione%' ORDER BY created_at")
    assert [v["evento"] for v in voci] == ["catena.ricostruzione_da_worm_avviata",
                                           "catena.ricostruzione_da_worm_conclusa"]
    # Controllo di coerenza superato e registro che riparte consistente
    esito = await coerenza.verifica()
    assert esito.coerente, esito.come_dict()
    tenant = (await sql(backup, "SELECT tenant_id FROM public.aip LIMIT 1"))[0]["tenant_id"]
    classe = (await sql(backup, "SELECT classe_doc_id FROM public.pdv LIMIT 1"))[0][
        "classe_doc_id"]
    await crea_pda(backup, tenant, classe, n_doc=1)
    await _giro(ORA + timedelta(hours=1), testimone)
    async with get_async_session() as session:
        consistenza = await prove.prova_consistenza(session, n, n + 1)
        nuovo = await archivio.ultimo_checkpoint(session)
    from libs.catena.rfc6962 import da_esadecimale, verifica_consistenza

    vecchia = nota.analizza(nota.separa(originale["checkpoint"][-1][2])[0])
    assert verifica_consistenza(n, n + 1, vecchia.radice, bytes.fromhex(nuovo["radice"]),
                                da_esadecimale(consistenza["percorso"]))
    assert nuovo["stato_testimone"] == "CONTROFIRMATO"


async def test_registro_indietro_ricostruisce_dalla_base(db_url, copie, s3, testimone):
    """Database tornato a 10 foglie (checkpoint 10 nel DB): si legge il WORM dal
    checkpoint 10 verificato, non da 0, e si arriva alle stesse foglie."""
    backup, _ = await _scenario(db_url, copie, testimone, backup_dopo_giri=2)
    originale = await _registro(db_url)
    await _usa(backup)
    assert (await _registro(backup))["stato"][0] == 10
    assert await operatore.esegui(operatore.analizza(
        ["ricostruisci-da-worm", "--fino-a", "24", "--prova"])) == 0
    r = await ricostruzione.ricostruisci(fino_a=24, prova=False, operatore="Mario Rossi",
                                         motivo="ripristino di prova dal checkpoint 10")
    assert r.riuscito, r.problemi
    assert (r.base, r.foglie_lette, r.foglie_scritte) == (10, 14, 14)
    ricostruito = await _registro(backup)
    for chiave in ("foglie", "nodi", "checkpoint", "interne"):
        assert ricostruito[chiave] == originale[chiave], chiave
    assert (await coerenza.verifica()).coerente


async def test_senza_operatore_e_motivo_non_scrive(db_url, copie, s3, testimone):
    backup, _ = await _scenario(db_url, copie, testimone)
    await _usa(backup)
    assert await operatore.esegui(operatore.analizza(
        ["ricostruisci-da-worm", "--fino-a", "24"])) == 1
    assert (await _registro(backup))["foglie"] == []


async def _non_scrive(backup: str, fino_a: int, frammento: str) -> None:
    prima = await _registro(backup)
    for prova in (True, False):
        r = await ricostruzione.ricostruisci(fino_a=fino_a, prova=prova, operatore="Op",
                                             motivo="tentativo di ricostruzione di prova")
        assert not r.riuscito and r.foglie_scritte == 0, r
        assert any(frammento in p for p in r.problemi), r.problemi
    assert await _registro(backup) == prima


async def test_checkpoint_con_firma_falsa_nel_worm_non_ricostruisce(db_url, copie, s3,
                                                                    testimone):
    backup, _ = await _scenario(db_url, copie, testimone)
    # Un checkpoint con la radice GIUSTA ma firmato da un'altra chiave, alla fine di un
    # segmento senza checkpoint (7): solo la verifica della firma lo ferma
    from libs.catena.rfc6962 import radice

    hash_7 = [bytes(r["hash"]) for r in await sql(
        db_url, "SELECT hash FROM public.catena_registro WHERE indice < 7 ORDER BY indice")]
    falso, _ = nota.genera_chiave("verumdoc-registro")
    corpo = nota.formatta(nota.Checkpoint(origine_registro(settings), 7, radice(hash_7)))
    await archivio_worm.scrivi(archivio_worm.chiave_checkpoint(7), nota.componi(
        corpo, [nota.analizza_firmatario(falso).firma(corpo)]).encode(), "text/plain", ORA)
    await archivio_worm.scrivi(archivio_worm.chiave_segmento(3, 7),
                               (await _righe(db_url, 3, 7)).encode(), "application/x-ndjson", ORA)
    await _usa(backup)
    await _non_scrive(backup, 24, "checkpoint 7: firma del registro non valida")


async def _righe(url: str, da: int, a: int) -> str:
    righe = await sql(url, "SELECT dati FROM public.catena_registro WHERE indice >= $1 AND "
                           "indice < $2 ORDER BY indice", da, a)
    return "".join(r["dati"] + "\n" for r in righe)


async def test_segmento_sovrapposto_diverso_non_ricostruisce(db_url, copie, s3, testimone):
    backup, _ = await _scenario(db_url, copie, testimone)
    contenuto = (await _righe(db_url, 0, 2)).replace("urn:test", "urn:falso", 1)
    await archivio_worm.scrivi(archivio_worm.chiave_segmento(0, 2), contenuto.encode(),
                               "application/x-ndjson", ORA)
    await _usa(backup)
    await _non_scrive(backup, 24, "sovrapposto")


async def test_pda_assente_dal_database_non_ricostruisce(db_url, copie, s3, testimone):
    """Un PdA conservato DOPO il backup: la sua riga aip non c'è più."""
    backup, _ = await _scenario(db_url, copie, testimone)
    tenant = (await sql(db_url, "SELECT tenant_id FROM public.aip LIMIT 1"))[0]["tenant_id"]
    classe = (await sql(db_url, "SELECT classe_doc_id FROM public.pdv LIMIT 1"))[0][
        "classe_doc_id"]
    tardivo = await crea_pda(db_url, tenant, classe, n_doc=1)
    await _giro(ORA + timedelta(minutes=10), None)
    await _usa(backup)
    await _non_scrive(backup, 25, f"PdA {tardivo.urn} assente dal database")


async def test_fino_a_oltre_il_worm_non_ricostruisce(db_url, copie, s3, testimone):
    backup, _ = await _scenario(db_url, copie, testimone)
    await _usa(backup)
    await _non_scrive(backup, 30, "solo fino a 24")


async def test_lease_del_ciclo_impedisce_la_scrittura(db_url, copie, s3, testimone):
    backup, _ = await _scenario(db_url, copie, testimone)
    await _usa(backup)
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, "ciclo-in-esercizio", datetime.now(UTC),
                                               datetime.now(UTC) + timedelta(hours=1))
    r = await ricostruzione.ricostruisci(fino_a=24, prova=False, operatore="Op",
                                         motivo="tentativo con il registro in esercizio")
    assert not r.riuscito and "lease" in r.problemi[0]
    assert (await _registro(backup))["foglie"] == []


async def test_checkpoint_firmato_con_radice_di_un_altra_storia_non_ricostruisce(
        db_url, copie, s3, testimone):
    """Checkpoint con la chiave GIUSTA ma la radice di un'altra storia (biforcazione
    firmata) alla fine di un segmento: solo il confronto della radice lo ferma."""
    backup, _ = await _scenario(db_url, copie, testimone)
    corpo = nota.formatta(nota.Checkpoint(origine_registro(settings), 7, b"\x07" * 32))
    await archivio_worm.scrivi(archivio_worm.chiave_checkpoint(7), nota.componi(
        corpo, [firmatario_registro(settings).firma(corpo)]).encode(), "text/plain", ORA)
    await _usa(backup)
    await _non_scrive(backup, 24, "checkpoint 7: radice diversa")
