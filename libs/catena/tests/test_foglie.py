# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prove di libs/catena/canonico.py e libs/catena/foglie.py (contratto K1 di R04)."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from libs.catena.canonico import ValoreNonCanonico, json_canonico
from libs.catena.foglie import (
    ComponentePda,
    albero_pda,
    albero_pda_da_foglie,
    dati_foglia_documento,
    dati_foglia_ipda,
    dati_foglia_registro,
    dati_foglia_riversamento,
    impronta_metadati,
    iso_utc,
)
from libs.catena.rfc6962 import hash_foglia, prova_inclusione, radice, verifica_inclusione

SHA = hashlib.sha256(b"file").hexdigest()
IPDA = hashlib.sha256(b"ipda").hexdigest()


def test_json_canonico_chiavi_ordinate_senza_spazi_utf8():
    assert json_canonico({"b": 1, "a": ["è", None, True]}) == \
        '{"a":["è",null,true],"b":1}'.encode()
    assert json_canonico({"z": {"y": 1, "x": 2}}) == b'{"z":{"x":2,"y":1}}'


@pytest.mark.parametrize("valore", [{"a": 1.5}, {1: "a"}, {"a": float("nan")}, {"a": object()}])
def test_json_canonico_tipi_non_ammessi(valore):
    with pytest.raises(ValoreNonCanonico):
        json_canonico(valore)


def test_iso_utc_formato_unico():
    istante = datetime(2026, 10, 23, 10, 15, tzinfo=timezone(timedelta(hours=2)))
    assert iso_utc(istante) == "2026-10-23T08:15:00.000000Z"
    with pytest.raises(ValueError):
        iso_utc(datetime(2026, 1, 1))


def test_impronta_metadati_indipendente_dall_ordine_e_con_decimali():
    assert impronta_metadati({"a": 1, "b": 2}) == impronta_metadati({"b": 2, "a": 1})
    assert impronta_metadati(None) == hashlib.sha256(b"{}").hexdigest()
    assert impronta_metadati({"importo": 1.5}) == impronta_metadati({"importo": "1.5"})


def test_dati_foglia_documento_formato_contratto():
    dati = json.loads(dati_foglia_documento("urn:x", "doc-1", "f.pdf", SHA.upper(), {"a": 1}))
    assert dati == {"v": 1, "pda": "urn:x", "doc": "doc-1", "file": "f.pdf", "sha256": SHA,
                    "metadati": impronta_metadati({"a": 1})}
    assert json.loads(dati_foglia_ipda("urn:x", IPDA)) == {"v": 1, "pda": "urn:x", "ipda": IPDA}
    with pytest.raises(ValueError):
        dati_foglia_documento("urn:x", "d", "f", "non-hex", None)


def test_albero_pda_ordine_totale_e_ipda_ultima():
    componenti = [
        ComponentePda("doc-b", "b.pdf", SHA, None, 1),
        ComponentePda("doc-a", "a.pdf", SHA, {"x": 1}, 0),
        ComponentePda("doc-0", "z.pdf", SHA, None, 1),
    ]
    albero = albero_pda("urn:x", componenti, IPDA)
    assert albero.documenti == ("doc-a", "doc-0", "doc-b", "")
    assert json.loads(albero.foglie[-1])["ipda"] == IPDA
    # A parità di contenuto, stessa radice qualunque sia l'ordine d'ingresso (§12.8)
    assert albero_pda("urn:x", list(reversed(componenti)), IPDA).radice == albero.radice
    assert albero.radice == radice([hash_foglia(f.encode()) for f in albero.foglie])
    i = albero.indice_documento("doc-b")
    foglie = albero.hash_foglie()
    assert verifica_inclusione(foglie[i], i, len(foglie), prova_inclusione(foglie, i),
                               albero.radice)
    assert albero.indice_documento("assente") is None


def test_albero_pda_da_foglie_ricostruito_e_canonico():
    albero = albero_pda("urn:x", [ComponentePda("d", "f", SHA, None, 0)], IPDA)
    assert albero_pda_da_foglie(albero.foglie) == albero
    with pytest.raises(ValueError):
        albero_pda_da_foglie(['{"v": 1}'])


def test_dati_foglia_registro_e_riversamento():
    quando = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    dati = json.loads(dati_foglia_registro("urn:x", "ab" * 32, "ente-1", quando))
    assert dati == {"v": 1, "pda": "urn:x", "radice_pda": "ab" * 32, "ente": "ente-1",
                    "conservato_il": "2026-10-23T08:00:00.000000Z"}
    riv = json.loads(dati_foglia_riversamento("urn:y", SHA, "ente-1", quando,
                                              {"numero": 4, "hash": IPDA, "formato": 2}))
    assert riv["tipo"] == "riversamento" and riv["blocco"] == {"numero": 4, "hash": IPDA,
                                                               "formato": 2}
    assert "blocco" not in json.loads(dati_foglia_riversamento("urn:y", SHA, "e", quando))
