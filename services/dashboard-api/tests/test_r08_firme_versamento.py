# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R08 (D01) — nel dettaglio di un versamento la console riceve l'esito delle firme
leggibile (firmatario, valida/non valida/indeterminata, motivo in parole) dal
rapporto di verifica salvato sul PdV, mai il rapporto grezzo.
"""
from __future__ import annotations

import pytest

from tests.infra_reale import richiede_pg
from tests.test_g2_dati import _aip, _client, _token, crea_tenant_e_classe, db_url  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

RAPPORTO = {"versione": 1, "livello": "CRITTOGRAFICA", "file": [
    {"documento": "d1", "nome_file": "b.p7m", "esito_in_parole": "non valida",
     "esito": {"formato": "CAdES", "verificatore": "pyhanko", "rapporto_grezzo": "X" * 5000,
               "firme": [{"firmatario": "Dirigente di prova", "indicazione": "TOTAL_FAILED",
                          "revoca": "NON_CONTROLLATO",
                          "motivi": ["il documento è stato modificato dopo la firma"]}]}},
    {"documento": "d2", "nome_file": "a.pdf", "esito_in_parole": "non firmato",
     "esito": {"formato": "NESSUNO", "firme": [], "motivo": "nessuna firma nel file"}}]}


async def test_esito_delle_firme_nel_dettaglio_del_versamento(db_url):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    await _aip(db_url, tenant, classe, meta_pdv={"verifica": {"passi": [
        {"nome": "integrita_hash", "dettagli": {}},
        {"nome": "verifica_firme", "dettagli": {"livello": "CRITTOGRAFICA",
                                                "rapporto": RAPPORTO}}]}})
    await _aip(db_url, tenant, classe)  # versato prima di R08: nessuna firma
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/versamenti?tenant_id={tenant}",
                        headers=_token(str(tenant), "ADMIN_TENANT"))
    assert r.status_code == 200, r.text
    firme = [i["firme"] for i in r.json()["items"]]
    assert None in firme
    riassunto = next(f for f in firme if f)
    assert riassunto["livello"] == "CRITTOGRAFICA"
    assert [f["nome_file"] for f in riassunto["file"]] == ["a.pdf", "b.p7m"]
    b = riassunto["file"][1]
    assert b["esito"] == "non valida" and b["firme"][0]["firmatario"] == "Dirigente di prova"
    assert "modificato dopo la firma" in b["firme"][0]["motivi"][0]
    assert "rapporto_grezzo" not in r.text
