# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/auth/dipendenze.py — autenticazione delle API di distribution.

R03 b4 (VD-C-099): oltre al Bearer del gateway si accetta la chiave API
(``X-Api-Key`` oppure ``X-Api-Key-Id`` + ``X-Api-Key-Secret``) con la verifica
condivisa ``libs.shared.security.verifica_chiavi_api``: un sistema versante può
cercare e chiedere un PdD senza passare dal portale. Stesse regole del gateway
(revoca, scadenza, ente sospeso) e stesso pepper (API_KEY_PEPPER): fuori da
development/test, senza pepper il servizio non parte (REGOLE §19.4).
"""
from __future__ import annotations

from app.auth.jwt_handler import verify_access_token
from app.config import settings
from libs.shared.security.autenticazione_servizi import crea_dipendenza_utente
from libs.shared.security.chiavi_api import carica_pepper

# Caricato all'import (all'avvio del servizio).
_PEPPER: bytes | None = carica_pepper(
    env=settings.env,
    valore=settings.api_key_pepper.get_secret_value(),
    servizio=settings.service_name,
)


def pepper() -> bytes | None:
    """Pepper in uso (letto a ogni richiesta: i test lo sostituiscono)."""
    return _PEPPER


#: Dipendenza FastAPI: payload dell'utente (Bearer) o del sistema (chiave API).
utente_corrente = crea_dipendenza_utente(verifica_jwt=verify_access_token, pepper=lambda: pepper())
