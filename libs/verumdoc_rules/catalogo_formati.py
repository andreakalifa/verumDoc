# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/catalogo_formati.py — catalogo dei formati ammessi come dati
versionati (R08, decisione D02; REGOLE_SVILUPPO §8.1, §8.4, §8.12, §8.14).

La BASE del conservatore sta in ``dati/formati/allegato2.v<n>.json``: famiglie
di formati dell'Allegato 2 con gli identificativi PRONOM (PUID), i MIME e
l'esito predefinito. Ogni famiglia diventa un'impostazione del motore

    FORMATO.<ID>.ESITO   AMMESSO | AVVISO | DA_CONVERTIRE | RIFIUTATO

ai livelli CONSERVATORE (cambia l'esito per tutti), ENTE e CLASSE (restrizioni
dell'ente, anche per classe documentale). Verso «più sicuro» = ordine delle
scelte (RIFIUTATO è il più restrittivo): l'ente può solo restringere rispetto
al conservatore e al limite che il conservatore gli fissa (``LIMITE.FORMATO.<ID>.ESITO``),
con bozza, anteprima, applica e storico della console (R05/R07).

Esiti nel versamento e nella verifica (regole del catalogo v3):
  - RIFIUTATO     → RGL-107 (effetto predefinito BLOCCA);
  - AVVISO        → RGL-108 (AVVISA): ammesso, l'avviso va nel RdV;
  - DA_CONVERTIRE → RGL-108 (AVVISA): ammesso, segnalato da convertire;
  - AMMESSO       → nessun rilievo.

Corrispondenza file → famiglia: per PUID (verifica, Siegfried) o, senza PUID,
per MIME (gateway, firma binaria). Più famiglie con lo stesso MIME (PDF e
PDF/A): senza PUID vale la MENO restrittiva (il gateway non sa distinguerle;
la verifica decide col PUID e, per il PDF/A, con veraPDF). Una famiglia
``pdfa`` vale solo se il PDF/A non è stato trovato NON conforme.

Revisione R08 (X11): nella verifica (PUID noto) un PDF/A che NON è stato
validato (veraPDF non disponibile: ``pdfa_conforme`` None) non è mai AMMESSO
pieno. ``FORMATO.PDFA_NON_VALIDATO`` (riservata al conservatore):
  - AVVISO (predefinito): esito «PDF/A dichiarato, non validato», almeno AVVISO
    (RGL-108; resta RIFIUTATO o DA_CONVERTIRE se la famiglia lo è);
  - COME_PDF: vale l'esito della famiglia PDF generica (come un PDF/A non
    conforme), per chi richiede il PDF/A validato.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from libs.verumdoc_rules.impostazioni import (
    Impostazione,
    Livello,
    TipoImpostazione,
    VersoSicuro,
)

CARTELLA = Path(__file__).parent / "dati" / "formati"
FORMATO_FILE = "verumdoc-catalogo-formati-1"

AMMESSO = "AMMESSO"
AVVISO = "AVVISO"
DA_CONVERTIRE = "DA_CONVERTIRE"
RIFIUTATO = "RIFIUTATO"
#: Dalla più restrittiva alla meno (ordine delle scelte = verso sicuro).
ESITI: tuple[str, ...] = (RIFIUTATO, DA_CONVERTIRE, AVVISO, AMMESSO)
NON_IDENTIFICATO = "NON_IDENTIFICATO"

PAROLE_ESITO = {
    AMMESSO: "ammesso", AVVISO: "ammesso con avviso",
    DA_CONVERTIRE: "da convertire in un formato idoneo alla conservazione",
    RIFIUTATO: "rifiutato",
}


class CatalogoFormatiNonValido(ValueError):
    pass


@dataclass(frozen=True)
class VoceFormato:
    id: str
    nome: str
    famiglia: str
    puid: tuple[str, ...]
    mime: tuple[str, ...]
    esito: str
    pdfa: bool = False

    @property
    def codice(self) -> str:
        return codice_esito(self.id)


