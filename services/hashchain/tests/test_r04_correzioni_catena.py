# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Correzioni della revisione indipendente R04 (rev4) sul registro delle impronte:

  - rilievo 1: un checkpoint RIFIUTATO dal testimone non porta MAI a CONSERVATO;
    il rifiuto è un allarme persistente che sospende il completamento finché un
    operatore non lo gestisce; un checkpoint non controfirmato si usa senza
    controfirma solo senza testimone in un ambiente che lo ammette; a parità si
    preferisce il controfirmato; 423 del testimone = rifiuto non ritentabile;
  - rilievo 8: con ENV=production il testimone è obbligatorio; allarme se il
    testimone non risponde (anche quando il registro non cresce).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from app.config import settings
from app.registro import archivio, checkpoint, chiavi, conservazione, operatore, sequenziatore
from app.registro.ciclo import CicloCatena, ConfigCiclo
from app.registro.metriche import metriche
from app.registro.testimone import ClientTestimone
from app.services import block_sealer
from pydantic import SecretStr

from libs.catena import nota
from libs.shared.db import get_async_session
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.supporto_registro import FintoTestimone, FirmaFinta, crea_pda, identita

ORA = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
IO = "detentore-correzioni"


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


async def _lease(adesso: datetime) -> None:
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, adesso,
                                               adesso + timedelta(seconds=3600))


async def _cresci(url, tenant, classe, quanti: int, ident, adesso: datetime) -> list:
    """Nuovi PdA nel registro e un checkpoint sigillato (NON_FIRMATO)."""
    pda = [await crea_pda(url, tenant, classe, n_doc=1) for _ in range(quanti)]
    await _lease(adesso)
    await sequenziatore.aggiungi_lotto(detentore=IO, adesso=adesso, limite=1000)
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=adesso, identita=ident, segmento=10)
    await checkpoint.sigilla_in_sospeso(FirmaFinta(), adesso=adesso)
    return pda


async def _completa(ident, adesso: datetime, *, ammetti: bool = False):
    return await conservazione.completa_pda_coperti(
        adesso=adesso, ammetti_senza_testimone=ammetti, limite=100,
        verificatore=ident.firmatario.verificatore)


async def _stati_aip(url, pda) -> list[str]:
    return [(await sql(url, "SELECT stato::text AS s FROM public.aip WHERE id = $1",
                       p.aip_id))[0]["s"] for p in pda]


async def _conservati(url) -> list[dict]:
    return [json.loads(r["payload"]) for r in await sql(
        url, "SELECT payload FROM public.outbox_eventi WHERE topic = 'aip.conserved' "
             "ORDER BY sequenza")]


# ── Rilievo 1 ───────────────────────────────────────────────────────

