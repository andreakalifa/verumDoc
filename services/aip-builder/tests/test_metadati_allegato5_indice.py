# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R03 b4 — riga di metadati_allegato5 dai metadati versati (funzione pura, VD-C-093)."""
from __future__ import annotations

import pytest
from app.services.aip_engine import CAMPI_MINIMI_ALLEGATO5, riga_metadati_allegato5

MINIMI = {
    "identificativo_documento": "FT-1", "modalita_formazione": "A",
    "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
    "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme",
    "oggetto": "Fattura di prova",
}
DOC = {"id": "d1", "tipo": "PRINCIPALE", "formato": "application/pdf"}


def test_riga_metadati_allegato5_minimi_e_predefiniti():
    riga = riga_metadati_allegato5(DOC, MINIMI, 2)
    assert riga["modalita_formazione"] == "a" and riga["data_documento"] == "2026-03-19"
    assert riga["lingua"] == "it" and riga["versione_documento"] == "1.0"
    assert riga["formato"] == "application/pdf" and riga["numero_allegati"] == 2
    assert riga["metadati_extra"] == {} and riga["soggetto_destinatario"] == []


@pytest.mark.parametrize("campo", CAMPI_MINIMI_ALLEGATO5)
def test_riga_metadati_allegato5_minimo_mancante_nessuna_riga(campo):
    assert riga_metadati_allegato5(DOC, {**MINIMI, campo: None}, 0) is None


@pytest.mark.parametrize("modifica", [{"modalita_formazione": "SCANSIONE"},
                                      {"data_documento": "19/03/2026"}])
def test_riga_metadati_allegato5_valori_non_interpretabili_nessuna_riga(modifica):
    assert riga_metadati_allegato5(DOC, {**MINIMI, **modifica}, 0) is None


def test_riga_metadati_allegato5_senza_parametri():
    assert riga_metadati_allegato5(DOC, None, 0) is None


def test_riga_metadati_allegato5_extra_e_troncamento():
    riga = riga_metadati_allegato5(DOC, {
        **MINIMI, "aoo": "A" * 300, "importo": 10, "metadati_extra": {"cig": "Z"},
        "data_registrazione": "2026-03-20", "soggetto_destinatario": "Unico",
    }, 0)
    assert len(riga["aoo"]) == 100
    assert riga["metadati_extra"] == {"cig": "Z", "importo": 10}
    assert riga["data_registrazione"] == "2026-03-20"
    assert riga["soggetto_destinatario"] == ["Unico"]
