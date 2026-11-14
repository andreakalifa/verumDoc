# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/api_key.py — chiavi API dei sistemi versanti sul gateway.
Riferimento piano: F1.1.2; R03 b4 (VD-C-099). Regole: REGOLE_SVILUPPO.md §19.8, §19.4.

La verifica è UNICA per tutti i servizi pubblici:
``libs.shared.security.verifica_chiavi_api`` (forma della chiave, ricerca per
identificativo, HMAC con il pepper, scadenza, ente bloccato, ultimo uso). Qui
restano il pepper del processo e due funzioni storiche:

  - ``autentica_chiave_api(...)``: payload (ruolo API_SYSTEM) usato da
    ``app.auth.dependencies.get_current_user``;
  - ``validate_api_key(id, segreto)``: token interno firmato per chi lo usava
    (compatibilità; non è mai restituito al client).

Il sistema versante può presentarsi in due modi (stesso risultato):
  - X-Api-Key: <chiave completa>
  - X-Api-Key-Id: vd_<identificativo>  +  X-Api-Key-Secret: <chiave completa
    oppure solo il segreto>  (forma usata dall'SDK; l'id deve coincidere).

API_KEY_PEPPER: obbligatorio fuori da development/test, altrimenti il gateway
non parte (questo modulo è importato all'avvio da app.auth.dependencies). In
development/test senza pepper ogni uso di una chiave risponde 503 AUTH-030.
"""
from __future__ import annotations

from typing import Any

from app.auth.jwt_handler import create_access_token
from app.config import settings
from libs.shared.security.chiavi_api import carica_pepper
from libs.shared.security.verifica_chiavi_api import (
    ChiaveApiRifiutata,
    ChiaviApiNonDisponibili,
    VerificaChiaveNonEseguibile,
    chiave_da_intestazioni,
    errore_api_chiave,
    verifica_chiave_api,
)

# Caricato all'import: fuori da development/test senza pepper il gateway non parte.
_PEPPER: bytes | None = carica_pepper(
    env=settings.env,
    valore=settings.api_key_pepper.get_secret_value(),
    servizio=settings.service_name,
)


def pepper() -> bytes | None:
    """Pepper in uso (letto a ogni chiamata: i test lo sostituiscono)."""
    return _PEPPER


def componi_chiave(identificativo: str | None, segreto: str) -> str:
    """Chiave completa dalle intestazioni ricevute (vedi docstring del modulo)."""
    try:
        chiave = chiave_da_intestazioni(None, identificativo, segreto or "")
    except ChiaveApiRifiutata as exc:
        raise errore_api_chiave(exc) from None
    if chiave is None:
        raise errore_api_chiave(ChiaveApiRifiutata("AUTH-001", "chiave vuota"))
    return chiave


async def autentica_chiave_api(
    x_api_key: str | None, x_api_key_id: str | None, x_api_key_secret: str | None,
) -> dict[str, Any] | None:
    """Payload della chiave presentata, None se la richiesta non ne porta;
    ``ErroreApi`` AUTH-001/002/030/503 se non accettabile."""
    try:
        chiave = chiave_da_intestazioni(x_api_key, x_api_key_id, x_api_key_secret)
        if chiave is None:
            return None
        return (await verifica_chiave_api(chiave, pepper())).payload()
    except (ChiaveApiRifiutata, ChiaviApiNonDisponibili, VerificaChiaveNonEseguibile) as exc:
        raise errore_api_chiave(exc) from None


async def validate_api_key(client_id: str | None, client_secret: str) -> str:
    """
    Verifica la chiave e restituisce un token interno (ruolo API_SYSTEM, scope
    della chiave). 401 AUTH-001 se non valida/revocata/cliente bloccato,
    401 AUTH-002 se scaduta, 503 AUTH-030 se il pepper non è configurato.
    """
    payload = await autentica_chiave_api(None, client_id, client_secret or "")
    if payload is None:
        raise errore_api_chiave(ChiaveApiRifiutata("AUTH-001", "chiave vuota"))
    return create_access_token({k: v for k, v in payload.items() if k != "sub"})
