# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12b (VD-C-160) — autorizzazione del produttore alla RESTITUZIONE legata alla pratica.

La restituzione (riversamento in uscita verso l'ente a fine contratto) è l'unico
riversamento in uscita con l'autorizzazione firmata del produttore nella macchina a
stati. Come per lo scarto (VD-C-145):
  - caricamento: il documento deve riportare l'identificativo della pratica nel
    contenuto firmato da un firmatario autorizzato (422 altrimenti);
  - lavoratore (controllo di sicurezza, API compromessa): prima di esportare
    riverifica dal WORM iter, firma, firmatario, riferimento e riuso; con un difetto
    la pratica è BLOCCATA e nulla si esporta; altrimenti scrive il fatto
    ``RESTITUZIONE`` / ``AUTORIZZAZIONI_VERIFICATE`` (migrazione 20261106_053);
  - riuso: lo stesso documento (stessa impronta) già allegato PRIMA a un'altra
    restituzione dell'ente blocca; un documento di una pratica di un ALTRO tipo
    (scarto) non conta (stessa logica corretta in R12a);
  - transizione (come VD-P-115): gli allegati delle restituzioni aperte alla
    migrazione 20261106_053 passano con avviso, mai se riusati.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from libs.shared.firme import imposta_verificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from tests.firme_prova import PKI, autorizzazione
from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    PASSATO,
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pda,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
)
from tests.test_r05_revisione2_scarto_incompleto import _lavora_finche
from tests.test_r05_revisione4_scarto import _bloccata, _difetti
from tests.test_r08_firme_ciclo_vita import Giu

pytestmark = [pytest.mark.integration, richiede_pg]

VECCHIO = autorizzazione("documento generico senza pratica")
CORPO = {"motivo": "Fine del contratto di conservazione"}


async def _scenario(db_url, s3_ciclo, *, pda: int = 2) -> dict:  # type: ignore[no-untyped-def]
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant)
    for _ in range(pda):
        pdv = await crea_pdv(db_url, tenant, classe, admin)
        await crea_pda(db_url, tenant, pdv, scadenza=PASSATO, stato="CONSERVATO")
    conservatore = await crea_ente(db_url)
    rsc = await crea_utente(db_url, conservatore, "RSC_CONSERVATORE")
    bucket_worm(s3_ciclo)
    return {"tenant": tenant, "admin": intestazioni(tenant, "ADMIN_TENANT", admin),
            "rsc": intestazioni(conservatore, "RSC_CONSERVATORE", rsc)}


