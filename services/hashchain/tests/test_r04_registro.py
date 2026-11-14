# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Registro delle impronte a due livelli su PostgreSQL vero e S3 (moto con Object
Lock): sequenziatore con lease, checkpoint firmati nel DB e nel WORM, sigillo
qualificato con stato onesto, testimone K3, evento «conservato» solo a PdA nel
registro e coperto da checkpoint (VD-C-097), RdV definitivo v2 (VD-C-096, K5),
migrazione dei blocchi esistenti, API K1.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.config import settings
from app.main import app
from app.registro import archivio, checkpoint, conservazione, sequenziatore
from app.registro.archivio import LeasePerso
from app.registro.ciclo import CicloCatena, ConfigCiclo
from app.registro.rdv import NS, verifica_rdv
from app.services import block_sealer
from app.services.block_sealer import sigilla_blocco
from app.services.firma_blocchi import STATO_DA_RITENTARE, STATO_FIRMATO
from httpx import ASGITransport, AsyncClient

from libs.catena import albero, nota
from libs.catena.rfc6962 import (
    da_esadecimale,
    hash_foglia,
    radice,
    verifica_consistenza,
    verifica_inclusione,
)
from libs.shared.db import get_async_session
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.supporto_registro import FintoTestimone, FirmaFinta, crea_pda, identita
from tests.token_di_prova import intestazioni

pytestmark = [pytest.mark.integration, richiede_pg]

ORA = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
IO = "detentore-di-prova"


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


async def _lease(detentore: str = IO, adesso: datetime = ORA, durata: int = 60) -> bool:
    async with get_async_session() as session:
        return await archivio.acquisisci_lease(session, detentore, adesso,
                                               adesso + timedelta(seconds=durata))


async def _sequenzia(adesso: datetime = ORA, limite: int = 1000) -> sequenziatore.EsitoLotto:
    assert await _lease(adesso=adesso)
    return await sequenziatore.aggiungi_lotto(detentore=IO, adesso=adesso, limite=limite)


async def _emetti(ident, adesso: datetime = ORA) -> dict | None:
    assert await _lease(adesso=adesso)
    return await checkpoint.emetti_checkpoint(detentore=IO, adesso=adesso, identita=ident,
                                              segmento=3)


async def _completa(ident, adesso: datetime = ORA):
    return await conservazione.completa_pda_coperti(
        adesso=adesso, ammetti_senza_testimone=True, limite=100,
        verificatore=ident.firmatario.verificatore)


async def _outbox(url: str, topic: str) -> list[dict]:
    righe = await sql(url, "SELECT chiave, payload FROM public.outbox_eventi WHERE topic = $1 "
                           "ORDER BY sequenza", topic)
    return [{"chiave": r["chiave"], **json.loads(r["payload"])} for r in righe]


# ── Sequenziatore ────────────────────────────────────────────────────

async def test_sequenziatore_foglie_in_ordine_nodi_e_stato(db_url, s3):
    """Foglie con indice progressivo nell'ordine della coda, nodi = albero
    ricostruito da zero, PdA a IN_CATENA, coda svuotata (K1)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = [await crea_pda(db_url, tenant, classe) for _ in range(5)]
    esito = await _sequenzia()
    assert (esito.aggiunte, esito.dimensione) == (5, 5)
    foglie = await sql(db_url, "SELECT indice, aip_id, tipo, dati, hash FROM "
                               "public.catena_registro ORDER BY indice")
    assert [f["aip_id"] for f in foglie] == [p.aip_id for p in pda]
    assert {f["tipo"] for f in foglie} == {"PDA"}
    dati = json.loads(foglie[2]["dati"])
    assert dati == {"v": 1, "pda": pda[2].urn, "radice_pda": pda[2].radice,
                    "ente": str(tenant), "conservato_il": "2026-10-23T08:00:00.000000Z"}
    hash_foglie = [bytes(f["hash"]) for f in foglie]
    assert hash_foglie == [hash_foglia(f["dati"].encode()) for f in foglie]
    nodi = {(r["livello"], r["indice"]): bytes(r["hash"]) for r in
            await sql(db_url, "SELECT livello, indice, hash FROM public.catena_nodi")}
    assert nodi == albero.tutti_i_nodi(hash_foglie)
    stati = await sql(db_url, "SELECT DISTINCT stato::text AS s FROM public.aip")
    assert [s["s"] for s in stati] == ["IN_CATENA"]
    assert await sql(db_url, "SELECT 1 FROM public.catena_coda") == []
    # A coda vuota il sequenziatore non fa nulla
    assert (await _sequenzia()).aggiunte == 0


async def test_sequenziatore_lotti_successivi_usano_la_frontiera(db_url, s3):
    """Lotti piccoli consecutivi: stessi nodi della costruzione unica (append-only)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    for _ in range(7):
        await crea_pda(db_url, tenant, classe, n_doc=1)
    for atteso in (2, 4, 6, 7):
        assert (await _sequenzia(limite=2)).dimensione == atteso
    hash_foglie = [bytes(r["hash"]) for r in await sql(
        db_url, "SELECT hash FROM public.catena_registro ORDER BY indice")]
    nodi = {(r["livello"], r["indice"]): bytes(r["hash"]) for r in
            await sql(db_url, "SELECT livello, indice, hash FROM public.catena_nodi")}
    assert nodi == albero.tutti_i_nodi(hash_foglie)