@dataclass(frozen=True)
class CatalogoFormati:
    versione: int
    valido_dal: str
    norm_ref: str
    voci: tuple[VoceFormato, ...]

    def __getitem__(self, id_formato: str) -> VoceFormato:
        for v in self.voci:
            if v.id == id_formato:
                return v
        raise KeyError(id_formato)

    def per_puid(self, puid: str) -> VoceFormato | None:
        return next((v for v in self.voci if puid in v.puid), None)

    def per_mime(self, mime: str) -> tuple[VoceFormato, ...]:
        base = mime.split(";", 1)[0].strip().lower()
        return tuple(v for v in self.voci if base in v.mime)

    def riferimento(self) -> dict[str, Any]:
        return {"versione": self.versione, "valido_dal": self.valido_dal}


def codice_esito(id_formato: str) -> str:
    return f"FORMATO.{id_formato}.ESITO"


def _voce(d: Mapping[str, Any]) -> VoceFormato:
    if d.get("esito") not in ESITI:
        raise CatalogoFormatiNonValido(f"esito non valido per {d.get('id')!r}")
    return VoceFormato(str(d["id"]), str(d["nome"]), str(d.get("famiglia") or ""),
                       tuple(d.get("puid") or ()), tuple(m.lower() for m in d.get("mime") or ()),
                       str(d["esito"]), bool(d.get("pdfa")))


@cache
def catalogo(versione: int = 1) -> CatalogoFormati:
    """Catalogo base della versione indicata (dati del pacchetto)."""
    dati = json.loads((CARTELLA / f"allegato2.v{versione}.json").read_text(encoding="utf-8"))
    if dati.get("formato") != FORMATO_FILE or dati.get("versione") != versione:
        raise CatalogoFormatiNonValido("file del catalogo dei formati non riconosciuto")
    voci = tuple(_voce(d) for d in dati["formati"])
    if len({v.id for v in voci}) != len(voci) or NON_IDENTIFICATO not in {v.id for v in voci}:
        raise CatalogoFormatiNonValido("identificativi ripetuti o NON_IDENTIFICATO mancante")
    return CatalogoFormati(versione, str(dati["valido_dal"]), str(dati["norm_ref"]), voci)


#: Catalogo in vigore.
CATALOGO_FORMATI = catalogo(1)

_AMBITO = frozenset({Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE})


#: X11: trattamento del PDF/A dichiarato ma non validato (veraPDF assente).
PDFA_NON_VALIDATO = "FORMATO.PDFA_NON_VALIDATO"
PDFA_COME_PDF, PDFA_AVVISO = "COME_PDF", "AVVISO"

IMPOSTAZIONE_PDFA_NON_VALIDATO = Impostazione(
    PDFA_NON_VALIDATO, TipoImpostazione.SCELTA, predefinito=PDFA_AVVISO,
    descrizione=("PDF/A dichiarato ma non validato (veraPDF non disponibile): AVVISO "
                 "(predefinito: ammesso al più con avviso, mai AMMESSO pieno) o COME_PDF "
                 "(vale l'esito della famiglia PDF generica, per chi richiede il PDF/A "
                 "validato)"),
    norm_ref="Linee guida AgID 2021, Allegato 2 (PDF/A)", scelte=(PDFA_COME_PDF, PDFA_AVVISO),
    locked=True, applicata_da="validation (catalogo dei formati)", gruppo="formati",
    verso_sicuro=VersoSicuro.ORDINE_SCELTE)


def impostazioni_formati(cat: CatalogoFormati = CATALOGO_FORMATI) -> tuple[Impostazione, ...]:
    """Una voce ``FORMATO.<ID>.ESITO`` per famiglia (gruppo «formati» della console)
    e ``FORMATO.PDFA_NON_VALIDATO``."""
    return (*_impostazioni_famiglie(cat), IMPOSTAZIONE_PDFA_NON_VALIDATO)


def _impostazioni_famiglie(cat: CatalogoFormati) -> tuple[Impostazione, ...]:
    return tuple(
        Impostazione(
            v.codice, TipoImpostazione.SCELTA, predefinito=v.esito,
            descrizione=(f"Esito del formato «{v.nome}» ({v.famiglia}) nel versamento e nella "
                         "verifica: AMMESSO, AVVISO, DA_CONVERTIRE o RIFIUTATO; l'ente può "
                         "solo restringerlo, anche per classe"),
            norm_ref=cat.norm_ref, scelte=ESITI, ambito_ammesso=_AMBITO,
            applicata_da="gateway e validation (catalogo dei formati)", gruppo="formati",
            verso_sicuro=VersoSicuro.ORDINE_SCELTE)
        for v in cat.voci)


