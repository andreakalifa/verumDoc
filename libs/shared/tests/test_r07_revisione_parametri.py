# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R07, revisione W3 — il calcolo dei valori effettivi ignora (e segnala) le righe
a un livello che la voce non ammette: prima una riga ENTE di una voce solo del
conservatore (piani, periodo dell'audit), scritta a mano, veniva applicata."""
from __future__ import annotations

from libs.shared.db import metriche
from libs.shared.parametri import con_limiti, risolvi
from libs.shared.security.quote import CODICI_PARAMETRI_QUOTE, ImpostazioniQuote, _parametri_quote
from libs.verumdoc_rules.impostazioni import Livello


def test_riga_ente_di_voce_solo_conservatore_ignorata_e_contata() -> None:
    metriche.azzera()
    livelli = {Livello.CONSERVATORE: {}, Livello.ENTE: {
        "QUOTA_PIANO_STARTER_PDV_GIORNO": 9_999_999, "AUDIT_EXPORT_PERIODO_MINUTI": 60,
        "QUOTA_RALLENTAMENTO_SECONDI": 1, "IDEMPOTENZA_TRANSIZIONE_FINO_AL": "2099-12-31"}}
    valori = risolvi(livelli, list(livelli[Livello.ENTE]))
    assert valori["QUOTA_PIANO_STARTER_PDV_GIORNO"] == 1000
    assert valori["AUDIT_EXPORT_PERIODO_MINUTI"] == 15
    assert valori["QUOTA_RALLENTAMENTO_SECONDI"] == 30
    # voce riservata ma ammessa al livello ENTE (la scrive il conservatore): vale
    assert str(valori["IDEMPOTENZA_TRANSIZIONE_FINO_AL"]) == "2099-12-31"
    assert metriche.valore("verumdoc_parametri_scartati", codice="AUDIT_EXPORT_PERIODO_MINUTI",
                           livello="ENTE", motivo="livello") == 1
    assert metriche.valore("verumdoc_parametri_scartati",
                           codice="IDEMPOTENZA_TRANSIZIONE_FINO_AL", livello="ENTE",
                           motivo="livello") is None


def test_valore_non_valido_contato() -> None:
    metriche.azzera()
    assert risolvi({Livello.ENTE: {"PDD_VALIDITA_ORE": "molte"}},
                   ["PDD_VALIDITA_ORE"]) == {"PDD_VALIDITA_ORE": 72}
    assert metriche.valore("verumdoc_parametri_scartati", codice="PDD_VALIDITA_ORE",
                           livello="ENTE", motivo="valore") == 1


def test_quote_piano_ignora_la_riga_ente_e_legge_il_limite() -> None:
    righe = [{"livello": "ENTE", "codice": "QUOTA_PIANO_STARTER_PDV_GIORNO", "valore": 9_999_999}]
    assert _parametri_quote("starter", righe, ImpostazioniQuote())["pdv_giorno_piano"] == 1000
    assert "LIMITE.QUOTA_SUPERAMENTO" in CODICI_PARAMETRI_QUOTE
    assert con_limiti(["PDD_VALIDITA_ORE", "AUDIT_EXPORT_PERIODO_MINUTI"]) == [
        "PDD_VALIDITA_ORE", "AUDIT_EXPORT_PERIODO_MINUTI", "LIMITE.PDD_VALIDITA_ORE"]