async def test_sequenziatore_senza_lease_non_scrive(db_url, s3):
    """§12.9: un secondo processo non scrive finché il lease dell'altro è valido."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    await crea_pda(db_url, tenant, classe)
    assert await _lease("primo", ORA, 30)
    assert not await _lease("secondo", ORA + timedelta(seconds=10), 30)
    with pytest.raises(LeasePerso):
        await sequenziatore.aggiungi_lotto(detentore="secondo", adesso=ORA, limite=10)
    assert await sql(db_url, "SELECT 1 FROM public.catena_registro") == []
    # Lease scaduto: il secondo subentra
    dopo = ORA + timedelta(seconds=31)
    assert await _lease("secondo", dopo, 30)
    esito = await sequenziatore.aggiungi_lotto(detentore="secondo", adesso=dopo, limite=10)
    assert esito.aggiunte == 1
    with pytest.raises(LeasePerso):
        await sequenziatore.aggiungi_lotto(detentore="primo", adesso=dopo, limite=10)


async def test_sequenziatore_pda_gia_nel_registro_non_duplicato(db_url, s3):
    """Un PdA rimesso in coda per errore esce dalla coda senza seconda foglia."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    p = await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await sql(db_url, "INSERT INTO public.catena_coda (aip_id) VALUES ($1)", p.aip_id)
    esito = await _sequenzia()
    assert (esito.aggiunte, esito.gia_presenti, esito.dimensione) == (0, 1, 1)
    assert await sql(db_url, "SELECT 1 FROM public.catena_coda") == []


async def test_tabelle_del_registro_append_only(db_url, s3):
    """Trigger: foglie, nodi e alberi dei PdA non si modificano né si cancellano;
    il contenuto di un checkpoint non cambia."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await _emetti(identita())
    for istruzione in (
        "UPDATE public.catena_registro SET dati = 'x'",
        "DELETE FROM public.catena_nodi",
        "UPDATE public.catena_pda SET radice = 'x'",
        "TRUNCATE public.catena_registro CASCADE",
        "UPDATE public.catena_checkpoint SET testo = 'x'",
        "DELETE FROM public.catena_checkpoint",
    ):
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await sql(db_url, istruzione)
    await sql(db_url, "UPDATE public.catena_checkpoint SET stato_sigillo = 'NON_FIRMATO'")
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await sql(db_url, "UPDATE public.catena_checkpoint SET stato_sigillo = 'FIRMATO'")


# ── Migrazione dei blocchi esistenti (riversamento) ──────────────────

async def test_riversamento_pda_della_vecchia_catena_e_senza_albero(db_url, s3):
    """PdA nella vecchia catena (blocco formato 2) e PdA su storage senza albero
    (costruiti prima di R04): foglia di riversamento che cita il blocco d'origine;
    il blocco resta com'era e verificabile; lo stato del PdA già in catena non cambia."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    vecchio = await crea_pda(db_url, tenant, classe, con_albero=False, in_coda=False)
    blocco = await sigilla_blocco([str(vecchio.aip_id)], ora=ORA - timedelta(days=1))
    senza_albero = await crea_pda(db_url, tenant, classe, con_albero=False, in_coda=False)
    # In coda come li mette la migrazione 20261023_025 (provata a parte)
    for aip in (vecchio.aip_id, senza_albero.aip_id):
        await sql(db_url, "INSERT INTO public.catena_coda (aip_id) VALUES ($1)", aip)
    nuovo = await crea_pda(db_url, tenant, classe)
    await _sequenzia()

    foglie = await sql(db_url, "SELECT aip_id, tipo, dati FROM public.catena_registro "
                               "ORDER BY indice")
    assert [f["tipo"] for f in foglie] == ["RIVERSAMENTO", "RIVERSAMENTO", "PDA"]
    citazione = json.loads(foglie[0]["dati"])
    assert citazione["tipo"] == "riversamento" and citazione["blocco"] == {
        "numero": blocco["numero_blocco"], "hash": blocco["hash_blocco"], "formato": 2}
    assert "blocco" not in json.loads(foglie[1]["dati"])
    stati = {r["id"]: r["s"] for r in await sql(
        db_url, "SELECT id, stato::text AS s FROM public.aip")}
    assert stati[vecchio.aip_id] == "IN_CATENA"          # già in catena: invariato
    assert stati[senza_albero.aip_id] == "IN_CATENA"     # SU_STORAGE → IN_CATENA
    assert stati[nuovo.aip_id] == "IN_CATENA"
    integra, messaggio = await block_sealer.verify_chain_integrity()
    assert integra, messaggio

    # Il PdA della vecchia catena si completa col vecchio flusso (RdV v1); quello
    # senza albero col nuovo (RdV v2 dichiarato «riversamento»)
    ident = identita()
    await _emetti(ident)
    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=ORA)
    esito = await _completa(ident)
    assert sorted(esito.completati) == sorted([str(senza_albero.aip_id), str(nuovo.aip_id)])
    rif = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                                senza_albero.pdv_id))[0]["metadata_json"])["rdv_definitiva"]
    xml = s3.get_object(Bucket=rif["s3_bucket"], Key=rif["s3_key"])["Body"].read()
    assert ET.fromstring(xml).find(f"{{{NS}}}PdA").get("riversamento") == "true"
    assert verifica_rdv(xml, [ident.firmatario.verificatore]) == []


