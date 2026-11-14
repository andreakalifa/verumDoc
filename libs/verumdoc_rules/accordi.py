# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/accordi.py — accordi di versamento come livello della catena
di risoluzione (R05 Q1; REGOLE_SVILUPPO §8.2, §8.4, §10.5, §11.3).

Un accordo di versamento è un insieme di valori di impostazioni (formati,
metadati obbligatori, dimensioni, effetti delle regole…) concordato con l'ente,
per una classe documentale o per tutte le classi dell'ente. È VERSIONATO e mai
modificato: una nuova versione ha la sua decorrenza (``valido_dal``, giorno
italiano) e vale finché non ne decorre una successiva; una versione con
``cessato`` chiude l'accordo da quel giorno. Tabella: ``accordi_versamento``
(migrazione 20261023_036), append-only.

Scelta dell'accordo per un versamento (un solo punto, ``seleziona_accordo``):
  1. se l'indice indica ``accordo`` (codice): quell'accordo dell'ente, in vigore
     nel giorno e valido per la classe; altrimenti il versamento si rifiuta
     (RGL-112), mai un ripiego silenzioso;
  2. altrimenti l'accordo in vigore dell'ente PER LA CLASSE;
  3. altrimenti l'accordo in vigore dell'ente per tutte le classi;
  4. altrimenti nessun accordo (comportamento di prima, §8.14).
Due accordi in vigore per lo stesso ambito sono un errore di configurazione
(``AccordiAmbigui``): la console lo impedisce, il versamento non indovina.

Pacchetto puro: il giorno arriva come argomento.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class AccordoNonValido(ValueError):
    """L'accordo indicato nell'indice non esiste, non è in vigore o non vale per la classe."""

    def __init__(self, codice: str, motivo: str) -> None:
        super().__init__(f"Accordo di versamento {codice!r}: {motivo}")
        self.codice = codice
        self.motivo = motivo


class AccordiAmbigui(ValueError):
    """Più accordi in vigore per lo stesso ambito (errore di configurazione)."""


@dataclass(frozen=True)
class AccordoVersamento:
    """Una versione di un accordo di versamento."""

    tenant_id: str
    codice: str
    versione: int
    valido_dal: date
    classe_id: str | None = None
    valori: Mapping[str, Any] = field(default_factory=dict)
    cessato: bool = False
    descrizione: str = ""
    id: str | None = None

    def riferimento(self) -> dict[str, Any]:
        """Ciò che il PdV fotografa dell'accordo applicato (REGOLE §8.3)."""
        return {"id": self.id, "codice": self.codice, "versione": self.versione,
                "valido_dal": self.valido_dal.isoformat(), "classe_id": self.classe_id}

    def come_dict(self) -> dict[str, Any]:
        return {**self.riferimento(), "tenant_id": self.tenant_id, "cessato": self.cessato,
                "descrizione": self.descrizione, "valori": dict(self.valori)}

    @classmethod
    def da_dict(cls, dati: Mapping[str, Any]) -> AccordoVersamento:
        return cls(tenant_id=str(dati["tenant_id"]), codice=str(dati["codice"]),
                   versione=int(dati["versione"]),
                   valido_dal=date.fromisoformat(str(dati["valido_dal"])),
                   classe_id=str(dati["classe_id"]) if dati.get("classe_id") else None,
                   valori=dict(dati.get("valori") or {}), cessato=bool(dati.get("cessato")),
                   descrizione=str(dati.get("descrizione") or ""),
                   id=str(dati["id"]) if dati.get("id") else None)


def in_vigore(versioni: Iterable[AccordoVersamento], giorno: date) -> AccordoVersamento | None:
    """Versione in vigore nel giorno di UN accordo: la più alta già decorsa; None se
    nessuna è decorsa o se quella in vigore chiude l'accordo (``cessato``)."""
    decorse = [v for v in versioni if v.valido_dal <= giorno]
    if not decorse:
        return None
    ultima = max(decorse, key=lambda v: (v.versione, v.valido_dal))
    return None if ultima.cessato else ultima


def vigenti_per_codice(versioni: Iterable[AccordoVersamento],
                       giorno: date) -> dict[str, AccordoVersamento]:
    """Accordi in vigore nel giorno, per codice."""
    per_codice: dict[str, list[AccordoVersamento]] = {}
    for v in versioni:
        per_codice.setdefault(v.codice, []).append(v)
    vigenti = {c: in_vigore(vs, giorno) for c, vs in per_codice.items()}
    return {c: v for c, v in sorted(vigenti.items()) if v is not None}


def seleziona_accordo(versioni: Iterable[AccordoVersamento], *, giorno: date,
                      classe_id: str | None, richiesto: str | None = None
                      ) -> AccordoVersamento | None:
    """Accordo che vale per il versamento (vedi docstring del modulo)."""
    vigenti = vigenti_per_codice(versioni, giorno)
    if richiesto:
        scelto = vigenti.get(richiesto)
        if scelto is None:
            raise AccordoNonValido(richiesto, "non esiste per l'ente o non è in vigore nel giorno "
                                              "del versamento")
        if scelto.classe_id is not None and scelto.classe_id != classe_id:
            raise AccordoNonValido(richiesto, "non vale per la classe documentale del versamento")
        return scelto
    ambiti = (classe_id, None) if classe_id is not None else (None,)
    for ambito in ambiti:
        candidati = [v for v in vigenti.values() if v.classe_id == ambito]
        if len(candidati) > 1:
            raise AccordiAmbigui(
                "più accordi di versamento in vigore per lo stesso ambito: "
                + ", ".join(v.codice for v in candidati))
        if candidati:
            return candidati[0]
    return None


__all__ = [
    "AccordiAmbigui",
    "AccordoNonValido",
    "AccordoVersamento",
    "in_vigore",
    "seleziona_accordo",
    "vigenti_per_codice",
]
