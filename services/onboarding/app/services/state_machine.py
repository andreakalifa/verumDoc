# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
State machine del ciclo di vita tenant.
Riferimento piano: F1.5.2

Transizioni ammesse:
  TRIAL      → ATTIVO     (attivazione manuale o automatica)
  TRIAL      → DISATTIVATO (trial scaduto / rifiuto)
  ATTIVO     → SOSPESO    (violazione SLA / morosità)
  ATTIVO     → DISATTIVATO (recesso / scadenza contratto)
  SOSPESO    → ATTIVO     (problema risolto)
  SOSPESO    → DISATTIVATO (sospensione definitiva)
  DISATTIVATO → (nessuna transizione — stato finale)
"""
from __future__ import annotations

from fastapi import HTTPException

from libs.shared.models import TenantStato

# Grafo delle transizioni: stato_corrente → set(stati_ammessi)
TRANSIZIONI: dict[TenantStato, set[TenantStato]] = {
    TenantStato.TRIAL:       {TenantStato.ATTIVO, TenantStato.DISATTIVATO},
    TenantStato.ATTIVO:      {TenantStato.SOSPESO, TenantStato.DISATTIVATO},
    TenantStato.SOSPESO:     {TenantStato.ATTIVO, TenantStato.DISATTIVATO},
    TenantStato.DISATTIVATO: set(),  # stato finale
}


def validate_transition(stato_corrente: TenantStato, stato_nuovo: TenantStato) -> None:
    """
    Verifica che la transizione di stato sia ammessa.
    Lancia HTTPException 422 se non valida.
    """
    ammessi = TRANSIZIONI.get(stato_corrente, set())
    if stato_nuovo not in ammessi:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "ONB-201",
                "title": "Transizione di stato non ammessa",
                "detail": (
                    f"Transizione {stato_corrente.value} → {stato_nuovo.value} non consentita. "
                    f"Transizioni ammesse da {stato_corrente.value}: "
                    f"{[s.value for s in ammessi] or 'nessuna (stato finale)'}."
                ),
            },
        )


def get_transizioni_ammesse(stato: TenantStato) -> list[str]:
    """Ritorna la lista dei valori di stato raggiungibili dallo stato corrente."""
    return [s.value for s in TRANSIZIONI.get(stato, set())]