@pytest.mark.integration
@richiede_pg
async def test_checkpoint_rifiutato_mai_conservato_allarme_persistente(db_url, s3):
    """Lo scenario del revisore: un checkpoint CONTROFIRMATO (2) e uno RIFIUTATO (4)
    con 422 emesso da oltre 600 s. Nessun PdA passa a CONSERVATO finché il rifiuto è
    aperto; dopo la gestione dell'operatore si completa SOLO quanto è coperto dal
    controfirmato, mai col rifiutato."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    primi = await _cresci(db_url, tenant, classe, 2, ident, ORA)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    secondi = await _cresci(db_url, tenant, classe, 2, ident, ORA + timedelta(seconds=1))
    testimone.risposta_forzata = (422, "prova non valida")
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "RIFIUTATO"

    dopo = ORA + timedelta(seconds=3600)
    esito = await _completa(ident, dopo, ammetti=True)
    assert esito.completati == [] and esito.sospeso and "rifiutati" in esito.sospeso
    assert await _stati_aip(db_url, primi + secondi) == ["IN_CATENA"] * 4
    assert await _conservati(db_url) == []
    # Allarme persistente: a ogni giro, anche dopo un riavvio (stato nel DB)
    assert await checkpoint.controlla_rifiuti() == [4]
    assert metriche().rifiutati_aperti._value.get() == 1
    # Il checkpoint successivo non «supera» il rifiutato
    await _cresci(db_url, tenant, classe, 1, ident, ORA + timedelta(seconds=2))
    stati = {r["dimensione"]: r["stato_testimone"] for r in await sql(
        db_url, "SELECT dimensione, stato_testimone FROM public.catena_checkpoint")}
    assert stati == {2: "CONTROFIRMATO", 4: "RIFIUTATO", 5: "IN_ATTESA"}
    # Il trigger impedisce di «riabilitare» un rifiuto da SQL
    with pytest.raises(asyncpg.RaiseError, match="rifiutato dal testimone"):
        await sql(db_url, "UPDATE public.catena_checkpoint SET stato_testimone = 'SUPERATO' "
                          "WHERE dimensione = 4")

    # Gestione dell'operatore (CLI): motivo obbligatorio, voce di audit
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuto", "--dimensione", "4", "--operatore", "Op", "--motivo", "breve"])) == 1
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuto", "--dimensione", "4", "--operatore", "Mario Rossi",
        "--motivo", "422 da prova costruita male: nessuna biforcazione"])) == 0
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuto", "--dimensione", "4", "--operatore", "Mario Rossi",
        "--motivo", "seconda volta: non ammessa dal trigger né dal codice"])) == 1
    audit = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                              "WHERE evento = 'catena.rifiuti_testimone_gestiti'")
    assert len(audit) == 1 and json.loads(audit[0]["dettagli"])["dimensioni"] == [4, 4]
    assert await checkpoint.controlla_rifiuti() == []

    esito = await _completa(ident, dopo, ammetti=True)
    assert sorted(esito.completati) == sorted(str(p.aip_id) for p in primi)
    assert await _stati_aip(db_url, secondi) == ["IN_CATENA"] * 2
    assert {e["checkpoint_dimensione"] for e in await _conservati(db_url)} == {2}
    riga = (await sql(db_url, "SELECT stato_testimone, rifiuto_gestito_da FROM "
                              "public.catena_checkpoint WHERE dimensione = 4"))[0]
    assert (riga["stato_testimone"], riga["rifiuto_gestito_da"]) == ("RIFIUTATO", "Mario Rossi")


@pytest.mark.integration
@richiede_pg
async def test_rifiuto_423_testimone_bloccato_non_ritentato(db_url, s3):
    """423 (testimone bloccato da una biforcazione) = RIFIUTATO, non ritentato."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore,
                               risposta_forzata=(423, "bloccato"))
    await _cresci(db_url, tenant, classe, 1, ident, ORA)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "RIFIUTATO"
    # Nessun nuovo invio dello stesso checkpoint
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) is None
    assert len(testimone.richieste) == 1
    riga = (await sql(db_url, "SELECT esito_testimone FROM public.catena_checkpoint"))[0]
    assert riga["esito_testimone"].startswith("423")


async def test_client_423_rifiuto_non_ritentabile():
    priv, _ = nota.genera_chiave("verumdoc-registro")
    corpo = nota.formatta(nota.Checkpoint("o", 1, b"\x00" * 32))
    firmata = nota.componi(corpo, [nota.analizza_firmatario(priv).firma(corpo)])
    v = nota.analizza_firmatario(nota.genera_chiave("verumdoc-testimone")[0]).verificatore
    client = ClientTestimone("http://t", "tok", v, transport=httpx.MockTransport(
        lambda _r: httpx.Response(423, json={"codice": "TST-423"})))
    esito = await client.invia(0, [], firmata)
    assert esito.esito == "RIFIUTATO" and esito.motivo.startswith("423")


@pytest.mark.integration
@richiede_pg
async def test_non_controfirmato_mai_con_testimone_configurato(db_url, s3):
    """Testimone configurato che tace: nessun completamento, a nessuna età, anche
    se l'ambiente ammetterebbe il registro senza testimone (lo stato è IN_ATTESA,
    non NON_CONFIGURATO)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    pda = await _cresci(db_url, tenant, classe, 1, ident, ORA)
    for secondi in (0, 601, 30 * 86_400):
        esito = await _completa(ident, ORA + timedelta(seconds=secondi), ammetti=True)
        assert esito.completati == []
    assert await _stati_aip(db_url, pda) == ["IN_CATENA"]


@pytest.mark.integration
@richiede_pg
async def test_senza_testimone_solo_dove_l_ambiente_lo_ammette(db_url, s3):
    """NON_CONFIGURATO: si completa solo se ammesso (fuori da produzione)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=False)
    pda = await _cresci(db_url, tenant, classe, 1, ident, ORA)
    assert (await _completa(ident, ORA, ammetti=False)).completati == []
    assert (await _completa(ident, ORA, ammetti=True)).completati == [str(pda[0].aip_id)]


