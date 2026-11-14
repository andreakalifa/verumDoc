# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test del registro delle impostazioni e della catena di risoluzione (REGOLE §8.1, §8.2, §8.5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from libs.verumdoc_rules.impostazioni import (
    ASSENTE,
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Impostazione,
    ImpostazioneSconosciuta,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    TipoImpostazione,
    ValoreImpostazioneNonValido,
    effettiva,
    valore_per_fotografia,
)

ADESSO = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
CTX = ContestoRisoluzione(adesso=ADESSO, tenant_id="t1", classe_id="c1")

LIMITE = Impostazione("LIMITE", TipoImpostazione.INTERO, 100, "limite", minimo=1)
BLOCCATA = Impostazione(
    "BLOCCATA",
    TipoImpostazione.BOOLEANO,
    True,
    "imposta dalla norma",
    norm_ref="norma",
    locked=True,
)


def _risolutore(valori: dict) -> Risolutore:
    return Risolutore(RegistroImpostazioni([LIMITE, BLOCCATA]), FornitoreDaDizionari(valori))


def test_effettiva_senza_valori_restituisce_predefinito_con_origine():
    assert _risolutore({}).effettiva(CTX, "LIMITE") == (100, Livello.PREDEFINITO)


def test_effettiva_classe_prevale_su_ente_e_conservatore():
    valori = {
        Livello.CLASSE: {"LIMITE": 5},
        Livello.ENTE: {"LIMITE": 7},
        Livello.CONSERVATORE: {"LIMITE": 9},
    }
    valore, origine = _risolutore(valori).effettiva(CTX, "LIMITE")
    assert (valore, origine) == (5, Livello.CLASSE)


def test_effettiva_ente_prevale_su_conservatore():
    valori = {Livello.ENTE: {"LIMITE": 7}, Livello.CONSERVATORE: {"LIMITE": 9}}
    assert _risolutore(valori).effettiva(CTX, "LIMITE") == (7, Livello.ENTE)


def test_effettiva_livelli_r05_previsti_e_primi_nella_catena():
    """Override del PdV e accordo di versamento (R05) prevalgono su tutto."""
    valori = {
        Livello.OVERRIDE_PDV: {"LIMITE": 1},
        Livello.ACCORDO: {"LIMITE": 2},
        Livello.CLASSE: {"LIMITE": 3},
    }
    assert _risolutore(valori).effettiva(CTX, "LIMITE") == (1, Livello.OVERRIDE_PDV)
    del valori[Livello.OVERRIDE_PDV]
    assert _risolutore(valori).effettiva(CTX, "LIMITE") == (2, Livello.ACCORDO)


def test_effettiva_locked_ignora_i_livelli_del_cliente():
    valori = {Livello.CLASSE: {"BLOCCATA": False}, Livello.ENTE: {"BLOCCATA": False}}
    assert _risolutore(valori).effettiva(CTX, "BLOCCATA") == (True, Livello.PREDEFINITO)


def test_effettiva_locked_ammette_il_conservatore():
    valori = {Livello.ENTE: {"BLOCCATA": True}, Livello.CONSERVATORE: {"BLOCCATA": False}}
    assert _risolutore(valori).effettiva(CTX, "BLOCCATA") == (False, Livello.CONSERVATORE)


def test_effettiva_valore_di_tipo_errato_errore_esplicito():
    with pytest.raises(ValoreImpostazioneNonValido) as err:
        _risolutore({Livello.CLASSE: {"LIMITE": "molti"}}).effettiva(CTX, "LIMITE")
    assert err.value.livello is Livello.CLASSE and err.value.codice == "LIMITE"


def test_effettiva_valore_sotto_il_minimo_errore():
    with pytest.raises(ValoreImpostazioneNonValido):
        _risolutore({Livello.ENTE: {"LIMITE": 0}}).effettiva(CTX, "LIMITE")


def test_effettiva_codice_sconosciuto_errore():
    with pytest.raises(ImpostazioneSconosciuta):
        _risolutore({}).effettiva(CTX, "NON_ESISTE")


def test_effettiva_forma_funzionale_uguale_al_risolutore():
    registro = RegistroImpostazioni([LIMITE])
    fornitore = FornitoreDaDizionari({Livello.ENTE: {"LIMITE": 3}})
    assert effettiva(CTX, "LIMITE", registro=registro, fornitore=fornitore) == (3, Livello.ENTE)


def test_registro_codice_doppio_rifiutato():
    with pytest.raises(ValueError):
        RegistroImpostazioni([LIMITE, LIMITE])


def test_registro_predefinito_di_tipo_errato_rifiutato():
    with pytest.raises(ValoreImpostazioneNonValido):
        RegistroImpostazioni([Impostazione("X", TipoImpostazione.INTERO, "dieci", "x")])


def test_fornitore_da_dizionari_livello_assente():
    assert FornitoreDaDizionari({}).valore(Livello.ENTE, CTX, "LIMITE") is ASSENTE


@pytest.mark.parametrize(
    ("tipo", "grezzo", "atteso"),
    [
        (TipoImpostazione.INTERO, "42", 42),
        (TipoImpostazione.BOOLEANO, False, False),
        (TipoImpostazione.CODICI, ["a", " b "], ("a", "b")),
        (TipoImpostazione.DATA, "2027-03-31", date(2027, 3, 31)),
        (TipoImpostazione.DURATA, 60, timedelta(seconds=60)),
    ],
)
def test_converti_valori_ammessi(tipo, grezzo, atteso):
    voce = Impostazione("X", tipo, None if tipo is not TipoImpostazione.CODICI else (), "x")
    assert voce.converti(grezzo) == atteso


@pytest.mark.parametrize(
    ("tipo", "grezzo"),
    [
        (TipoImpostazione.INTERO, True),
        (TipoImpostazione.BOOLEANO, "true"),
        (TipoImpostazione.CODICI, "a,b"),
        (TipoImpostazione.CODICI, ["a", ""]),
        (TipoImpostazione.DATA, "31/03/2027"),
        (TipoImpostazione.DATA, datetime(2027, 3, 31, tzinfo=UTC)),
        (TipoImpostazione.DURATA, -1),
    ],
)
def test_converti_valori_non_ammessi(tipo, grezzo):
    voce = Impostazione("X", tipo, None, "x")
    with pytest.raises(ValoreImpostazioneNonValido):
        voce.converti(grezzo)


def test_converti_scelta_fuori_elenco_rifiutata():
    voce = Impostazione("X", TipoImpostazione.SCELTA, "A", "x", scelte=("A", "B"))
    assert voce.converti("B") == "B"
    with pytest.raises(ValoreImpostazioneNonValido):
        voce.converti("C")


def test_valore_per_fotografia_serializzabile():
    assert valore_per_fotografia(("a",)) == ["a"]
    assert valore_per_fotografia(date(2027, 1, 2)) == "2027-01-02"
    assert valore_per_fotografia(timedelta(minutes=1)) == 60
    assert valore_per_fotografia(5) == 5


def test_giorno_locale_usa_l_ora_italiana():
    from libs.verumdoc_rules.impostazioni import giorno_locale

    # 23:30 UTC del 31 marzo = 1 aprile in Italia (ora legale)
    assert giorno_locale(datetime(2027, 3, 31, 23, 30, tzinfo=UTC)) == date(2027, 4, 1)
    assert giorno_locale(datetime(2027, 3, 31, 21, 0, tzinfo=UTC)) == date(2027, 3, 31)
    with pytest.raises(ValueError):
        giorno_locale(datetime(2027, 3, 31))