# ── Checkpoint ──────────────────────────────────────────────────────

async def test_checkpoint_firmato_in_db_worm_ed_evento(db_url, s3):
    """K2: nota C2SP firmata Ed25519, radice dei nodi, WORM prima della
    pubblicazione (oggetto con retention), foglie esportate, evento in outbox."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    for _ in range(7):
        await crea_pda(db_url, tenant, classe, n_doc=1)
    await _sequenzia()
    ident = identita()
    payload = await _emetti(ident)
    assert payload is not None and payload["dimensione"] == 7
    riga = (await sql(db_url, "SELECT * FROM public.catena_checkpoint"))[0]
    cp = nota.analizza(riga["testo"])
    hash_foglie = [bytes(r["hash"]) for r in await sql(
        db_url, "SELECT hash FROM public.catena_registro ORDER BY indice")]
    assert (cp.origine, cp.dimensione, cp.radice) == (
        "verumdoc.it/registro/v1/test", 7, radice(hash_foglie))
    assert [f.nome for f in nota.verifica(riga["testo"], [ident.firmatario.verificatore])] == \
        ["verumdoc-registro"]
    assert riga["stato_sigillo"] == "DA_FIRMARE" and riga["stato_testimone"] == "NON_CONFIGURATO"
    oggetto = s3.get_object(Bucket=settings.s3_bucket_aip, Key="registro/checkpoint/7.txt")
    assert oggetto["Body"].read().decode() == riga["testo"]
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    assert oggetto["ObjectLockLegalHoldStatus"] == "ON"
    # Foglie esportate a segmenti (3 per segmento): il registro si ricostruisce dal WORM
    righe_worm: list[str] = []
    for da, a in ((0, 3), (3, 6), (6, 7)):
        corpo = s3.get_object(Bucket=settings.s3_bucket_aip,
                              Key=f"registro/foglie/{da:012d}-{a:012d}.jsonl")["Body"].read()
        righe_worm += corpo.decode().splitlines()
    assert radice([hash_foglia(r.encode()) for r in righe_worm]) == cp.radice
    eventi = await _outbox(db_url, "catena.checkpoint")
    assert eventi == [{"chiave": "7", "dimensione": 7, "radice": cp.radice.hex(),
                       "emesso_il": "2026-10-23T08:00:00.000000Z", "testo": riga["testo"]}]
    # Registro fermo: nessun nuovo checkpoint
    assert await _emetti(ident, ORA + timedelta(minutes=1)) is None


async def test_checkpoint_senza_orizzonte_d_ambiente_usa_il_parametro(db_url, s3, monkeypatch):
    """R09 (D04, VD-C-070): senza RETENTION_PERMANENTE_ORIZZONTE_ANNI il checkpoint
    va nel WORM con l'orizzonte del parametro RETENTION_PERMANENTE_ANNI (30)."""
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", None)
    tenant, classe = await crea_tenant_e_classe(db_url)
    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    assert await _emetti(identita()) is not None
    assert len(await sql(db_url, "SELECT 1 FROM public.catena_checkpoint")) == 1