@pytest.mark.integration
@richiede_pg
async def test_a_parita_si_preferisce_il_controfirmato(db_url, s3):
    """Checkpoint 2 CONTROFIRMATO e 4 NON_CONFIGURATO (testimone tolto dopo, in un
    ambiente che lo ammette): i PdA coperti da entrambi usano il controfirmato."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    primi = await _cresci(db_url, tenant, classe, 2, ident, ORA)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    senza = identita(testimone=False)
    senza = checkpoint.Identita(ident.firmatario, senza.origine, False)
    secondi = await _cresci(db_url, tenant, classe, 2, senza, ORA + timedelta(seconds=1))
    esito = await _completa(ident, ORA + timedelta(seconds=2), ammetti=True)
    assert len(esito.completati) == 4
    per_pdv = {}
    for p in primi + secondi:
        meta = (await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                          p.pdv_id))[0]["metadata_json"]
        per_pdv[p.pdv_id] = json.loads(meta)["rdv_definitiva"]
    assert [per_pdv[p.pdv_id]["checkpoint_dimensione"] for p in primi] == [2, 2]
    assert [per_pdv[p.pdv_id]["stato_testimone"] for p in primi] == ["CONTROFIRMATO"] * 2
    assert [per_pdv[p.pdv_id]["checkpoint_dimensione"] for p in secondi] == [4, 4]


# ── Rilievo 8 ───────────────────────────────────────────────────────

def _cfg(**altro):
    base = {"env": "production", "catena_origine": "",
            "catena_chiave_privata": SecretStr(nota.genera_chiave("verumdoc-registro")[0]),
            "testimone_url": "", "testimone_verificatore": "",
            "testimone_service_token": SecretStr(""), "catena_checkpoint_secondi": 60.0,
            "catena_controfirma_max_secondi": 600.0,
            "catena_sequenziatore_intervallo_secondi": 1.0, "catena_lease_secondi": 30.0,
            "catena_sequenziatore_lotto": 1000, "catena_completamento_lotto": 200,
            "catena_worm_segmento_foglie": 10000, "testimone_sonda_secondi": 60.0}
    base.update(altro)
    return SimpleNamespace(**base)


def test_produzione_senza_testimone_non_parte():
    with pytest.raises(chiavi.ConfigurazioneCatenaNonValida,
                       match="TESTIMONE_URL obbligatorio con ENV=production"):
        chiavi.verifica_configurazione(_cfg())
    assert not chiavi.testimone_facoltativo(_cfg())
    assert chiavi.testimone_facoltativo(_cfg(env="staging"))
    assert chiavi.problemi_configurazione(_cfg(env="staging")) == []
    configurato = _cfg(testimone_url="https://testimone",
                       testimone_verificatore=nota.genera_chiave("verumdoc-testimone")[1],
                       testimone_service_token=SecretStr("t"))
    assert chiavi.problemi_configurazione(configurato) == []


async def test_avvio_hashchain_in_produzione_senza_testimone_fallisce(monkeypatch):
    """Il lifespan si ferma prima di aprire il database."""
    from app import main as principale

    monkeypatch.setattr(principale.settings, "env", "production")
    monkeypatch.setattr(principale.settings, "testimone_url", "")
    monkeypatch.setattr(principale.settings, "catena_chiave_privata",
                        SecretStr(nota.genera_chiave("verumdoc-registro")[0]))
    with pytest.raises(chiavi.ConfigurazioneCatenaNonValida, match="TESTIMONE_URL"):
        async with principale.lifespan(principale.app):
            pass  # pragma: no cover


@pytest.mark.integration
@richiede_pg
async def test_allarme_testimone_che_non_risponde(db_url, s3, caplog):
    """Registro fermo (nulla da inviare): la sonda periodica rileva il testimone
    che non risponde (metrica a 0, log d'errore) e il ritorno (metrica a 1)."""
    ident = identita(testimone=True)
    giu = {"si": True}

    def gestore(_r: httpx.Request) -> httpx.Response:
        if giu["si"]:
            raise httpx.ConnectError("rifiutata")
        return httpx.Response(404, text="nessun checkpoint")

    client = ClientTestimone("http://t", "tok", ident.firmatario.verificatore,
                             transport=httpx.MockTransport(gestore))
    orologio = {"t": 0.0}
    ciclo = CicloCatena(
        config=ConfigCiclo(intervallo=1, checkpoint_secondi=60, controfirma_max_secondi=600,
                           lease_secondi=30, lotto=10, completamento_lotto=10,
                           segmento_worm=10, ripresa_secondi=60, sonda_testimone_secondi=60),
        identita=ident, servizio_firma=FirmaFinta(), testimone=client, detentore=IO,
        adesso=lambda: ORA, orologio=lambda: orologio["t"])
    checkpoint._raggiungibile = None
    await ciclo.giro()
    assert metriche().testimone_configurato._value.get() == 1
    assert metriche().testimone_raggiungibile._value.get() == 0
    giu["si"] = False
    orologio["t"] = 30.0          # prima della prossima sonda: nessuna lettura
    await ciclo.giro()
    assert metriche().testimone_raggiungibile._value.get() == 0
    orologio["t"] = 61.0
    await ciclo.giro()
    assert metriche().testimone_raggiungibile._value.get() == 1


# ── Rilievo 12 ──────────────────────────────────────────────────────

@pytest.mark.integration
@richiede_pg
async def test_rdv_riusato_riferimento_ed_evento_citano_il_suo_checkpoint(db_url, s3):
    """Crash dopo la scrittura del RdV nel WORM e prima del commit; al giro dopo
    c'è un checkpoint più recente. Prima: RdV nel WORM con il checkpoint 1,
    riferimento ed evento con il 2. Ora: tutti citano il checkpoint 1."""
    from app.registro.conservazione import NOME_RDV_V2
    from app.services.block_sealer import _carica_rdv

    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=False)
    [p] = await _cresci(db_url, tenant, classe, 1, ident, ORA)
    async with get_async_session() as session:
        cp1 = await archivio.ultimo_checkpoint(session)
    xml, _ = await conservazione.costruisci_rdv_pda(str(p.aip_id), cp1, emesso_il=ORA)
    riga = (await sql(db_url, "SELECT data_versamento FROM public.aip WHERE id = $1",
                      p.aip_id))[0]
    await _carica_rdv({"id": str(p.aip_id), "tenant_id": str(tenant),
                       "data_versamento": riga["data_versamento"], "anni_conservazione": 10,
                       "conservazione_permanente": False}, xml, nome=NOME_RDV_V2)
    # ... crash: nessun commit. Il registro cresce, nuovo checkpoint (2).
    await _cresci(db_url, tenant, classe, 1, ident, ORA + timedelta(seconds=1))
    esito = await _completa(ident, ORA + timedelta(seconds=2), ammetti=True)
    assert str(p.aip_id) in esito.completati
    rif = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                                p.pdv_id))[0]["metadata_json"])["rdv_definitiva"]
    evento = [e for e in await _conservati(db_url) if e["aip_id"] == str(p.aip_id)][0]
    oggetto = s3.get_object(Bucket=rif["s3_bucket"], Key=rif["s3_key"])["Body"].read()
    from app.registro.rdv import riferimenti_rdv

    nel_worm = riferimenti_rdv(oggetto)["checkpoint_dimensione"]
    assert nel_worm == 1
    assert rif["checkpoint_dimensione"] == evento["checkpoint_dimensione"] == nel_worm
    assert rif["emessa_il"] == riferimenti_rdv(oggetto)["emessa_il"]


