# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/conservatore.py — dati del conservatore dalla configurazione (R15).

Fonte: variabili d'ambiente ``CONSERVATORE_*`` del servizio che genera (dashboard-api in
esercizio, lo strumento ``tools/conformita/genera_documenti.py`` da riga di comando): le
stesse del manuale di R02 (``services/dashboard-api/app/services/manuale.py``). Un campo
vuoto, segnaposto o non valido è «DA COMPILARE» con la variabile da impostare: mai un
valore inventato e mai un dato di prova spacciato per reale (gli indirizzi dei domini
riservati ``.example``, ``.invalid``, ``.test``, ``.local`` non valgono come dato reale).

Modulo puro: riceve la mappa delle variabili (``os.environ`` la passa il chiamante).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from libs.conformita.modello import DaCompilare, valore_o_da_compilare, vuoto

CHI_CONSERVATORE = "il conservatore (legale rappresentante o responsabile del servizio)"


@dataclass(frozen=True)
class Campo:
    chiave: str
    descrizione: str
    tipo: str = "testo"

    @property
    def variabile(self) -> str:
        return "CONSERVATORE_" + self.chiave.upper()


CAMPI: tuple[Campo, ...] = (
    Campo("denominazione", "denominazione del conservatore"),
    Campo("codice_fiscale", "codice fiscale del conservatore", "cf_ente"),
    Campo("partita_iva", "partita IVA del conservatore", "piva"),
    Campo("sede_legale", "sede legale"),
    Campo("pec", "indirizzo PEC del conservatore", "email"),
    Campo("email_contatto", "posta elettronica di contatto", "email"),
    Campo("responsabile_servizio_nome", "responsabile del servizio di conservazione"),
    Campo("responsabile_funzione_archivistica_nome",
          "responsabile della funzione archivistica di conservazione"),
    Campo("responsabile_sicurezza_nome", "responsabile della sicurezza dei sistemi"),
    Campo("responsabile_protezione_dati_nome", "responsabile della protezione dei dati"),
    Campo("ubicazione_data_center", "ubicazione dichiarata dei centri dati (Italia, UE, "
          "extra-UE)"),
)
PER_CHIAVE = {c.chiave: c for c in CAMPI}

_DOMINI_DI_PROVA = (".example", ".invalid", ".test", ".local")


def _piva_valida(v: str) -> bool:
    if not re.fullmatch(r"\d{11}", v) or set(v) == {"0"}:
        return False
    somma = 0
    for i, c in enumerate(v[:10]):
        n = int(c)
        if i % 2 == 1:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        somma += n
    return (10 - somma % 10) % 10 == int(v[10])


def _valido(tipo: str, v: str) -> bool:
    if tipo == "piva":
        return _piva_valida(v)
    if tipo == "cf_ente":
        return _piva_valida(v) or bool(
            re.fullmatch(r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]", v.upper()))
    if tipo == "email":
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v, re.I)) and \
            not v.lower().endswith(_DOMINI_DI_PROVA)
    return True


def dato(ambiente: Mapping[str, str], chiave: str) -> str | DaCompilare:
    """Il valore del campo o «DA COMPILARE» con la variabile da impostare."""
    c = PER_CHIAVE[chiave]
    grezzo = (ambiente.get(c.variabile) or "").strip()
    if not vuoto(grezzo) and not _valido(c.tipo, grezzo):
        return DaCompilare(f"conservatore.{chiave}", f"{c.descrizione} (valore attuale non "
                           "valido)", CHI_CONSERVATORE, f"variabile {c.variabile}")
    return valore_o_da_compilare(grezzo, f"conservatore.{chiave}", c.descrizione,
                                 CHI_CONSERVATORE, f"variabile {c.variabile}")


def dati(ambiente: Mapping[str, str]) -> dict[str, str | DaCompilare]:
    return {c.chiave: dato(ambiente, c.chiave) for c in CAMPI}


def variabili(ambiente: Mapping[str, str]) -> dict[str, str]:
    """Solo le variabili ``CONSERVATORE_*`` note (fonte dichiarata nei documenti)."""
    return {c.variabile: ambiente[c.variabile] for c in CAMPI if c.variabile in ambiente}


__all__ = ["CAMPI", "CHI_CONSERVATORE", "Campo", "dati", "dato", "variabili"]