async def _proposta(c, sc) -> str:  # type: ignore[no-untyped-def]
    corpo = {"ente": str(sc["tenant"]), **CORPO}
    piano = (await c.post("/api/v1/ciclo-vita/pratiche/restituzione", headers=sc["rsc"],
                          json=corpo)).json()
    r = await c.post("/api/v1/ciclo-vita/pratiche/restituzione", headers=sc["rsc"],
                     json={**corpo, "impronta_piano": piano["impronta_piano"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=sc["rsc"], json={
        "stato_atteso": "BOZZA", "nuovo_stato": "PROPOSTA", "motivo": "Proposta all'ente"})
    assert r.status_code == 200, r.text
    return pid


async def _carica(c, sc, pid, contenuto: bytes, *, giu: bool = False) -> dict:  # type: ignore[no-untyped-def]
    """``giu``: API compromessa (qui: verificatore giù al caricamento), il documento
    entra «da verificare» e decide il lavoratore."""
    if giu:
        imposta_verificatore(Giu())  # type: ignore[arg-type]
    try:
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                         data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                         files={"file": ("autorizzazione.p7m", contenuto,
                                         "application/pkcs7-mime")})
    finally:
        imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
    return r.json() | {"status": r.status_code}


async def _fino_a_eseguita(c, sc, pid, allegato_id: str) -> None:  # type: ignore[no-untyped-def]
    passi = ((sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE", [allegato_id], 200),
             (sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE", [], 200),
             (sc["rsc"], "ESEGUIBILE", "ESEGUITA", [], 202))
    for chi, da, a, allegati, atteso in passi:
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi, json={
            "stato_atteso": da, "nuovo_stato": a, "allegati": allegati,
            "motivo": "Passo della restituzione di prova"})
        assert r.status_code == atteso, r.text


async def _in_transizione(db_url, allegato_id: str, pid: str) -> None:  # type: ignore[no-untyped-def]
    """Come la migrazione 20261106_053 (superutente: nessun ruolo dei servizi la scrive)."""
    await sql(db_url, "INSERT INTO public.ciclo_vita_transizione_r12 (allegato_id, "
                      "pratica_id, tipo) VALUES ($1::uuid, $2::uuid, 'AUTORIZZAZIONE_PRODUTTORE')",
              allegato_id, pid)


async def _stato(db_url, pid) -> str:  # type: ignore[no-untyped-def]
    return (await sql(db_url, "SELECT stato FROM public.pratiche WHERE id = $1::uuid",
                      pid))[0]["stato"]


async def _fatto(db_url, pid) -> list[dict]:  # type: ignore[no-untyped-def]
    return await sql(db_url, "SELECT tipo, dettagli::text AS d FROM public.esiti_esecuzione "
                             "WHERE pratica_id = $1::uuid AND esito = "
                             "'AUTORIZZAZIONI_VERIFICATE'", pid)


async def test_caricamento_autorizzazione_di_un_altra_pratica_rifiutato(db_url, s3_ciclo) -> None:
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        pid = await _proposta(c, sc)
        altra = await _carica(c, sc, pid, autorizzazione(str(uuid.uuid4())))
        giusta = await _carica(c, sc, pid, autorizzazione(pid))
    assert altra["status"] == 422 and altra["codice"] == "LC-422-FIRMA", altra
    assert "identificativo della pratica" in altra["detail"]
    assert giusta["status"] == 201, giusta


async def test_lavoratore_blocca_documento_di_un_altra_pratica_e_non_esporta(db_url,
                                                                             s3_ciclo) -> None:
    sc = await _scenario(db_url, s3_ciclo)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _proposta(c, sc)
        aut = await _carica(c, sc, pid, autorizzazione(str(uuid.uuid4())), giu=True)
        assert aut["status"] == 201, aut
        await _fino_a_eseguita(c, sc, pid, aut["id"])
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert bloccata["lavoro"] is None and "identificativo della pratica" in _difetti(bloccata)
    assert dip.esportatore.richieste == []          # nessun PdD chiesto a distribution
    assert await _fatto(db_url, pid) == []
    audit = await sql(db_url, "SELECT count(*) AS n FROM public.audit_global WHERE evento = "
                              "'ciclo_vita.restituzione.autorizzazioni_non_verificate'")
    assert audit[0]["n"] == 1


async def test_restituzione_legata_si_esegue_con_il_fatto_del_lavoratore(db_url,
                                                                         s3_ciclo) -> None:
    sc = await _scenario(db_url, s3_ciclo)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _proposta(c, sc)
        aut = await _carica(c, sc, pid, autorizzazione(pid), giu=True)
        await _fino_a_eseguita(c, sc, pid, aut["id"])
        await _lavora_finche(db_url, dip, pid)
    assert await _stato(db_url, pid) == "ESEGUITA", await _bloccata(db_url, pid)
    assert [f["tipo"] for f in await _fatto(db_url, pid)] == ["RESTITUZIONE"]
    assert len(dip.esportatore.richieste) == 1


async def test_transizione_senza_riferimento_con_avviso(db_url, s3_ciclo) -> None:
    """Restituzione aperta prima della migrazione 053 (allegato nell'elenco): senza il
    riferimento alla pratica si esegue, con l'avviso nel fatto del lavoratore."""
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        pid = await _proposta(c, sc)
        aut = await _carica(c, sc, pid, VECCHIO, giu=True)
        await _in_transizione(db_url, aut["id"], pid)
        await _fino_a_eseguita(c, sc, pid, aut["id"])
        await _lavora_finche(db_url, dipendenze(db_url), pid)
    assert await _stato(db_url, pid) == "ESEGUITA", await _bloccata(db_url, pid)
    fatto = await _fatto(db_url, pid)
    assert fatto and "prima dell'obbligo" in fatto[0]["d"]


async def test_riuso_nella_stessa_specie_bloccato_anche_in_transizione(db_url, s3_ciclo) -> None:
    """Lo stesso documento allegato a una restituzione respinta e poi a una nuova
    restituzione dell'ente (in transizione): il lavoratore blocca (ALLEGATO_GIA_USATO)."""
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        pid1 = await _proposta(c, sc)
        aut1 = await _carica(c, sc, pid1, VECCHIO, giu=True)
        assert aut1["status"] == 201
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid1}/transizione", headers=sc["rsc"],
                         json={"stato_atteso": "PROPOSTA", "nuovo_stato": "RESPINTA",
                               "motivo": "Restituzione da rifare"})
        assert r.status_code == 200, r.text
        pid2 = await _proposta(c, sc)
        aut2 = await _carica(c, sc, pid2, VECCHIO, giu=True)
        await _in_transizione(db_url, aut2["id"], pid2)
        await _fino_a_eseguita(c, sc, pid2, aut2["id"])
        await _lavora_finche(db_url, dipendenze(db_url), pid2)
    bloccata = await _bloccata(db_url, pid2)
    assert bloccata["lavoro"] is None and "ALLEGATO_GIA_USATO" in _difetti(bloccata), bloccata
    assert "pratica di restituzione" in _difetti(bloccata)


