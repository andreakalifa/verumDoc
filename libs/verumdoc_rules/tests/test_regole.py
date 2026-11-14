# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test del catalogo delle regole v1 (REGOLE §8.6: per ogni regola un test per ciascun
effetto BLOCCA / AVVISA / IGNORA; VD-C-094).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from libs.verumdoc_rules import (
    CATALOGO_V1,
    REGISTRO_V1,
    AmbienteRegole,
    CampoProfilo,
    ContestoRisoluzione,
    DatiVersamento,
    DocumentoDaValutare,
    Effetto,
    Fase,
    FornitoreDaDizionari,
    Livello,
    ProfiloMetadati,
    Regola,
    Risolutore,
    TipoCampo,
)
from libs.verumdoc_rules.catalogo_v1 import cerca_metadato, limite_byte_classe
from libs.verumdoc_rules.regole import CatalogoRegole

ADESSO = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
MINIMI = {
    "identificativo_documento": "FT-1",
    "modalita_formazione": "a",
    "tipologia_documentale": "fattura",
    "data_documento": "2026-10-01",
    "soggetto_produttore_id": "01114601006",
    "soggetto_produttore_nome": "Acme",
    "oggetto": "Fattura di prova",
}
CAMPI_NOTI = frozenset({*MINIMI, "lingua", "metadati_extra"})
PROFILO = ProfiloMetadati(
    codice="fattura",
    versione=1,
    valido_dal=date(2026, 1, 1),
    campi=(
        CampoProfilo("importo", TipoCampo.DECIMALE, obbligatorio=True),
        CampoProfilo("cf_cliente", TipoCampo.CODICE_FISCALE, sensibile=True),
    ),
)


def _ambiente(
    *,
    effetti: dict[str, str] | None = None,
    classe: dict | None = None,
    profilo: ProfiloMetadati | None = None,
    livello: Livello = Livello.CLASSE,
) -> AmbienteRegole:
    valori: dict = {livello: {f"REGOLA.{k}.EFFETTO": v for k, v in (effetti or {}).items()}}
    valori.setdefault(Livello.CLASSE, {}).update(classe or {})
    contesto = ContestoRisoluzione(adesso=ADESSO, tenant_id="t", classe_id="c")
    return AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari(valori)), contesto, profilo)


def _dati(
    metadati: dict | None = None,
    *,
    dimensione: int | None = None,
    formato: str | None = None,
    tipo: str = "PRINCIPALE",
) -> DatiVersamento:
    doc = DocumentoDaValutare(
        "doc-1",
        tipo,
        "f.pdf",
        metadati if metadati is not None else dict(MINIMI),
        dimensione,
        formato,
    )
    return DatiVersamento("fattura", (doc,), CAMPI_NOTI)


def _rilievi(regola: str, dati: DatiVersamento, ambiente: AmbienteRegole, fase: Fase):
    return [r for r in CATALOGO_V1.valuta(dati, ambiente, fase).rilievi if r.regola == regola]


# ── RGL-101 metadati minimi (locked) ───────────────────────────────


def test_rgl101_metadato_minimo_mancante_blocca():
    meta = {k: v for k, v in MINIMI.items() if k != "oggetto"}
    rilievi = _rilievi("RGL-101", _dati(meta), _ambiente(), Fase.INDICE)
    assert [(r.effetto, r.campo) for r in rilievi] == [(Effetto.BLOCCA, "oggetto")]


def test_rgl101_avvisa_solo_dal_conservatore():
    meta = {k: v for k, v in MINIMI.items() if k != "oggetto"}
    ambiente = _ambiente(effetti={"RGL-101": "AVVISA"}, livello=Livello.CONSERVATORE)
    assert [r.effetto for r in _rilievi("RGL-101", _dati(meta), ambiente, Fase.INDICE)] == [
        Effetto.AVVISA
    ]


def test_rgl101_ignora_dal_cliente_non_ammesso_resta_blocca():
    """La regola è locked: l'IGNORA della classe (livello cliente) non vale."""
    ambiente = _ambiente(effetti={"RGL-101": "IGNORA"}, livello=Livello.CLASSE)
    rilievi = _rilievi("RGL-101", _dati({}), ambiente, Fase.INDICE)
    assert len(rilievi) == 7 and {r.effetto for r in rilievi} == {Effetto.BLOCCA}


