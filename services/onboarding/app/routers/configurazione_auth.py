# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/routers/configurazione_auth.py — chi chiama le API di
configurazione (contratto R05 Q1; REGOLE_SVILUPPO §14, §19.1, §19.8).

Due modi, stesso risultato (un ``Attore`` con ruolo ed ente):
  - ``Authorization: Bearer <jwt>``: token RS256 del gateway verificato con la
    chiave pubblica (firma, iss, aud, exp), come le altre API di onboarding;
  - chiave API (``X-Api-Key: vd_<id>_<segreto>`` oppure ``X-Api-Key-Id`` +
    ``X-Api-Key-Secret``), verificata dalla funzione condivisa
    ``libs.shared.security.verifica_chiavi_api``: SOLO per il validatore
    preventivo e il profilo esportato (ruolo ``API_SYSTEM`` del proprio ente).
In entrambi i casi poi la revoca (``libs.shared.security.revoca``).

Cosa può fare ogni ruolo lo decide ``libs.verumdoc_rules.configurazione``
(``motivo_blocco_*``): qui si stabilisce solo l'identità (REGOLE §14.9).
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Header

from app.auth.jwt_handler import verify_access_token
from app.config import settings
from libs.shared.errori import ErroreApi
from libs.shared.security.chiavi_api import carica_pepper
from libs.shared.security.revoca import richiedi_non_revocato
from libs.shared.security.verifica_chiavi_api import (
    ChiaveApiRifiutata,
    ChiaviApiNonDisponibili,
    VerificaChiaveNonEseguibile,
    chiave_da_intestazioni,
    errore_api_chiave,
    verifica_chiave_api,
)
from libs.verumdoc_rules.configurazione import Attore

_INTESTAZIONE_401 = {"WWW-Authenticate": "Bearer"}
#: Ruolo delle chiavi API (versare e validare per il proprio ente).
RUOLO_CHIAVE = "API_SYSTEM"

# All'import: fuori da development/test senza pepper il servizio non parte (§19.4)
_PEPPER: bytes | None = carica_pepper(
    env=settings.env, valore=settings.api_key_pepper.get_secret_value(),
    servizio=settings.service_name,
)


def imposta_pepper(pepper: bytes | None) -> None:
    """Solo per i test: pepper delle chiavi API."""
    global _PEPPER
    _PEPPER = pepper


def _uuid_o_none(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


def _attore(payload: dict[str, Any]) -> Attore:
    return Attore(ruolo=str(payload.get("ruolo") or ""),
                  tenant_id=_uuid_o_none(payload.get("tenant_id")),
                  user_id=_uuid_o_none(payload.get("user_id")))


async def _da_jwt(authorization: str) -> dict[str, Any]:
    schema, _, token = authorization.partition(" ")
    if schema.lower() != "bearer" or not token.strip():
        raise ErroreApi("AUTH-001", "Formato atteso: Authorization: Bearer <token>",
                        headers=_INTESTAZIONE_401)
    return verify_access_token(token.strip())


async def attore_jwt(authorization: str | None = Header(default=None)) -> Attore:
    """Dipendenza: utente con token verificato e non revocato (console)."""
    if not authorization:
        raise ErroreApi("AUTH-000", "Header Authorization: Bearer <token> richiesto",
                        headers=_INTESTAZIONE_401)
    payload = await _da_jwt(authorization)
    await richiedi_non_revocato(payload)
    return _attore(payload)


async def attore_jwt_o_chiave(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_api_key_id: str | None = Header(default=None, alias="X-Api-Key-Id"),
    x_api_key_secret: str | None = Header(default=None, alias="X-Api-Key-Secret"),
) -> Attore:
    """Dipendenza: utente (JWT) oppure sistema versante (chiave API), non revocato."""
    if authorization:
        payload = await _da_jwt(authorization)
    elif x_api_key or x_api_key_secret:
        try:
            chiave = chiave_da_intestazioni(x_api_key, x_api_key_id, x_api_key_secret)
            identita = await verifica_chiave_api(chiave or "", _PEPPER)
        except (ChiaveApiRifiutata, ChiaviApiNonDisponibili, VerificaChiaveNonEseguibile) as exc:
            raise errore_api_chiave(exc) from None
        payload = identita.payload()
    else:
        raise ErroreApi("AUTH-000", "Fornire un Bearer token oppure la chiave API (X-Api-Key)",
                        headers=_INTESTAZIONE_401)
    await richiedi_non_revocato(payload)
    attore = _attore(payload)
    if attore.ruolo == RUOLO_CHIAVE and attore.tenant_id is None:
        raise ErroreApi("AUTH-021")  # il cliente non si ricava: negato (§14.3)
    return attore


# Regole dichiarate per l'OpenAPI e il catalogo degli endpoint (R03 f2, REV-10)
attore_jwt.__verumdoc_autenticazione__ = {"metodi": ["jwt"]}  # type: ignore[attr-defined]
attore_jwt_o_chiave.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt", "chiave_api"]}