@dataclass(frozen=True)
class EsitoFormato:
    voce: VoceFormato
    esito: str
    motivo: str

    def come_dict(self) -> dict[str, Any]:
        return {"formato": self.voce.id, "nome": self.voce.nome, "esito": self.esito,
                "motivo": self.motivo}


def esito_formato(*, puid: str | None, mime: str | None, pdfa_conforme: bool | None,
                  valore: Callable[[str], Any],
                  cat: CatalogoFormati = CATALOGO_FORMATI) -> EsitoFormato:
    """Esito di un file. ``valore(codice)`` dà l'esito risolto (motore o fotografia).
    ``pdfa_conforme``: False se veraPDF ha trovato il PDF/A non conforme."""
    candidati: tuple[VoceFormato, ...] = ()
    if puid:
        trovata = cat.per_puid(puid)
        if trovata is not None:
            candidati = (trovata,)
    if not candidati and mime:
        candidati = cat.per_mime(mime)
        if puid:  # PUID noto a PRONOM ma non nel catalogo: niente PDF/A senza PUID
            candidati = tuple(c for c in candidati if not c.pdfa) or candidati
    note = ""
    minimo: str | None = None
    generici = tuple(c for c in cat.per_mime("application/pdf") if not c.pdfa)
    if any(c.pdfa for c in candidati) and pdfa_conforme is False:
        candidati = generici or candidati
        note = " (PDF/A dichiarato ma non conforme: vale come PDF)"
    elif puid and any(c.pdfa for c in candidati) and pdfa_conforme is None:
        # X11: PDF/A riconosciuto dal PUID (spesso dal solo XMP) ma non validato
        if _valore_o(valore, PDFA_NON_VALIDATO, PDFA_AVVISO) == PDFA_COME_PDF:
            candidati = generici or candidati
            note = " (PDF/A dichiarato, non validato: vale come PDF)"
        else:
            minimo = AVVISO
            note = " (PDF/A dichiarato, non validato)"
    if not candidati:
        voce = cat[NON_IDENTIFICATO]
        esito = str(valore(voce.codice))
        return EsitoFormato(voce, esito, f"formato non identificato dal contenuto: "
                                         f"{PAROLE_ESITO[esito]}")
    scelti = [(c, str(valore(c.codice))) for c in candidati]
    voce, esito = max(scelti, key=lambda ce: ESITI.index(ce[1]))  # la meno restrittiva
    if minimo is not None and ESITI.index(esito) > ESITI.index(minimo):
        esito = minimo  # mai meno restrittivo del minimo (X11)
    return EsitoFormato(voce, esito, f"{voce.nome}: {PAROLE_ESITO[esito]}{note}")


def _valore_o(valore: Callable[[str], Any], codice: str, predefinito: str) -> str:
    """Valore di una voce che le fotografie precedenti non hanno: predefinito."""
    try:
        risolto = valore(codice)
    except KeyError:
        return predefinito
    return predefinito if risolto is None else str(risolto)


def codici_formati(cat: CatalogoFormati = CATALOGO_FORMATI) -> tuple[str, ...]:
    return (*(v.codice for v in cat.voci), PDFA_NON_VALIDATO)


def voci_da_mostrare(cat: CatalogoFormati = CATALOGO_FORMATI) -> list[dict[str, Any]]:
    """Catalogo per la console (identificativi, MIME, esito base)."""
    return [{"id": v.id, "nome": v.nome, "famiglia": v.famiglia, "puid": list(v.puid),
             "mime": list(v.mime), "esito_base": v.esito, "pdfa": v.pdfa,
             "codice": v.codice} for v in cat.voci]


__all__ = [
    "AMMESSO", "AVVISO", "CATALOGO_FORMATI", "DA_CONVERTIRE", "ESITI", "NON_IDENTIFICATO",
    "PDFA_AVVISO", "PDFA_COME_PDF", "PDFA_NON_VALIDATO",
    "PAROLE_ESITO", "RIFIUTATO", "CatalogoFormati", "CatalogoFormatiNonValido", "EsitoFormato",
    "VoceFormato", "catalogo", "codice_esito", "codici_formati", "esito_formato",
    "impostazioni_formati", "voci_da_mostrare",
]
