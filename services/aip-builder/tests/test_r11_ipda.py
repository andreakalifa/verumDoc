# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R11 — IPdA: mappatura dei profili dell'Allegato 5 (D07) e conformità allo schema
XSD caricabile (D03, VD-C-071 aperta).

  - i metadati dei profili v2 (Soggetti, Tracciature, Riservato, Verifica…) escono
    in ``MetadatiAllegato5`` con i nomi dell'Allegato 5, strutture comprese, e non
    più come testo JSON fra i metadati aggiuntivi; nessun dato si perde;
  - test di CONFORMITÀ parametrici (DI, DAI, AGG): si attivano quando lo schema
    ufficiale UNI 11386 è registrato in ``libs/sincro/schemas``; altrimenti SKIP
    esplicito con il motivo (mai «conforme» senza XSD);
  - con lo schema di PROVA non ufficiale dei test l'IPdA delle tre tipologie è
    valido ma l'esito resta «schema di prova», mai «conforme».
"""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET  # noqa: S405 — XML generato dal test stesso
from pathlib import Path

import pytest
from app.services.sincro_builder import build_sincro_xml

from libs.sincro.validatore import (
    EsitoXsd,
    alterazioni,
    registra_schema,
    schemi_registrati,
    valida_ipda,
)

RADICE = Path(__file__).resolve().parents[3]
PROVA = RADICE / "libs" / "sincro" / "tests" / "dati" / "schema_prova_NON_UFFICIALE"
NS = {"s": "https://schemi.verumdoc.it/ipda/provvisorio/1"}

META_V2 = {
    "identificativo_documento": "DOC-1", "modalita_formazione": "a",
    "tipologia_documentale": "Determina", "data_documento": "2026-11-20",
    "soggetto_produttore_id": "01234567897", "soggetto_produttore_nome": "Ente di prova",
    "oggetto": "Determina di prova", "riservato": False, "tipologia_flusso": "U",
    "tipo_registro": "Protocollo Ordinario/Protocollo Emergenza", "codice_registro": "REG",
    "firmato_digitalmente": True, "sigillato_elettronicamente": False,
    "marcatura_temporale": False, "versione_documento": "2",
    "soggetti": [
        {"ruolo": "Amministrazione Che Effettua La Registrazione", "tipo_soggetto": "PAI",
         "codice_ipa": "ente_prova", "aoo": "AOO_1", "nota_libera": "x"},
        {"ruolo": "Autore", "tipo_soggetto": "PF", "cognome": "Prova", "nome": "Autore",
         "indirizzi_digitali": ["autore@prova.invalid"]}],
    "tracciature_modifiche": [{"tipo_modifica": "Rettifica", "autore_modifica": "op1",
                               "data_ora_modifica": "2026-11-21T10:00:00Z",
                               "id_doc_versione_precedente": "DOC-0"}],
    "parole_chiave": ["determina", "prova"],
}
DOCUMENTI = [{"id": "d1", "nome_file": "determina.pdf", "tipo": "PRINCIPALE",
              "formato": "application/pdf", "dimensione_bytes": 10, "hash_sha256": "0" * 64}]


def _xml(tipo: str = "DAI", meta: dict | None = None) -> bytes:
    return build_sincro_xml("aip-1", "pdv-1", "ente-1", "DETERMINE", DOCUMENTI,
                            allegato5=meta if meta is not None else META_V2, tipo_xml=tipo)


def _sezione(xml: bytes) -> ET.Element:
    radice = ET.fromstring(xml)  # noqa: S314
    trovata = radice.find(".//s:MetadatiDocumento/s:MetadatiAllegato5", NS)
    assert trovata is not None
    return trovata


def test_metadati_allegato5_con_i_nomi_del_testo() -> None:
    sezione = _sezione(_xml())
    testo = lambda p: sezione.findtext(p, namespaces=NS)  # noqa: E731
    assert testo("s:Riservato") == "false"
    assert testo("s:Verifica/s:FirmatoDigitalmente") == "true"
    assert testo("s:DatiDiRegistrazione/s:TipologiaDiFlusso") == "U"
    assert testo("s:DatiDiRegistrazione/s:CodiceRegistro") == "REG"
    assert testo("s:NomeDelDocumento") == "determina.pdf"  # calcolato dal nome del file
    soggetti = sezione.findall("s:Soggetti/s:Elemento", NS)
    assert [s.findtext("s:Ruolo/s:TipoRuolo", namespaces=NS) for s in soggetti] == [
        "Amministrazione Che Effettua La Registrazione", "Autore"]
    assert soggetti[1].findtext("s:Cognome", namespaces=NS) == "Prova"
    assert [v.text for v in soggetti[1].findall(
        "s:IndirizziDigitaliDiRiferimento/s:Valore", NS)] == ["autore@prova.invalid"]
    # chiave non prevista dentro la struttura: conservata con il suo nome
    assert soggetti[0].find("s:Metadato[@nome='nota_libera']", NS).text == "x"
    assert testo("s:TracciatureModificheDocumento/s:Elemento/s:TipoModifica") == "Rettifica"


def test_nessun_metadato_duplicato_ne_perso() -> None:
    radice = ET.fromstring(_xml())  # noqa: S314
    aggiuntivi = {m.get("nome") for m in radice.findall(".//s:MetadatiAggiuntivi/s:Metadato", NS)}
    assert not aggiuntivi & {"soggetti", "riservato", "tracciature_modifiche", "parole_chiave"}
    assert "versione_documento" not in aggiuntivi  # ora in VersioneDelDocumento
    assert radice.findtext(".//s:MetadatiAllegato5/s:VersioneDelDocumento", namespaces=NS) == "2"


def test_registro_dal_soggetto_registrante() -> None:
    registro = ET.fromstring(_xml()).find("s:Registro", NS)  # noqa: S314
    assert registro.findtext("s:CodiceIPA", namespaces=NS) == "ente_prova"
    assert registro.findtext("s:AOO", namespaces=NS) == "AOO_1"


def test_metadati_r05_invariati() -> None:
    """Senza metadati dei profili v2 l'IPdA non ha la sezione nuova (formato invariato)."""
    meta = {k: META_V2[k] for k in ("identificativo_documento", "modalita_formazione",
                                    "tipologia_documentale", "data_documento",
                                    "soggetto_produttore_id", "soggetto_produttore_nome",
                                    "oggetto")}
    assert b"MetadatiAllegato5" not in _xml("DI", meta)


