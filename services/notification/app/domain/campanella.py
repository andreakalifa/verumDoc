# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/domain/campanella.py — categorie della campanella (R10, D32).

Modulo PURO: dal tipo e dai dati di un evento (``eventi_cliente`` o messaggio
Kafka, stessi campi) la categoria, il permesso che serve per vederlo, se è «da
fare» e un testo in parole semplici. Una sola tabella per la campanella e per la
posta per utente (REGOLE §9.2).

  PRATICHE               annullamento da approvare (seconda persona), scarto in attesa
                         dell'autorizzazione del produttore        → da fare
  PROPOSTE_SCARTO        proposta di scarto automatica in bozza     → da fare
  ANNULLAMENTI_RESPINTI  annullamento non applicato dal sistema (R09)
  QUOTE                  quota giornaliera all'80 o al 100%
  SCADENZE_STAGING       copie di transito di un versamento rifiutato in scadenza
  ALLARMI                anomalia d'integrità di un pacchetto dell'ente
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from libs.verumdoc_rules.parametri_servizio import CANALI_NOTIFICHE, CATEGORIE_NOTIFICHE
from libs.verumdoc_rules.permessi import (
    ANNULLAMENTO,
    ANNULLAMENTO_APPROVA,
    CONSULTA,
    SCARTO_AUTORIZZA,
    VERSA,
)

NOMI: dict[str, str] = {
    "PRATICHE": "Pratiche da approvare",
    "PROPOSTE_SCARTO": "Proposte di scarto da controllare",
    "ANNULLAMENTI_RESPINTI": "Annullamenti non applicati",
    "QUOTE": "Quote vicine al limite",
    "SCADENZE_STAGING": "Versamenti rifiutati in scadenza",
    "ALLARMI": "Allarmi d'integrità",
}
#: Permessi con cui una categoria riguarda la persona (uno basta)
PERMESSI_CATEGORIA: dict[str, tuple[str, ...]] = {
    "PRATICHE": (ANNULLAMENTO_APPROVA, SCARTO_AUTORIZZA),
    "PROPOSTE_SCARTO": (SCARTO_AUTORIZZA,),
    "ANNULLAMENTI_RESPINTI": (ANNULLAMENTO,),
    "QUOTE": (VERSA,),
    "SCADENZE_STAGING": (VERSA,),
    "ALLARMI": (CONSULTA,),
}
#: Tipi di evento (eventi_cliente) letti dalla campanella
TIPI: tuple[str, ...] = ("ciclo_vita.avviso", "pratica.cambio_stato", "quota.soglia_raggiunta",
                         "versamento.staging_scaduto", "integrita.anomalia")
#: Stati di una pratica che chiedono un'azione dell'ente (scarto: autorizzazione)
_STATI_DA_AUTORIZZARE = {("SCARTO", "PROPOSTA"), ("RESTITUZIONE", "PROPOSTA")}


@dataclass(frozen=True)
class Voce:
    categoria: str
    permesso: str
    da_fare: bool
    titolo: str
    collegamento: str
    pratica_id: str | None = None


def _testo(valore: Any) -> str:
    return "" if valore is None else str(valore)


def classifica(tipo: str, dati: Mapping[str, Any]) -> Voce | None:
    """Voce della campanella per l'evento, o None se l'evento non ci va."""
    if tipo == "ciclo_vita.avviso":
        sotto = _testo(dati.get("sottotipo"))
        pratica = _testo(dati.get("pratica_id")) or None
        link = f"/pratiche/{pratica}" if pratica else "/pratiche"
        if sotto == "ANNULLAMENTO_DA_APPROVARE":
            return Voce("PRATICHE", ANNULLAMENTO_APPROVA, True,
                        "Un annullamento aspetta la tua approvazione come seconda persona",
                        link, pratica)
        if sotto == "PROPOSTA_SCARTO":
            return Voce("PROPOSTE_SCARTO", SCARTO_AUTORIZZA, True,
                        "È pronta una proposta di scarto da controllare", link, pratica)
        if sotto == "ANNULLAMENTO_RESPINTO":
            return Voce("ANNULLAMENTI_RESPINTI", ANNULLAMENTO, False,
                        "Un annullamento non è stato applicato perché qualcosa è cambiato: "
                        "puoi chiederlo di nuovo", link, pratica)
        return None
    if tipo == "pratica.cambio_stato":
        coppia = (_testo(dati.get("tipo")), _testo(dati.get("a_stato")))
        if coppia in _STATI_DA_AUTORIZZARE:
            pratica = _testo(dati.get("pratica_id")) or None
            return Voce("PRATICHE", SCARTO_AUTORIZZA, True,
                        "Una pratica di scarto aspetta l'autorizzazione dell'ente",
                        f"/pratiche/{pratica}" if pratica else "/pratiche", pratica)
        return None
    if tipo == "quota.soglia_raggiunta":
        soglia = _testo(dati.get("soglia"))
        return Voce("QUOTE", VERSA, False,
                    f"I versamenti di oggi hanno raggiunto il {soglia or '…'}% della quota",
                    "/overview")
    if tipo == "versamento.staging_scaduto":
        return Voce("SCADENZE_STAGING", VERSA, False,
                    "Le copie di un versamento rifiutato stanno per essere eliminate: "
                    "rielaboralo se serve", _testo(dati.get("rielabora")) or "/versamenti")
    if tipo == "integrita.anomalia":
        return Voce("ALLARMI", CONSULTA, False,
                    "La verifica periodica ha trovato un problema su un pacchetto: il "
                    "conservatore lo sta già gestendo", "/aip")
    return None


def scelte_effettive(scelte: Mapping[str, Any], obbligatorie: tuple[str, ...]
                     ) -> dict[str, dict[str, bool]]:
    """Per categoria e canale: scelta dell'utente (predefinito: console sì, e-mail no)
    più gli avvisi obbligatori dell'ente (non spegnibili)."""
    risultato: dict[str, dict[str, bool]] = {}
    for cat in CATEGORIE_NOTIFICHE:
        utente = scelte.get(cat) if isinstance(scelte.get(cat), Mapping) else {}
        risultato[cat] = {}
        for canale in CANALI_NOTIFICHE:
            predefinito = canale == "CONSOLE"
            valore = utente.get(canale.lower(), predefinito)
            risultato[cat][canale] = bool(valore) or f"{cat}.{canale}" in obbligatorie
    return risultato


def scelte_spente_obbligatorie(scelte: Mapping[str, Any], obbligatorie: tuple[str, ...]
                               ) -> list[str]:
    """Avvisi obbligatori che le nuove scelte vorrebbero spegnere."""
    spenti = []
    for voce in obbligatorie:
        cat, _, canale = voce.partition(".")
        utente = scelte.get(cat) if isinstance(scelte.get(cat), Mapping) else {}
        if utente.get(canale.lower()) is False:
            spenti.append(voce)
    return spenti


__all__ = ["NOMI", "PERMESSI_CATEGORIA", "TIPI", "Voce", "classifica", "scelte_effettive",
           "scelte_spente_obbligatorie"]
