# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/modello.py — struttura dei documenti generati (R15, VD-C-177).

Un ``Documento`` è un elenco di ``Sezione`` (con sottosezioni); una sezione è fatta di
blocchi: ``Paragrafo``, ``Elenco``, ``Tabella``, ``Nota`` e i due blocchi «dichiarati»:

  - ``DaCompilare``  un dato che il sistema non ha: si scrive «DA COMPILARE» con il campo,
                     CHI lo deve fornire e DOVE si inserisce. È l'UNICO segnaposto ammesso
                     nei documenti generati (``tools/docs/verifica_documentazione.py``
                     rifiuta ogni altro segnaposto e ogni «DA COMPILARE» senza «fornisce:»);
  - ``DaVerificare`` un requisito normativo il cui testo non è stato riscontrato sulla fonte
                     ufficiale: «da verificare sul testo ufficiale», mai un testo inventato.

``valore_o_da_compilare`` è il solo modo di mettere in un testo un dato della
configurazione: restituisce il valore o il blocco ``DaCompilare``.

Modulo puro (solo libreria standard).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

DA_COMPILARE = "DA COMPILARE"
DA_VERIFICARE = "da verificare sul testo ufficiale"

#: Tipi dei documenti generati (chiave stabile nel database e nel WORM)
MANUALE = "MANUALE_CONSERVAZIONE"
PIANO_SICUREZZA = "PIANO_SICUREZZA"
GUIDA = "GUIDA_FUNZIONALITA"
CATALOGO_ROUTE = "CATALOGO_ROUTE"
TIPI_DOCUMENTO: tuple[str, ...] = (MANUALE, PIANO_SICUREZZA, GUIDA, CATALOGO_ROUTE)


@dataclass(frozen=True)
class Paragrafo:
    testo: str


@dataclass(frozen=True)
class Nota:
    testo: str


@dataclass(frozen=True)
class Elenco:
    voci: tuple[str, ...]


@dataclass(frozen=True)
class Tabella:
    intestazioni: tuple[str, ...]
    righe: tuple[tuple[str, ...], ...]
    didascalia: str = ""


@dataclass(frozen=True)
class DaCompilare:
    """Dato mancante: ``campo`` è una chiave stabile (``conservatore.denominazione``)."""

    campo: str
    descrizione: str
    chi: str
    dove: str = ""


@dataclass(frozen=True)
class DaVerificare:
    """Requisito normativo non riscontrato sul testo ufficiale."""

    requisito: str
    motivo: str


Blocco = Paragrafo | Nota | Elenco | Tabella | DaCompilare | DaVerificare


@dataclass
class Sezione:
    """Sezione numerata. ``id`` è stabile fra le versioni (confronto delle differenze);
    ``requisiti`` sono i codici di ``requisiti.REQUISITI`` che la sezione copre;
    ``fonti`` dicono da dove vengono i dati (``db:``, ``repo:``, ``config:``, ``codice:``)."""

    id: str
    numero: str
    titolo: str
    blocchi: list[Blocco] = field(default_factory=list)
    requisiti: tuple[str, ...] = ()
    fonti: tuple[str, ...] = ()
    sottosezioni: list[Sezione] = field(default_factory=list)

    def tutte(self) -> Iterator[Sezione]:
        yield self
        for s in self.sottosezioni:
            yield from s.tutte()

    def da_compilare(self) -> list[DaCompilare]:
        return [b for s in self.tutte() for b in s.blocchi if isinstance(b, DaCompilare)]

    def da_verificare(self) -> list[DaVerificare]:
        return [b for s in self.tutte() for b in s.blocchi if isinstance(b, DaVerificare)]


@dataclass
class Documento:
    tipo: str
    titolo: str
    sottotitolo: str
    sezioni: list[Sezione]
    #: avvertenze in testa al documento (es. edizione senza database)
    avvertenze: tuple[str, ...] = ()

    def tutte(self) -> Iterator[Sezione]:
        for s in self.sezioni:
            yield from s.tutte()

    def da_compilare(self) -> list[DaCompilare]:
        return [d for s in self.sezioni for d in s.da_compilare()]

    def da_verificare(self) -> list[DaVerificare]:
        return [d for s in self.sezioni for d in s.da_verificare()]

    def fonti(self) -> list[str]:
        return sorted({f for s in self.tutte() for f in s.fonti})

    def riepilogo_da_compilare(self) -> list[dict[str, Any]]:
        """Per la console: una voce per sezione con i dati mancanti e chi li fornisce."""
        voci = []
        for s in self.tutte():
            mancanti = [b for b in s.blocchi if isinstance(b, DaCompilare)]
            if mancanti:
                voci.append({"sezione": s.id, "numero": s.numero, "titolo": s.titolo,
                             "campi": [{"campo": m.campo, "descrizione": m.descrizione,
                                        "chi": m.chi, "dove": m.dove} for m in mancanti]})
        return voci


#: Valori che non sono mai un dato reale (configurazione non compilata)
SEGNAPOSTO_CONFIGURAZIONE = frozenset({
    "", "00000000000", "0000000000000000", "da nominare", "da definire", "n/a", "tbd",
    "todo", "xxx", "da_impostare", "da impostare", "-", "—",
})


def vuoto(valore: Any) -> bool:
    """True se il valore non è un dato (None, stringa vuota o segnaposto, elenco vuoto)."""
    if valore is None:
        return True
    if isinstance(valore, str):
        v = valore.strip().lower()
        return v in SEGNAPOSTO_CONFIGURAZIONE or v.startswith("da_impostare")
    if isinstance(valore, list | tuple | dict | set | frozenset):
        return len(valore) == 0
    return False


def valore_o_da_compilare(valore: Any, campo: str, descrizione: str, chi: str,
                          dove: str = "") -> str | DaCompilare:
    """Il valore come testo o il blocco ``DaCompilare``: mai un valore predefinito."""
    if vuoto(valore):
        return DaCompilare(campo, descrizione, chi, dove)
    if isinstance(valore, bool):
        return "sì" if valore else "no"
    if isinstance(valore, list | tuple):
        return ", ".join(str(x) for x in valore)
    return str(valore)


def blocchi_campi(righe: Iterable[tuple[str, str | DaCompilare]]) -> list[Blocco]:
    """Tabella «voce | valore» con i valori presenti; i mancanti come ``DaCompilare``
    dopo la tabella (ogni dato mancante ha il suo blocco dichiarato)."""
    presenti: list[tuple[str, ...]] = []
    mancanti: list[Blocco] = []
    for etichetta, v in righe:
        if isinstance(v, DaCompilare):
            presenti.append((etichetta, DA_COMPILARE))
            mancanti.append(v)
        else:
            presenti.append((etichetta, v))
    blocchi: list[Blocco] = [Tabella(("Voce", "Valore"), tuple(presenti))] if presenti else []
    return blocchi + mancanti


__all__ = ["CATALOGO_ROUTE", "DA_COMPILARE", "DA_VERIFICARE", "GUIDA", "MANUALE",
           "PIANO_SICUREZZA", "SEGNAPOSTO_CONFIGURAZIONE", "TIPI_DOCUMENTO", "Blocco",
           "DaCompilare", "DaVerificare", "Documento", "Elenco", "Nota", "Paragrafo",
           "Sezione", "Tabella", "blocchi_campi", "valore_o_da_compilare", "vuoto"]