def test_rgl101_ignora_dal_conservatore_non_esegue():
    ambiente = _ambiente(effetti={"RGL-101": "IGNORA"}, livello=Livello.CONSERVATORE)
    assert _rilievi("RGL-101", _dati({}), ambiente, Fase.INDICE) == []


def test_rgl101_metadati_completi_nessun_rilievo():
    assert _rilievi("RGL-101", _dati(), _ambiente(), Fase.INDICE) == []


def test_rgl101_solo_sul_principale():
    assert _rilievi("RGL-101", _dati({}, tipo="ALLEGATO"), _ambiente(), Fase.INDICE) == []


# ── RGL-102 metadati obbligatori della classe ─────────────────────


def test_rgl102_obbligatorio_di_classe_mancante_blocca():
    ambiente = _ambiente(classe={"METADATI_OBBLIGATORI": ["numero_fattura"]})
    rilievi = _rilievi("RGL-102", _dati(), ambiente, Fase.INDICE)
    assert [(r.effetto, r.campo) for r in rilievi] == [(Effetto.BLOCCA, "numero_fattura")]


def test_rgl102_avvisa():
    ambiente = _ambiente(effetti={"RGL-102": "AVVISA"}, classe={"METADATI_OBBLIGATORI": ["x"]})
    assert [r.effetto for r in _rilievi("RGL-102", _dati(), ambiente, Fase.INDICE)] == [
        Effetto.AVVISA
    ]


def test_rgl102_ignora():
    ambiente = _ambiente(effetti={"RGL-102": "IGNORA"}, classe={"METADATI_OBBLIGATORI": ["x"]})
    assert _rilievi("RGL-102", _dati(), ambiente, Fase.INDICE) == []


def test_rgl102_obbligatorio_trovato_nei_metadati_liberi():
    ambiente = _ambiente(classe={"METADATI_OBBLIGATORI": ["numero_fattura"]})
    meta = {**MINIMI, "metadati_extra": {"numero_fattura": "12"}}
    assert _rilievi("RGL-102", _dati(meta), ambiente, Fase.INDICE) == []


def test_rgl102_obbligatorio_del_profilo_mancante_blocca():
    rilievi = _rilievi("RGL-102", _dati(), _ambiente(profilo=PROFILO), Fase.INDICE)
    assert [r.campo for r in rilievi] == ["importo"]


# ── RGL-103 metadati non previsti ─────────────────────────────────


def test_rgl103_metadato_non_previsto_avvisa_predefinito():
    meta = {**MINIMI, "importo": "10.00"}
    rilievi = _rilievi("RGL-103", _dati(meta), _ambiente(), Fase.INDICE)
    assert [(r.effetto, r.campo) for r in rilievi] == [(Effetto.AVVISA, "importo")]


def test_rgl103_blocca():
    meta = {**MINIMI, "importo": "10.00"}
    ambiente = _ambiente(effetti={"RGL-103": "BLOCCA"})
    assert [r.effetto for r in _rilievi("RGL-103", _dati(meta), ambiente, Fase.INDICE)] == [
        Effetto.BLOCCA
    ]


def test_rgl103_ignora():
    meta = {**MINIMI, "importo": "10.00"}
    assert (
        _rilievi("RGL-103", _dati(meta), _ambiente(effetti={"RGL-103": "IGNORA"}), Fase.INDICE)
        == []
    )


def test_rgl103_campo_del_profilo_e_metadati_liberi_previsti():
    meta = {**MINIMI, "importo": "10.00", "metadati_extra": {"qualsiasi": 1}}
    assert _rilievi("RGL-103", _dati(meta), _ambiente(profilo=PROFILO), Fase.INDICE) == []


# ── RGL-104 dimensione massima della classe ───────────────────────


def test_rgl104_file_oltre_il_limite_blocca():
    ambiente = _ambiente(classe={"CLASSE_DIMENSIONE_MAX_MB": 1})
    rilievi = _rilievi("RGL-104", _dati(dimensione=1024 * 1024 + 1), ambiente, Fase.FILE)
    assert [r.effetto for r in rilievi] == [Effetto.BLOCCA]


