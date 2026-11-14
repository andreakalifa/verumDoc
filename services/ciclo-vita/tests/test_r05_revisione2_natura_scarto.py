# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievo S5: la natura dell'ente si RIVALUTA quando uno
scarto chiede di diventare ESEGUIBILE. Prima ``richiede_nulla_osta`` restava
quello della creazione: un ente corretto da PRIVATO a PA teneva le sue pratiche
senza nulla osta della Soprintendenza.
"""
from __future__ import annotations

import json

import pytest

from tests.firme_prova import autorizzazione
from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app
from tests.test_r05_revisione_scarto import CORPO, _ente_con_pda, _tr

pytestmark = [pytest.mark.integration, richiede_pg]


async def _fino_ad_autorizzata(c, sc) -> str:
    corpo = {"ente": str(sc["tenant"]), **CORPO}
    piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                          json=corpo)).json()
    r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                     json={**corpo, "impronta_piano": piano["impronta_piano"]})
    pid = r.json()["id"]
    assert r.json()["richiede_nulla_osta"] is False      # ente PRIVATO alla creazione
    assert (await _tr(c, pid, sc["admin"], "BOZZA", "PROPOSTA")).status_code == 200
    aut = (await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                        data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                        files={"file": (
                            "a.p7m", autorizzazione(pid), "application/pkcs7-mime")})).json()["id"]
    assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                      [aut])).status_code == 200
    return pid


async def test_ente_diventato_pa_richiede_il_nulla_osta_prima_di_eseguibile(
        db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1, natura="PRIVATO")
    async with client_app() as c:
        pid = await _fino_ad_autorizzata(c, sc)
        # il conservatore corregge la natura dell'ente: è una pubblica amministrazione
        await sql(db_url, "UPDATE public.tenants SET natura = 'PA' WHERE id = $1", sc["tenant"])
        r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-TRANSIZIONE", r.text
        assert "nulla osta" in r.text
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()
        assert dettaglio["richiede_nulla_osta"] is True
        assert dettaglio["stato"] == "AUTORIZZATA_PRODUTTORE"
        # il percorso giusto ora è il nulla osta
        nulla_osta = (await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati",
                                   headers=sc["admin"], data={"tipo": "NULLA_OSTA"},
                                   files={"file": ("n.pdf", b"%PDF nulla osta",
                                                   "application/pdf")})).json()["id"]
        assert (await _tr(c, pid, sc["admin"], "AUTORIZZATA_PRODUTTORE", "NULLA_OSTA",
                          [nulla_osta])).status_code == 200
        assert (await _tr(c, pid, sc["rsc"], "NULLA_OSTA", "ESEGUIBILE")).status_code == 200
    voci = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                             "WHERE evento = 'ciclo_vita.scarto.nulla_osta_richiesto'")
    assert len(voci) == 1 and json.loads(voci[0]["dettagli"])["natura_attuale"] == "PA"


async def test_ente_ancora_privato_passa_senza_nulla_osta(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1, natura="PRIVATO")
    async with client_app() as c:
        pid = await _fino_ad_autorizzata(c, sc)
        r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 200, r.text
    assert await sql(db_url, "SELECT 1 FROM public.audit_global "
                             "WHERE evento = 'ciclo_vita.scarto.nulla_osta_richiesto'") == []
