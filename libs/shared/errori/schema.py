# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Forma del catalogo errori: tipi e validazione (VD-C-100).

Solo libreria standard e nessun caricamento: lo usa anche lo strumento
``tools/docs/genera_catalogo_errori.py`` prima che i file derivati esistano.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Esito(StrEnum):
    """Classe dell'esito, per decidere che cosa fare senza leggere il testo."""

    #: Colpa del pacchetto o della richiesta: correggere e reinviare.
    NON_VALIDO = "NON_VALIDO"
    #: Regola o policy: ritentare la stessa richiesta non serve.
    RIFIUTATO = "RIFIUTATO"
    #: Problema del servizio o temporaneo: si può ritentare se `ritentabile`.
    ERRORE = "ERRORE"


CONTESTI = ("http", "asincrono")
CAMPI_OBBLIGATORI = ("famiglia", "status", "titolo", "descrizione", "azione", "esito",
                     "ritentabile")
CAMPI_FACOLTATIVI = ("retry_after", "contesto", "alias", "dismesso", "sostituito_da")
#: Prefisso obbligatorio del titolo di un codice dismesso (R03 i1).
PREFISSO_DISMESSO = "Dismesso"
FORMA_CODICE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


@dataclass(frozen=True)
class Famiglia:
    sigla: str
    descrizione: str


@dataclass(frozen=True)
class VoceCatalogo:
    """Un codice d'errore del catalogo."""

    codice: str
    famiglia: str
    status: int
    titolo: str
    descrizione: str
    azione: str
    esito: Esito
    ritentabile: bool
    retry_after: int | None = None
    contesto: str = "http"
    alias: tuple[str, ...] = ()
    #: Non più emesso dal servizio: resta nel catalogo per i client che lo
    #: conoscono (un codice pubblicato non si riusa); ``sostituito_da`` dice quali
    #: codici lo sostituiscono.
    dismesso: bool = False
    sostituito_da: tuple[str, ...] = ()


class CatalogoNonValido(ValueError):
    """Il catalogo non rispetta la forma attesa (elenco dei problemi in `problemi`)."""

    def __init__(self, problemi: list[str]) -> None:
        super().__init__("catalogo errori non valido:\n- " + "\n- ".join(problemi))
        self.problemi = problemi


def valida_catalogo(dati: Mapping[str, Any]) -> list[str]:
    """Problemi di forma del catalogo (lista vuota se è valido)."""
    problemi: list[str] = []
    famiglie = dati.get("famiglie")
    codici = dati.get("codici")
    if not isinstance(dati.get("versione"), str):
        problemi.append("manca `versione` (testo)")
    if not isinstance(famiglie, Mapping) or not famiglie:
        return [*problemi, "manca `famiglie`"]
    if not isinstance(codici, Mapping) or not codici:
        return [*problemi, "manca `codici`"]
    for sigla, fam in famiglie.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", str(sigla)):
            problemi.append(f"famiglia {sigla!r}: sigla non valida")
        if not isinstance(fam, Mapping) or not str(fam.get("descrizione") or "").strip():
            problemi.append(f"famiglia {sigla}: manca la descrizione")
    alias_visti: dict[str, str] = {}
    for codice, v in codici.items():
        if not FORMA_CODICE.fullmatch(str(codice)):
            problemi.append(f"{codice}: forma del codice non valida")
        if not isinstance(v, Mapping):
            problemi.append(f"{codice}: voce non è un oggetto")
            continue
        for campo in CAMPI_OBBLIGATORI:
            if campo not in v:
                problemi.append(f"{codice}: manca `{campo}`")
        estranei = set(v) - set(CAMPI_OBBLIGATORI) - set(CAMPI_FACOLTATIVI)
        if estranei:
            problemi.append(f"{codice}: campi non previsti {sorted(estranei)}")
        fam = v.get("famiglia")
        if fam not in famiglie:
            problemi.append(f"{codice}: famiglia {fam!r} non dichiarata")
        elif str(codice).split("-", 1)[0] != fam:
            problemi.append(f"{codice}: il prefisso non corrisponde alla famiglia {fam}")
        status = v.get("status")
        if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
            problemi.append(f"{codice}: `status` deve essere un intero fra 400 e 599")
        for campo in ("titolo", "descrizione", "azione"):
            if campo in v and not str(v.get(campo) or "").strip():
                problemi.append(f"{codice}: `{campo}` vuoto")
        esito = v.get("esito")
        if esito not in Esito.__members__:
            problemi.append(f"{codice}: esito {esito!r} non ammesso ({', '.join(Esito)})")
        ritentabile = v.get("ritentabile")
        if not isinstance(ritentabile, bool):
            problemi.append(f"{codice}: `ritentabile` deve essere vero o falso")
        elif ritentabile and esito in (Esito.NON_VALIDO, Esito.RIFIUTATO):
            problemi.append(f"{codice}: un esito {esito} non può essere ritentabile")
        ra = v.get("retry_after")
        if ra is not None and (not isinstance(ra, int) or isinstance(ra, bool) or ra <= 0):
            problemi.append(f"{codice}: `retry_after` deve essere un intero positivo")
        if v.get("contesto", "http") not in CONTESTI:
            problemi.append(f"{codice}: contesto non ammesso ({', '.join(CONTESTI)})")
        for a in v.get("alias") or ():
            if a in codici or a in alias_visti:
                problemi.append(f"{codice}: alias {a!r} già usato")
            alias_visti[str(a)] = str(codice)
        problemi += _problemi_dismissione(str(codice), v, codici)
    return problemi


def _problemi_dismissione(codice: str, v: Mapping[str, Any],
                          codici: Mapping[str, Any]) -> list[str]:
    """``dismesso`` vero o falso; un dismesso dichiara la dismissione nel titolo
    («Dismesso: vedi …»); ``sostituito_da`` solo per i dismessi, verso codici
    esistenti e non a loro volta dismessi."""
    problemi: list[str] = []
    dismesso = v.get("dismesso", False)
    if not isinstance(dismesso, bool):
        return [f"{codice}: `dismesso` deve essere vero o falso"]
    sostituti = v.get("sostituito_da") or []
    if not isinstance(sostituti, list) or not all(isinstance(c, str) for c in sostituti):
        return [f"{codice}: `sostituito_da` deve essere un elenco di codici"]
    if dismesso and not str(v.get("titolo") or "").startswith(PREFISSO_DISMESSO):
        problemi.append(f"{codice}: il titolo di un codice dismesso inizia con "
                        f"«{PREFISSO_DISMESSO}»")
    if sostituti and not dismesso:
        problemi.append(f"{codice}: `sostituito_da` vale solo per un codice dismesso")
    for c in sostituti:
        voce = codici.get(c)
        if not isinstance(voce, Mapping):
            problemi.append(f"{codice}: sostituto {c!r} non è nel catalogo")
        elif voce.get("dismesso"):
            problemi.append(f"{codice}: sostituto {c} è a sua volta dismesso")
    return problemi
