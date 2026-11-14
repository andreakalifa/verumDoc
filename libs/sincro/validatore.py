# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/sincro/validatore.py — validazione dell'IPdA contro uno schema XSD
caricabile (R11: decisione D03, VD-N-01; VD-C-071 resta aperta).

Lo schema ufficiale UNI 11386:2020 NON è nel repository finché il committente
non lo acquista. Il conservatore lo carica così (README, «Schema UNI 11386»):

  1. copia i file in ``libs/sincro/schemas/<versione>/`` (es.
     ``libs/sincro/schemas/uni11386-2020/``): finiscono nell'immagine
     dell'aip-builder perché il Dockerfile copia ``libs/``;
  2. registra lo schema con
     ``python tools/sincro/verifica_ipda.py registra --cartella … --principale …
     --versione uni11386-2020 --ufficiale --fonte "…"``: il comando scrive
     ``schema.json`` con l'impronta SHA-256 di OGNI file della cartella
     (qualsiasi estensione).

Revisione R11:
  - T3: i byte di ogni file registrato si leggono UNA volta; l'impronta si
    controlla su quei byte e lo schema si compila da quei byte. Gli
    ``xs:include``/``xs:import`` passano da un resolver che serve SOLO i file
    registrati dentro la cartella; ogni altro riferimento (fuori dalla cartella,
    non registrato, remoto ``http``/``https``) è rifiutato e l'esito è
    ``SCHEMA_NON_UTILIZZABILE`` con il riferimento, mai un'eccezione. Nessuna
    finestra fra controllo e uso (TOCTOU).
  - T4: «ufficiale» NON lo dice il descrittore: lo è solo uno schema i cui file
    coincidono con le impronte fissate nel codice in ``schemi_ufficiali.py``
    (vuoto finché la norma non è acquistata, decisione VD-P-111).
    ``registra_schema(ufficiale=True)`` rifiuta uno schema diverso e qualunque
    file marcato «SCHEMA DI PROVA NON UFFICIALE».

Esiti (``EsitoXsd``): ``CONFORME`` SOLO con uno schema ufficiale integro e un IPdA
valido. Senza schema: ``SCHEMA_NON_DISPONIBILE`` («schema ufficiale non
disponibile»), mai «conforme». Uno schema non ufficiale dà
``VALIDO_SCHEMA_DI_PROVA`` o ``NON_VALIDO_SCHEMA_DI_PROVA``. Un file cambiato,
mancante o aggiunto dopo la registrazione: ``SCHEMA_ALTERATO``.

Validatori intercambiabili (pluggable): ``VALIDATORI`` associa un nome a una
funzione ``(xml, schema, file) -> errori``; il predefinito è ``xsd-lxml``.

Nessun elemento né namespace UNI è scritto qui: lo schema è un dato caricato.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from libs.sincro.schemi_ufficiali import IMPRONTE_UFFICIALI

#: Cartella degli schemi caricati dal conservatore (una sottocartella per versione).
CARTELLA_SCHEMI = Path(__file__).parent / "schemas"
FILE_DESCRITTORE = "schema.json"
FORMATO_DESCRITTORE = "verumdoc-schema-xsd-1"
#: Marcatore degli schemi di prova: mai registrabili come ufficiali.
MARCATORE_PROVA = b"SCHEMA DI PROVA NON UFFICIALE"
#: Errori di validazione riportati al più.
ERRORI_MAX = 20


class EsitoXsd(StrEnum):
    CONFORME = "CONFORME"
    NON_CONFORME = "NON_CONFORME"
    VALIDO_SCHEMA_DI_PROVA = "VALIDO_SCHEMA_DI_PROVA"
    NON_VALIDO_SCHEMA_DI_PROVA = "NON_VALIDO_SCHEMA_DI_PROVA"
    SCHEMA_NON_DISPONIBILE = "SCHEMA_NON_DISPONIBILE"
    SCHEMA_ALTERATO = "SCHEMA_ALTERATO"
    SCHEMA_NON_UTILIZZABILE = "SCHEMA_NON_UTILIZZABILE"


