# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f1 — difetto 3 del rapporto c2: i metadati minimi hanno UNA definizione, nel
motore delle regole (``libs.verumdoc_rules``), usata dal gateway (regola RGL-101)
e dal passo 6 della verifica con l'elenco FOTOGRAFATO nel PdV.

Prima il passo 6 aveva un elenco proprio cablato e cercava solo al primo livello,
mentre il gateway accettava i minimi anche in ``metadati_extra``: un PdV accettato
con 202 poteva essere rifiutato in asincrono per lo stesso controllo.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services.regole_pdv import RegoleNonLeggibili, regole_del_pdv
from app.validators.base import ValidationContext
from app.validators.steps import Step6MetadatiAllegato5

from libs.verumdoc_rules import (
    CATALOGO_V1,
    REGISTRO_V1,
    AmbienteRegole,
    ContestoRisoluzione,
    DatiVersamento,
    DocumentoDaValutare,
    Fase,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
)
from libs.verumdoc_rules.catalogo_v1 import METADATI_MINIMI_PRINCIPALE

COMPLETI = {
    "identificativo_documento": "FT-2026-001", "modalita_formazione": "a",
    "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
    "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
    "oggetto": "Fattura servizi IT marzo 2026",
}


def _ambiente(minimi: list[str] | None = None) -> AmbienteRegole:
    valori = {} if minimi is None else {
        Livello.CONSERVATORE: {METADATI_MINIMI_PRINCIPALE: minimi}}
    return AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari(valori)),
                          ContestoRisoluzione(adesso=datetime(2026, 10, 21, tzinfo=UTC)))


def _gateway_accetta(parametri: dict, ambiente: AmbienteRegole) -> bool:
    """Esito della regola RGL-101 del gateway (stessa libreria)."""
    dati = DatiVersamento("fattura", (DocumentoDaValutare("d1", "PRINCIPALE", "f.pdf",
                                                          parametri),),
                          frozenset({*COMPLETI, "metadati_extra"}))
    esito = CATALOGO_V1.valuta(dati, ambiente, Fase.INDICE)
    return not [r for r in esito.bloccanti if r.regola == "RGL-101"]


async def _validation_accetta(parametri: dict, fotografia: dict | None) -> bool:
    regole = regole_del_pdv(fotografia, limite_mb_predefinito=100, formati_predefiniti=[])
    ctx = ValidationContext(pdv_id="p", tenant_id="t", regole=regole, indice={
        "classe_documentale": "fattura",
        "documenti": [{"id": "d1", "tipo": "PRINCIPALE", "nome_file": "f.pdf",
                       "hash": {"impronta": "a" * 64, "algoritmo": "SHA-256"},
                       "parametri": parametri}]})
    return (await Step6MetadatiAllegato5().validate(ctx)).ok


def _senza(*campi: str, **extra) -> dict:
    parametri = {k: v for k, v in COMPLETI.items() if k not in campi}
    parametri.update(extra)
    return parametri


CASI = [
    COMPLETI,
    _senza("oggetto"),
    _senza("oggetto", metadati_extra={"oggetto": "Oggetto spostato fra i liberi"}),
    _senza("data_documento", "soggetto_produttore_id"),
    {**COMPLETI, "soggetto_produttore_nome": "  "},
]


@pytest.mark.parametrize("parametri", CASI)
@pytest.mark.parametrize("minimi", [None, ["identificativo_documento", "oggetto"]])
async def test_gateway_e_verifica_decidono_allo_stesso_modo(parametri, minimi):
    """Stessa fotografia, stessa decisione: accettato dal gateway ⇔ passo 6 superato."""
    ambiente = _ambiente(minimi)
    gateway = _gateway_accetta(parametri, ambiente)
    fotografia = CATALOGO_V1.fotografia(ambiente)
    assert await _validation_accetta(parametri, fotografia) is gateway


async def test_elenco_fotografato_ridotto_non_rifiuta_cio_che_il_gateway_ha_accettato():
    """Prima l'elenco cablato del passo 6 (7 campi) rifiutava questo PdV, accettato dal
    gateway con i minimi in vigore al versamento."""
    ambiente = _ambiente(["identificativo_documento", "oggetto"])
    parametri = _senza("data_documento", "soggetto_produttore_id")
    assert _gateway_accetta(parametri, ambiente)
    assert await _validation_accetta(parametri, CATALOGO_V1.fotografia(ambiente))


async def test_minimi_in_metadati_extra_rifiutati_da_entrambi():
    parametri = _senza("oggetto", metadati_extra={"oggetto": "Oggetto spostato fra i liberi"})
    assert not _gateway_accetta(parametri, _ambiente())
    assert not await _validation_accetta(parametri, None)


def test_fotografia_dei_minimi_letta_o_rifiutata():
    ambiente = _ambiente(["oggetto"])
    assert _gateway_accetta(dict(COMPLETI), ambiente)   # la regola consulta l'elenco
    foto = CATALOGO_V1.fotografia(ambiente)
    regole = regole_del_pdv(foto, limite_mb_predefinito=100, formati_predefiniti=[])
    assert regole.metadati_minimi == ("oggetto",)
    assert regole.dettagli()["metadati_minimi"] == ["oggetto"]
    # PdV senza fotografia (prima di R03): predefinito normativo del catalogo
    assert len(regole_del_pdv(None, limite_mb_predefinito=1,
                              formati_predefiniti=[]).metadati_minimi) == 7
    rotta = {"impostazioni": {METADATI_MINIMI_PRINCIPALE: {"valore": "oggetto",
                                                           "origine": "x"}}}
    with pytest.raises(RegoleNonLeggibili):
        regole_del_pdv(rotta, limite_mb_predefinito=1, formati_predefiniti=[])


@pytest.mark.parametrize("effetto", ["AVVISA", "IGNORA"])
async def test_rgl101_non_bloccante_al_versamento_non_bloccante_in_verifica(effetto):
    """Il conservatore può portare RGL-101 ad AVVISA/IGNORA: il gateway accetta il PdV
    senza minimi e la verifica, con la stessa fotografia, non lo rifiuta."""
    valori = {Livello.CONSERVATORE: {"REGOLA.RGL-101.EFFETTO": effetto}}
    ambiente = AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari(valori)),
                              ContestoRisoluzione(adesso=datetime(2026, 10, 21, tzinfo=UTC)))
    parametri = _senza("oggetto", "data_documento")
    assert _gateway_accetta(parametri, ambiente)
    assert await _validation_accetta(parametri, CATALOGO_V1.fotografia(ambiente))
    # anche un principale senza «parametri»
    assert _gateway_accetta(None, ambiente)  # type: ignore[arg-type]
    assert await _validation_accetta(None, CATALOGO_V1.fotografia(ambiente))  # type: ignore[arg-type]