async def test_sigillo_checkpoint_stato_onesto(db_url, s3):
    """Senza fornitore: NON_FIRMATO con motivo; con fornitore: FIRMATO, busta nel WORM;
    servizio giù: resta DA_FIRMARE."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita()
    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await _emetti(ident)
    assert await checkpoint.sigilla_in_sospeso(FirmaFinta(STATO_DA_RITENTARE), adesso=ORA) == 0
    firma = FirmaFinta(STATO_FIRMATO)
    assert await checkpoint.sigilla_in_sospeso(firma, adesso=ORA) == 1
    corpo, _ = nota.separa((await sql(db_url, "SELECT testo FROM public.catena_checkpoint"))[0]
                           ["testo"])
    assert firma.chiamate == [corpo.encode()]   # si sigilla il corpo del checkpoint
    riga = (await sql(db_url, "SELECT stato_sigillo, sigillo_worm_chiave, data_marca "
                              "FROM public.catena_checkpoint"))[0]
    assert riga["stato_sigillo"] == "FIRMATO" and riga["data_marca"] is not None
    busta = s3.get_object(Bucket=settings.s3_bucket_aip, Key=riga["sigillo_worm_chiave"])
    assert busta["Body"].read() == b"busta" + corpo.encode()

    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await _emetti(ident)
    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=ORA)
    riga = (await sql(db_url, "SELECT stato_sigillo, motivo_sigillo FROM public.catena_checkpoint "
                              "WHERE dimensione = 2"))[0]
    assert riga["stato_sigillo"] == "NON_FIRMATO" and "non configurato" in riga["motivo_sigillo"]


# ── Testimone (K3, lato client) ─────────────────────────────────────

async def test_testimone_controfirma_verificata_e_registrata(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    for giro in range(3):
        for _ in range(giro + 2):
            await crea_pda(db_url, tenant, classe, n_doc=1)
        await _sequenzia()
        await _emetti(ident)
        esito = await checkpoint.invia_al_testimone(testimone.client(), detentore=IO, adesso=ORA)
        assert esito == "CONTROFIRMATO"
    assert testimone.dimensione == 9
    assert testimone.richieste[1].startswith("old 2\n")   # consistenza dal precedente
    riga = (await sql(db_url, "SELECT testo, controfirma, stato_testimone FROM "
                              "public.catena_checkpoint WHERE dimensione = 9"))[0]
    assert riga["stato_testimone"] == "CONTROFIRMATO"
    completa = archivio.nota_pubblicata(riga)
    assert {f.nome for f in nota.verifica(completa, [ident.firmatario.verificatore,
                                                     testimone.firmatario.verificatore])} == \
        {"verumdoc-registro", "verumdoc-testimone"}
    worm = s3.get_object(Bucket=settings.s3_bucket_aip,
                         Key="registro/checkpoint/9.controfirmato.txt")["Body"].read().decode()
    assert worm == completa
    assert (await sql(db_url, "SELECT testimone_dimensione FROM public.catena_stato"))[0][
        "testimone_dimensione"] == 9


async def test_testimone_409_riprova_dalla_sua_dimensione(db_url, s3):
    """409 con la dimensione che il testimone conosce: nuovo invio da quella."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    for _ in range(3):
        await crea_pda(db_url, tenant, classe, n_doc=1)
    await _sequenzia()
    await _emetti(ident)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    # Il registro «dimentica» quanto il testimone ha già controfirmato
    await sql(db_url, "UPDATE public.catena_stato SET testimone_dimensione = 0")
    for _ in range(2):
        await crea_pda(db_url, tenant, classe, n_doc=1)
    await _sequenzia()
    await _emetti(ident)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    assert [r.split("\n")[0] for r in testimone.richieste] == ["old 0", "old 0", "old 3"]


