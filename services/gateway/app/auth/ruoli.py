# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/ruoli.py — permessi (scope) assegnati a ogni ruolo
nei token emessi dal gateway (accesso SPID/CIE, chiavi API).

Stessa tabella del login di sviluppo (app/routers/dev_auth.py, F1-05): i token
reali e quelli di sviluppo devono dare gli stessi permessi allo stesso ruolo.
"""
from __future__ import annotations

from libs.shared.security.verifica_chiavi_api import SCOPE_CHIAVE_API

SCOPES_PER_RUOLO: dict[str, list[str]] = {
    "SUPER_ADMIN":      ["*"],
    "ADMIN_TENANT":     ["versamento:ud", "versamento:doc", "read",
                         "distribution", "lifecycle", "admin:tenant"],
    "OPERATORE":        ["versamento:ud", "versamento:doc", "read", "lifecycle"],
    "VIEWER":           ["read"],
    "RSC_CONSERVATORE": ["*", "rsc:approve", "rsc:report"],
    # Chiavi API e token di canale (definizione unica in libs.shared.security)
    "API_SYSTEM":       list(SCOPE_CHIAVE_API),
}

#: Stati del cliente in cui i suoi utenti e sistemi NON possono accedere
#: (stessa regola dei fruitori PDND, app/routers/pdnd.py).
STATI_CLIENTE_BLOCCATI = frozenset({"SOSPESO", "DISATTIVATO"})


def scopes_del_ruolo(ruolo: str) -> list[str]:
    """Scope del ruolo; un ruolo sconosciuto non riceve alcun permesso (§14.2)."""
    return list(SCOPES_PER_RUOLO.get(ruolo, []))
