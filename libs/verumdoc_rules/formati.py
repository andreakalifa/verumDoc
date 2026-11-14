# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/formati.py — registro dei formati riconosciuti dal servizio e
estensioni dei nomi file che ne derivano (R03 f1, difetto 4 del rapporto c2).

Riferimenti: REGOLE_SVILUPPO §8.1 (nessun elenco normativo cablato nei
controlli), §8.12 (elenchi di riferimento come dati versionati), §8.15 (il
formato si riconosce dal contenuto; l'estensione è solo un primo filtro);
Linee guida AgID sul documento informatico (2021), Allegato 2.

Prima di R03 f1 l'estensione si controllava contro un elenco fisso
(``.pdf .xml .p7m .tif .tiff .jpg .jpeg .png``) indipendente dalla classe.
Ora le estensioni ammesse DERIVANO da:

  - i formati (MIME) della classe, impostazione ``FORMATI_AMMESSI`` risolta dal
    motore delle regole, quando la regola RGL-105 BLOCCA;
  - altrimenti (classe senza restrizioni o RGL-105 che avvisa/ignora) da TUTTI
    i formati del registro, cioè quelli che il servizio sa riconoscere dal
    contenuto.

Il registro è un dato versionato: la versione 1 riproduce esattamente l'elenco
di R02 (§8.14, nessun cambio di comportamento per chi c'è già). Un formato
nuovo (es. ODF) si aggiunge con una versione 2 del registro INSIEME al suo
riconoscimento dal contenuto; finché il servizio non lo riconosce, ammetterne
l'estensione produrrebbe solo rifiuti VRS-109 più tardi.

Limite dichiarato: il registro sta nel codice come dato versionato, non ancora
in una tabella con validità caricata da fonte esterna (PRONOM ↔ Allegato 2,
§8.12): è previsto con la console del conservatore (R05).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class FormatoRegistrato:
    """Un formato del registro: MIME canonico, estensioni e MIME equivalenti."""

    mime: str
    estensioni: tuple[str, ...]
    equivalenti: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegistroFormati:
    """Registro versionato dei formati riconosciuti dal servizio."""

    versione: str
    formati: tuple[FormatoRegistrato, ...]
    norm_ref: str = "Linee guida AgID sul documento informatico (2021), Allegato 2"

    def _per_mime(self) -> dict[str, FormatoRegistrato]:
        indice: dict[str, FormatoRegistrato] = {}
        for f in self.formati:
            for nome in (f.mime, *f.equivalenti):
                indice[nome.lower()] = f
        return indice

    def tutte_le_estensioni(self) -> frozenset[str]:
        return frozenset(e for f in self.formati for e in f.estensioni)

    def estensioni_di(self, formati: Iterable[str]) -> frozenset[str]:
        """Estensioni dei MIME indicati (quelli fuori registro non ne danno)."""
        indice = self._per_mime()
        trovate: set[str] = set()
        for mime in formati:
            voce = indice.get(_base(mime))
            if voce is not None:
                trovate.update(voce.estensioni)
        return frozenset(trovate)


def _base(mime: str) -> str:
    return mime.split(";", 1)[0].strip().lower()


#: Versione 1: i formati riconosciuti dal contenuto in R02-R03 (stesse estensioni
#: dell'elenco fisso di R02).
REGISTRO_FORMATI_V1 = RegistroFormati(
    versione="1",
    formati=(
        FormatoRegistrato("application/pdf", (".pdf",)),
        FormatoRegistrato("application/xml", (".xml",), ("text/xml",)),
        FormatoRegistrato("application/pkcs7-mime", (".p7m",), ("application/x-pkcs7-mime",)),
        FormatoRegistrato("image/tiff", (".tif", ".tiff"), ("image/tif",)),
        FormatoRegistrato("image/jpeg", (".jpg", ".jpeg"), ("image/jpg",)),
        FormatoRegistrato("image/png", (".png",)),
    ),
)

#: Registro in vigore.
REGISTRO_FORMATI = REGISTRO_FORMATI_V1


def estensione(nome_file: str) -> str:
    """Estensione (con il punto, minuscola) dell'ULTIMO segmento: «a.pdf.p7m» → «.p7m»."""
    return "." + nome_file.rsplit(".", 1)[-1].lower() if "." in nome_file else ""


@dataclass(frozen=True)
class EstensioniAmmesse:
    """Estensioni ammesse per un versamento e da dove vengono."""

    estensioni: frozenset[str]
    #: «classe» (FORMATI_AMMESSI con RGL-105 bloccante) o «registro»
    origine: str
    registro: str


def estensioni_ammesse(formati_classe: Iterable[str], *, restrizione_attiva: bool,
                       registro: RegistroFormati = REGISTRO_FORMATI,
                       normalizza: Callable[[str], str] = _base) -> EstensioniAmmesse:
    """Estensioni ammesse dati i formati della classe.

    ``restrizione_attiva``: True se la regola dei formati della classe (RGL-105)
    BLOCCA; con elenco vuoto o regola non bloccante valgono tutte le estensioni
    del registro (nessuna restrizione della classe, come per RGL-105)."""
    formati = [normalizza(f) for f in formati_classe if f and f.strip()]
    if restrizione_attiva and formati:
        return EstensioniAmmesse(registro.estensioni_di(formati), "classe", registro.versione)
    return EstensioniAmmesse(registro.tutte_le_estensioni(), "registro", registro.versione)


def motivo_estensione(nome_file: str, ammesse: EstensioniAmmesse) -> str | None:
    """None se l'estensione del nome è ammessa, altrimenti il motivo del rifiuto."""
    ext = estensione(nome_file)
    if ext in ammesse.estensioni:
        return None
    da = ("dai formati della classe documentale" if ammesse.origine == "classe"
          else f"dal registro dei formati (versione {ammesse.registro})")
    elenco = ", ".join(sorted(ammesse.estensioni)) or "nessuna"
    return f"Estensione {ext or '(assente)'!s} del file {nome_file!r} non ammessa {da}: {elenco}"


__all__ = [
    "REGISTRO_FORMATI", "REGISTRO_FORMATI_V1", "EstensioniAmmesse", "FormatoRegistrato",
    "RegistroFormati", "estensione", "estensioni_ammesse", "motivo_estensione",
]