# ── Secondo giro, N3: rifiuti raggruppati in allarmi, gestione in blocco ────

@pytest.mark.integration
@richiede_pg
async def test_rifiuti_con_la_stessa_causa_un_solo_allarme_gestione_in_blocco(db_url, s3,
                                                                              capsys):
    """Prova del revisore (prova_checkpoint_rifiutato_v2.py §6): un'ora di 403 =
    60 checkpoint RIFIUTATI da gestire uno per uno. Ora: i rifiuti consecutivi
    con la stessa causa formano UN allarme con il contatore; una causa diversa
    apre un secondo allarme; l'operatore gestisce l'incidente in blocco, con un
    motivo, in UNA operazione registrata; il completamento riprende."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    primi = await _cresci(db_url, tenant, classe, 1, ident, ORA)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=ORA) == "CONTROFIRMATO"
    testimone.risposta_forzata = (403, "firma non valida")
    for minuto in range(1, 7):  # un checkpoint al minuto, tutti rifiutati con 403
        adesso = ORA + timedelta(minutes=minuto)
        await _cresci(db_url, tenant, classe, 1, ident, adesso)
        assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                                   adesso=adesso) == "RIFIUTATO"
    testimone.risposta_forzata = (423, "bloccato")
    adesso = ORA + timedelta(minutes=7)
    await _cresci(db_url, tenant, classe, 1, ident, adesso)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=adesso) == "RIFIUTATO"

    allarmi = await sql(db_url, "SELECT id, causa, rifiuti, prima_dimensione, "
                                "ultima_dimensione FROM public.catena_allarmi_testimone "
                                "WHERE gestito_il IS NULL ORDER BY id")
    assert [(a["causa"], a["rifiuti"], a["prima_dimensione"], a["ultima_dimensione"])
            for a in allarmi] == [("HTTP_403", 6, 2, 7), ("HTTP_423", 1, 8, 8)]
    assert len(await checkpoint.controlla_rifiuti()) == 7
    assert metriche().allarmi_testimone._value.get() == 2
    assert await operatore.esegui(operatore.analizza(["rifiuti"])) == 0
    elenco = [r for r in capsys.readouterr().out.splitlines() if r.startswith("allarme ")]
    assert len(elenco) == 2 and "HTTP_403" in elenco[0] and "rifiuti=6" in elenco[0]

    esito = await _completa(ident, adesso + timedelta(hours=1), ammetti=True)
    assert esito.completati == [] and esito.sospeso

    # Gestione in blocco: motivo obbligatorio
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuti", "--tutti", "--conferma-causa", "HTTP_403", "--operatore", "Op",
        "--motivo", "breve"])) == 1
    # T3 (terzo giro): --tutti senza conferma di ogni causa non chiude nulla, e la
    # biforcazione (423) non si chiude mai in blocco
    motivo = "chiave del testimone ruotata male: corretta, nessuna biforcazione"
    for argomenti in (["--tutti"],
                      ["--tutti", "--conferma-causa", "HTTP_403", "--conferma-causa", "HTTP_423"]):
        assert await operatore.esegui(operatore.analizza([
            "gestisci-rifiuti", *argomenti, "--operatore", "Mario Rossi",
            "--motivo", motivo])) == 1
    assert len(await checkpoint.controlla_rifiuti()) == 7
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuti", "--tutti", "--conferma-causa", "HTTP_403", "--operatore",
        "Mario Rossi", "--motivo", motivo])) == 0
    audit = [json.loads(r["dettagli"]) for r in await sql(
        db_url, "SELECT dettagli FROM public.audit_global "
                "WHERE evento = 'catena.rifiuti_testimone_gestiti'")]
    assert len(audit) == 1
    assert audit[0]["checkpoint"] == 6 and audit[0]["dimensioni"] == [2, 7]
    assert [a["causa"] for a in audit[0]["allarmi"]] == ["HTTP_403"]
    assert [a["causa"] for a in audit[0]["rimasti_aperti"]] == ["HTTP_423"]
    assert await checkpoint.controlla_rifiuti() == [8]
    assert metriche().allarmi_testimone._value.get() == 1
    # la biforcazione: né dal checkpoint né in blocco, solo con il suo id
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuto", "--dimensione", "8", "--operatore", "Mario Rossi",
        "--motivo", "biforcazione esaminata a parte con il testimone"])) == 1
    biforcazione = (await sql(db_url, "SELECT id FROM public.catena_allarmi_testimone "
                                      "WHERE causa = 'HTTP_423'"))[0]["id"]
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuti", "--allarme", str(biforcazione), "--operatore", "Mario Rossi",
        "--motivo", "testimone bloccato da un registro di prova: sbloccato, nessun fork"])) == 0
    assert len(await sql(db_url, "SELECT 1 FROM public.audit_global "
                                 "WHERE evento = 'catena.rifiuti_testimone_gestiti'")) == 2
    assert await checkpoint.controlla_rifiuti() == []
    assert metriche().allarmi_testimone._value.get() == 0
    gestiti = await sql(db_url, "SELECT count(*) AS n FROM public.catena_checkpoint "
                                "WHERE stato_testimone = 'RIFIUTATO' "
                                "AND rifiuto_gestito_da = 'Mario Rossi'")
    assert gestiti[0]["n"] == 7
    # niente da gestire: errore, nessuna voce nuova
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuti", "--tutti", "--conferma-causa", "HTTP_403", "--operatore",
        "Mario Rossi", "--motivo", "di nuovo, ma non c'è più nulla di aperto"])) == 1
    esito = await _completa(ident, adesso + timedelta(hours=1), ammetti=True)
    assert sorted(esito.completati) == sorted(str(p.aip_id) for p in primi)

    # Un rifiuto dopo la gestione apre un allarme NUOVO
    testimone.risposta_forzata = (403, "firma non valida")
    adesso = ORA + timedelta(minutes=8)
    await _cresci(db_url, tenant, classe, 1, ident, adesso)
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO,
                                               adesso=adesso) == "RIFIUTATO"
    aperti = await sql(db_url, "SELECT causa, rifiuti FROM public.catena_allarmi_testimone "
                               "WHERE gestito_il IS NULL")
    assert [(a["causa"], a["rifiuti"]) for a in aperti] == [("HTTP_403", 1)]


@pytest.mark.integration
@richiede_pg
async def test_gestione_di_un_solo_allarme(db_url, s3):
    """``--allarme ID`` chiude solo quell'incidente; gli altri restano aperti."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    for minuto, risposta in enumerate(((422, "prova"), (422, "prova"), (423, "bloccato"))):
        testimone.risposta_forzata = risposta
        adesso = ORA + timedelta(minutes=minuto)
        await _cresci(db_url, tenant, classe, 1, ident, adesso)
        await checkpoint.invia_al_testimone(testimone.client(), detentore=IO, adesso=adesso)
    allarmi = {a["causa"]: a["id"] for a in await sql(
        db_url, "SELECT id, causa FROM public.catena_allarmi_testimone")}
    assert set(allarmi) == {"HTTP_422", "HTTP_423"}
    assert await operatore.esegui(operatore.analizza([
        "gestisci-rifiuti", "--allarme", str(allarmi["HTTP_422"]), "--operatore", "Op",
        "--motivo", "prove ricostruite dopo il guasto del disco"])) == 0
    aperti = await sql(db_url, "SELECT causa FROM public.catena_allarmi_testimone "
                               "WHERE gestito_il IS NULL")
    assert [a["causa"] for a in aperti] == ["HTTP_423"]
    assert await checkpoint.controlla_rifiuti() == [3]


