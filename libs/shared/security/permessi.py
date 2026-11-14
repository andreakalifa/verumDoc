# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/permessi.py — permessi dell'identità verificata (R10, D30).

Riferimenti: catalogo puro ``libs.verumdoc_rules.permessi``; REGOLE_SVILUPPO
§14.8 (il ruolo dà il perimetro, il permesso l'azione), §14.9 (helper unici).

Un token di una persona con un RUOLO PERSONALIZZATO porta (gateway, emissione):
  - ``ruolo``: il ruolo base (OPERATORE o VIEWER), cioè il perimetro;
  - ``permessi``: i permessi del ruolo personalizzato concessi all'accesso
    (intersecati con i concedibili del conservatore in quel momento);
  - ``rp``: identificativo del ruolo personalizzato; ``pv``: versione dei
    permessi dell'utente (``users.permessi_versione``). La revoca
    (``libs.shared.security.revoca``) rifiuta il token se l'assegnazione o il
    ruolo cambiano: serve un nuovo accesso.
Senza ``rp`` valgono i permessi del ruolo fisso (modello).

Le azioni MAI delegabili (``MAI_DELEGABILI``) le compie solo un ruolo FISSO che
le ha nel modello: un token con ``rp`` non le ottiene mai, qualunque cosa
contenga ``permessi`` (seconda difesa dietro il formato del parametro e il
vincolo del database).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from libs.verumdoc_rules.permessi import MAI_DELEGABILI, permessi_del_modello

CLAIM_PERMESSI = "permessi"
CLAIM_RUOLO_PERSONALIZZATO = "rp"
CLAIM_VERSIONE_PERMESSI = "pv"
#: R16 (VD-C-153): versioni dei metodi d'accesso ammessi e dei permessi concedibili
#: (conservatore + ente, tabella ``versioni_accesso``) all'emissione del token
CLAIM_VERSIONE_METODI = "vm"
CLAIM_VERSIONE_CONCEDIBILI = "vc"


def ruolo_personalizzato(payload: Mapping[str, Any]) -> str | None:
    valore = payload.get(CLAIM_RUOLO_PERSONALIZZATO)
    return str(valore) if valore else None


def permessi_identita(payload: Mapping[str, Any]) -> frozenset[str]:
    """Permessi effettivi del token verificato."""
    if ruolo_personalizzato(payload):
        grezzi = payload.get(CLAIM_PERMESSI)
        if not isinstance(grezzi, (list, tuple)):
            return frozenset()  # token con ruolo personalizzato ma senza permessi: nessuno
        return frozenset(str(p) for p in grezzi) - MAI_DELEGABILI
    return permessi_del_modello(str(payload.get("ruolo") or ""))


def ha_permesso(payload: Mapping[str, Any], codice: str) -> bool:
    """True se l'identità può compiere l'azione ``codice``."""
    if codice in MAI_DELEGABILI:
        return ha_permesso_non_delegabile(payload, codice)
    return codice in permessi_identita(payload)


def ha_permesso_non_delegabile(payload: Mapping[str, Any], codice: str) -> bool:
    """Azioni mai delegabili: solo un ruolo fisso (nessun ``rp``) che le ha nel modello."""
    if ruolo_personalizzato(payload):
        return False
    return codice in permessi_del_modello(str(payload.get("ruolo") or ""))


def motivo_permesso_mancante(payload: Mapping[str, Any], codice: str) -> str | None:
    """Controllo CENTRALE per gli endpoint che autorizzano per ruolo base (revisione R10,
    V3): con un ruolo personalizzato serve anche il permesso dell'azione; con un ruolo
    fisso decide il controllo per ruolo già presente. ``None`` = ammesso."""
    if ruolo_personalizzato(payload) and not ha_permesso(payload, codice):
        return f"Il tuo ruolo non comprende questa azione (serve il permesso «{codice}»)"
    return None


__all__ = ["CLAIM_PERMESSI", "CLAIM_RUOLO_PERSONALIZZATO", "CLAIM_VERSIONE_PERMESSI",
           "ha_permesso", "ha_permesso_non_delegabile", "motivo_permesso_mancante",
           "permessi_identita", "ruolo_personalizzato"]
