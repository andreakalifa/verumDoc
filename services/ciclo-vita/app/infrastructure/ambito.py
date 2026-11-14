# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/infrastructure/ambito.py — ambito d'ente delle transazioni (Row Level Security).

Riferimenti: VD-C-080 (R05, agente r05-sicurezza: ``libs.shared.db.ambito``),
REGOLE_SVILUPPO §14.2-§14.3.

Le richieste HTTP ricevono l'ambito dall'identità verificata (lo imposta la
verifica di ``libs/shared/security``). Il LAVORATORE del ciclo di vita opera
su più enti (pratiche di enti diversi, PdA, PdV): dichiara l'ambito del
conservatore con il motivo, una volta all'avvio di ogni giro.

Le tabelle delle pratiche hanno la politica per ente dalla migrazione
20261023_040 (unione R05).
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from libs.shared.db.ambito import ambito_conservatore


@contextlib.contextmanager
def ambito_lavoratore(motivo: str) -> Iterator[Any]:
    """Ambito «conservatore» per il lavoratore (motivo nel log della libreria)."""
    with ambito_conservatore(motivo) as ambito:
        yield ambito


__all__ = ["ambito_lavoratore"]
