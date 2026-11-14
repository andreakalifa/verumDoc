# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R11 (D07, VD-C-136) — console: passaggio di una classe alla versione 2 del profilo di
tipologia con anteprima (subito o programmato) e limiti dei profili di classe.

Su PostgreSQL reale:
  - la classe sceglie ``PROFILO_TIPOLOGIA`` e ``PROFILO_TIPOLOGIA_VERSIONE`` = 2 con
    una bozza: anteprima, poi applicazione dalla data da cui la v2 vale (subito se
    già valida, altrimenti programmata; prima di quella data CFG-423);
  - una versione inesistente è bloccata nel piano;
  - un profilo di classe che ridefinisce un campo dell'Allegato 5 o aggiunge un
    campo obbligatorio è bloccato (l'ente aggiunge solo campi facoltativi propri).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from libs.verumdoc_rules.configurazione import (
    MOTIVO_CAMPO_DI_TIPOLOGIA,
    MOTIVO_CAMPO_PROPRIO_FACOLTATIVO,
)
from libs.verumdoc_rules.tipologie import profili_predefiniti
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401 — fixture
from tests.test_r05_configurazione import _bozza, _chiama, _pdv, _scenario, servizio

V2 = profili_predefiniti("documento_informatico")[1]


def _oggi() -> date:
    from libs.verumdoc_rules.impostazioni import giorno_locale

    return giorno_locale(datetime.now(UTC))


@richiede_pg
async def test_classe_passa_alla_v2_con_anteprima(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    await _pdv(db_url, s.tid, s.cid, s.admin)
    classe = {"livello": "CLASSE", "ente": s.tid, "classe": s.cid}
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": classe,
        "modifiche": [{"codice": "PROFILO_TIPOLOGIA", "valore": "documento_informatico"},
                      {"codice": "PROFILO_TIPOLOGIA_VERSIONE", "valore": 2}],
        "motivo": "Passaggio ai metadati riscontrati sull'Allegato 5"})
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert stato == 200 and piano["bloccati"] == [], piano
    assert piano["dettagli"]["profilo_tipologia"] == {"codice": "documento_informatico",
                                                      "versione": 2}
    impatto = piano["impatto"]
    assert impatto["campione"]["letti"] == 1
    oggi = _oggi()
    if V2.valido_dal > oggi:
        # prima della data della v2: avviso, applicazione immediata rifiutata, programmata sì
        assert any("programmare" in a for a in piano["avvisi"])
        stato, err = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                                   corpo={"impronta_piano": piano["impronta_piano"]})
        assert stato >= 400 and err["codice"] == "CFG-423", err
        stato, esito = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                                     corpo={"impronta_piano": piano["impronta_piano"],
                                            "valido_dal": V2.valido_dal.isoformat()})
        assert stato == 200, esito
        riga = await sql(db_url, "SELECT stato FROM public.configurazione_bozze WHERE id = $1",
                         uuid.UUID(b["bozza_id"]))
        assert riga[0]["stato"] == "PROGRAMMATA"
    else:
        # la v2 è già valida: il PdV recente senza i metadati nuovi sarebbe rifiutato
        assert impatto["pdv_ultimi_30_giorni_che_sarebbero_rifiutati"] == 1
        stato, esito = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                                     corpo={"impronta_piano": piano["impronta_piano"]})
        assert stato == 200, esito
        valori = await sql(db_url, "SELECT valore::text AS v FROM public.impostazioni_valori "
                                   "WHERE classe_id = $1 AND codice = "
                                   "'PROFILO_TIPOLOGIA_VERSIONE'", uuid.UUID(s.cid))
        assert valori[0]["v"] == "2"


@richiede_pg
async def test_versione_inesistente_bloccata(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "CLASSE", "ente": s.tid,
                                               "classe": s.cid},
        "modifiche": [{"codice": "PROFILO_TIPOLOGIA", "valore": "fattura"},
                      {"codice": "PROFILO_TIPOLOGIA_VERSIONE", "valore": 9}],
        "motivo": "Versione sbagliata"})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert {"codice": "PROFILO_TIPOLOGIA_VERSIONE",
            "motivo": "tipologia fattura: la versione 9 non esiste"} in piano["bloccati"]


@richiede_pg
async def test_versione_non_si_sceglie_per_ente(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
        "modifiche": [{"codice": "PROFILO_TIPOLOGIA_VERSIONE", "valore": 2}],
        "motivo": "Tutte le classi alla versione 2"})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert [x["codice"] for x in piano["bloccati"]] == ["PROFILO_TIPOLOGIA_VERSIONE"]


