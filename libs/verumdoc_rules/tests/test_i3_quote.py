# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R03 i3 — quote personalizzate per ente nel registro delle impostazioni (livello ENTE)."""
from __future__ import annotations

import pytest

from libs.verumdoc_rules.impostazioni import Livello, ValoreImpostazioneNonValido
from libs.verumdoc_rules.quote import (
    CODICI_QUOTE,
    QUOTA_PDV_GIORNO,
    QUOTA_RICHIESTE_MINUTO,
    REGISTRO_QUOTE,
    QuotePersonalizzate,
    quote_personalizzate,
)


def test_registro_due_codici_predefinito_zero():
    assert set(CODICI_QUOTE) == {QUOTA_PDV_GIORNO, QUOTA_RICHIESTE_MINUTO}
    assert all(REGISTRO_QUOTE[c].predefinito == 0 for c in CODICI_QUOTE)
    # personalizzabili dal livello ENTE (non locked)
    assert not any(REGISTRO_QUOTE[c].locked for c in CODICI_QUOTE)


def test_senza_valori_vale_il_piano():
    assert quote_personalizzate({}) == QuotePersonalizzate(None, None)


def test_zero_vale_il_piano_valore_positivo_prevale():
    q = quote_personalizzate({QUOTA_PDV_GIORNO: 500000, QUOTA_RICHIESTE_MINUTO: 0})
    assert q == QuotePersonalizzate(pdv_giorno=500000, richieste_minuto=None)
    # intero in forma di testo (JSON di una console): convertito dal registro
    assert quote_personalizzate({QUOTA_RICHIESTE_MINUTO: "9000"}).richieste_minuto == 9000


def test_codici_estranei_ignorati():
    assert quote_personalizzate({"FORMATI_AMMESSI": ["application/pdf"]}) == \
        QuotePersonalizzate(None, None)


@pytest.mark.parametrize("valore", [-1, "molti", True, 10_000_001])
def test_valore_non_valido_errore_di_configurazione(valore):
    with pytest.raises(ValoreImpostazioneNonValido) as errore:
        quote_personalizzate({QUOTA_PDV_GIORNO: valore})
    assert errore.value.livello is Livello.ENTE