async def test_testimone_rifiuta_e_allarme_oltre_il_limite(db_url, s3, caplog):
    """422 → RIFIUTATO con motivo: allarme persistente dei rifiuti (non quello
    «senza controfirma», che riguarda chi attende); il checkpoint successivo NON
    supera il rifiutato (rev4 rilievo 1: un rifiuto non sparisce in silenzio)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore, risposta_forzata=(422, "no"))
    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await _emetti(ident)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "RIFIUTATO"
    riga = (await sql(db_url, "SELECT stato_testimone, esito_testimone FROM "
                              "public.catena_checkpoint"))[0]
    assert riga["stato_testimone"] == "RIFIUTATO" and "422" in riga["esito_testimone"]
    assert await checkpoint.controlla_controfirme(adesso=ORA + timedelta(seconds=601),
                                                  massimo_secondi=600) is None
    assert await checkpoint.controlla_rifiuti() == [1]
    from app.registro.metriche import metriche
    assert metriche().rifiutati_aperti._value.get() == 1
    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await _emetti(ident, ORA + timedelta(minutes=2))
    stati = [r["stato_testimone"] for r in await sql(
        db_url, "SELECT stato_testimone FROM public.catena_checkpoint ORDER BY dimensione")]
    assert stati == ["RIFIUTATO", "IN_ATTESA"]
    assert await checkpoint.controlla_controfirme(adesso=ORA + timedelta(minutes=13),
                                                  massimo_secondi=600) == 2
    assert metriche().senza_controfirma._value.get() >= 601


async def test_testimone_controfirma_falsa_non_registrata(db_url, s3):
    """Una «controfirma» che non verifica con la chiave del testimone non vale."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    falsa = nota.analizza_firmatario(nota.genera_chiave("verumdoc-testimone")[0])
    corpo_falso = falsa.firma("altro\n")
    testimone = FintoTestimone(ident.firmatario.verificatore,
                               risposta_forzata=(200, corpo_falso + "\n"))
    await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    await _emetti(ident)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "RIFIUTATO"
    assert (await sql(db_url, "SELECT controfirma FROM public.catena_checkpoint"))[0][
        "controfirma"] is None


# ── VD-C-097 e RdV definitivo v2 (K5) ───────────────────────────────

async def test_conservato_solo_nel_registro_e_coperto_da_checkpoint(db_url, s3):
    """VD-C-097: nessun CONSERVATO né evento finché il PdA non è nel registro, coperto
    da un checkpoint con sigillo definitivo; poi RdV v2 nel WORM, AIP e PdV
    CONSERVATO, evento aip.conserved con pda, foglia_indice, checkpoint_dimensione."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    avvisi = [{"codice": "RGL-104", "effetto": "AVVISA", "campo": "oggetto",
               "documento": "doc-0", "messaggio": "Oggetto molto breve"}]
    p = await crea_pda(db_url, tenant, classe, avvisi=avvisi)
    ident = identita()

    assert (await _completa(ident)).completati == []          # solo su storage
    await _sequenzia()
    assert (await _completa(ident)).completati == []          # nel registro, nessun checkpoint
    await _emetti(ident)
    assert (await _completa(ident)).completati == []          # sigillo ancora DA_FIRMARE
    assert await _outbox(db_url, "aip.conserved") == []
    stato = await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1", p.pdv_id)
    assert stato[0]["s"] == "IN_BUILD"

    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=ORA)
    esito = await _completa(ident, ORA + timedelta(seconds=5))
    assert esito.completati == [str(p.aip_id)] and esito.falliti == {}
    aip = (await sql(db_url, "SELECT stato::text AS s, data_conservazione FROM public.aip "
                             "WHERE id = $1", p.aip_id))[0]
    assert aip["s"] == "CONSERVATO" and aip["data_conservazione"] == ORA
    pdv = (await sql(db_url, "SELECT stato::text AS s, metadata_json FROM public.pdv "
                             "WHERE id = $1", p.pdv_id))[0]
    assert pdv["s"] == "CONSERVATO"
    rif = json.loads(pdv["metadata_json"])["rdv_definitiva"]
    assert rif["formato"] == "verumdoc-rdv-definitivo-2" and rif["checkpoint_dimensione"] == 1
    eventi = await _outbox(db_url, "aip.conserved")
    assert len(eventi) == 1
    evento = eventi[0]
    assert (evento["pda"], evento["foglia_indice"], evento["checkpoint_dimensione"],
            evento["fase"], evento["pdv_id"]) == (p.urn, 0, 1, "CONSERVATO", str(p.pdv_id))
    assert evento["radice_pda"] == p.radice and evento["num_files"] == 2

    oggetto = s3.get_object(Bucket=rif["s3_bucket"], Key=rif["s3_key"])
    xml = oggetto["Body"].read()
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    assert hashlib.sha256(xml).hexdigest() == rif["impronta_sha256"]
    assert verifica_rdv(xml, [ident.firmatario.verificatore]) == []
    radice_xml = ET.fromstring(xml)
    q = f"{{{NS}}}"
    assert radice_xml.tag == f"{q}RapportoVersamento" and "agid" not in xml.decode().lower()
    assert radice_xml.find(f"{q}Avvisi/{q}Avviso").get("codice") == "RGL-104"
    assert radice_xml.find(f"{q}Registro/{q}Checkpoint/{q}SigilloQualificato").get("stato") \
        == "NON_FIRMATO"
    documenti = radice_xml.findall(f"{q}Documenti/{q}Documento")
    assert [d.find(f"{q}Identificativo").text for d in documenti] == ["doc-0", "doc-1"]
    # Una seconda passata non fa nulla (idempotenza)
    assert (await _completa(ident, ORA + timedelta(seconds=10))).completati == []


async def test_rdv_alterato_rifiutato_dalla_verifica(db_url, s3):
    """Ogni alterazione del RdV (impronta, foglia, radice, nota) è rilevata."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    p = await crea_pda(db_url, tenant, classe)
    ident = identita()
    await _sequenzia()
    await _emetti(ident)
    cp = await _cp()
    xml, _ = await conservazione.costruisci_rdv_pda(str(p.aip_id), cp, emesso_il=ORA)
    assert verifica_rdv(xml, [ident.firmatario.verificatore]) == []
    testo = xml.decode()
    sha = hashlib.sha256(p.documenti[0][2]).hexdigest()
    for alterato in (
        testo.replace(sha, "0" * 64, 1),
        testo.replace(p.radice, "1" * 64, 1),
        testo.replace('"ente":"', '"ente":"x', 1),
    ):
        assert verifica_rdv(alterato.encode(), [ident.firmatario.verificatore]) != []
    # Chiave non fidata: la firma del registro non basta se la chiave è un'altra
    assert verifica_rdv(xml, [identita().firmatario.verificatore]) != []


