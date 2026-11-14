# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/atti_sistema.py — atti di sistema del ciclo di vita (R09, D24).

Riferimenti: decisione D24 (opzione A), REGOLE_SVILUPPO §8.1, §9.2 (una sola
funzione per una regola condivisa).

L'atto di RETTIFICA è un XML prodotto dal servizio ciclo-vita e versato come
documento principale di un PdV nuovo della stessa classe dell'originale. È
sempre ammesso come «tipo di sistema», qualunque siano i formati della classe:
nessun rifiuto inatteso (RGL-105, RGL-107/108, estensioni) e nessuna
configurazione da ricordare. Vale SOLO per il canale CICLO_VITA (token di canale
firmato con la chiave propria di ciclo-vita): un cliente che imitasse i
parametri della rettifica da un altro canale resta soggetto ai formati della
classe. Il resto della verifica (impronte, eseguibili, coerenza del MIME col
contenuto, metadati) vale come per ogni documento.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Canale dei versamenti del servizio ciclo-vita (gateway, contratto C2)
CANALE_CICLO_VITA = "CICLO_VITA"
#: Formato dell'atto di rettifica
FORMATO_ATTO = "application/xml"
#: Parametri che il servizio imposta sull'atto (riservati al servizio)
PARAMETRI_ATTO = ("rettifica_di", "pdv_rettificato", "pratica_rettifica")


def e_atto_di_sistema(canale: str | None, *, tipo: str | None, formato: str | None,
                      parametri: Mapping[str, Any] | None) -> bool:
    """True per l'atto XML di rettifica versato da ciclo-vita (vedi modulo)."""
    if canale != CANALE_CICLO_VITA or tipo != "PRINCIPALE":
        return False
    if (formato or "").strip().lower() != FORMATO_ATTO:
        return False
    valori = dict(parametri or {})
    return all(valori.get(p) for p in PARAMETRI_ATTO) and str(
        valori.get("identificativo_documento") or "").startswith("rettifica-")


__all__ = ["CANALE_CICLO_VITA", "FORMATO_ATTO", "PARAMETRI_ATTO", "e_atto_di_sistema"]
