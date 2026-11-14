# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
JWT RS256 del gateway — creazione e validazione token.
Riferimento piano: F1.1.2. Regole: REGOLE_SVILUPPO.md §19.1, §19.3, §19.4.

VD-C-004: le chiavi arrivano solo da variabili d'ambiente/secret
(JWT_PRIVATE_KEY, JWT_PUBLIC_KEY), mai da file. Il gateway firma i token: se la
chiave privata manca, l'import di questo modulo (e quindi l'avvio del servizio)
fallisce con ErroreChiaviJwt e un messaggio chiaro. Unica eccezione ENV=test
senza chiavi: coppia effimera in memoria per i test automatici.

Ogni token emesso porta iss, aud, iat, exp, jti; ogni token ricevuto è
verificato su firma, iss, aud ed exp (libs.shared.security.jwt_rs256).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.config import settings
from libs.shared.security.jwt_rs256 import (
    ChiaviJwt,
    ErroreTokenNonValido,
    carica_chiavi_jwt,
    firma_token,
    verifica_token,
)

# Caricate all'import: senza chiavi valide il servizio non parte.
_CHIAVI: ChiaviJwt = carica_chiavi_jwt(
    env=settings.env,
    servizio=settings.service_name,
    chiave_pubblica=settings.jwt_public_key,
    chiave_privata=settings.jwt_private_key.get_secret_value(),
    richiede_privata=True,
)


def chiavi_jwt() -> ChiaviJwt:
    """Chiavi in uso (la privata non compare nel repr)."""
    return _CHIAVI


def create_access_token(
    tenant_id: str | dict[str, Any],
    user_id: str | None = None,
    ruolo: str | None = None,
    scopes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    """
    Crea un JWT RS256. Due firme:
      create_access_token(payload_dict)
      create_access_token(tenant_id, user_id, ruolo, scopes=[], extra={})
    """
    if isinstance(tenant_id, dict):
        payload = dict(tenant_id)
    else:
        payload = {
            "tenant_id": tenant_id,
            "user_id":   user_id,
            "ruolo":     ruolo,
            "scopes":    scopes or [],
            **(extra or {}),
            **kwargs,
        }
    return firma_token(
        payload,
        chiave_privata_pem=_CHIAVI.privata_pem or "",
        emittente=settings.jwt_issuer,
        pubblico=settings.jwt_audience,
        durata_secondi=settings.jwt_expiry_seconds,
    )


def verify_access_token(token: str) -> dict[str, Any]:
    """
    Valida un JWT RS256 (firma, iss, aud, exp, claim tenant_id e ruolo).
    Ritorna il payload o solleva HTTPException 401 (AUTH-001 non valido, AUTH-002 scaduto).
    """
    try:
        return verifica_token(
            token,
            chiave_pubblica_pem=_CHIAVI.pubblica_pem,
            emittente=settings.jwt_issuer,
            pubblico=settings.jwt_audience,
        )
    except ErroreTokenNonValido as exc:
        raise HTTPException(
            401,
            {"type": exc.codice, "title": exc.titolo, "detail": exc.dettaglio},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