MESSAGGI = {
    EsitoXsd.CONFORME: "IPdA valido rispetto allo schema ufficiale registrato",
    EsitoXsd.NON_CONFORME: "IPdA NON valido rispetto allo schema ufficiale registrato",
    EsitoXsd.VALIDO_SCHEMA_DI_PROVA: (
        "IPdA valido rispetto a uno schema NON ufficiale: nessuna conformità dichiarata"),
    EsitoXsd.NON_VALIDO_SCHEMA_DI_PROVA: (
        "IPdA non valido rispetto a uno schema NON ufficiale"),
    EsitoXsd.SCHEMA_NON_DISPONIBILE: (
        "schema ufficiale non disponibile: l'IPdA non è verificato e non si dichiara conforme"),
    EsitoXsd.SCHEMA_ALTERATO: (
        "schema alterato: i file non corrispondono alle impronte registrate"),
    EsitoXsd.SCHEMA_NON_UTILIZZABILE: (
        "schema non utilizzabile: riferimenti non registrati o schema non compilabile; "
        "l'IPdA non è verificato"),
}


class SchemaNonValido(ValueError):
    """Cartella o descrittore dello schema non utilizzabili, o registrazione rifiutata."""


def impronte_ufficiali_di(versione: str) -> dict[str, str] | None:
    """Impronte attese fissate nel codice (None = versione senza schema ufficiale noto)."""
    attese = IMPRONTE_UFFICIALI.get(versione)
    return dict(attese) if attese else None


@dataclass(frozen=True)
class SchemaXsd:
    """Schema registrato: versione, file principale, impronte di ogni file."""

    versione: str
    cartella: Path
    principale: str
    impronte: dict[str, str]
    fonte: str
    registrato_il: str
    #: flag del descrittore: solo informativo (T4)
    dichiarato_ufficiale: bool = False

    @property
    def ufficiale(self) -> bool:
        """Vero SOLO se le impronte registrate sono quelle fissate nel codice."""
        return impronte_ufficiali_di(self.versione) == self.impronte

    def riferimento(self) -> dict[str, Any]:
        return {"versione": self.versione, "principale": self.principale,
                "sha256_principale": self.impronte[self.principale],
                "ufficiale": self.ufficiale, "dichiarato_ufficiale": self.dichiarato_ufficiale,
                "fonte": self.fonte, "registrato_il": self.registrato_il}


@dataclass(frozen=True)
class EsitoValidazioneXsd:
    esito: EsitoXsd
    schema: dict[str, Any] | None = None
    errori: tuple[str, ...] = field(default_factory=tuple)
    validatore: str | None = None

    @property
    def conforme(self) -> bool:
        """Vero SOLO per uno schema ufficiale integro e un IPdA valido."""
        return self.esito is EsitoXsd.CONFORME and bool(self.schema and self.schema["ufficiale"])

    @property
    def messaggio(self) -> str:
        return MESSAGGI[self.esito]

    def come_dict(self) -> dict[str, Any]:
        return {"esito": self.esito.value, "conforme": self.conforme,
                "messaggio": self.messaggio, "schema": self.schema,
                "errori": list(self.errori), "validatore": self.validatore}


def _sha256(dati: bytes) -> str:
    return hashlib.sha256(dati).hexdigest()


def _file_cartella(cartella: Path) -> dict[str, Path]:
    """Tutti i file della cartella (qualsiasi estensione), tranne il descrittore."""
    return {p.relative_to(cartella).as_posix(): p for p in sorted(cartella.rglob("*"))
            if p.is_file() and p.name != FILE_DESCRITTORE and "__pycache__" not in p.parts}


def impronte_cartella(cartella: Path) -> dict[str, str]:
    """Impronta SHA-256 di ogni file della cartella (per fissarle nel codice)."""
    return {nome: _sha256(p.read_bytes()) for nome, p in _file_cartella(cartella).items()}


