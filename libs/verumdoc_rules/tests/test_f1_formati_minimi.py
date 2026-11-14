# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f1 — difetti 3 e 4 del rapporto c2 nel motore delle regole.

  - metadati minimi: UNA definizione (``metadati_minimi_mancanti``), solo al primo
    livello di ``parametri``: un valore in ``metadati_extra`` non li sostituisce;
    l'elenco si legge dalla fotografia del PdV (``minimi_fotografati``);
  - estensioni ammesse derivate dai formati della classe e dal registro dei
    formati (``libs.verumdoc_rules.formati``), non da un elenco fisso.
"""
from __future__ import annotations

from datetime import UTC, datetime

from libs.verumdoc_rules import (
    CATALOGO_V1,
    REGISTRO_V1,
    AmbienteRegole,
    ContestoRisoluzione,
    DatiVersamento,
    DocumentoDaValutare,
    Fase,
    FornitoreDaDizionari,
    Risolutore,
)
from libs.verumdoc_rules.catalogo_v1 import (
    METADATI_MINIMI_PRINCIPALE,
    metadati_minimi_mancanti,
    minimi_fotografati,
    minimi_predefiniti,
)
from libs.verumdoc_rules.formati import (
    REGISTRO_FORMATI_V1,
    estensione,
    estensioni_ammesse,
    motivo_estensione,
)

MINIMI = {
    "identificativo_documento": "FT-1", "modalita_formazione": "a",
    "tipologia_documentale": "fattura", "data_documento": "2026-10-01",
    "soggetto_produttore_id": "01114601006", "soggetto_produttore_nome": "Acme",
    "oggetto": "Fattura di prova",
}


def _rgl101(metadati: dict) -> list[str]:
    ambiente = AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari({})),
                              ContestoRisoluzione(adesso=datetime(2026, 10, 21, tzinfo=UTC)))
    dati = DatiVersamento("fattura", (DocumentoDaValutare("d1", "PRINCIPALE", "a.pdf",
                                                          metadati),),
                          frozenset({*MINIMI, "metadati_extra"}))
    return [r.campo for r in CATALOGO_V1.valuta(dati, ambiente, Fase.INDICE).rilievi
            if r.regola == "RGL-101"]


def test_minimi_in_metadati_extra_non_valgono():
    """Prima ``cerca_metadato`` li cercava anche in metadati_extra (gateway li accettava,
    validation li rifiutava: PdV accettato con 202 e rifiutato dopo)."""
    meta = dict(MINIMI)
    oggetto = meta.pop("oggetto")
    meta["metadati_extra"] = {"oggetto": oggetto}
    assert metadati_minimi_mancanti(meta, minimi_predefiniti()) == ["oggetto"]
    assert _rgl101(meta) == ["oggetto"]
    assert _rgl101(dict(MINIMI)) == []


def test_minimi_vuoti_e_assenti():
    assert metadati_minimi_mancanti(None, ("a", "b")) == ["a", "b"]
    assert metadati_minimi_mancanti({"a": " ", "b": []}, ("a", "b")) == ["a", "b"]
    assert metadati_minimi_mancanti({"a": "x", "b": 0}, ("a", "b")) == []


def test_minimi_dalla_fotografia():
    foto = {"impostazioni": {METADATI_MINIMI_PRINCIPALE: {"valore": ["oggetto"],
                                                          "origine": "CONSERVATORE"}}}
    assert minimi_fotografati(foto) == ("oggetto",)
    assert minimi_fotografati({"impostazioni": {}}) is None
    assert minimi_fotografati({"impostazioni": {METADATI_MINIMI_PRINCIPALE: {"valore": 3}}}) \
        is None
    assert minimi_fotografati(None) is None
    assert len(minimi_predefiniti()) == 7 and "oggetto" in minimi_predefiniti()


def test_registro_v1_riproduce_l_elenco_di_r02():
    assert REGISTRO_FORMATI_V1.tutte_le_estensioni() == frozenset(
        {".pdf", ".xml", ".p7m", ".tif", ".tiff", ".jpg", ".jpeg", ".png"})


def test_estensioni_dai_formati_della_classe():
    classe = estensioni_ammesse(["application/pdf", "image/jpg"], restrizione_attiva=True)
    assert classe.origine == "classe" and classe.estensioni == {".pdf", ".jpg", ".jpeg"}
    # MIME equivalenti e parametri
    assert estensioni_ammesse(["text/xml; charset=utf-8"],
                              restrizione_attiva=True).estensioni == {".xml"}
    # regola dei formati non bloccante o classe senza formati: tutto il registro
    for formati, attiva in ((["application/pdf"], False), ([], True)):
        tutte = estensioni_ammesse(formati, restrizione_attiva=attiva)
        assert tutte.origine == "registro"
        assert tutte.estensioni == REGISTRO_FORMATI_V1.tutte_le_estensioni()
    # formato della classe fuori registro: nessuna estensione (il servizio non lo riconosce)
    assert estensioni_ammesse(["application/vnd.oasis.opendocument.text"],
                              restrizione_attiva=True).estensioni == frozenset()


def test_motivo_estensione_e_ultima_estensione():
    ammesse = estensioni_ammesse(["application/pkcs7-mime"], restrizione_attiva=True)
    assert estensione("fattura.pdf.p7m") == ".p7m" and estensione("senza") == ""
    assert motivo_estensione("fattura.pdf.p7m", ammesse) is None
    motivo = motivo_estensione("fattura.pdf", ammesse)
    assert motivo is not None and "classe documentale" in motivo and ".p7m" in motivo