async def _cp() -> dict:
    async with get_async_session() as session:
        cp = await archivio.ultimo_checkpoint(session)
    assert cp is not None
    return cp


async def test_completamento_attende_la_controfirma_anche_oltre_il_limite(db_url, s3):
    """Con il testimone configurato il PdA attende la controfirma, anche oltre il
    limite (rev4 rilievo 1: solo allarme, mai CONSERVATO senza controfirma); con
    la controfirma si completa e il RdV la dichiara."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    p = await crea_pda(db_url, tenant, classe)
    ident = identita(testimone=True)
    await _sequenzia()
    await _emetti(ident)
    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=ORA)
    for secondi in (100, 601, 86_400):
        esito = await conservazione.completa_pda_coperti(
            adesso=ORA + timedelta(seconds=secondi), ammetti_senza_testimone=True, limite=10,
            verificatore=ident.firmatario.verificatore)
        assert esito.completati == []
    assert await checkpoint.controlla_controfirme(adesso=ORA + timedelta(seconds=601),
                                                  massimo_secondi=600) == 1
    testimone = FintoTestimone(ident.firmatario.verificatore)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    esito = await conservazione.completa_pda_coperti(
        adesso=ORA + timedelta(seconds=700), ammetti_senza_testimone=False, limite=10,
        verificatore=ident.firmatario.verificatore)
    assert esito.completati == [str(p.aip_id)]
    rif = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                                p.pdv_id))[0]["metadata_json"])["rdv_definitiva"]
    assert rif["stato_testimone"] == "CONTROFIRMATO"


async def test_ciclo_un_giro_porta_a_conservato(db_url, s3):
    """Il ciclo di fondo: lease, sequenziatore, checkpoint, sigillo, testimone,
    completamento in un giro."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    p = await crea_pda(db_url, tenant, classe)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    ciclo = CicloCatena(
        config=ConfigCiclo(intervallo=1, checkpoint_secondi=60, controfirma_max_secondi=600,
                           lease_secondi=30, lotto=100, completamento_lotto=100,
                           segmento_worm=100, ripresa_secondi=60),
        identita=ident, servizio_firma=FirmaFinta(), testimone=testimone.client(),
        detentore="ciclo-di-prova", adesso=lambda: ORA, orologio=lambda: 0.0)
    await ciclo.giro()
    stato = (await sql(db_url, "SELECT stato::text AS s FROM public.aip WHERE id = $1",
                       p.aip_id))[0]["s"]
    assert stato == "CONSERVATO"
    cp = await _cp()
    assert cp["stato_testimone"] == "CONTROFIRMATO" and cp["stato_sigillo"] == "NON_FIRMATO"
    # Un secondo processo nello stesso istante non ottiene il lease e non fa nulla
    altro = CicloCatena(config=ciclo.config, identita=ident, servizio_firma=FirmaFinta(),
                        testimone=None, detentore="altro", adesso=lambda: ORA,
                        orologio=lambda: 0.0)
    await crea_pda(db_url, tenant, classe)
    await altro.giro()
    assert len(await sql(db_url, "SELECT 1 FROM public.catena_coda")) == 1


