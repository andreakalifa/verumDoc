# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/auth/jwt_handler.py
Verifica dei JWT RS256 emessi dal gateway (REGOLE_SVILUPPO.md §19.1, §19.3).

VD-C-001: ogni token è verificato con la chiave pubblica da configurazione
(JWT_PUBLIC_KEY, variabile d'ambiente/secret) su firma, iss, aud ed exp; ruolo
e tenant si leggono SOLO dal payload verificato.
VD-C-004: nessuna chiave da file; senza JWT_PUBLIC_KEY il servizio non parte,
tranne con ENV=test (coppia effimera in memoria per i test automatici).
Onboarding non firma token in esercizio: la chiave privata non serve.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.config import settings
from libs.shared.security.jwt_rs256 import (
    ChiaviJwt,
    ErroreChiaviJwt,
    ErroreTokenNonValido,
    carica_chiavi_jwt,
    firma_token,
    verifica_token,
)

# Caricate all'import: senza chiave pubblica valida il servizio non parte.
_CHIAVI: ChiaviJwt = carica_chiavi_jwt(
    env=settings.env,
    servizio=settings.service_name,
    chiave_pubblica=settings.jwt_public_key,
    chiave_privata=settings.jwt_private_key.get_secret_value(),
    richiede_privata=False,
)


def chiavi_jwt() -> ChiaviJwt:
    return _CHIAVI


def verify_access_token(token: str) -> dict[str, Any]:
    """Valida un JWT RS256 (firma, iss, aud, exp, tenant_id, ruolo) o solleva 401."""
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


def utente_da_authorization(authorization: str | None) -> dict[str, Any]:
    """Estrae e VERIFICA il Bearer token dall'header Authorization."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            {"type": "AUTH-000", "title": "Autenticazione obbligatoria",
             "detail": "Header Authorization: Bearer <token> richiesto"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_access_token(authorization.split(" ", 1)[1].strip())


def create_access_token(
    tenant_id: str | dict[str, Any],
    user_id: str | None = None,
    ruolo: str | None = None,
    scopes: list[str] | None = None,
    expiry_seconds: int | None = None,
    **kwargs: Any,
) -> str:
    """
    Firma un JWT — SOLO per i test (ENV=test con coppia effimera) o se una
    JWT_PRIVATE_KEY è configurata. In esercizio i token li emette il gateway.
    """
    if not _CHIAVI.privata_pem:
        raise ErroreChiaviJwt("onboarding non ha una chiave privata: non firma token")
    if isinstance(tenant_id, dict):
        payload = dict(tenant_id)
    else:
        payload = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "ruolo": ruolo,
            "scopes": scopes or [],
            **kwargs,
        }
    return firma_token(
        payload,
        chiave_privata_pem=_CHIAVI.privata_pem,
        emittente=settings.jwt_issuer,
        pubblico=settings.jwt_audience,
        durata_secondi=expiry_seconds or settings.jwt_expiry_seconds,
    )
