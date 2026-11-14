# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/infrastructure/cifratura.py
Cifratura a riposo dei segreti dei webhook — contratto C3, REGOLE §19.3.

R10: l'implementazione è in ``libs/shared/security/cifratura.py`` (una sola per
i webhook e i segreti TOTP del gateway); qui resta il nome usato dal servizio.
"""
from __future__ import annotations

from libs.shared.security.cifratura import (
    FORMATO,
    LUNGHEZZA_CHIAVE,
    LUNGHEZZA_NONCE,
    Cifratore,
    ErroreCifratura,
    contesto_segreto,
)

__all__ = ["FORMATO", "LUNGHEZZA_CHIAVE", "LUNGHEZZA_NONCE", "Cifratore", "ErroreCifratura",
           "contesto_segreto"]
