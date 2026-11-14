# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/auth/dipendenze.py — autenticazione con Bearer o chiave API.

R03 b4 (VD-C-099): le letture che un sistema versante deve poter fare senza
portale (elenco dei propri versamenti, elenco dei pacchetti con i metadati,
ricerca) accettano anche la chiave API (``X-Api-Key`` oppure ``X-Api-Key-Id``
+ ``X-Api-Key-Secret``), con la verifica condivisa
``libs.shared.security.verifica_chiavi_api`` e la stessa revoca dei token.

Il pepper (API_KEY_PEPPER, uguale a gateway e onboarding) si legge qui per non
toccare la configurazione del servizio: fuori da development/test, senza
pepper il servizio non parte (REGOLE §19.4).
"""
from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.jwt_handler import verify_access_token
from libs.shared.security.autenticazione_servizi import crea_dipendenza_utente
from libs.shared.security.chiavi_api import carica_pepper


class _ImpostazioniChiavi(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    env: str = "production"
    api_key_pepper: SecretStr = SecretStr("")


_IMP = _ImpostazioniChiavi()
_PEPPER: bytes | None = carica_pepper(
    env=_IMP.env, valore=_IMP.api_key_pepper.get_secret_value(), servizio="dashboard-api",
)


def pepper() -> bytes | None:
    """Pepper in uso (letto a ogni richiesta: i test lo sostituiscono)."""
    return _PEPPER


#: Dipendenza FastAPI: payload dell'utente (Bearer) o del sistema (chiave API).
utente_corrente = crea_dipendenza_utente(verifica_jwt=verify_access_token,
                                         pepper=lambda: pepper())
