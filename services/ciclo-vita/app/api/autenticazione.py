# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/api/autenticazione.py — autenticazione delle API del ciclo di vita.

Riferimenti: REGOLE_SVILUPPO §19.1 (token sempre verificati), §19.8 (chiavi
API), §14.3 (ente non determinabile → negato); librerie condivise
``libs/shared/security`` (stesse regole di gateway, distribution, notification).

Credenziali: ``Authorization: Bearer <JWT utente>`` (RS256 del gateway: firma,
iss, aud, exp) oppure chiave API (``X-Api-Key`` o ``X-Api-Key-Id`` +
``X-Api-Key-Secret``); poi la revoca (utente disattivato, ente sospeso, ruolo
cambiato → 401 AUTH-003). Il token di canale NON vale qui.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Header

from app.config import settings
from app.domain.permessi import Identita
from libs.shared.errori import ErroreApi
from libs.shared.security.chiavi_api import carica_pepper
from libs.shared.security.jwt_rs256 import (
    ChiaviJwt,
    ErroreTokenNonValido,
    carica_chiavi_jwt,
    verifica_token,
)
from libs.shared.security.revoca import richiedi_non_revocato
from libs.shared.security.verifica_chiavi_api import (
    ChiaveApiRifiutata,
    ChiaviApiNonDisponibili,
    VerificaChiaveNonEseguibile,
    chiave_da_intestazioni,
    errore_api_chiave,
    verifica_chiave_api,
)

_INTESTAZIONE_401 = {"WWW-Authenticate": "Bearer"}

# All'import: senza chiave pubblica (fuori da ENV=test) o senza pepper (fuori da
# development/test) il servizio non parte (REGOLE §19.4).
_CHIAVI: ChiaviJwt = carica_chiavi_jwt(
    env=settings.env, servizio=settings.service_name,
    chiave_pubblica=settings.jwt_public_key.get_secret_value(),
    chiave_privata=None, richiede_privata=False,
)
_PEPPER: bytes | None = carica_pepper(
    env=settings.env, valore=settings.api_key_pepper.get_secret_value(),
    servizio=settings.service_name,
)


def chiavi_jwt() -> ChiaviJwt:
    """Chiavi caricate (la privata esiste solo con ENV=test, per firmare nei test)."""
    return _CHIAVI


def imposta_pepper(pepper: bytes | None) -> None:
    """Solo per i test: pepper delle chiavi API."""
    global _PEPPER
    _PEPPER = pepper


def _uuid_o_none(valore: Any) -> str | None:
    try:
        return str(UUID(str(valore)))
    except (TypeError, ValueError):
        return None


def _verifica_jwt(token: str) -> dict[str, Any]:
    try:
        return verifica_token(token, chiave_pubblica_pem=_CHIAVI.pubblica_pem,
                              emittente=settings.jwt_issuer, pubblico=settings.jwt_audience)
    except ErroreTokenNonValido as exc:
        raise ErroreApi(exc.codice, exc.dettaglio or None, headers=_INTESTAZIONE_401) from None


async def identita_verificata(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_api_key_id: str | None = Header(default=None, alias="X-Api-Key-Id"),
    x_api_key_secret: str | None = Header(default=None, alias="X-Api-Key-Secret"),
) -> Identita:
    """Dipendenza: identità verificata (JWT o chiave API), non revocata, con ente."""
    if authorization:
        schema, _, token = authorization.partition(" ")
        if schema.lower() != "bearer" or not token.strip():
            raise ErroreApi("AUTH-001", "Formato atteso: Authorization: Bearer <token>",
                            headers=_INTESTAZIONE_401)
        payload = _verifica_jwt(token.strip())
        metodo = "jwt"
    elif x_api_key or x_api_key_secret:
        try:
            chiave = chiave_da_intestazioni(x_api_key, x_api_key_id, x_api_key_secret)
            payload = (await verifica_chiave_api(chiave or "", _PEPPER)).payload()
        except (ChiaveApiRifiutata, ChiaviApiNonDisponibili, VerificaChiaveNonEseguibile) as exc:
            raise errore_api_chiave(exc) from None
        metodo = "api_key"
    else:
        raise ErroreApi("AUTH-000", "Fornire un Bearer token oppure la chiave API (X-Api-Key)",
                        headers=_INTESTAZIONE_401)
    await richiedi_non_revocato(payload)
    tenant_id = _uuid_o_none(payload.get("tenant_id"))
    if tenant_id is None:
        raise ErroreApi("AUTH-021")
    from libs.shared.security.permessi import permessi_identita, ruolo_personalizzato

    rp = ruolo_personalizzato(payload)
    return Identita(tenant_id=tenant_id, user_id=_uuid_o_none(payload.get("user_id")),
                    ruolo=str(payload.get("ruolo") or ""), metodo=metodo,
                    api_key_id=payload.get("api_key_id"), ruolo_personalizzato=rp,
                    permessi=permessi_identita(payload) if rp else None)


# Regole dichiarate per l'OpenAPI e il catalogo degli endpoint (tools/docs)
identita_verificata.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt", "chiave_api"]}

__all__ = ["chiavi_jwt", "identita_verificata", "imposta_pepper"]