def registra_schema(cartella: Path, *, principale: str, versione: str, ufficiale: bool,
                    fonte: str, adesso: datetime | None = None) -> SchemaXsd:
    """Scrive ``schema.json`` con l'impronta di ogni file della cartella (azione del
    conservatore). ``ufficiale=True`` è accettato SOLO se le impronte coincidono con
    quelle fissate nel codice (``schemi_ufficiali.py``) e nessun file è marcato come
    schema di prova."""
    if not versione.strip() or not fonte.strip():
        raise SchemaNonValido("indicare versione e fonte dello schema")
    file = _file_cartella(cartella)
    if principale not in file:
        raise SchemaNonValido(f"file principale {principale!r} non trovato in {cartella}")
    contenuti = {nome: p.read_bytes() for nome, p in file.items()}
    impronte = {nome: _sha256(b) for nome, b in contenuti.items()}
    if ufficiale:
        if any(MARCATORE_PROVA in b for b in contenuti.values()):
            raise SchemaNonValido("uno schema di prova NON ufficiale non si registra come "
                                  "ufficiale")
        attese = impronte_ufficiali_di(versione)
        if attese is None:
            raise SchemaNonValido(
                f"nessuna impronta ufficiale fissata nel codice per {versione!r} "
                "(libs/sincro/schemi_ufficiali.py, decisione VD-N dopo l'acquisto)")
        if attese != impronte:
            raise SchemaNonValido("i file non coincidono con le impronte ufficiali fissate "
                                  "nel codice per questa versione")
    quando = (adesso or datetime.now(UTC)).isoformat()
    descrittore = {"formato": FORMATO_DESCRITTORE, "versione": versione,
                   "principale": principale, "ufficiale": bool(ufficiale), "fonte": fonte,
                   "registrato_il": quando, "impronte": impronte}
    (cartella / FILE_DESCRITTORE).write_text(
        json.dumps(descrittore, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return SchemaXsd(versione, cartella, principale, impronte, fonte, quando, bool(ufficiale))


def carica_schema(cartella: Path) -> SchemaXsd:
    """Schema registrato nella cartella (``SchemaNonValido`` se il descrittore manca o
    non è leggibile). L'integrità si controlla sui byte letti (``leggi_file``)."""
    try:
        dati = json.loads((cartella / FILE_DESCRITTORE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaNonValido(f"{cartella}: descrittore {FILE_DESCRITTORE} illeggibile") from exc
    if not isinstance(dati, dict) or dati.get("formato") != FORMATO_DESCRITTORE \
            or not isinstance(dati.get("impronte"), dict) \
            or dati.get("principale") not in dati["impronte"]:
        raise SchemaNonValido(f"{cartella}: descrittore {FILE_DESCRITTORE} non valido")
    return SchemaXsd(str(dati["versione"]), cartella, str(dati["principale"]),
                     {str(k): str(v) for k, v in dati["impronte"].items()},
                     str(dati.get("fonte") or ""), str(dati.get("registrato_il") or ""),
                     bool(dati.get("ufficiale")))


def leggi_file(schema: SchemaXsd) -> tuple[dict[str, bytes], list[str]]:
    """Byte dei file registrati, letti UNA volta, e alterazioni (cambiati, mancanti,
    aggiunti dopo la registrazione) controllate SU QUEI BYTE."""
    presenti = _file_cartella(schema.cartella)
    contenuti: dict[str, bytes] = {}
    problemi = [f"{n}: mancante" for n in sorted(set(schema.impronte) - set(presenti))]
    problemi += [f"{n}: aggiunto dopo la registrazione"
                 for n in sorted(set(presenti) - set(schema.impronte))]
    for nome in sorted(set(schema.impronte) & set(presenti)):
        dati = presenti[nome].read_bytes()
        if _sha256(dati) != schema.impronte[nome]:
            problemi.append(f"{nome}: impronta diversa")
        contenuti[nome] = dati
    return contenuti, problemi


def alterazioni(schema: SchemaXsd) -> list[str]:
    """File cambiati, mancanti o aggiunti dopo la registrazione."""
    return leggi_file(schema)[1]


def schemi_registrati(cartella: Path = CARTELLA_SCHEMI) -> dict[str, SchemaXsd]:
    """Schemi registrati, per versione (sottocartelle con ``schema.json``)."""
    if not cartella.is_dir():
        return {}
    schemi = {}
    for sotto in sorted(p for p in cartella.iterdir() if p.is_dir()):
        if (sotto / FILE_DESCRITTORE).is_file():
            schema = carica_schema(sotto)
            schemi[schema.versione] = schema
    return schemi


class SchemaNonUtilizzabile(ValueError):
    """Lo schema non si compila con i soli file registrati."""


def _nome_registrato(url: str, radice: Path, file: Mapping[str, bytes]) -> str | None:
    """Nome relativo del file registrato a cui punta ``url`` (None = rifiutato)."""
    analisi = urlparse(url)
    if analisi.scheme not in ("", "file"):
        return None                              # remoto (http, https, ftp…): mai
    percorso = Path(unquote(analisi.path if analisi.scheme == "file" else url))
    try:
        relativo = percorso.resolve().relative_to(radice)
    except (ValueError, OSError):
        return None                              # fuori dalla cartella registrata
    nome = relativo.as_posix()
    return nome if nome in file else None


def _valida_lxml(xml: bytes, schema: SchemaXsd, file: Mapping[str, bytes]) -> tuple[str, ...]:
    """Errori di validazione con lxml (vuoto = valido). Lo schema si compila dai byte
    già controllati; include e import solo fra i file registrati; nessuna rete,
    nessuna entità esterna. ``SchemaNonUtilizzabile`` se non si compila."""
    from lxml import etree

    radice = schema.cartella.resolve()
    rifiutati: list[str] = []

    class SoloRegistrati(etree.Resolver):  # type: ignore[misc]
        def resolve(self, url: str, pubid: str | None, context: Any) -> Any:
            nome = _nome_registrato(url, radice, file)
            if nome is None:
                rifiutati.append(url)
                # documento vuoto al posto del riferimento: la compilazione fallisce
                return self.resolve_string("<rifiutato/>", context)
            return self.resolve_string(file[nome], context,
                                       base_url=str(radice / nome))

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             huge_tree=False)
    parser.resolvers.add(SoloRegistrati())
    try:
        documento_xsd = etree.fromstring(file[schema.principale], parser,
                                         base_url=str(radice / schema.principale))
        xsd = etree.XMLSchema(documento_xsd)
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError) as exc:
        dettaglio = [f"riferimento non registrato: {u}" for u in rifiutati] or [str(exc)]
        raise SchemaNonUtilizzabile("; ".join(dettaglio[:ERRORI_MAX])) from None
    if rifiutati:  # pragma: no cover — un riferimento rifiutato fa fallire la compilazione
        raise SchemaNonUtilizzabile(f"riferimento non registrato: {rifiutati[0]}")
    lettore = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                              huge_tree=False)
    try:
        documento = etree.fromstring(xml, lettore)
    except etree.XMLSyntaxError as exc:
        return (f"XML non leggibile: {exc}",)
    if xsd.validate(documento):
        return ()
    return tuple(f"riga {e.line}: {e.message}" for e in list(xsd.error_log)[:ERRORI_MAX])


#: Validatori intercambiabili: nome → funzione (``registra_validatore`` per aggiungerne).
Validatore = Callable[[bytes, SchemaXsd, Mapping[str, bytes]], tuple[str, ...]]
VALIDATORI: dict[str, Validatore] = {"xsd-lxml": _valida_lxml}
VALIDATORE_PREDEFINITO = "xsd-lxml"


def registra_validatore(nome: str, funzione: Validatore) -> None:
    if nome in VALIDATORI:
        raise ValueError(f"validatore {nome!r} già registrato")
    VALIDATORI[nome] = funzione


def valida_ipda(xml: bytes, *, versione: str | None = None,
                cartella: Path = CARTELLA_SCHEMI,
                validatore: str = VALIDATORE_PREDEFINITO) -> EsitoValidazioneXsd:
    """Valida l'IPdA con lo schema della ``versione`` (o, senza versione, con lo schema
    ufficiale registrato più recente per nome; senza ufficiali, con un altro schema
    registrato, mai conforme). Senza schemi: ``SCHEMA_NON_DISPONIBILE``."""
    try:
        schemi = schemi_registrati(cartella)
    except SchemaNonValido as exc:
        return EsitoValidazioneXsd(EsitoXsd.SCHEMA_NON_UTILIZZABILE, None, (str(exc),))
    if versione is not None:
        schema = schemi.get(versione)
    else:
        ufficiali = [s for v, s in sorted(schemi.items()) if s.ufficiale]
        schema = ufficiali[-1] if ufficiali else next(iter(schemi.values()), None)
    if schema is None:
        return EsitoValidazioneXsd(EsitoXsd.SCHEMA_NON_DISPONIBILE)
    rif = schema.riferimento()
    file, problemi = leggi_file(schema)
    if problemi:
        return EsitoValidazioneXsd(EsitoXsd.SCHEMA_ALTERATO, rif, tuple(problemi))
    try:
        errori = VALIDATORI[validatore](xml, schema, file)
    except SchemaNonUtilizzabile as exc:
        return EsitoValidazioneXsd(EsitoXsd.SCHEMA_NON_UTILIZZABILE, rif, (str(exc),),
                                   validatore)
    if schema.ufficiale:
        esito = EsitoXsd.NON_CONFORME if errori else EsitoXsd.CONFORME
    else:
        esito = EsitoXsd.NON_VALIDO_SCHEMA_DI_PROVA if errori else EsitoXsd.VALIDO_SCHEMA_DI_PROVA
    return EsitoValidazioneXsd(esito, rif, errori, validatore)


__all__ = [
    "CARTELLA_SCHEMI", "EsitoValidazioneXsd", "EsitoXsd", "SchemaNonUtilizzabile",
    "SchemaNonValido", "SchemaXsd", "VALIDATORI", "alterazioni", "carica_schema",
    "impronte_cartella", "impronte_ufficiali_di", "leggi_file", "registra_schema",
    "registra_validatore", "schemi_registrati", "valida_ipda",
]
