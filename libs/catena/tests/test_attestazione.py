# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Revisione 2 di R05 (S8): attestazione firmata del ciclo di vita di un PdA.
Revisione 3 di R05 (T2, T3): v2 con l'istante del rilascio e lo stato del PdA."""
from __future__ import annotations

import pytest

from libs.catena import attestazione as at
from libs.catena import nota
from libs.catena.rfc6962 import hash_foglia

PDA = "urn:verumdoc:pda:00000000-0000-0000-0000-000000000001:2026:000042"


def _firmatario() -> nota.Firmatario:
    return nota.analizza_firmatario(nota.genera_chiave("verumdoc-registro")[0])


def _att(**altro) -> at.Attestazione:
    voci = [(12, hash_foglia(b"scarto")), (7, hash_foglia(b"rettifica"))]
    return at.Attestazione(**{"origine": "verumdoc.it/registro/v1", "dimensione": 20,
                              "pda": PDA, "foglie": 2, "elenco": at.impronta_elenco(voci),
                              "rilasciata": "2026-10-23T08:00:00Z", "stato": "CONSERVATO",
                              **altro})


def test_firma_analisi_e_verifica() -> None:
    f = _firmatario()
    testo = at.firma(_att(), f)
    assert at.analizza(testo) == _att()
    assert at.firmata_da(testo, f.verificatore)
    assert not at.firmata_da(testo, _firmatario().verificatore)
    # un campo cambiato dopo la firma (meno foglie): la firma non torna
    alterato = testo.replace("foglie 2\n", "foglie 1\n")
    assert at.analizza(alterato).foglie == 1 and not at.firmata_da(alterato, f.verificatore)


def test_impronta_dell_elenco_indipendente_dall_ordine_e_sensibile_alle_foglie() -> None:
    a, b = (7, hash_foglia(b"rettifica")), (12, hash_foglia(b"scarto"))
    assert at.impronta_elenco([a, b]) == at.impronta_elenco([b, a])
    assert at.impronta_elenco([a]) != at.impronta_elenco([a, b])
    assert at.impronta_elenco([]) == at.impronta_elenco(())


def test_l_attestazione_non_si_scambia_per_un_checkpoint() -> None:
    """Stessa chiave del registro: il corpo NON deve essere un checkpoint valido."""
    with pytest.raises(nota.ErroreNota):
        nota.analizza(at.corpo(_att()))
    corpo_cp = nota.formatta(nota.Checkpoint("verumdoc.it/registro/v1", 20, b"\x00" * 32))
    with pytest.raises(at.AttestazioneNonValida):
        at.analizza(nota.componi(corpo_cp, [_firmatario().firma(corpo_cp)]))


@pytest.mark.parametrize("campo", [{"elenco": "XYZ"}, {"foglie": -1}, {"pda": " x"},
                                   {"origine": "a\nb"}, {"rilasciata": "2026-10-23"},
                                   {"rilasciata": "2026-10-23T08:00:00+02:00"},
                                   {"stato": "ELIMINAZIONE"}, {"stato": "conservato"}])
def test_valori_non_validi(campo) -> None:
    with pytest.raises(at.AttestazioneNonValida):
        _att(**campo)


def test_v2_impegna_istante_e_stato_firmati() -> None:
    """T2/T3: istante del rilascio e stato del PdA fanno parte del testo firmato:
    cambiarli invalida la firma; una nota v1 (senza) non si accetta più."""
    f = _firmatario()
    testo = at.firma(_att(stato="SCARTO_PARZIALE"), f)
    assert "\nrilasciata 2026-10-23T08:00:00Z\n" in testo
    assert "\nstato SCARTO_PARZIALE\n" in testo
    assert at.analizza(testo).stato == "SCARTO_PARZIALE"
    for vecchio, nuovo in (("stato SCARTO_PARZIALE", "stato CONSERVATO"),
                           ("rilasciata 2026-10-23T08:00:00Z", "rilasciata 2030-01-01T00:00:00Z")):
        alterato = testo.replace(vecchio, nuovo)
        assert at.analizza(alterato) != at.analizza(testo)
        assert not at.firmata_da(alterato, f.verificatore)
    a = _att()
    v1 = (f"verumdoc.it/attestazione-ciclo-vita/v1\norigine {a.origine}\ncheckpoint "
          f"{a.dimensione}\npda {a.pda}\nfoglie {a.foglie}\nelenco {a.elenco}\n")
    with pytest.raises(at.AttestazioneNonValida, match="v2"):
        at.analizza(nota.componi(v1, [f.firma(v1)]))


@pytest.mark.parametrize(("stato_db", "atteso"), [
    ("CONSERVATO", "CONSERVATO"), ("ESIBIZIONE", "CONSERVATO"), ("IN_CATENA", "CONSERVATO"),
    ("ELIMINAZIONE", "SCARTO"), ("ELIMINAZIONE_INCOMPLETA", "SCARTO_PARZIALE")])
def test_stato_attestato_dallo_stato_del_pda(stato_db, atteso) -> None:
    assert at.stato_da_aip(stato_db) == atteso


def test_istante_canonico_utc() -> None:
    from datetime import UTC, datetime, timedelta, timezone

    roma = timezone(timedelta(hours=2))
    assert at.istante(datetime(2026, 10, 23, 10, 0, 5, 999, tzinfo=roma)) == "2026-10-23T08:00:05Z"
    assert at.istante(datetime(2026, 10, 23, 8, 0, tzinfo=UTC)) == "2026-10-23T08:00:00Z"
    with pytest.raises(at.AttestazioneNonValida):
        at.istante(datetime(2026, 10, 23, 8, 0))  # noqa: DTZ001 — senza fuso: rifiutato