@pytest.mark.parametrize("tipo", ["DI", "DAI", "AGG"])
def test_conformita_allo_schema_ufficiale(tipo: str) -> None:
    """Si attiva quando l'XSD ufficiale UNI 11386 è registrato (README, «Schema UNI
    11386»). Finché il costruttore non è adeguato allo schema, questo test fallirà
    con gli errori dello schema: è il lavoro che resta di VD-C-071."""
    esito = valida_ipda(_xml(tipo))
    if esito.esito is EsitoXsd.SCHEMA_NON_DISPONIBILE:
        pytest.skip("schema ufficiale UNI 11386:2020 non disponibile in libs/sincro/schemas "
                    "(D03, VD-C-071): conformità non verificabile")
    if esito.schema is not None and not esito.schema["ufficiale"]:
        pytest.skip("in libs/sincro/schemas c'è solo uno schema di prova: nessuna conformità")
    # controllo indipendente dall'esito: uno schema UFFICIALE integro è registrato su disco
    # e l'esito cita proprio quello (un validatore che dichiara «conforme» senza non passa)
    ufficiali = [x for x in schemi_registrati().values() if x.ufficiale and not alterazioni(x)]
    assert ufficiali, "esito senza uno schema ufficiale integro registrato"
    assert (esito.schema or {}).get("sha256_principale") in {
        x.impronte[x.principale] for x in ufficiali}
    assert esito.conforme, esito.come_dict()


@pytest.mark.parametrize("tipo", ["DI", "DAI", "AGG"])
def test_schema_di_prova_valida_ma_non_dichiara_conformita(tipo: str, tmp_path: Path) -> None:
    cartella = tmp_path / "schemi" / "prova"
    shutil.copytree(PROVA, cartella)
    registra_schema(cartella, principale="ipda_prova_NON_UFFICIALE.xsd", versione="prova",
                    ufficiale=False, fonte="schema di prova dei test, NON ufficiale")
    esito = valida_ipda(_xml(tipo), cartella=cartella.parent)
    assert esito.esito is EsitoXsd.VALIDO_SCHEMA_DI_PROVA, esito.errori
    assert not esito.conforme
