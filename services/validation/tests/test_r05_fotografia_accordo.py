# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 Q1 — la verifica legge dalla fotografia del PdV anche l'accordo di versamento
e la personalizzazione motivata (catalogo 2): i limiti con origine ACCORDO o PDV
valgono come al versamento, i riferimenti vanno nei dettagli dei passi (RdV), una
fotografia con accordo o override malformati non si indovina (VLD-802).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services.regole_pdv import RegoleNonLeggibili, regole_del_pdv

from libs.verumdoc_rules import CATALOGO, REGISTRO, valida_override
from libs.verumdoc_rules.accordi import AccordoVersamento
from libs.verumdoc_rules.impostazioni import Livello
from libs.verumdoc_rules.lettura import prepara_ambiente


def _fotografia(accordo: bool = True, override: bool = True) -> dict:
    accordi = [AccordoVersamento("t", "AV-1", 3, datetime(2026, 1, 1).date(),
                                 valori={"CLASSE_DIMENSIONE_MAX_MB": 2}, id="a3")] \
        if accordo else []
    pdv = valida_override({"motivo": "Concordato col produttore",
                           "valori": {"REGOLA.RGL-103.EFFETTO": "IGNORA"}}, REGISTRO) \
        if override else None
    ambiente = prepara_ambiente(registro=REGISTRO, livelli={Livello.CONSERVATORE: {}},
                                adesso=datetime(2026, 10, 23, tzinfo=UTC), tenant_id="t",
                                classe_id="c", accordi=accordi, override=pdv)
    for codice in REGISTRO.codici():  # come il gateway (carica_ambiente)
        ambiente.effettiva(codice)
    return CATALOGO.fotografia(ambiente)


def test_limite_dell_accordo_e_riferimenti_nei_dettagli() -> None:
    regole = regole_del_pdv(_fotografia(), limite_mb_predefinito=5120, formati_predefiniti=[])
    assert regole.limite_byte_file == 2 * 1024 * 1024 and regole.origine_limite == "ACCORDO"
    dettagli = regole.dettagli()
    assert dettagli["accordo"] == {"codice": "AV-1", "versione": 3, "valido_dal": "2026-01-01"}
    assert dettagli["override"] == ["REGOLA.RGL-103.EFFETTO"]
    assert dettagli["catalogo_regole"] == CATALOGO.versione  # R08: catalogo 3 in vigore
    assert regole.contenuto is not None and regole.contenuto["FIRME_LIVELLO_VERIFICA"] == \
        "RILEVATA"


def test_fotografia_senza_accordo_e_override() -> None:
    regole = regole_del_pdv(_fotografia(False, False), limite_mb_predefinito=5120,
                            formati_predefiniti=[])
    assert regole.dettagli()["accordo"] is None and regole.dettagli()["override"] is None


@pytest.mark.parametrize("chiave,valore", [
    ("accordo", {"versione": 1}),
    ("accordo", "AV-1"),
    ("override", {"valori": {"X": 1}}),
    ("override", {"motivo": "ok ok ok ok", "valori": "x"}),
])
def test_accordo_o_override_malformati_non_si_indovinano(chiave, valore) -> None:
    foto = _fotografia()
    foto[chiave] = valore
    with pytest.raises(RegoleNonLeggibili):
        regole_del_pdv(foto, limite_mb_predefinito=5120, formati_predefiniti=[])