@pytest.mark.integration
@richiede_pg
async def test_registro_sospeso_dopo_un_ripristino_non_sequenzia_ne_invia(db_url, s3):
    """Runbook ripristino_registro_da_backup.md: con CATENA_REGISTRO_SOSPESO il
    ciclo non rimette in registro la coda (nessun ordine nuovo = nessun falso
    fork) e non contatta il testimone; la API resta per i controlli."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    ident = identita(testimone=True)
    testimone = FintoTestimone(ident.firmatario.verificatore)
    await crea_pda(db_url, tenant, classe, n_doc=1)
    config = {"intervallo": 1, "checkpoint_secondi": 60, "controfirma_max_secondi": 600,
              "lease_secondi": 30, "lotto": 10, "completamento_lotto": 10,
              "segmento_worm": 10, "ripresa_secondi": 60, "sonda_testimone_secondi": 60}
    ciclo = CicloCatena(config=ConfigCiclo(**config, sospeso=True), identita=ident,
                        servizio_firma=FirmaFinta(), testimone=testimone.client(),
                        detentore=IO, adesso=lambda: ORA, orologio=lambda: 0.0)
    await ciclo.giro()
    assert (await sql(db_url, "SELECT dimensione FROM public.catena_stato"))[0]["dimensione"] == 0
    assert testimone.richieste == []
    ciclo.config = ConfigCiclo(**config)
    await ciclo.giro()
    assert (await sql(db_url, "SELECT dimensione FROM public.catena_stato"))[0]["dimensione"] == 1
