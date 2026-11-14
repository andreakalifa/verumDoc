# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/tipologie.py — profili di metadati per TIPOLOGIA dell'Allegato 5
come dati versionati (R05 Q1; REGOLE_SVILUPPO §8.4, §8.8, §8.12; VD-C-088).

I profili predefiniti stanno in ``dati/profili/<codice>.v<versione>.json``: sono
DATI, non classi Python (§8.8). Un file può dichiarare ``estende`` (un'altra
tipologia): i campi della base si ereditano e quelli con lo stesso nome si
sostituiscono. Le versioni successive create dal conservatore stanno nel
database (tabella ``profili_tipologia``, migrazione 20261023_036) e si
uniscono a queste con ``versioni_tipologia``: vale la versione più alta fra
quelle valide nel giorno (``seleziona_profilo``).

Un Allegato 5 aggiornato = un file ``<codice>.v2.json`` (o una versione creata
dalla console) con il suo ``valido_dal``: le versioni precedenti restano per i
PdV già versati (fotografia §8.3).

R11 (D07, VD-C-136): formato ``verumdoc-profilo-tipologia-2`` per le versioni
riscontrate sul testo ufficiale dell'Allegato 5:
  - ``estende_versione``: la versione della base da cui si eredita è FISSATA
    nel file. I file del formato 1 non la indicano ed ereditano la versione 1
    della base (quella che esisteva quando sono stati pubblicati): così una v2
    del documento informatico non cambia la v1 del documento amministrativo;
  - ``adozione`` ``esplicita``: la versione non sostituisce da sola quella in
    uso; la classe la sceglie con ``PROFILO_TIPOLOGIA_VERSIONE`` (bozza con
    anteprima, anche programmata, §8.4 e §13);
  - ``origine`` del profilo: ``allegato5`` (disciplinato dal testo) o
    ``conservatore`` (profilo proprio del conservatore: fattura, registro di
    protocollo, contratto non hanno un profilo nell'Allegato 5);
  - ``impronte.json``: impronta di OGNI versione pubblicata (campi composti
    con l'eredità): un test fallisce se una versione già pubblicata cambia.

R16 (VD-C-158): ``regole_incrociate`` del file (condizioni fra campi diversi,
``profili.RegolaIncrociata``), ereditate dalla base alla versione fissata.

Pacchetto puro: lettura dei file del pacchetto all'import, nessun orologio.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from libs.verumdoc_rules.profili import (
    ADOZIONE_AUTOMATICA,
    ADOZIONI,
    CampoProfilo,
    ProfiloMetadati,
    ProfiloNonValido,
    RegolaIncrociata,
    seleziona_profilo,
)

#: Cartella dei profili predefiniti (dati versionati del pacchetto).
CARTELLA_PROFILI = Path(__file__).parent / "dati" / "profili"
FORMATO_FILE = "verumdoc-profilo-tipologia-1"
#: R11: formato con eredità a versione fissata, adozione e origine.
FORMATO_FILE_2 = "verumdoc-profilo-tipologia-2"
FORMATI_FILE = (FORMATO_FILE, FORMATO_FILE_2)
#: Impronte delle versioni pubblicate (append-only, controllate dai test).
FILE_IMPRONTE = CARTELLA_PROFILI / "impronte.json"
ORIGINI_PROFILO = ("allegato5", "conservatore")


@dataclass(frozen=True)
class Tipologia:
    """Una tipologia con il nome da mostrare e la base da cui eredita."""

    codice: str
    nome: str
    estende: str | None
    nota: str
    #: R11: ``allegato5`` o ``conservatore`` (profilo proprio, non dell'Allegato 5)
    origine: str = "allegato5"


def _leggi_file(percorso: Path) -> dict[str, Any]:
    dati = json.loads(percorso.read_text(encoding="utf-8"))
    if not isinstance(dati, dict) or dati.get("formato") not in FORMATI_FILE:
        raise ProfiloNonValido(f"{percorso.name}: formato del file non riconosciuto")
    if dati["formato"] == FORMATO_FILE_2:
        if dati.get("estende") and not isinstance(dati.get("estende_versione"), int):
            raise ProfiloNonValido(f"{percorso.name}: «estende» senza «estende_versione»")
        if dati.get("adozione") not in ADOZIONI:
            raise ProfiloNonValido(f"{percorso.name}: «adozione» mancante o non ammessa")
        if dati.get("origine") not in ORIGINI_PROFILO:
            raise ProfiloNonValido(f"{percorso.name}: «origine» mancante o non ammessa")
    return dati


def _versione_base(dati: Mapping[str, Any]) -> int:
    """Versione della base ereditata: fissata nel file (formato 2); i file del
    formato 1 ereditano la versione 1 (l'unica esistente quando sono nati)."""
    return int(dati.get("estende_versione") or 1)


def _campi_con_base(codice: str, versione: int, grezzi: Mapping[str, dict[str, Any]],
                    visti: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Campi del profilo con quelli ereditati (``estende``), base prima."""
    if codice in visti:
        raise ProfiloNonValido(f"ereditarietà circolare fra profili: {' → '.join(visti)}")
    dati = grezzi[f"{codice}.v{versione}"]
    base = dati.get("estende")
    campi: dict[str, dict[str, Any]] = {}
    if base:
        versione_base = _versione_base(dati)
        if f"{base}.v{versione_base}" not in grezzi:
            raise ProfiloNonValido(
                f"{codice}: la tipologia base {base!r} v{versione_base} non esiste")
        # R11: la versione della base è fissata (mai «la più alta»: una versione
        # nuova della base non deve cambiare un profilo già pubblicato)
        for campo in _campi_con_base(base, versione_base, grezzi, (*visti, codice)):
            campi[campo["nome"]] = campo
    for campo in dati["campi"]:
        campi[campo["nome"]] = campo
    return list(campi.values())


def _regole_con_base(codice: str, versione: int, grezzi: Mapping[str, dict[str, Any]]
                     ) -> tuple[RegolaIncrociata, ...]:
    """R16 (VD-C-158): regole incrociate del file con quelle della base (stessa versione
    fissata dei campi); una regola con lo stesso codice sostituisce quella della base."""
    dati = grezzi[f"{codice}.v{versione}"]
    regole: dict[str, RegolaIncrociata] = {}
    if dati.get("estende"):
        for r in _regole_con_base(str(dati["estende"]), _versione_base(dati), grezzi):
            regole[r.codice] = r
    for grezza in dati.get("regole_incrociate") or ():
        r = RegolaIncrociata.da_dict(grezza)
        regole[r.codice] = r
    return tuple(regole.values())


@cache
def _caricati() -> tuple[dict[str, Tipologia], dict[str, tuple[ProfiloMetadati, ...]]]:
    grezzi = {p.name.removesuffix(".json"): _leggi_file(p)
              for p in sorted(CARTELLA_PROFILI.glob("*.v*.json"))}
    tipologie: dict[str, Tipologia] = {}
    profili: dict[str, list[ProfiloMetadati]] = {}
    # nome, nota e origine mostrati: quelli della versione più alta
    for chiave, dati in sorted(grezzi.items(), key=lambda kv: int(kv[1]["versione"])):
        codice, versione = dati["codice"], int(dati["versione"])
        if chiave != f"{codice}.v{versione}":
            raise ProfiloNonValido(f"{chiave}.json: nome del file diverso da codice e versione")
        tipologie[codice] = Tipologia(codice, str(dati["nome"]), dati.get("estende"),
                                      str(dati.get("nota") or ""),
                                      str(dati.get("origine") or "allegato5"))
        profili.setdefault(codice, []).append(ProfiloMetadati(
            codice=codice, versione=versione,
            valido_dal=date.fromisoformat(dati["valido_dal"]),
            valido_al=date.fromisoformat(dati["valido_al"]) if dati.get("valido_al") else None,
            campi=tuple(CampoProfilo.da_dict(c)
                        for c in _campi_con_base(codice, versione, grezzi)),
            id=f"tipologia:{codice}:v{versione}", tipologia=codice,
            norm_ref=str(dati.get("norm_ref") or ""),
            adozione=str(dati.get("adozione") or ADOZIONE_AUTOMATICA),
            regole_incrociate=_regole_con_base(codice, versione, grezzi),
        ))
    return tipologie, {k: tuple(sorted(v, key=lambda p: p.versione)) for k, v in profili.items()}


def tipologie() -> dict[str, Tipologia]:
    """Tipologie con un profilo predefinito, per codice."""
    return dict(_caricati()[0])


def codici_tipologie() -> tuple[str, ...]:
    return tuple(sorted(_caricati()[0]))


def profili_predefiniti(codice: str) -> tuple[ProfiloMetadati, ...]:
    """Versioni predefinite (file del pacchetto) di una tipologia; vuoto se sconosciuta."""
    return _caricati()[1].get(codice, ())


def versioni_tipologia(codice: str,
                       dal_database: Iterable[ProfiloMetadati] = ()) -> list[ProfiloMetadati]:
    """Tutte le versioni (predefinite e create dal conservatore), per versione."""
    tutte = {p.versione: p for p in profili_predefiniti(codice)}
    for p in dal_database:
        if p.versione in tutte:
            raise ProfiloNonValido(
                f"tipologia {codice}: la versione {p.versione} esiste già fra i predefiniti")
        tutte[p.versione] = p
    return [tutte[v] for v in sorted(tutte)]


def profilo_tipologia(codice: str | None, giorno: date,
                      dal_database: Iterable[ProfiloMetadati] = (),
                      versione: int = 0) -> ProfiloMetadati | None:
    """Versione della tipologia valida nel giorno; None se nessuna tipologia scelta.

    ``versione`` (R11, impostazione ``PROFILO_TIPOLOGIA_VERSIONE`` della classe):
    0 = la più alta valida nel giorno fra quelle ad adozione AUTOMATICA (il
    comportamento di R05: una versione ad adozione esplicita non entra da sola);
    N = esattamente la versione N, che deve essere valida nel giorno.

    Una tipologia scelta ma sconosciuta o senza versione valida è un errore di
    configurazione (``ProfiloNonValido``): non si applica un profilo indovinato."""
    if not codice:
        return None
    versioni = versioni_tipologia(codice, dal_database)
    if not versioni:
        raise ProfiloNonValido(f"tipologia di profilo sconosciuta: {codice}")
    if versione:
        scelta = next((p for p in versioni if p.versione == versione), None)
        if scelta is None:
            raise ProfiloNonValido(f"tipologia {codice}: la versione {versione} non esiste")
        if not scelta.valido_il(giorno):
            raise ProfiloNonValido(
                f"tipologia {codice}: la versione {versione} non è valida il {giorno}")
        return scelta
    scelto = seleziona_profilo([p for p in versioni if p.adozione == ADOZIONE_AUTOMATICA],
                               giorno)
    if scelto is None:
        raise ProfiloNonValido(f"tipologia {codice}: nessuna versione valida il {giorno}")
    return scelto


def impronta_profilo(profilo: ProfiloMetadati) -> str:
    """Impronta SHA-256 della forma canonica di una versione (campi composti)."""
    forma = {k: v for k, v in profilo.come_dict().items() if k not in ("id", "composto_da")}
    testo = json.dumps(forma, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


def impronte_pubblicate() -> dict[str, str]:
    """Impronte registrate in ``impronte.json`` (chiave ``<codice>.v<versione>``)."""
    dati = json.loads(FILE_IMPRONTE.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in dati["versioni"].items()}


def differenze_impronte() -> list[str]:
    """Versioni predefinite la cui impronta non è quella pubblicata (o non registrate).
    Vuoto = nessuna versione pubblicata è cambiata (§8.4)."""
    registrate = impronte_pubblicate()
    problemi = []
    for codice in codici_tipologie():
        for p in profili_predefiniti(codice):
            chiave = f"{codice}.v{p.versione}"
            if chiave not in registrate:
                problemi.append(f"{chiave}: non registrata in impronte.json")
            elif registrate[chiave] != impronta_profilo(p):
                problemi.append(f"{chiave}: CAMBIATA rispetto alla versione pubblicata")
    return problemi


def campi_allegato5() -> dict[str, CampoProfilo]:
    """Nome del campo → definizione, per i campi con origine ``allegato5`` delle
    versioni predefinite (formato 2): il costruttore dell'IPdA ne usa il nome del
    metadato dell'Allegato 5 (``allegato5``, anche dei sottocampi). Con più
    definizioni dello stesso campo vale la versione più alta."""
    mappa: dict[str, tuple[int, CampoProfilo]] = {}
    for codice in codici_tipologie():
        for p in profili_predefiniti(codice):
            for c in p.campi:
                if c.origine == "allegato5" and c.allegato5 and \
                        p.versione >= mappa.get(c.nome, (0, c))[0]:
                    mappa[c.nome] = (p.versione, c)
    return {nome: campo for nome, (_, campo) in mappa.items()}


__all__ = [
    "CARTELLA_PROFILI",
    "FORMATI_FILE",
    "Tipologia",
    "codici_tipologie",
    "differenze_impronte",
    "impronta_profilo",
    "impronte_pubblicate",
    "campi_allegato5",
    "profili_predefiniti",
    "profilo_tipologia",
    "tipologie",
    "versioni_tipologia",
]
