# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test dei profili di metadati versionati e della validazione dei tipi (REGOLE §8.4, §8.8)."""

from __future__ import annotations

from datetime import date

import pytest

from libs.verumdoc_rules.profili import (
    CampoProfilo,
    ProfiloMetadati,
    ProfiloNonValido,
    TipoCampo,
    motivo_codice_fiscale,
    motivo_partita_iva,
    motivo_tipo,
    seleziona_profilo,
)


def _profilo(versione: int, dal: date, al: date | None = None) -> ProfiloMetadati:
    return ProfiloMetadati("fattura", versione, dal, (CampoProfilo("a", TipoCampo.STRINGA),), al)


def test_seleziona_profilo_versione_valida_alla_data():
    v1 = _profilo(1, date(2026, 1, 1), date(2026, 6, 30))
    v2 = _profilo(2, date(2026, 7, 1))
    assert seleziona_profilo([v1, v2], date(2026, 6, 30)) is v1
    assert seleziona_profilo([v1, v2], date(2026, 7, 1)) is v2


def test_seleziona_profilo_nessuna_versione_valida():
    assert seleziona_profilo([_profilo(1, date(2027, 1, 1))], date(2026, 1, 1)) is None
    assert seleziona_profilo([], date(2026, 1, 1)) is None


def test_seleziona_profilo_sovrapposte_vince_la_piu_alta():
    v1, v2 = _profilo(1, date(2026, 1, 1)), _profilo(2, date(2026, 1, 1))
    assert seleziona_profilo([v2, v1], date(2026, 3, 1)) is v2


def test_profilo_campi_doppi_rifiutato():
    with pytest.raises(ProfiloNonValido):
        ProfiloMetadati(
            "x",
            1,
            date(2026, 1, 1),
            (CampoProfilo("a", TipoCampo.STRINGA), CampoProfilo("a", TipoCampo.INTERO)),
        )


def test_profilo_date_invertite_rifiutato():
    with pytest.raises(ProfiloNonValido):
        _profilo(1, date(2026, 2, 1), date(2026, 1, 1))


def test_campo_da_dict_e_ritorno():
    campo = CampoProfilo.da_dict(
        {
            "nome": "stato",
            "tipo": "enum",
            "obbligatorio": True,
            "sensibile": False,
            "valori_ammessi": ["A", "B"],
        }
    )
    assert campo.tipo is TipoCampo.ENUM and campo.obbligatorio
    assert CampoProfilo.da_dict(campo.come_dict()) == campo


@pytest.mark.parametrize(
    "dati",
    [
        {"nome": "x"},
        {"nome": "x", "tipo": "colore"},
        {"nome": "1x", "tipo": "stringa"},
        {"nome": "x", "tipo": "enum"},
    ],
)
def test_campo_da_dict_non_valido(dati):
    with pytest.raises(ProfiloNonValido):
        CampoProfilo.da_dict(dati)


def test_profilo_riferimento_e_nomi_sensibili():
    profilo = ProfiloMetadati(
        "f",
        3,
        date(2026, 1, 1),
        (
            CampoProfilo("a", TipoCampo.STRINGA),
            CampoProfilo("b", TipoCampo.STRINGA, sensibile=True),
        ),
        id="p-1",
    )
    assert profilo.nomi_sensibili() == {"b"} and profilo.campo("a") is not None
    assert profilo.riferimento() == {
        "id": "p-1",
        "codice": "f",
        "versione": 3,
        "valido_dal": "2026-01-01",
        "valido_al": None,
    }


@pytest.mark.parametrize(
    ("tipo", "valore"),
    [
        (TipoCampo.STRINGA, "testo"),
        (TipoCampo.DATA, "2026-02-28"),
        (TipoCampo.INTERO, 7),
        (TipoCampo.INTERO, "-7"),
        (TipoCampo.DECIMALE, "10.50"),
        (TipoCampo.DECIMALE, 3.5),
        (TipoCampo.BOOLEANO, False),
        (TipoCampo.CODICE_FISCALE, "RSSMRA85T10A562S"),
        (TipoCampo.CODICE_FISCALE, "80003170661"),
        (TipoCampo.PARTITA_IVA, "01114601006"),
        (TipoCampo.CODICE_IPA, "c_h501"),
    ],
)
def test_motivo_tipo_valore_ammesso(tipo, valore):
    assert motivo_tipo(CampoProfilo("x", tipo), valore) is None


@pytest.mark.parametrize(
    ("tipo", "valore"),
    [
        (TipoCampo.STRINGA, 5),
        (TipoCampo.DATA, "2026-02-30"),
        (TipoCampo.DATA, "28/02/2026"),
        (TipoCampo.INTERO, True),
        (TipoCampo.INTERO, "7.0"),
        (TipoCampo.DECIMALE, "10,50"),
        (TipoCampo.DECIMALE, float("nan")),
        (TipoCampo.BOOLEANO, "true"),
        (TipoCampo.CODICE_FISCALE, "RSSMRA85T10A562X"),
        (TipoCampo.CODICE_FISCALE, "RSSMRA85T99A562S"),
        (TipoCampo.PARTITA_IVA, "12345678901"),
        (TipoCampo.PARTITA_IVA, "1234"),
        (TipoCampo.CODICE_IPA, "c h501"),
    ],
)
def test_motivo_tipo_valore_non_ammesso(tipo, valore):
    assert motivo_tipo(CampoProfilo("x", tipo), valore) is not None


def test_motivo_tipo_enum():
    campo = CampoProfilo("x", TipoCampo.ENUM, valori_ammessi=("A", "B"))
    assert motivo_tipo(campo, "A") is None
    assert "A, B" in (motivo_tipo(campo, "C") or "")


def test_motivi_non_contengono_il_valore():
    for motivo in (motivo_codice_fiscale("RSSMRA85T10A562X"), motivo_partita_iva("99999999999")):
        assert motivo and "RSSMRA" not in motivo and "9999" not in motivo