async def test_documento_di_una_pratica_di_altro_tipo_non_e_riuso(db_url, s3_ciclo) -> None:
    """Riuso solo fra pratiche dello STESSO tipo (logica corretta in R12a): lo stesso
    documento caricato prima in una pratica di SCARTO non blocca la restituzione."""
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        pid = await _proposta(c, sc)
        aut = await _carica(c, sc, pid, VECCHIO, giu=True)
        # un allegato con la stessa impronta, caricato PRIMA, in una pratica di scarto
        scarto = uuid.uuid4()
        await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, "
                          "dati) VALUES ($1, $2, 'SCARTO', 'RESPINTA', "
                          "'scarto di prova respinto', '{}'::jsonb)", scarto, sc["tenant"])
        await sql(db_url, "INSERT INTO public.pratiche_allegati (pratica_id, tenant_id, tipo, "
                          "nome_file, content_type, dimensione, sha256, bucket, chiave, "
                          "conserva_fino_a, created_at) SELECT $1, tenant_id, tipo, nome_file, "
                          "content_type, dimensione, sha256, bucket, chiave || '-scarto', "
                          "conserva_fino_a, created_at - "
                          "interval '1 day' FROM public.pratiche_allegati WHERE id = $2::uuid",
                  scarto, aut["id"])
        await _in_transizione(db_url, aut["id"], pid)
        await _fino_a_eseguita(c, sc, pid, aut["id"])
        await _lavora_finche(db_url, dipendenze(db_url), pid)
    assert await _stato(db_url, pid) == "ESEGUITA", await _bloccata(db_url, pid)


async def test_oggetto_di_un_altro_ente_blocca_la_restituzione(db_url, s3_ciclo) -> None:
    """API compromessa: aggiunge alla restituzione un PdA di un altro ente."""
    sc = await _scenario(db_url, s3_ciclo)
    estraneo = await _scenario(db_url, s3_ciclo, pda=1)
    aip = (await sql(db_url, "SELECT id FROM public.aip WHERE tenant_id = $1",
                     estraneo["tenant"]))[0]["id"]
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _proposta(c, sc)
        await sql(db_url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, "
                          "urn, esito) VALUES ($1::uuid, $2, $3, 'urn:estraneo', 'SELEZIONATO')",
                  pid, sc["tenant"], aip)
        aut = await _carica(c, sc, pid, autorizzazione(pid))
        await _fino_a_eseguita(c, sc, pid, aut["id"])
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert "OGGETTI_ALTRO_ENTE" in _difetti(bloccata)
    assert dip.esportatore.richieste == []


async def test_migrazione_053_elenca_solo_le_restituzioni_aperte(db_url, s3_ciclo) -> None:
    """L'elenco di transizione della migrazione: autorizzazioni delle restituzioni NON
    concluse; nulla per quelle concluse né per gli altri tipi d'allegato."""
    percorso = (Path(__file__).resolve().parents[2] / "gateway" / "alembic" / "versions"
                / "20261106_053_restituzione_legata.py")
    spec = importlib.util.spec_from_file_location("migrazione_053", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        aperta = await _proposta(c, sc)
        aut = await _carica(c, sc, aperta, autorizzazione(aperta))
        conclusa = await _proposta(c, sc)
        aut_c = await _carica(c, sc, conclusa, autorizzazione(conclusa))
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{conclusa}/transizione",
                         headers=sc["rsc"], json={"stato_atteso": "PROPOSTA",
                                                  "nuovo_stato": "RESPINTA",
                                                  "motivo": "Restituzione respinta"})
        assert r.status_code == 200, r.text
    await sql(db_url, modulo._ELENCO)
    await sql(db_url, modulo._ELENCO)  # riprendibile
    elenco = {str(r["allegato_id"]) for r in await sql(
        db_url, "SELECT allegato_id FROM public.ciclo_vita_transizione_r12 "
                "WHERE pratica_id = ANY($1::uuid[])", [aperta, conclusa])}
    assert elenco == {aut["id"]} and aut_c["id"] not in elenco
