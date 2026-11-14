# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/indice_pdd.py — lettura sicura dell'indice di un PdD ricevuto da un
altro conservatore (riversamento in ingresso).

Riferimenti: contratto Q2 di R05; UNI 11386:2020 (SInCRO); REGOLE_SVILUPPO
§10.3 (impronte calcolate sui byte, mai fidate), §19.5 (file caricati).

Parser: ``defusedxml`` con DTD, entità e riferimenti esterni vietati (come le
ricevute PEC di ``libs.shared.pec``), con dimensione massima dell'indice decisa
dal chiamante.

Strutture riconosciute (nessun valore indovinato: un file senza nome o senza
impronta è un rilievo, mai un default):
  - IPdA di VerumDoc (radice ``SInCRO``, ``FileGroup/File`` con ``NomeFile`` e
    ``Hash/Impronta`` + ``Hash/Algoritmo``, impronta esadecimale);
  - indice in stile UNI 11386 (radice ``PIndex`` o altra, ``File`` con ``Path`` o
    ``ID`` e ``Hash`` con attributo ``function`` e impronta esadecimale o
    base64). Lo schema XSD ufficiale non è nel repository (VD-N-01): la lettura
    di questa forma è TOLLERANTE e non vale come validazione dello schema; il
    rapporto di verifica lo dichiara (``schema_validato: false``).
Gli elementi si cercano per nome locale (qualsiasi namespace).
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

ALGORITMI = {"SHA-256": 64, "SHA-512": 128, "SHA256": 64, "SHA512": 128}
_NOME_ALGORITMO = {"SHA256": "SHA-256", "SHA512": "SHA-512"}
_HEX = re.compile(r"^[0-9a-fA-F]+$")


class IndiceNonValido(ValueError):
    """Indice illeggibile o non sicuro (DTD, entità) o senza file verificabili."""


@dataclass(frozen=True)
class FileIndice:
    nome: str
    algoritmo: str
    impronta: str  # esadecimale minuscola
    tipo: str | None = None
    formato: str | None = None


@dataclass
class IndicePdd:
    radice: str
    identificativo: str | None
    conservatore: str | None
    file: list[FileIndice] = field(default_factory=list)
    metadati: dict[str, Any] = field(default_factory=dict)
    rilievi: list[str] = field(default_factory=list)


