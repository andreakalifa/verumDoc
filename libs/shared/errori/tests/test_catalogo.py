# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Catalogo errori: caricamento, ricerca e validazione della forma (VD-C-100)."""
from __future__ import annotations

import copy

import pytest

from libs.shared.errori import (
    CODICI,
    FAMIGLIE,
    CatalogoNonValido,
    Esito,
    cerca,
    codice_generico,
    valida_catalogo,
    voce,
)
from libs.shared.errori._catalogo_dati import DATI
from libs.shared.errori.catalogo import _costruisci


def test_catalogo_caricato_e_valido():
    assert valida_catalogo(DATI) == []
    assert len(CODICI) > 150
    for v in CODICI.values():
        assert v.famiglia in FAMIGLIE
        assert v.codice.startswith(v.famiglia + "-")
        assert isinstance(v.esito, Esito)
        if v.esito is not Esito.ERRORE:
            assert v.ritentabile is False, v.codice


def test_famiglie_nuove_di_r03_presenti():
    for sigla in ("UPL", "SFTP", "WH", "EVT", "CMP", "RGL", "QTA", "VAL", "HTTP", "INTERNAL"):
        assert FAMIGLIE[sigla].descrizione


@pytest.mark.parametrize("codice", [
    "VRS-102", "AUTH-003", "RATE-001", "LC-501", "DST-404", "PDND-503", "SIG-403",
    "HC-409", "INTERNAL-001", "VAL-422", "HTTP-404", "FIRMA-503-NON-CONFIGURATA",
])
def test_codici_esistenti_censiti(codice):
    assert voce(codice).codice == codice


def test_codice_sconosciuto_keyerror_chiaro():
    with pytest.raises(KeyError, match="non presente nel catalogo"):
        voce("ZZZ-999")
    assert cerca("ZZZ-999") is None
    assert cerca(None) is None


def test_alias_del_vecchio_formato_pdnd():
    assert cerca("invalid_token").codice == "PDND-401"
    assert cerca("token_expired").codice == "PDND-401-SCADUTO"
    assert cerca("pdnd_not_configured").codice == "PDND-503"


def test_retry_after_dal_catalogo():
    assert voce("RATE-001").retry_after == 60
    assert voce("VRS-501").retry_after == 30
    assert voce("VRS-102").retry_after is None


def test_codice_generico():
    assert codice_generico(404) == "HTTP-404"
    assert codice_generico(418) == "HTTP-400"
    assert codice_generico(599) == "HTTP-500"


def _valido() -> dict:
    return copy.deepcopy(DATI)


@pytest.mark.parametrize(("modifica", "atteso"), [
    (lambda d: d["codici"]["VRS-102"].update(status=200), "fra 400 e 599"),
    (lambda d: d["codici"]["VRS-102"].update(esito="FORSE"), "esito 'FORSE'"),
    (lambda d: d["codici"]["VRS-102"].update(ritentabile=True), "non può essere ritentabile"),
    (lambda d: d["codici"]["VRS-102"].update(famiglia="XYZ"), "non dichiarata"),
    (lambda d: d["codici"]["VRS-102"].update(famiglia="SEC"), "prefisso"),
    (lambda d: d["codici"]["VRS-102"].pop("azione"), "manca `azione`"),
    (lambda d: d["codici"]["VRS-102"].update(extra=1), "campi non previsti"),
    (lambda d: d["codici"]["VRS-102"].update(retry_after=-1), "intero positivo"),
    (lambda d: d["codici"]["VRS-102"].update(contesto="boh"), "contesto"),
    (lambda d: d["codici"]["VRS-102"].update(alias=["invalid_token"]), "già usato"),
    (lambda d: d["codici"].update({"vrs_1": dict(d["codici"]["VRS-102"])}), "forma del codice"),
    (lambda d: d["famiglie"]["VRS"].update(descrizione=""), "manca la descrizione"),
    (lambda d: d.pop("versione"), "versione"),
])
def test_validazione_scopre_i_difetti(modifica, atteso):
    d = _valido()
    modifica(d)
    problemi = valida_catalogo(d)
    assert any(atteso in p for p in problemi), problemi
    with pytest.raises(CatalogoNonValido):
        _costruisci(d)


def test_voce_dismessa_letta_con_i_sostituti():
    """R03 i1: i codici dismessi restano nel catalogo, marcati, con i sostituti."""
    from libs.shared.errori.catalogo import voce

    v = voce("VRS-103")
    assert v.dismesso is True and v.sostituito_da == ("VRS-130", "RGL-101")
    assert v.titolo.startswith("Dismesso")
    assert voce("VRS-130").dismesso is False and voce("VRS-130").sostituito_da == ()
