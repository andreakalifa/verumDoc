# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
End-to-end della RETTIFICA dei metadati e dell'ANNULLAMENTO (contratto Q2,
REGOLE §10.1, §10.5, §23.6).

Rettifica: richiesta → il lavoratore archivia l'atto nel WORM e lo versa con il
gateway (finto che realizza i contratti C1/C2 e crea il PdV RICEVUTO come il
caso d'uso ``versa``) → IN_VERSAMENTO → il PdV di rettifica segue la pipeline
(qui portato a CONSERVATO con il suo PdA) → foglia «rettifica» nel registro →
COMPLETATA ed evento ``versamento.rettificato``. L'originale non cambia.
"""
from __future__ import annotations

import json
import uuid

import pytest

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
    lavora,
    outbox,
)

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_rettifica_end_to_end(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    operatore = await crea_utente(db_url, tenant, "OPERATORE")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, operatore)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    originale_prima = await sql(db_url, "SELECT stato::text, metadata_json FROM public.pdv "
                                        "WHERE id = $1", pdv)
    chi = intestazioni(tenant, "OPERATORE", operatore)
    corpo = {"aip_id": str(aip), "metadati_nuovi": {"oggetto": "Contratto rettificato",
                                                    "classificazione": "6.3"},
             "motivo": "Oggetto errato nel versamento originale"}
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica", headers={
            **chi, "Idempotency-Key": "rettifica-prova-0001"}, json=corpo)
        assert r.status_code == 202, r.text
        pratica = r.json()
        assert pratica["tipo"] == "RETTIFICA" and pratica["stato"] == "RICHIESTA"
        # ripetizione con la stessa chiave: stessa pratica (200)
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica", headers={
            **chi, "Idempotency-Key": "rettifica-prova-0001"}, json=corpo)
        assert r.status_code == 200 and r.json()["id"] == pratica["id"]
        # una seconda rettifica aperta sullo stesso PdV → 409
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica", headers=chi,
                         json={**corpo, "metadati_nuovi": {"oggetto": "Altro"}})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-PRATICA-APERTA"

        dip = dipendenze(db_url)
        await lavora(dip)
        r = await c.get(f"/api/v1/ciclo-vita/pratiche/{pratica['id']}", headers=chi)
        dettaglio = r.json()
        assert dettaglio["stato"] == "IN_VERSAMENTO", dettaglio
        nuovo_pdv = dettaglio["pdv_risultato_id"]
        versamento = dip.versatore.versamenti[f"cv-{pratica['id']}-versamento"]
        principale = versamento["indice"]["documenti"][0]
        assert principale["tipo"] == "PRINCIPALE" and principale["formato"] == "application/xml"
        assert principale["parametri"]["oggetto"] == "Contratto rettificato"
        assert principale["parametri"]["rettifica_di"] == urn
        assert principale["parametri"]["pdv_rettificato"] == str(pdv)
        assert principale["parametri"]["tipologia_documentale"] == "contratto"
        assert versamento["indice"]["chiave"]["registro"] == "RETTIFICA"
        # per conto dell'utente che ha chiesto la rettifica, con il ruolo attuale
        assert dip.versatore.per_conto[0] == {"tenant_id": str(tenant),
                                              "user_id": str(operatore), "ruolo": "OPERATORE"}
        atto = next(a for a in dettaglio["allegati"] if a["tipo"] == "ATTO")
        assert dip.versatore.caricamenti[principale and versamento["file"][
            principale["nome_file"]]]["sha256"] == atto["sha256"]

        # il PdV di rettifica segue la pipeline normale: qui arriva a CONSERVATO
        await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1::uuid",
                  nuovo_pdv)
        _, urn_rettifica = await crea_pda(db_url, tenant, uuid.UUID(nuovo_pdv), scadenza=PASSATO)
        await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() WHERE id = "
                          "$1::uuid", pratica["id"])
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pratica['id']}",
                                 headers=chi)).json()
    assert dettaglio["stato"] == "COMPLETATA", dettaglio
    assert dettaglio["dati"]["registro"]["urn_rettifica"] == urn_rettifica
    foglia = dip.registro.foglie[("rettifica", urn, pratica["id"])]
    assert foglia["riferimento"]["pdv_rettifica"] == nuovo_pdv
    assert foglia["impronta"] == atto["sha256"]
    # R06 (VD-C-119): il fatto del lavoratore che il registro pretende
    fatti = await sql(db_url, "SELECT esito, dettagli FROM public.esiti_esecuzione "
                              "WHERE tipo = 'RETTIFICA' AND pratica_id = $1::uuid", pratica["id"])
    assert [f["esito"] for f in fatti] == ["PDV_VERSATO"]
    assert json.loads(fatti[0]["dettagli"]) == {"atto_sha256": atto["sha256"],
                                                "pdv_rettifica": nuovo_pdv}
    eventi = [e for e in await outbox(db_url, "versamento.rettificato")
              if e["pratica_id"] == pratica["id"]]
    assert eventi and eventi[0]["pdv_rettifica_id"] == nuovo_pdv and eventi[0]["urn"] == urn
    # l'originale è immutato
    assert await sql(db_url, "SELECT stato::text, metadata_json FROM public.pdv WHERE id = $1",
                     pdv) == originale_prima


async def test_rettifica_rifiutata_dal_gateway_respinta(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    chi = intestazioni(tenant, "ADMIN_TENANT", admin)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica", headers=chi, json={
            "pdv_id": str(pdv), "metadati_nuovi": {"oggetto": "Nuovo"},
            "motivo": "Correzione dell'oggetto del contratto"})
        pid = r.json()["id"]
        dip = dipendenze(db_url)
        dip.versatore.rifiuta_con = {"codice": "VRS-130", "title": "Regole non rispettate"}
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=chi)).json()
    assert dettaglio["stato"] == "RESPINTA"
    assert dettaglio["dati"]["errore"]["codice"] == "VRS-130"
    assert "VRS-130" in dettaglio["cronologia"][-1]["motivo"]


async def test_rettifica_solo_pdv_conservato_e_ruoli(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant)
    in_lavorazione = await crea_pdv(db_url, tenant, classe, admin, stato="IN_VALIDAZIONE")
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica",
                         headers=intestazioni(tenant, "ADMIN_TENANT", admin), json={
                             "pdv_id": str(in_lavorazione), "metadati_nuovi": {"oggetto": "X"},
                             "motivo": "Correzione dell'oggetto"})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-PDV-STATO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica",
                         headers=intestazioni(tenant, "VIEWER"), json={
                             "pdv_id": str(in_lavorazione), "metadati_nuovi": {"oggetto": "X"},
                             "motivo": "Correzione dell'oggetto"})
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-RUOLO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica",
                         headers=intestazioni(tenant, "ADMIN_TENANT", admin), json={
                             "pdv_id": str(in_lavorazione),
                             "metadati_nuovi": {"rettifica_di": "falso"},
                             "motivo": "Correzione dell'oggetto"})
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-METADATI"


async def test_annullamento_dopo_conservazione(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    operatore = await crea_utente(db_url, tenant, "OPERATORE")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    corpo = {"pdv_id": str(pdv), "motivo": "Documento versato per errore"}
    async with client_app() as c:
        # dopo la conservazione: solo ADMIN_TENANT (regola predefinita)
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(tenant, "OPERATORE", operatore), json=corpo)
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-RUOLO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(tenant, "ADMIN_TENANT", admin), json=corpo)
        assert r.status_code == 201, r.text
        pratica = r.json()
        # revisione R09 (Z3): l'API mette in attesa, il lavoratore applica
        assert pratica["stato"] == "RICHIESTA" and pratica["esecuzione"]["lavoro"] == "ANNULLA"
        assert (await sql(db_url, "SELECT stato::text FROM public.pdv WHERE id = $1", pdv))[0][
            "stato"] == "CONSERVATO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(tenant, "ADMIN_TENANT", admin), json=corpo)
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-ANNULLATO"
        dip = dipendenze(db_url)
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pratica['id']}",
                                 headers=intestazioni(tenant, "VIEWER"))).json()
    riga = (await sql(db_url, "SELECT stato::text AS stato, metadata_json FROM public.pdv "
                              "WHERE id = $1", pdv))[0]
    assert riga["stato"] == "ANNULLATO"
    meta = json.loads(riga["metadata_json"]) if isinstance(riga["metadata_json"], str) \
        else riga["metadata_json"]
    assert meta["annullamento"]["pratica_id"] == pratica["id"]
    # nessun file toccato: il PdA resta CONSERVATO
    assert (await sql(db_url, "SELECT stato::text FROM public.aip WHERE id = $1", aip))[0][
        "stato"] == "CONSERVATO"
    evento = [e for e in await outbox(db_url, "versamento.annullato") if e["pdv_id"] == str(pdv)]
    assert len(evento) == 1 and evento[0]["urn"] == urn
    foglia = dip.registro.foglie[("annullamento", urn, pratica["id"])]
    atto = next(a for a in dettaglio["allegati"] if a["tipo"] == "ATTO")
    assert foglia["impronta"] == atto["sha256"]
    # R06 (VD-C-119): il fatto del lavoratore che il registro pretende
    fatti = await sql(db_url, "SELECT esito, dettagli FROM public.esiti_esecuzione "
                              "WHERE tipo = 'ANNULLAMENTO' AND pratica_id = $1::uuid",
                      pratica["id"])
    assert sorted(f["esito"] for f in fatti) == ["ATTO_ARCHIVIATO", "AUTORIZZAZIONI_VERIFICATE"]
    atto_fatto = next(f for f in fatti if f["esito"] == "ATTO_ARCHIVIATO")
    assert json.loads(atto_fatto["dettagli"])["atto_sha256"] == atto["sha256"]
    assert dettaglio["stato"] == "ESEGUITA"
    assert dettaglio["esecuzione"]["lavoro"] is None
    assert dettaglio["dati"]["registro"]["nel_registro"] is True


async def test_annullamento_prima_della_conservazione_e_in_lavorazione(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    operatore = await crea_utente(db_url, tenant, "OPERATORE")
    classe = await crea_classe(db_url, tenant)
    ricevuto = await crea_pdv(db_url, tenant, classe, operatore, stato="RICEVUTO")
    validato = await crea_pdv(db_url, tenant, classe, operatore, stato="VALIDATO")
    bucket_worm(s3_ciclo)
    chi = intestazioni(tenant, "OPERATORE", operatore)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(validato), "motivo": "Documento versato per errore"})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-PDV-STATO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(ricevuto), "motivo": "Documento versato per errore"})
        assert r.status_code == 201, r.text
        dip = dipendenze(db_url)
        await lavora(dip)
    assert dip.registro.foglie == {}  # nessun PdA nel registro: nessuna foglia
    assert (await sql(db_url, "SELECT stato::text FROM public.pdv WHERE id = $1", ricevuto))[0][
        "stato"] == "ANNULLATO"
