# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/hashchain/app/auth/jwt_handler.py — verifica dei JWT RS256 del gateway.

g3-backend (REGOLE_SVILUPPO §19.1, §14.3): le API della catena non sono più
aperte a chiunque nel cluster. Ogni chiamata porta il token dell'utente
(inoltrato da dashboard-api); si verificano firma RS256, iss, aud, exp e la
presenza di tenant_id e ruolo con libs/shared/security/jwt_rs256.py.

- ``utente_verificato``: dipendenza FastAPI, 401 senza token o con token non valido.
- ``richiede_ruolo_verifica``: in più 403 se il ruolo non è SUPER_ADMIN o
  RSC_CONSERVATORE (verifica completa della catena).

Chiave: solo JWT_PUBLIC_KEY dall'ambiente (§19.3). Senza, il servizio non parte;
unica eccezione ENV=test, con una coppia effimera in memoria.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from app.config import settings
from libs.shared.security.jwt_rs256 import (
    ChiaviJwt,
    ErroreTokenNonValido,
    carica_chiavi_jwt,
    verifica_token,
)
from libs.shared.security.revoca import richiedi_non_revocato

#: Ruoli che possono avviare la verifica completa della catena.
RUOLI_VERIFICA: frozenset[str] = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})

_CHIAVI: ChiaviJwt = carica_chiavi_jwt(
    env=settings.env,
    servizio=settings.service_name,
    chiave_pubblica=settings.jwt_public_key.get_secret_value(),
    chiave_privata=None,
    richiede_privata=False,
)


def chiavi_jwt() -> ChiaviJwt:
    """Chiavi caricate (la privata esiste solo con ENV=test, per firmare nei test)."""
    return _CHIAVI


def _non_autenticato(codice: str, titolo: str, dettaglio: str = "") -> HTTPException:
    corpo = {"type": codice, "title": titolo}
    if dettaglio:
        corpo["detail"] = dettaglio
    return HTTPException(401, corpo, headers={"WWW-Authenticate": "Bearer"})


def verify_access_token(token: str) -> dict[str, Any]:
    """Payload verificato oppure 401 (AUTH-001 non valido, AUTH-002 scaduto)."""
    try:
        return verifica_token(
            token,
            chiave_pubblica_pem=_CHIAVI.pubblica_pem,
            emittente=settings.jwt_issuer,
            pubblico=settings.jwt_audience,
        )
    except ErroreTokenNonValido as exc:
        # Il motivo tecnico (es. firma) resta nel dettaglio, mai testo d'eccezione SQL
        raise _non_autenticato(exc.codice, exc.titolo, exc.dettaglio) from exc


async def utente_verificato(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Dipendenza: utente del token verificato (Authorization: Bearer <jwt>)."""
    if not authorization:
        raise _non_autenticato("AUTH-001", "Autenticazione richiesta",
                               "Header Authorization: Bearer <token> richiesto")
    schema, _, token = authorization.partition(" ")
    if schema.lower() != "bearer" or not token.strip():
        raise _non_autenticato("AUTH-001", "Token JWT non valido",
                               "Formato atteso: Authorization: Bearer <token>")
    payload = verify_access_token(token.strip())
    # Revisione B5: utente disattivato o ente sospeso → 401 anche con token valido
    await richiedi_non_revocato(payload)
    return payload


async def richiede_ruolo_verifica(
    utente: dict[str, Any] = Depends(utente_verificato),
) -> dict[str, Any]:
    """Dipendenza: solo SUPER_ADMIN e RSC_CONSERVATORE (403 HC-403 per gli altri)."""
    if utente.get("ruolo") not in RUOLI_VERIFICA:
        raise HTTPException(403, {
            "type": "HC-403",
            "title": "Ruolo non autorizzato alla verifica completa della catena",
            "detail": "Consentita solo a SUPER_ADMIN e RSC_CONSERVATORE",
        })
    return utente
