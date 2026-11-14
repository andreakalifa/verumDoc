# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R8: riversamento in INGRESSO.

Un riversamento in ingresso si chiude ESEGUITA solo se i documenti sono stati
davvero versati; chi lo avvia deve poter versare per l'ente (i documenti si
versano con il token di canale a nome di chi avvia). Prima della correzione:
avviato da RSC_CONSERVATORE (utente di un altro ente) o con l'avviante poi
disattivato, la pratica finiva ESEGUITA con i file ancora SELEZIONATO e nessun
versamento.
"""
from __future__ import annotations

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
)
from tests.test_riversamento import _caricamento, _zip_pdd

pytestmark = [pytest.mark.integration, richiede_pg]

CORPO = {"classe_documentale": "delibere", "conservatore_origine": "Altro conservatore",
         "metadati": {"tipologia_documentale": "delibera"},
         "motivo": "Presa in carico dal precedente conservatore"}


async def _preparazione(db_url, s3_ciclo):
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    await crea_classe(db_url, tenant, "delibere")
    conservatore = await crea_ente(db_url)
    rsc = await crea_utente(db_url, conservatore, "RSC_CONSERVATORE")
    bucket_worm(s3_ciclo)
    contenuto, _ = _zip_pdd(altera=False)
    cid = await _caricamento(db_url, s3_ciclo, tenant, admin, contenuto)
    return {"tenant": tenant, "admin_id": admin, "cid": cid,
            "admin": intestazioni(tenant, "ADMIN_TENANT", admin),
            "rsc": intestazioni(conservatore, "RSC_CONSERVATORE", rsc)}


async def _accetta_ed_esegui(c, pid, rsc) -> None:
    for da, a in (("PROPOSTA", "ESEGUIBILE"), ("ESEGUIBILE", "ESEGUITA")):
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=rsc,
                         json={"stato_atteso": da, "nuovo_stato": a,
                               "motivo": "Accettazione del riversamento"})
        assert r.status_code in (200, 202), r.text


async def test_ingresso_avviato_dal_conservatore_rifiutato(db_url, s3_ciclo) -> None:
    """R8: un utente del conservatore non può versare per l'ente: l'avvio è
    rifiutato (prima: la pratica si chiudeva ESEGUITA senza versare nulla)."""
    p = await _preparazione(db_url, s3_ciclo)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-ingresso", headers=p["rsc"],
                         json={"ente": str(p["tenant"]), "caricamento_id": str(p["cid"]),
                               **CORPO})
    assert r.status_code == 403, r.text
    assert r.json()["codice"] == "LC-403-RUOLO"
    assert await sql(db_url, "SELECT 1 FROM public.pratiche WHERE tipo = "
                             "'RIVERSAMENTO_INGRESSO'") == []


async def test_avviante_disattivato_pratica_respinta_mai_eseguita(db_url, s3_ciclo) -> None:
    """R8: l'amministratore che ha avviato il riversamento viene disattivato prima
    dell'esecuzione: nessun versamento, pratica RESPINTA, file in ERRORE."""
    p = await _preparazione(db_url, s3_ciclo)
    dip = dipendenze(db_url)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-ingresso", headers=p["admin"],
                         json={"ente": str(p["tenant"]), "caricamento_id": str(p["cid"]),
                               **CORPO})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        await lavora(dip)  # acquisizione e verifica
        await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", p["admin_id"])
        await _accetta_ed_esegui(c, pid, p["rsc"])
        await lavora(dip)
        d = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=p["rsc"])).json()
    assert d["stato"] == "RESPINTA", d
    assert dip.versatore.versamenti == {}
    assert d["conteggi"] == {"ERRORE": 2}
    assert d["pdv_risultato_id"] is None


async def test_versamento_rifiutato_dal_gateway_pratica_respinta(db_url, s3_ciclo) -> None:
    """R8: rifiuto definitivo del gateway (non ritentabile): nulla è stato versato,
    quindi la pratica è RESPINTA con l'errore, non ESEGUITA."""
    p = await _preparazione(db_url, s3_ciclo)
    dip = dipendenze(db_url)
    dip.versatore.rifiuta_con = {"codice": "VRS-101", "title": "Classe non ammessa"}
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-ingresso", headers=p["admin"],
                         json={"ente": str(p["tenant"]), "caricamento_id": str(p["cid"]),
                               **CORPO})
        pid = r.json()["id"]
        await lavora(dip)
        await _accetta_ed_esegui(c, pid, p["rsc"])
        await lavora(dip)
        d = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=p["rsc"])).json()
    assert d["stato"] == "RESPINTA", d
    assert d["dati"]["errore"]["codice"] == "VRS-101"
    assert "VERSATO" not in d["conteggi"]
