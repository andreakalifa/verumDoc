# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R08 (D01, D02) — console: catalogo base dei formati, restrizione dell'ente per
classe con bozza, anteprima (impatto sui PdV recenti) e applicazione; il
validatore preventivo mostra i file che verrebbero rifiutati; il livello delle
firme l'ente può solo alzarlo rispetto al conservatore.
"""
from __future__ import annotations

from tests.infra_reale import db_url, richiede_pg  # noqa: F401 — fixture
from tests.test_r05_configurazione import _bozza, _chiama, _indice, _pdv, _scenario

pytestmark = [richiede_pg]


async def test_catalogo_restrizione_per_classe_e_validatore(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    stato, catalogo = await _chiama("GET", "/formati", token=s.t_admin)
    assert stato == 200
    voci = {f["id"]: f for f in catalogo["formati"]}
    assert voci["PDFA"]["pdfa"] and "fmt/477" in voci["PDFA"]["puid"]
    assert voci["NON_IDENTIFICATO"]["esito_base"] == "RIFIUTATO"
    stato, elenco = await _chiama("GET", "/impostazioni", token=s.t_admin,
                                  params={"livello": "CLASSE", "ente": s.tid, "classe": s.cid,
                                          "gruppo": "formati"})
    assert stato == 200 and {v["codice"] for v in elenco["items"]} >= {"FORMATO.PNG.ESITO"}

    for formato in ("image/png", "application/pdf"):
        await _pdv(db_url, s.tid, s.cid, s.admin, formato=formato, nome="doc.png"
                   if formato == "image/png" else "doc.pdf")
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "CLASSE", "ente": s.tid,
                                               "classe": s.cid},
        "modifiche": [{"codice": "FORMATO.PNG.ESITO", "valore": "RIFIUTATO"}],
        "motivo": "La classe non accetta immagini PNG"})
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert stato == 200 and piano["bloccati"] == [], piano
    assert piano["impatto"]["pdv_ultimi_30_giorni_che_sarebbero_rifiutati"] == 1
    assert piano["impatto"]["esempi"][0]["rilievi"][0]["regola"] == "RGL-107"
    stato, _ = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                             corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200

    indice = _indice()
    indice["documenti"].append({"id": "d2", "tipo": "ALLEGATO", "nome_file": "foto.png",
                                "hash": {"impronta": "cd" * 32, "algoritmo": "SHA-256"}})
    stato, esito = await _chiama("POST", "/valida", token=s.t_admin, corpo={
        "indice": indice, "file_impronte": {
            "doc.pdf": {"impronta": "ab" * 32, "formato": "application/pdf"},
            "foto.png": {"impronta": "cd" * 32, "formato": "image/png"}}})
    assert stato == 200, esito
    assert esito["file_rifiutati"] == ["foto.png"]
    assert esito["firme"]["livello"] == "RILEVATA"


async def test_livello_firme_l_ente_puo_solo_alzarlo(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "ENTE", "ente": s.tid},
        "modifiche": [{"codice": "FIRME_LIVELLO_VERIFICA", "valore": "NESSUNA"},
                      {"codice": "NULLA_OSTA_FIRMA", "valore": "BLOCCANTE"}],
        "motivo": "Prova dei limiti delle firme"})
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert stato == 200
    bloccati = {x["codice"]: x["motivo"] for x in piano["bloccati"]}
    assert "più restrittivo" in bloccati["FIRME_LIVELLO_VERIFICA"]
    assert "NULLA_OSTA_FIRMA" not in bloccati
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "ENTE", "ente": s.tid},
        "modifiche": [{"codice": "FIRME_LIVELLO_VERIFICA", "valore": "CON_REVOCHE"}],
        "motivo": "Verifica con revoche per l'ente"})
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert stato == 200 and piano["bloccati"] == []