# ── API K1 ──────────────────────────────────────────────────────────

async def _get(percorso: str, **headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(percorso, headers=headers)


async def test_api_checkpoint_e_prove(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    altro_tenant, _ = await crea_tenant_e_classe(db_url)
    ident = identita()
    cliente = intestazioni("OPERATORE", tenant_id=str(tenant))
    assert (await _get("/api/v1/hashchain/checkpoint", **cliente)).status_code == 404

    pda = [await crea_pda(db_url, tenant, classe, n_doc=3) for _ in range(3)]
    await _sequenzia()
    await _emetti(ident)
    for _ in range(4):
        await crea_pda(db_url, altro_tenant, classe, n_doc=1)
    await _sequenzia()
    await _emetti(ident, ORA + timedelta(minutes=1))

    r = await _get("/api/v1/hashchain/checkpoint", **cliente)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert nota.analizza(r.text).dimensione == 7 and nota.verifica(
        r.text, [ident.firmatario.verificatore])
    vecchio = await _get("/api/v1/hashchain/checkpoint/3", **cliente)
    assert nota.analizza(vecchio.text).dimensione == 3
    assert (await _get("/api/v1/hashchain/checkpoint/5", **cliente)).json()["codice"] == "CAT-404"

    # Prova d'inclusione documento → PdA → registro, verificata da sola
    r = await _get(f"/api/v1/hashchain/prova/inclusione?pda={pda[1].urn}&documento=doc-2",
                   **cliente)
    assert r.status_code == 200, r.text
    corpo = r.json()
    cp = nota.analizza(corpo["checkpoint"])
    foglia_doc = json.loads(corpo["foglia_dati"])
    assert foglia_doc["doc"] == "doc-2" and foglia_doc["sha256"] == hashlib.sha256(
        pda[1].documenti[2][2]).hexdigest()
    pp = corpo["prova_pda"]
    assert verifica_inclusione(hash_foglia(corpo["foglia_dati"].encode()), pp["foglia_indice"],
                               pp["dimensione"], da_esadecimale(pp["percorso"]),
                               bytes.fromhex(corpo["radice_pda"]))
    assert json.loads(corpo["foglia_registro_dati"])["radice_pda"] == corpo["radice_pda"]
    pr = corpo["prova_registro"]
    assert pr["dimensione_albero"] == cp.dimensione == 7
    assert verifica_inclusione(hash_foglia(corpo["foglia_registro_dati"].encode()),
                               pr["foglia_indice"], 7, da_esadecimale(pr["percorso"]), cp.radice)
    # Rispetto a un checkpoint precedente
    r3 = await _get(f"/api/v1/hashchain/prova/inclusione?pda={pda[1].urn}&documento=doc-0"
                    "&dimensione=3", **cliente)
    assert r3.json()["prova_registro"]["dimensione_albero"] == 3

    # Isolamento: PdA di un altro ente = 404 (anche se esiste); conservatore vede tutto
    urn_altro = (await sql(db_url, "SELECT urn FROM public.aip WHERE tenant_id = $1 LIMIT 1",
                           altro_tenant))[0]["urn"]
    negato = await _get(f"/api/v1/hashchain/prova/inclusione?pda={urn_altro}&documento=doc-0",
                        **cliente)
    assert negato.status_code == 404 and negato.json()["codice"] == "CAT-404"
    assert (await _get(f"/api/v1/hashchain/prova/inclusione?pda={urn_altro}&documento=doc-0",
                       **intestazioni("SUPER_ADMIN"))).status_code == 200
    assert (await _get(f"/api/v1/hashchain/prova/inclusione?pda={urn_altro}&documento=doc-0"
                       "&dimensione=3", **intestazioni("SUPER_ADMIN"))).json()["codice"] \
        == "CAT-409"
    assert (await _get(f"/api/v1/hashchain/prova/inclusione?pda={pda[0].urn}&documento=x",
                       **cliente)).status_code == 404

    # Consistenza fra i due checkpoint
    r = await _get("/api/v1/hashchain/prova/consistenza?da=3&a=7", **cliente)
    corpo = r.json()
    radice3 = nota.analizza(vecchio.text).radice
    assert verifica_consistenza(3, 7, radice3, cp.radice, da_esadecimale(corpo["percorso"]))
    for errata in ("da=5&a=3", "da=1&a=8"):
        r = await _get(f"/api/v1/hashchain/prova/consistenza?{errata}", **cliente)
        assert r.status_code == 422 and r.json()["codice"] == "CAT-422"

    # Lo stato della catena per la dashboard riporta il registro
    registro = (await _get("/api/v1/hashchain/status", **cliente)).json()["registro"]
    assert registro["dimensione"] == 7
    assert registro["ultimo_checkpoint"]["dimensione"] == 7
    assert registro["ultimo_checkpoint"]["stato_sigillo"] == "DA_FIRMARE"
    assert registro["ultimo_checkpoint"]["stato_testimone"] == "NON_CONFIGURATO"


async def test_api_foglie_solo_conservatore_o_servizio(db_url, s3):
    from app.auth.jwt_handler import chiavi_jwt

    from libs.shared.security.jwt_rs256 import firma_token_servizio

    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita()
    for _ in range(5):
        await crea_pda(db_url, tenant, classe, n_doc=1)
    await _sequenzia()
    await _emetti(ident)
    await crea_pda(db_url, tenant, classe, n_doc=1)
    await _sequenzia()                                   # foglia 5 non ancora pubblicata

    operatore = await _get("/api/v1/hashchain/registro/foglie",
                           **intestazioni("OPERATORE", tenant_id=str(tenant)))
    assert operatore.status_code == 403 and operatore.json()["codice"] == "CAT-403"

    def servizio(scope: str) -> dict:
        tok = firma_token_servizio("testimone", chiave_privata_pem=chiavi_jwt().privata_pem,
                                   emittente=settings.jwt_issuer, durata_secondi=300,
                                   pubblico=settings.catena_pubblico_servizio, scopes=[scope])
        return {"Authorization": f"Bearer {tok}"}

    r = await _get("/api/v1/hashchain/registro/foglie?da=0&limite=3", **servizio("registro:leggi"))
    assert r.status_code == 200 and len(r.json()["foglie"]) == 3
    tutte = (await _get("/api/v1/hashchain/registro/foglie?da=0&limite=100",
                        **intestazioni("RSC_CONSERVATORE"))).json()
    assert tutte["dimensione_checkpoint"] == 5 and len(tutte["foglie"]) == 5
    testo = (await _get("/api/v1/hashchain/checkpoint", **intestazioni("SUPER_ADMIN"))).text
    assert radice([bytes.fromhex(h) for h in tutte["foglie"]]) == nota.analizza(testo).radice
    assert (await _get("/api/v1/hashchain/registro/foglie",
                       **servizio("firma:servizio"))).status_code == 403
    # Token di servizio di un altro pubblico (signature): non vale come lettore
    tok = firma_token_servizio("hashchain", chiave_privata_pem=chiavi_jwt().privata_pem,
                               emittente=settings.jwt_issuer, durata_secondi=300)
    assert (await _get("/api/v1/hashchain/registro/foglie",
                       Authorization=f"Bearer {tok}")).status_code == 401


async def test_api_registro_senza_token_401(db_url):
    for percorso in ("/api/v1/hashchain/checkpoint", "/api/v1/hashchain/checkpoint/1",
                     "/api/v1/hashchain/prova/inclusione?pda=x&documento=y",
                     "/api/v1/hashchain/prova/consistenza?da=0&a=1",
                     "/api/v1/hashchain/registro/foglie"):
        assert (await _get(percorso)).status_code == 401, percorso


async def test_api_parametri_non_validi_422_mai_500(db_url):
    h = intestazioni("SUPER_ADMIN")
    for percorso in ("/api/v1/hashchain/checkpoint/abc", "/api/v1/hashchain/checkpoint/-1",
                     "/api/v1/hashchain/prova/consistenza?da=-1&a=1",
                     "/api/v1/hashchain/registro/foglie?limite=0",
                     "/api/v1/hashchain/prova/inclusione?pda=&documento=x"):
        r = await _get(percorso, **h)
        assert r.status_code == 422, (percorso, r.status_code)


def test_base64_radice_nel_checkpoint():
    """La radice del checkpoint è base64 standard dei 32 byte (K2)."""
    cp = nota.Checkpoint("o", 1, hashlib.sha256(b"x").digest())
    assert nota.formatta(cp).split("\n")[2] == base64.b64encode(cp.radice).decode()
