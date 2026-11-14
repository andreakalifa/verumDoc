# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/domain/identita.py
Identità verificata di chi chiama le API pubbliche (JWT utente o chiave API).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Identita:
    tenant_id: str
    user_id: str | None
    ruolo: str
    metodo: str  # "jwt" | "api_key"
    api_key_id: str | None = None
