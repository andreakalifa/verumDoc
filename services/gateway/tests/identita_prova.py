# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Identità di prova per i test che sostituiscono la dipendenza d'autenticazione
(``app.dependency_overrides[get_current_user]``).

R05 (VD-C-080): in esercizio la verifica del token imposta l'ambito d'ente della
Row Level Security (libs/shared/security/jwt_rs256.py); una dipendenza sostituita
salta la verifica, quindi deve impostarlo lei, come farebbe il token. La
dipendenza è ASINCRONA: FastAPI la esegue nel contesto della richiesta (una
funzione sincrona girerebbe in un thread e l'ambito andrebbe perso).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from libs.shared.db.ambito import imposta_ambito_identita


def dipendenza_utente(utente: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Dipendenza che restituisce ``utente`` e ne imposta l'ambito d'ente."""

    async def _utente() -> dict[str, Any]:
        imposta_ambito_identita(utente)
        return utente

    return _utente