def test_rgl104_avvisa():
    ambiente = _ambiente(effetti={"RGL-104": "AVVISA"}, classe={"CLASSE_DIMENSIONE_MAX_MB": 1})
    rilievi = _rilievi("RGL-104", _dati(dimensione=2 * 1024 * 1024), ambiente, Fase.FILE)
    assert [r.effetto for r in rilievi] == [Effetto.AVVISA]


def test_rgl104_ignora():
    ambiente = _ambiente(effetti={"RGL-104": "IGNORA"}, classe={"CLASSE_DIMENSIONE_MAX_MB": 1})
    assert _rilievi("RGL-104", _dati(dimensione=2 * 1024 * 1024), ambiente, Fase.FILE) == []


def test_rgl104_al_limite_esatto_ammesso():
    ambiente = _ambiente(classe={"CLASSE_DIMENSIONE_MAX_MB": 1})
    assert _rilievi("RGL-104", _dati(dimensione=1024 * 1024), ambiente, Fase.FILE) == []
    assert limite_byte_classe(ambiente) == 1024 * 1024


def test_rgl104_classe_fino_a_5_gib_come_le_sessioni():
    """R03 i1: il tetto della classe è 5120 MB, come le sessioni di caricamento."""
    from libs.verumdoc_rules import ValoreImpostazioneNonValido

    ambiente = _ambiente(classe={"CLASSE_DIMENSIONE_MAX_MB": 5120})
    assert limite_byte_classe(ambiente) == 5 * 1024 ** 3
    assert _rilievi("RGL-104", _dati(dimensione=5 * 1024 ** 3), ambiente, Fase.FILE) == []
    with pytest.raises(ValoreImpostazioneNonValido):
        limite_byte_classe(_ambiente(classe={"CLASSE_DIMENSIONE_MAX_MB": 5121}))


def test_rgl104_predefinito_cento_mb():
    assert limite_byte_classe(_ambiente()) == 100 * 1024 * 1024


# ── RGL-105 formati ammessi ───────────────────────────────────────


def test_rgl105_formato_fuori_classe_blocca():
    ambiente = _ambiente(classe={"FORMATI_AMMESSI": ["application/pdf"]})
    rilievi = _rilievi("RGL-105", _dati(formato="image/png"), ambiente, Fase.FILE)
    assert [(r.effetto, r.campo) for r in rilievi] == [(Effetto.BLOCCA, "formato")]


def test_rgl105_avvisa():
    ambiente = _ambiente(
        effetti={"RGL-105": "AVVISA"}, classe={"FORMATI_AMMESSI": ["application/pdf"]}
    )
    assert [
        r.effetto for r in _rilievi("RGL-105", _dati(formato="image/png"), ambiente, Fase.FILE)
    ] == [Effetto.AVVISA]


def test_rgl105_ignora():
    ambiente = _ambiente(
        effetti={"RGL-105": "IGNORA"}, classe={"FORMATI_AMMESSI": ["application/pdf"]}
    )
    assert _rilievi("RGL-105", _dati(formato="image/png"), ambiente, Fase.FILE) == []


def test_rgl105_elenco_vuoto_nessuna_restrizione():
    assert _rilievi("RGL-105", _dati(formato="image/png"), _ambiente(), Fase.FILE) == []


def test_rgl105_usa_la_normalizzazione_del_chiamante():
    ambiente = _ambiente(classe={"FORMATI_AMMESSI": ["text/xml"]})
    doc = DocumentoDaValutare("d", "PRINCIPALE", "a.xml", dict(MINIMI), 10, "application/xml")
    dati = DatiVersamento(
        "fattura",
        (doc,),
        CAMPI_NOTI,
        normalizza_formato=lambda m: "application/xml" if m == "text/xml" else m,
    )
    assert _rilievi("RGL-105", dati, ambiente, Fase.FILE) == []


# ── RGL-106 tipi dei metadati del profilo ─────────────────────────


def test_rgl106_tipo_errato_blocca_senza_mostrare_il_valore():
    meta = {**MINIMI, "importo": "dieci", "cf_cliente": "RSSMRA85T10A562X"}
    rilievi = _rilievi("RGL-106", _dati(meta), _ambiente(profilo=PROFILO), Fase.INDICE)
    assert [(r.effetto, r.campo) for r in rilievi] == [
        (Effetto.BLOCCA, "importo"),
        (Effetto.BLOCCA, "cf_cliente"),
    ]
    assert all("dieci" not in r.messaggio and "RSSMRA" not in r.messaggio for r in rilievi)


