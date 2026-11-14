# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R11 (D03, VD-C-071) — validatore pluggable dell'IPdA contro lo schema XSD caricato.

  - senza schema: «schema ufficiale non disponibile», mai conforme (anche con
    la cartella del repository, finché l'XSD UNI non è acquistato);
  - con lo schema di PROVA non ufficiale dei test: valido o non valido, mai
    conforme (mutazione: «conforme» senza schema ufficiale);
  - impronte SHA-256 registrate: un file cambiato o aggiunto dopo la
    registrazione dà «schema alterato»;
  - parser senza rete né entità esterne; validatori intercambiabili.

Revisione R11:
  - T3: lo schema si compila dai byte controllati; include e import solo fra i
    file registrati dentro la cartella (qualsiasi estensione); fuori dalla
    cartella o remoti = «schema non utilizzabile», mai un'eccezione;
  - T4: «ufficiale» solo con le impronte fissate nel codice
    (``schemi_ufficiali.IMPRONTE_UFFICIALI``); lo schema di prova non si registra
    come ufficiale; lo schema di prova sta sotto ``tests/``, esclusa dall'immagine.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from libs.sincro import schemi_ufficiali
from libs.sincro import validatore as modulo
from libs.sincro.validatore import (
    CARTELLA_SCHEMI,
    VALIDATORI,
    EsitoXsd,
    SchemaNonValido,
    alterazioni,
    carica_schema,
    impronte_cartella,
    registra_schema,
    registra_validatore,
    valida_ipda,
)

PROVA = Path(__file__).parent / "dati" / "schema_prova_NON_UFFICIALE"
PRINCIPALE = "ipda_prova_NON_UFFICIALE.xsd"
NS = "https://schemi.verumdoc.it/ipda/provvisorio/1"
VALIDO = (f'<SInCRO xmlns="{NS}" versione="2.0" tipo="DI"><Intestazione>'
          "<IdentificativoAIP>a</IdentificativoAIP><IdentificativoPdV>p</IdentificativoPdV>"
          "<TipologiaOggetto>DI</TipologiaOggetto></Intestazione><FileGroup/></SInCRO>").encode()
NON_VALIDO = f'<SInCRO xmlns="{NS}" versione="2.0" tipo="XX"><FileGroup/></SInCRO>'.encode()


def _togli_marcatore(cartella: Path) -> None:
    """Copia senza il marcatore «di prova»: simula uno schema acquistato (solo nei test)."""
    xsd = cartella / PRINCIPALE
    xsd.write_bytes(xsd.read_bytes().replace(modulo.MARCATORE_PROVA, b"SCHEMA SIMULATO"))


def _schemi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None, *,
            ufficiale: bool = False) -> Path:
    cartella = tmp_path / "schemi" / "prova"
    shutil.copytree(PROVA, cartella)
    if ufficiale:
        assert monkeypatch is not None
        _togli_marcatore(cartella)
        monkeypatch.setitem(schemi_ufficiali.IMPRONTE_UFFICIALI, "prova",
                            impronte_cartella(cartella))
    registra_schema(cartella, principale=PRINCIPALE, versione="prova", ufficiale=ufficiale,
                    fonte="schema dei test")
    return cartella.parent


def test_senza_schema_mai_conforme(tmp_path: Path) -> None:
    esito = valida_ipda(VALIDO, cartella=tmp_path / "vuota")
    assert esito.esito is EsitoXsd.SCHEMA_NON_DISPONIBILE and not esito.conforme
    assert "schema ufficiale non disponibile" in esito.messaggio
    assert esito.come_dict()["conforme"] is False


def test_cartella_del_repository_senza_schema_ufficiale() -> None:
    """Finché l'XSD UNI non è acquistato la cartella del repository non ne ha."""
    esito = valida_ipda(VALIDO, cartella=CARTELLA_SCHEMI)
    if esito.schema is not None and esito.schema["ufficiale"]:
        pytest.skip("schema ufficiale caricato: vale test_r11_conformita_xsd dell'aip-builder")
    assert esito.esito is EsitoXsd.SCHEMA_NON_DISPONIBILE and not esito.conforme