@richiede_pg
async def test_profilo_di_classe_solo_campi_facoltativi_propri(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    corpo = {"risorsa": "profilo", "ambito": {"livello": "CLASSE", "ente": s.tid,
                                               "classe": s.cid},
             "modifiche": [{"campi": [
                 {"nome": "riservato", "tipo": "booleano", "obbligatorio": False},
                 {"nome": "commessa", "tipo": "stringa", "obbligatorio": True},
                 {"nome": "centro_costo", "tipo": "stringa"}]}],
             "motivo": "Metadati propri della classe"}
    b = await _bozza(s.t_admin, corpo)
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    bloccati = {x["codice"]: x["motivo"] for x in piano["bloccati"]}
    assert bloccati == {"classe:fattura.riservato": MOTIVO_CAMPO_DI_TIPOLOGIA,
                        "classe:fattura.commessa": MOTIVO_CAMPO_PROPRIO_FACOLTATIVO}
    stato, err = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                               corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 422 and err["codice"] == "CFG-422"
    assert await sql(db_url, "SELECT 1 FROM public.profili_metadati WHERE classe_id = $1",
                     uuid.UUID(s.cid)) == []
    # solo il campo facoltativo proprio: applicato
    b = await _bozza(s.t_admin, {**corpo, "modifiche": [{"campi": [
        {"nome": "centro_costo", "tipo": "stringa"}]}]})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert piano["bloccati"] == []
    stato, _ = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                             corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200


# ── Revisione T1: l'ente non toglie né abbassa il profilo dell'Allegato 5 ────

async def _imposta(db_url: str, s: Any, livello: str, codice: str, valore: Any) -> None:
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, classe_id, "
                      "codice, valore) VALUES ($1, $2, $3, $4, CAST($5 AS jsonb))",
              livello, uuid.UUID(s.tid), uuid.UUID(s.cid) if livello == "CLASSE" else None,
              codice, json.dumps(valore))


@richiede_pg
async def test_ente_non_abbassa_il_profilo_della_classe(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    await _imposta(db_url, s, "CLASSE", "PROFILO_TIPOLOGIA",
                   "documento_amministrativo_informatico")
    await _imposta(db_url, s, "CLASSE", "PROFILO_TIPOLOGIA_VERSIONE", 2)
    classe = {"livello": "CLASSE", "ente": s.tid, "classe": s.cid}
    for modifiche in ([{"codice": "PROFILO_TIPOLOGIA_VERSIONE", "valore": 0}],
                      [{"codice": "PROFILO_TIPOLOGIA", "valore": ""}],
                      [{"codice": "PROFILO_TIPOLOGIA", "valore": "documento_informatico"}],
                      [{"codice": "PROFILO_TIPOLOGIA_VERSIONE", "eredita": True}]):
        b = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": classe,
                                     "modifiche": modifiche, "motivo": "Abbassare il profilo"})
        _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
        assert {"codice": "PROFILO_TIPOLOGIA", "motivo": servizio.MOTIVO_PROFILO_ABBASSATO} \
            in piano["bloccati"], (modifiche, piano)
    # il conservatore può, con motivo: solo un avviso
    b = await _bozza(s.t_super, {"risorsa": "impostazioni", "ambito": classe,
                                 "modifiche": [{"codice": "PROFILO_TIPOLOGIA_VERSIONE",
                                                "valore": 0}],
                                 "motivo": "Ritorno alla versione 1 deciso dal responsabile"})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_super)
    assert piano["bloccati"] == [] and any("riduce gli obblighi" in a for a in piano["avvisi"])


@richiede_pg
async def test_accordo_non_sceglie_la_tipologia(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    b = await _bozza(s.t_admin, {
        "risorsa": "accordo", "ambito": {"livello": "ACCORDO", "accordo": "AV-1", "classe": s.cid},
        "modifiche": [{"codice": "PROFILO_TIPOLOGIA", "valore": ""}],
        "motivo": "Accordo senza profilo"})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert "PROFILO_TIPOLOGIA" in {x["codice"] for x in piano["bloccati"]}


@richiede_pg
async def test_ente_non_abbassa_le_classi_che_ereditano(db_url) -> None:  # noqa: F811
    s = await _scenario(db_url)
    await _imposta(db_url, s, "ENTE", "PROFILO_TIPOLOGIA", "documento_amministrativo_informatico")
    await _imposta(db_url, s, "CLASSE", "PROFILO_TIPOLOGIA_VERSIONE", 2)
    b = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                 "modifiche": [{"codice": "PROFILO_TIPOLOGIA",
                                                "valore": "documento_informatico"}],
                                 "motivo": "Tipologia più leggera per l'ente"})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    motivi = [x["motivo"] for x in piano["bloccati"] if x["codice"] == "PROFILO_TIPOLOGIA"]
    assert servizio.MOTIVO_PROFILO_ABBASSATO in motivi
    assert servizio.MOTIVO_PROFILO_ABBASSATO + " (classe fattura)" in motivi