def test_rgl106_avvisa():
    meta = {**MINIMI, "importo": "dieci"}
    ambiente = _ambiente(effetti={"RGL-106": "AVVISA"}, profilo=PROFILO)
    assert [r.effetto for r in _rilievi("RGL-106", _dati(meta), ambiente, Fase.INDICE)] == [
        Effetto.AVVISA
    ]


def test_rgl106_ignora():
    meta = {**MINIMI, "importo": "dieci"}
    ambiente = _ambiente(effetti={"RGL-106": "IGNORA"}, profilo=PROFILO)
    assert _rilievi("RGL-106", _dati(meta), ambiente, Fase.INDICE) == []


def test_rgl106_senza_profilo_nessun_controllo():
    meta = {**MINIMI, "importo": "dieci"}
    assert _rilievi("RGL-106", _dati(meta), _ambiente(), Fase.INDICE) == []


def test_rgl106_valori_corretti_nessun_rilievo():
    meta = {**MINIMI, "metadati_extra": {"importo": "10.50"}, "cf_cliente": "RSSMRA85T10A562S"}
    assert _rilievi("RGL-106", _dati(meta), _ambiente(profilo=PROFILO), Fase.INDICE) == []


# ── Motore ─────────────────────────────────────────────────────────


def test_valuta_esegue_solo_le_regole_della_fase():
    ambiente = _ambiente(classe={"CLASSE_DIMENSIONE_MAX_MB": 1})
    esito = CATALOGO_V1.valuta(_dati({}, dimensione=10**9), ambiente, Fase.INDICE)
    assert {r.regola for r in esito.rilievi} == {"RGL-101"}
    assert esito.blocca and esito.avvisi == [] and len(esito.bloccanti) == 7


def test_esito_unisci_e_avvisi():
    meta = {**MINIMI, "x": 1}
    ambiente = _ambiente()
    esito = CATALOGO_V1.valuta(_dati(meta), ambiente, Fase.INDICE).unisci(
        CATALOGO_V1.valuta(
            _dati(meta, formato="application/pdf", dimensione=1), ambiente, Fase.FILE
        )
    )
    assert not esito.blocca and [r.regola for r in esito.avvisi] == ["RGL-103"]
    assert esito.avvisi[0].come_dict()["effetto"] == "AVVISA"


def test_fotografia_versioni_e_origine_di_ogni_effetto():
    ambiente = _ambiente(effetti={"RGL-103": "IGNORA"}, profilo=PROFILO)
    CATALOGO_V1.valuta(_dati(), ambiente, Fase.INDICE)
    foto = CATALOGO_V1.fotografia(ambiente)
    assert foto["catalogo_regole"] == "1"
    assert (
        foto["profilo_metadati"]["codice"] == "fattura"
        and foto["profilo_metadati"]["versione"] == 1
    )
    assert foto["impostazioni"]["REGOLA.RGL-103.EFFETTO"] == {
        "valore": "IGNORA",
        "origine": "CLASSE",
    }
    assert foto["impostazioni"]["REGOLA.RGL-105.EFFETTO"]["origine"] == "PREDEFINITO"
    assert foto["impostazioni"]["METADATI_MINIMI_PRINCIPALE"]["origine"] == "PREDEFINITO"


def test_catalogo_regola_locked_non_bloccante_rifiutata():
    with pytest.raises(ValueError):
        CatalogoRegole(
            "x", [Regola("R-1", "r", Fase.INDICE, Effetto.AVVISA, lambda d, a: [], locked=True)]
        )


def test_catalogo_regola_doppia_rifiutata():
    regola = Regola("R-1", "r", Fase.INDICE, Effetto.AVVISA, lambda d, a: [])
    with pytest.raises(ValueError):
        CatalogoRegole("x", [regola, regola])


def test_cerca_metadato_vuoto_non_trovato():
    assert cerca_metadato({"a": "  "}, "a") == (False, None)
    assert cerca_metadato(None, "a") == (False, None)
    assert cerca_metadato({"metadati_extra": {"a": 0}}, "a") == (True, 0)
