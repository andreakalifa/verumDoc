# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/autenticazione.py
Autenticazione delle API pubbliche di notification (eventi, webhook) — contratto C3.

Due modi, stesso risultato (un'``Identita`` con l'ente):
  - ``Authorization: Bearer <jwt>``: token utente RS256 del gateway, verificato
    con libs/shared/security/jwt_rs256 (firma, iss, aud, exp; REGOLE §19.1);
  - chiave API: ``X-Api-Key: vd_<id>_<segreto>`` oppure ``X-Api-Key-Id`` +
    ``X-Api-Key-Secret`` (forma dell'SDK), verificata dalla funzione condivisa
    ``libs/shared/security/verifica_chiavi_api`` (VD-C-099, agente b4): stesse
    regole del gateway (chiave attiva, non scaduta, ente non bloccato).
In entrambi i casi poi la revoca (libs/shared/security/revoca): utente
disattivato, ente bloccato, ruolo cambiato → 401 AUTH-003.

Ruoli (decisione da confermare, vedi rapporto b3):
  - gestione dei webhook: solo ADMIN_TENANT (amministratore dell'ente);
  - lettura degli eventi: ogni identità con un ruolo noto dell'ente, come lo
    stato dei versamenti nel gateway (che chiede solo l'autenticazione).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import Depends, Header

from app.config import settings
from app.domain.identita import Identita
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

logger = structlog.get_logger(__name__)

#: Ruoli noti (stessa tabella dei ruoli del gateway, app/auth/ruoli.py).
RUOLI_LETTURA_EVENTI = frozenset({
    "SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE", "VIEWER", "API_SYSTEM",
})
RUOLI_GESTIONE_WEBHOOK = frozenset({"ADMIN_TENANT"})
_INTESTAZIONE_401 = {"WWW-Authenticate": "Bearer"}

# All'import: senza chiave pubblica (fuori da ENV=test) o senza pepper (fuori
# da development/test) il servizio non parte (REGOLE §19.4).
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


async def _verifica_chiave_api(chiave: str) -> dict[str, Any]:
    """Payload della chiave (ruolo API_SYSTEM) dalla verifica condivisa;
    errori del catalogo (AUTH-001/002/030/503)."""
    try:
        identita = await verifica_chiave_api(chiave, _PEPPER)
    except (ChiaveApiRifiutata, ChiaviApiNonDisponibili, VerificaChiaveNonEseguibile) as exc:
        raise errore_api_chiave(exc) from None
    return identita.payload()


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
        except ChiaveApiRifiutata as exc:
            raise errore_api_chiave(exc) from None
        payload = await _verifica_chiave_api(chiave or "")
        metodo = "api_key"
    else:
        raise ErroreApi("AUTH-000", "Fornire un Bearer token oppure la chiave API (X-Api-Key)",
                        headers=_INTESTAZIONE_401)
    # Revoca (utente disattivato, ente bloccato, ruolo cambiato): stessa regola ovunque
    await richiedi_non_revocato(payload)
    tenant_id = _uuid_o_none(payload.get("tenant_id"))
    if tenant_id is None:
        # Il cliente non si ricava: negato (REGOLE §14.3)
        raise ErroreApi("AUTH-021")
    return Identita(tenant_id=tenant_id, user_id=_uuid_o_none(payload.get("user_id")),
                    ruolo=str(payload.get("ruolo") or ""), metodo=metodo,
                    api_key_id=payload.get("api_key_id"))


async def lettore_eventi(identita: Identita = Depends(identita_verificata)) -> Identita:
    """Dipendenza: chi può leggere gli eventi dell'ente (ruolo noto)."""
    if identita.ruolo not in RUOLI_LETTURA_EVENTI:
        raise ErroreApi("AUTH-010", "Ruolo non autorizzato alla lettura degli eventi")
    return identita


async def amministratore_webhook(identita: Identita = Depends(identita_verificata)) -> Identita:
    """Dipendenza: solo l'amministratore dell'ente gestisce i webhook."""
    if identita.ruolo not in RUOLI_GESTIONE_WEBHOOK or identita.metodo != "jwt":
        raise ErroreApi("AUTH-010", "La gestione dei webhook è riservata all'amministratore "
                                    "dell'ente (ADMIN_TENANT)")
    return identita


# R03 f2 (REV-10): regole dichiarate per l'OpenAPI e il catalogo degli endpoint
# (tools/docs/genera_openapi.py le legge dalle dipendenze reali di ogni rotta);
# tests/test_f2_autenticazione_dichiarata.py verifica che dicano il vero.
identita_verificata.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt", "chiave_api"]}
lettore_eventi.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt", "chiave_api"]}
amministratore_webhook.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt"], "ruoli": sorted(RUOLI_GESTIONE_WEBHOOK)}