def _locale(el: Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _figlio(el: Element, nome: str) -> Element | None:
    return next((c for c in el if _locale(c) == nome), None)


def _testo(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    testo = el.text.strip()
    return testo or None


def _impronta_hex(valore: str, algoritmo: str) -> str:
    """Impronta in esadecimale da esadecimale o base64; errore se la lunghezza
    non è quella dell'algoritmo."""
    attesa = ALGORITMI[algoritmo]
    testo = valore.strip()
    if _HEX.fullmatch(testo) and len(testo) == attesa:
        return testo.lower()
    try:
        grezzo = base64.b64decode(testo, validate=True)
    except (binascii.Error, ValueError):
        raise IndiceNonValido(f"impronta non esadecimale né base64 per {algoritmo}") from None
    if len(grezzo) * 2 != attesa:
        raise IndiceNonValido(f"impronta di lunghezza errata per {algoritmo}")
    return grezzo.hex()


def _algoritmo(valore: str | None) -> str | None:
    if not valore:
        return None
    testo = valore.strip().upper().replace("_", "-")
    testo = _NOME_ALGORITMO.get(testo, testo)
    return testo if testo in ALGORITMI else None


def _file_da_elemento(el: Element, rilievi: list[str]) -> FileIndice | None:
    nome = (_testo(_figlio(el, "NomeFile")) or _testo(_figlio(el, "Path"))
            or _testo(_figlio(el, "ID")))
    if not nome:
        rilievi.append("File senza nome nell'indice")
        return None
    migliore: tuple[str, str] | None = None
    for h in (c for c in el if _locale(c) == "Hash"):
        algoritmo = _algoritmo(h.get("function") or _testo(_figlio(h, "Algoritmo")))
        valore = _testo(_figlio(h, "Impronta")) or _testo(h)
        if algoritmo is None or not valore:
            continue
        try:
            impronta = _impronta_hex(valore, algoritmo)
        except IndiceNonValido as exc:
            rilievi.append(f"{nome}: {exc}")
            continue
        if migliore is None or algoritmo == "SHA-256":
            migliore = (algoritmo, impronta)
    if migliore is None:
        rilievi.append(f"{nome}: nessuna impronta SHA-256 o SHA-512 leggibile")
        return None
    return FileIndice(nome=nome, algoritmo=migliore[0], impronta=migliore[1],
                      tipo=_testo(_figlio(el, "TipoFile")),
                      formato=_testo(_figlio(el, "FormatoMIME")))


_METADATI_VERUMDOC = {
    "IdentificativoDocumento": "identificativo_documento",
    "ModalitaFormazione": "modalita_formazione",
    "TipologiaDocumentale": "tipologia_documentale", "DataDocumento": "data_documento",
    "Oggetto": "oggetto", "Lingua": "lingua", "Classificazione": "classificazione",
}


def leggi_indice_pdd(xml: bytes, *, dimensione_massima: int) -> IndicePdd:
    """Indice del PdD ricevuto, con i file e le impronte dichiarate. Solleva
    ``IndiceNonValido`` se l'XML non è sicuro o ben formato, o non elenca file."""
    if len(xml) > dimensione_massima:
        raise IndiceNonValido(f"indice oltre {dimensione_massima} byte")
    try:
        radice = fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise IndiceNonValido("DTD ed entità non sono ammessi nell'indice") from None
    except ParseError as exc:
        raise IndiceNonValido(f"XML non ben formato: {exc}"[:300]) from None
    rilievi: list[str] = []
    intestazione = _figlio(radice, "Intestazione")
    identificativo = None
    conservatore = None
    if intestazione is not None:
        identificativo = _testo(_figlio(intestazione, "IdentificativoAIP"))
        conservatore = _testo(_figlio(intestazione, "ConservatoreAIP"))
    if identificativo is None:
        identificativo = radice.get("ID") or _testo(_figlio(radice, "ID"))
    file: list[FileIndice] = []
    for el in radice.iter():
        if _locale(el) == "File":
            trovato = _file_da_elemento(el, rilievi)
            if trovato is not None:
                file.append(trovato)
    if not file:
        raise IndiceNonValido("l'indice non elenca alcun file con impronta verificabile")
    nomi = [f.nome for f in file]
    doppi = sorted({n for n in nomi if nomi.count(n) > 1})
    if doppi:
        raise IndiceNonValido(f"file elencati più volte: {', '.join(doppi[:5])}")
    metadati: dict[str, Any] = {}
    for el in radice.iter():
        if _locale(el) == "MetadatiDocumento":
            for figlio in el:
                chiave = _METADATI_VERUMDOC.get(_locale(figlio))
                if chiave and _testo(figlio):
                    metadati[chiave] = _testo(figlio)
            produttore = _figlio(el, "SoggettoProduttore")
            if produttore is not None:
                for nome, chiave in (("Identificativo", "soggetto_produttore_id"),
                                     ("Denominazione", "soggetto_produttore_nome")):
                    valore = _testo(_figlio(produttore, nome))
                    if valore:
                        metadati[chiave] = valore
            break
    return IndicePdd(radice=_locale(radice), identificativo=identificativo,
                     conservatore=conservatore, file=file, metadati=metadati, rilievi=rilievi)


def nome_sicuro(nome: str) -> bool:
    """Nome di un membro dello ZIP accettabile (niente percorsi assoluti o «..»,
    niente caratteri di controllo): lo ZIP è un dato non fidato."""
    if not nome or nome.startswith("/") or "\\" in nome:
        return False
    parti = nome.split("/")
    if any(p in ("", ".", "..") for p in parti):
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in nome)


__all__ = ["FileIndice", "IndiceNonValido", "IndicePdd", "leggi_indice_pdd", "nome_sicuro"]
