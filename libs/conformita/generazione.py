# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/generazione.py — una generazione completa dei documenti (R15).

Usata dallo strumento da riga di comando (``tools/conformita/genera_documenti.py``) e dalla
console (dashboard-api, ``app/routers/documentazione_servizio.py``): stessi fatti, stesso
risultato. Le fonti di ogni documento sono le fonti dichiarate dalle sue sezioni, con
l'impronta SHA-256 dei file del repository presa dalla fotografia, più il database (se
letto) e le variabili del conservatore (solo i nomi: i valori stanno nel documento).

Modulo puro: il tempo (``generata_il``) e le versioni precedenti li passa il chiamante.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from libs.conformita import conservatore
from libs.conformita.documenti import GENERATORI, Fatti
from libs.conformita.modello import Documento
from libs.conformita.versioni import Edizione, Generato, componi


def fonti_documento(doc: Documento, fatti: Fatti) -> dict[str, str]:
    fonti: dict[str, str] = {}
    dichiarate = [f.removeprefix("repo:") for f in doc.fonti() if f.startswith("repo:")]
    for percorso, impronta in fatti.repo.get("fonti", {}).items():
        if any(percorso == d or percorso.startswith(d.rstrip("/") + "/") for d in dichiarate):
            fonti[f"repo:{percorso}"] = f"sha256 {impronta}"
    for f in doc.fonti():
        if f.startswith("db:"):
            fonti[f] = ("letta in sola lettura" if fatti.con_database
                        else "non letta (edizione senza database)")
        elif f.startswith("config:"):
            presenti = sorted(conservatore.variabili(fatti.ambiente))
            fonti[f] = ("impostate: " + ", ".join(presenti)) if presenti else "nessuna impostata"
        elif f.startswith("codice:"):
            fonti[f] = "registro nel codice del rilascio"
    rilascio = fatti.repo.get("rilascio") or {}
    if rilascio.get("changelog"):
        fonti["rilascio"] = f"{rilascio['changelog']} ({rilascio.get('data', '')})"
    return fonti


def genera(fatti: Fatti, *, generata_il: str | None = None,
           precedenti: Mapping[str, dict[str, Any] | None] | None = None,
           tipi: tuple[str, ...] | None = None) -> dict[str, Generato]:
    """Tutti i documenti (o ``tipi``). ``precedenti[tipo]`` = ultima versione registrata
    ({"versione", "impronta_contenuto", "sezioni"}) o None; senza ``generata_il`` è
    l'edizione del repository (nessuna versione né data)."""
    risultato: dict[str, Generato] = {}
    for tipo, funzione in GENERATORI.items():
        if tipi is not None and tipo not in tipi:
            continue
        doc = funzione(fatti)
        prec = (precedenti or {}).get(tipo)
        versione = None if generata_il is None else (int(prec["versione"]) + 1 if prec else 1)
        ed = Edizione(versione=versione, generata_il=generata_il, precedente=prec,
                      fonti=fonti_documento(doc, fatti))
        generato = componi(doc, ed)
        if prec and prec.get("impronta_contenuto") == generato.impronta_contenuto:
            # stesso contenuto: nessuna versione nuova (il chiamante tiene la precedente)
            generato.versione = None
        risultato[tipo] = generato
    return risultato


__all__ = ["fonti_documento", "genera"]