def test_schema_di_prova_mai_conforme(tmp_path: Path) -> None:
    cartella = _schemi(tmp_path)
    valido = valida_ipda(VALIDO, cartella=cartella)
    assert valido.esito is EsitoXsd.VALIDO_SCHEMA_DI_PROVA and not valido.conforme
    assert valido.schema["ufficiale"] is False and len(valido.schema["sha256_principale"]) == 64
    non_valido = valida_ipda(NON_VALIDO, cartella=cartella)
    assert non_valido.esito is EsitoXsd.NON_VALIDO_SCHEMA_DI_PROVA and non_valido.errori
    assert not non_valido.conforme


def test_conforme_solo_con_impronte_ufficiali_fissate_nel_codice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il meccanismo: con le impronte della copia fissate in IMPRONTE_UFFICIALI
    (simulato nel test) la stessa validazione dà CONFORME."""
    cartella = _schemi(tmp_path, monkeypatch, ufficiale=True)
    assert valida_ipda(VALIDO, cartella=cartella).conforme
    esito = valida_ipda(NON_VALIDO, cartella=cartella)
    assert esito.esito is EsitoXsd.NON_CONFORME and not esito.conforme


def test_schema_alterato_dopo_la_registrazione(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    cartella = _schemi(tmp_path, monkeypatch, ufficiale=True)
    xsd = cartella / "prova" / PRINCIPALE
    xsd.write_text(xsd.read_text(encoding="utf-8").replace('minOccurs="1"', 'minOccurs="0"'),
                   encoding="utf-8")
    esito = valida_ipda(VALIDO, cartella=cartella)
    assert esito.esito is EsitoXsd.SCHEMA_ALTERATO and not esito.conforme
    assert esito.errori == (f"{PRINCIPALE}: impronta diversa",)


def test_file_aggiunto_dopo_la_registrazione(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    cartella = _schemi(tmp_path, monkeypatch, ufficiale=True)
    (cartella / "prova" / "incluso.TXT").write_text("<x/>", encoding="utf-8")
    schema = carica_schema(cartella / "prova")
    assert alterazioni(schema) == ["incluso.TXT: aggiunto dopo la registrazione"]
    assert valida_ipda(VALIDO, cartella=cartella).esito is EsitoXsd.SCHEMA_ALTERATO


def test_registrazione_e_descrittore(tmp_path: Path) -> None:
    cartella = tmp_path / "s"
    shutil.copytree(PROVA, cartella)
    with pytest.raises(SchemaNonValido, match="non trovato"):
        registra_schema(cartella, principale="manca.xsd", versione="v", ufficiale=False,
                        fonte="prova")
    with pytest.raises(SchemaNonValido, match="versione e fonte"):
        registra_schema(cartella, principale=PRINCIPALE, versione="v", ufficiale=False,
                        fonte=" ")
    registra_schema(cartella, principale=PRINCIPALE, versione="v", ufficiale=False,
                    fonte="prova")
    dati = json.loads((cartella / "schema.json").read_text(encoding="utf-8"))
    assert dati["ufficiale"] is False and set(dati["impronte"]) == {PRINCIPALE}
    (cartella / "schema.json").write_text('{"formato": "altro"}', encoding="utf-8")
    with pytest.raises(SchemaNonValido, match="non valido"):
        carica_schema(cartella)


def test_entita_esterne_non_risolte(tmp_path: Path) -> None:
    cartella = _schemi(tmp_path)
    segreto = tmp_path / "segreto.txt"
    segreto.write_text("DATO-RISERVATO", encoding="utf-8")
    xml = (f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{segreto}">]>'
           f'<SInCRO xmlns="{NS}" versione="&e;" tipo="DI"><Intestazione>'
           "<IdentificativoAIP>a</IdentificativoAIP><IdentificativoPdV>p</IdentificativoPdV>"
           "<TipologiaOggetto>DI</TipologiaOggetto></Intestazione><F/></SInCRO>").encode()
    esito = valida_ipda(xml, cartella=cartella)
    assert "DATO-RISERVATO" not in json.dumps(esito.come_dict())


def test_validatore_intercambiabile(tmp_path: Path) -> None:
    cartella = _schemi(tmp_path)
    registra_validatore("sempre-errore-prova", lambda xml, schema, file: ("errore di prova",))
    try:
        esito = valida_ipda(VALIDO, cartella=cartella, validatore="sempre-errore-prova")
        assert esito.esito is EsitoXsd.NON_VALIDO_SCHEMA_DI_PROVA
        assert esito.validatore == "sempre-errore-prova"
        with pytest.raises(ValueError, match="già registrato"):
            registra_validatore("xsd-lxml", lambda xml, schema, file: ())
    finally:
        VALIDATORI.pop("sempre-errore-prova", None)


# ── Revisione T4: «ufficiale» solo con le impronte fissate nel codice ──────

def test_ufficiale_dichiarato_nel_json_non_basta(tmp_path: Path) -> None:
    """Prova della revisione: copia dello schema di prova con il flag forzato nel
    descrittore: resta non ufficiale, mai conforme."""
    cartella = _schemi(tmp_path)
    descrittore = cartella / "prova" / "schema.json"
    dati = json.loads(descrittore.read_text(encoding="utf-8"))
    dati.update({"ufficiale": True, "versione": "uni11386-2020"})
    descrittore.write_text(json.dumps(dati), encoding="utf-8")
    esito = valida_ipda(VALIDO, cartella=cartella)
    assert esito.esito is EsitoXsd.VALIDO_SCHEMA_DI_PROVA and not esito.conforme
    assert esito.schema["dichiarato_ufficiale"] is True and esito.schema["ufficiale"] is False


def test_registrazione_ufficiale_rifiutata_senza_impronte_nel_codice(tmp_path: Path) -> None:
    cartella = tmp_path / "s"
    shutil.copytree(PROVA, cartella)
    _togli_marcatore(cartella)
    assert schemi_ufficiali.IMPRONTE_UFFICIALI == {}  # la norma non è acquistata
    with pytest.raises(SchemaNonValido, match="nessuna impronta ufficiale"):
        registra_schema(cartella, principale=PRINCIPALE, versione="uni11386-2020",
                        ufficiale=True, fonte="tentativo")
    assert not (cartella / "schema.json").exists()


def test_schema_di_prova_mai_registrabile_come_ufficiale(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    cartella = tmp_path / "s"
    shutil.copytree(PROVA, cartella)
    # anche con le sue impronte (per errore) fissate nel codice
    monkeypatch.setitem(schemi_ufficiali.IMPRONTE_UFFICIALI, "x", impronte_cartella(cartella))
    with pytest.raises(SchemaNonValido, match="di prova"):
        registra_schema(cartella, principale=PRINCIPALE, versione="x", ufficiale=True,
                        fonte="tentativo")


def test_impronte_diverse_da_quelle_ufficiali_rifiutate(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    cartella = tmp_path / "s"
    shutil.copytree(PROVA, cartella)
    _togli_marcatore(cartella)
    monkeypatch.setitem(schemi_ufficiali.IMPRONTE_UFFICIALI, "x", {PRINCIPALE: "0" * 64})
    with pytest.raises(SchemaNonValido, match="non coincidono"):
        registra_schema(cartella, principale=PRINCIPALE, versione="x", ufficiale=True,
                        fonte="tentativo")


def test_schema_di_prova_fuori_dall_immagine() -> None:
    radice = Path(__file__).resolve().parents[3]
    regole = (radice / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "**/tests" in regole
    assert "tests" in PROVA.relative_to(radice).parts


# ── Revisione T3: solo i file registrati, letti una volta ─────────────────

_XS = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


def _schema_con(tmp_path: Path, principale: str, altri: dict[str, str]) -> Path:
    cartella = tmp_path / "schemi" / "s"
    cartella.mkdir(parents=True)
    (cartella / "main.xsd").write_text(principale, encoding="utf-8")
    for nome, testo in altri.items():
        percorso = tmp_path / nome if nome.startswith("../") else cartella / nome
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(testo, encoding="utf-8")
    registra_schema(cartella, principale="main.xsd", versione="s", ufficiale=False,
                    fonte="test")
    return cartella.parent


def test_include_registrato_con_estensione_qualsiasi(tmp_path: Path) -> None:
    tipi = f'<xs:schema {_XS}><xs:element name="Root"/></xs:schema>'
    cartella = _schema_con(tmp_path, f'<xs:schema {_XS}><xs:include schemaLocation="tipi.TXT"/>'
                           "</xs:schema>", {"tipi.TXT": tipi})
    assert valida_ipda(b"<Root/>", cartella=cartella).esito is EsitoXsd.VALIDO_SCHEMA_DI_PROVA
    (cartella / "s" / "tipi.TXT").write_text(tipi.replace("Root", "Altro"), encoding="utf-8")
    assert valida_ipda(b"<Root/>", cartella=cartella).esito is EsitoXsd.SCHEMA_ALTERATO


def test_include_fuori_dalla_cartella_non_utilizzabile(tmp_path: Path) -> None:
    """Prova della revisione: include ``../fuori/est.txt`` non registrato."""
    cartella = _schema_con(tmp_path, f'<xs:schema {_XS}><xs:include '
                           'schemaLocation="../../fuori/est.txt"/></xs:schema>',
                           {"fuori/est.txt": f'<xs:schema {_XS}><xs:element name="Root"/>'
                                             "</xs:schema>"})
    esito = valida_ipda(b"<Root/>", cartella=cartella)
    assert esito.esito is EsitoXsd.SCHEMA_NON_UTILIZZABILE and not esito.conforme
    assert "riferimento non registrato" in esito.errori[0] and "est.txt" in esito.errori[0]


def test_import_remoto_esito_gestito(tmp_path: Path) -> None:
    cartella = _schema_con(tmp_path, f'<xs:schema {_XS}><xs:import '
                           'namespace="http://www.w3.org/2000/09/xmldsig#" schemaLocation='
                           '"http://www.w3.org/TR/xmldsig-core/xmldsig-core-schema.xsd"/>'
                           '<xs:element name="Root"/></xs:schema>', {})
    esito = valida_ipda(b"<Root/>", cartella=cartella)
    assert esito.esito is EsitoXsd.SCHEMA_NON_UTILIZZABILE
    assert "http://www.w3.org/TR/xmldsig-core" in esito.errori[0]


def test_schema_compilato_dai_byte_controllati_non_dal_disco(tmp_path: Path) -> None:
    """Nessuna seconda lettura dal disco (TOCTOU): il validatore usa i byte passati."""
    cartella = _schema_con(tmp_path, f'<xs:schema {_XS}><xs:include schemaLocation="t.xsd"/>'
                           "</xs:schema>", {"t.xsd": f'<xs:schema {_XS}><xs:element '
                                                     'name="Root"/></xs:schema>'})
    schema = carica_schema(cartella / "s")
    file, problemi = modulo.leggi_file(schema)
    assert problemi == []
    # sul disco ora c'è altro: conta solo il contenuto letto e controllato
    (cartella / "s" / "t.xsd").write_text(f'<xs:schema {_XS}><xs:element name="Altro"/>'
                                          "</xs:schema>", encoding="utf-8")
    assert VALIDATORI["xsd-lxml"](b"<Root/>", schema, file) == ()
    assert VALIDATORI["xsd-lxml"](b"<Altro/>", schema, file) != ()
