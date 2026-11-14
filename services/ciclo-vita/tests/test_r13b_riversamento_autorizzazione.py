# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-162; R17, VD-P-155: RICHIESTA come valore generale) — autorizzazione del
produttore richiesta per il RIVERSAMENTO in uscita, con la logica di VD-C-160.

  - con la deroga NON_RICHIESTA concessa dal conservatore all'ente (l'ente di prova di
    ``crea_ente``): PROPOSTA → ESEGUIBILE dal conservatore, nessuna verifica del lavoratore;
  - con RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE = RICHIESTA per l'ente, alla
    creazione la pratica fissa ``richiede_autorizzazione_produttore``: il conservatore
    non salta l'autorizzazione; il produttore carica un documento firmato che cita la
    pratica (422 se cita un'altra pratica) e il lavoratore lo riverifica dal WORM prima
    di esportare (bloccata se il documento non regge);
  - la colonna non si cambia dopo la creazione (trigger della migrazione 055).
"""
from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from libs.shared.parametri import svuota_cache
from tests.firme_prova import autorizzazione
from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, dipendenze
from tests.test_r05_revisione2_scarto_incompleto import _lavora_finche
from tests.test_r05_revisione4_scarto import _bloccata, _difetti
from tests.test_r12b_restituzione_legata import _carica, _fatto, _scenario, _stato

pytestmark = [pytest.mark.integration, richiede_pg]

CORPO = {"criteri": {}, "conservatore_destinatario": "Nuovo conservatore S.p.A.",
         "motivo": "Cambio di conservatore per scadenza del contratto"}


async def _richiedi(db_url, tenant) -> None:  # type: ignore[no-untyped-def]
    # R17 (VD-P-155): l'ente di prova ha la deroga del conservatore; qui la si toglie
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                      "valore, motivo) VALUES ('ENTE', $1, "
                      "'RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE', CAST($2 AS jsonb), "
                      "'opzione di prova') ON CONFLICT ON CONSTRAINT "
                      "uq_impostazioni_valori_livello DO UPDATE SET valore = EXCLUDED.valore",
              tenant, json.dumps("RICHIESTA"))
    svuota_cache()


async def _proposta(c, sc) -> dict:  # type: ignore[no-untyped-def]
    corpo = {"ente": str(sc["tenant"]), **CORPO}
    piano = (await c.post("/api/v1/ciclo-vita/pratiche/riversamento-uscita", headers=sc["rsc"],
                          json=corpo)).json()
    r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-uscita", headers=sc["rsc"],
                     json={**corpo, "impronta_piano": piano["impronta_piano"]})
    assert r.status_code == 201, r.text
    return r.json()


async def _passo(c, chi, pid, da, a, allegati=()):  # type: ignore[no-untyped-def]
    return await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi, json={
        "stato_atteso": da, "nuovo_stato": a, "allegati": list(allegati),
        "motivo": "Passo del riversamento di prova"})


async def test_deroga_del_conservatore_come_prima(db_url, s3_ciclo) -> None:
    svuota_cache()
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        pratica = await _proposta(c, sc)
        assert pratica["richiede_autorizzazione_produttore"] is False
        pid = pratica["id"]
        # nessun passo del produttore fra le azioni
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()
        assert {a["nuovo_stato"] for a in dettaglio["azioni_consentite"]} == {
            "ESEGUIBILE", "RESPINTA"}
        r = await _passo(c, sc["admin"], pid, "PROPOSTA", "AUTORIZZATA_PRODUTTORE")
        assert r.status_code == 409
        assert (await _passo(c, sc["rsc"], pid, "PROPOSTA", "ESEGUIBILE")).status_code == 200


async def test_richiesta_si_esegue_solo_con_l_autorizzazione_verificata(db_url,
                                                                        s3_ciclo) -> None:
    sc = await _scenario(db_url, s3_ciclo)
    await _richiedi(db_url, sc["tenant"])
    dip = dipendenze(db_url)
    async with client_app() as c:
        pratica = await _proposta(c, sc)
        pid = pratica["id"]
        assert pratica["richiede_autorizzazione_produttore"] is True
        r = await _passo(c, sc["rsc"], pid, "PROPOSTA", "ESEGUIBILE")
        assert r.status_code == 409 and "autorizzazione" in r.json()["detail"]
        altra = await _carica(c, sc, pid, autorizzazione(str(uuid.uuid4())))
        assert altra["status"] == 422 and altra["codice"] == "LC-422-FIRMA", altra
        aut = await _carica(c, sc, pid, autorizzazione(pid))
        assert aut["status"] == 201, aut
        for chi, da, a, allegati, atteso in (
                (sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE", [aut["id"]], 200),
                (sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE", [], 200),
                (sc["rsc"], "ESEGUIBILE", "ESEGUITA", [], 202)):
            r = await _passo(c, chi, pid, da, a, allegati)
            assert r.status_code == atteso, r.text
        await _lavora_finche(db_url, dip, pid)
    assert await _stato(db_url, pid) == "ESEGUITA", await _bloccata(db_url, pid)
    assert [f["tipo"] for f in await _fatto(db_url, pid)] == ["RIVERSAMENTO_USCITA"]
    assert len(dip.esportatore.richieste) == 1


async def test_richiesta_documento_di_un_altra_pratica_blocca(db_url, s3_ciclo) -> None:
    """API compromessa (verificatore giù al caricamento): decide il lavoratore."""
    sc = await _scenario(db_url, s3_ciclo)
    await _richiedi(db_url, sc["tenant"])
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = (await _proposta(c, sc))["id"]
        aut = await _carica(c, sc, pid, autorizzazione(str(uuid.uuid4())), giu=True)
        assert aut["status"] == 201, aut
        for chi, da, a, allegati in (
                (sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE", [aut["id"]]),
                (sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE", []),
                (sc["rsc"], "ESEGUIBILE", "ESEGUITA", [])):
            assert (await _passo(c, chi, pid, da, a, allegati)).status_code in (200, 202)
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert bloccata["lavoro"] is None and "identificativo della pratica" in _difetti(bloccata)
    assert dip.esportatore.richieste == [] and await _fatto(db_url, pid) == []


async def test_colonna_fissata_alla_creazione(db_url, s3_ciclo) -> None:
    svuota_cache()
    sc = await _scenario(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await _proposta(c, sc))["id"]
    with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                       match="creazione"):
        await sql(db_url, "UPDATE public.pratiche SET richiede_autorizzazione_produttore = true "
                          "WHERE id = $1::uuid", pid)
