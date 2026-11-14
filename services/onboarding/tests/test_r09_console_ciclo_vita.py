# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R09 — console «Ciclo di vita automatico»: i parametri nuovi (D04, D05, D06, D20,
D21, D23, D27) sono voci del motore con il loro gruppo, il verso «più sicuro» vale
anche qui: l'ente attiva la proposta di scarto e rende la doppia approvazione più
severa, mai il contrario; la verifica di una classe si può solo rendere più
frequente; le voci riservate le cambia solo il conservatore.
"""
from __future__ import annotations

from tests.infra_reale import db_url, richiede_pg  # noqa: F401 — fixture
from tests.test_r05_configurazione import _bozza, _chiama, _scenario

pytestmark = [richiede_pg]

GRUPPI = {
    "scarto": {"SCARTO_PROPOSTA_AUTOMATICA", "SCARTO_PROPOSTA_FREQUENZA_GIORNI",
               "SCARTO_PROPOSTA_CLASSI_ESCLUSE", "SCARTO_TENTATIVI_PDA",
               "SCARTO_RIPRESE_AUTOMATICHE", "SCARTO_MINIMIZZAZIONE_GIORNI"},
    "annullamento": {"ANNULLAMENTO_DOPPIA_APPROVAZIONE", "ANNULLAMENTO_RUOLI_APPROVAZIONE",
                     "ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE",
                     "ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE"},
    "retention": {"RETENTION_PERMANENTE_ANNI", "RETENTION_RINNOVO_SOGLIA_ANNI"},
    "verifiche": {"VERIFICA_CICLO_GIORNI", "VERIFICA_CAMPIONE_INCIDENTE_PDA"},
}


async def _piano(token: str, ambito: dict, modifiche: list[dict]) -> dict:
    b = await _bozza(token, {"risorsa": "impostazioni", "ambito": ambito,
                             "modifiche": modifiche, "motivo": "Prova della console R09"})
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=token)
    assert stato == 200, piano
    return piano


async def test_gruppi_e_verso_sicuro_dell_ente(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    for gruppo, codici in GRUPPI.items():
        stato, elenco = await _chiama("GET", "/impostazioni", token=s.t_admin, params={
            "livello": "ENTE", "ente": s.tid, "gruppo": gruppo})
        assert stato == 200 and {v["codice"] for v in elenco["items"]} >= codici, gruppo
    ente = {"livello": "ENTE", "ente": s.tid}
    # l'ente attiva la proposta e chiede SEMPRE la doppia approvazione (verso sicuro)
    piano = await _piano(s.t_admin, ente, [
        {"codice": "SCARTO_PROPOSTA_AUTOMATICA", "valore": "ATTIVA"},
        {"codice": "ANNULLAMENTO_DOPPIA_APPROVAZIONE", "valore": "SEMPRE"}])
    assert piano["bloccati"] == [], piano
    # ma non la toglie (MAI è meno sicuro del valore del conservatore)
    piano = await _piano(s.t_admin, ente, [
        {"codice": "ANNULLAMENTO_DOPPIA_APPROVAZIONE", "valore": "MAI"}])
    assert piano["bloccati"], piano
    # voci riservate: l'ente non cambia ruoli né retention
    piano = await _piano(s.t_admin, ente, [
        {"codice": "ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE", "valore": []}])
    assert piano["bloccati"], piano


async def test_frequenza_della_verifica_per_classe_solo_piu_frequente(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    classe = {"livello": "CLASSE", "ente": s.tid, "classe": s.cid}
    piano = await _piano(s.t_admin, classe, [{"codice": "VERIFICA_CICLO_GIORNI", "valore": 90}])
    assert piano["bloccati"] == [], piano
    # fino al valore del conservatore (365) sì: oltre il massimo no
    piano = await _piano(s.t_admin, classe, [{"codice": "VERIFICA_CICLO_GIORNI", "valore": 365}])
    assert piano["bloccati"] == [], piano
    piano = await _piano(s.t_admin, {"livello": "ENTE", "ente": s.tid},
                         [{"codice": "VERIFICA_CICLO_GIORNI", "valore": 5}])
    assert piano["bloccati"], piano            # sotto il minimo di 7
    piano = await _piano(s.t_super, {"livello": "CONSERVATORE"},
                         [{"codice": "VERIFICA_CICLO_GIORNI", "valore": 400}])
    assert piano["bloccati"], piano            # oltre il massimo di 365
    piano = await _piano(s.t_super, {"livello": "CONSERVATORE"},
                         [{"codice": "RETENTION_PERMANENTE_ANNI", "valore": 40}])
    assert piano["bloccati"] == [], piano
