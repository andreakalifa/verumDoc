# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/auth/jwt_handler.py — verifica dei JWT RS256 del gateway.

VD-C-001/VD-C-004 (R02, g2-auth): modulo condiviso libs/shared/security/jwt_rs256.py
(modello be-sicurezza). Prima: python-jose senza verifica di iss/aud e chiavi
effimere generate in ogni ambiente diverso da production.

Regole: REGOLE_SVILUPPO.md §19.1, §19.3. Nessuna chiave da file; senza
JWT_PUBLIC_KEY il servizio non parte (tranne ENV=test, coppia effimera in
memoria). Il gateway emette `aud` e `iss`: si verificano entrambi, oltre a
firma ed exp; ruolo e tenant si leggono SOLO dal payload verificato.
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


def _segreto(valore: Any) -> str:
    """Accetta sia str sia pydantic.SecretStr."""
    if valore is None:
        return ""
    lettura = getattr(valore, "get_secret_value", None)
    return str(lettura() if callable(lettura) else valore)


_CHIAVI: ChiaviJwt = carica_chiavi_jwt(
    env=settings.env,
    servizio=getattr(settings, "service_name", "servizio"),
    chiave_pubblica=_segreto(getattr(settings, "jwt_public_key", "")),
    chiave_privata=_segreto(getattr(settings, "jwt_private_key", "")),
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
            emittente=getattr(settings, "jwt_issuer", "verumdoc.it"),
            pubblico=getattr(settings, "jwt_audience", "verumdoc-api"),
        )
    except ErroreTokenNonValido as exc:
        raise HTTPException(
            401,
            {"type": exc.codice, "title": exc.titolo, "detail": exc.dettaglio},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def create_access_token(
    tenant_id: str | dict[str, Any],
    user_id: str | None = None,
    ruolo: str | None = None,
    scopes: list[str] | None = None,
    expiry_seconds: int = 3600,
    **kwargs: Any,
) -> str:
    """Firma un JWT: solo nei test (ENV=test) o con JWT_PRIVATE_KEY configurata.
    In esercizio i token li emette il gateway; senza chiave privata: errore esplicito."""
    if not _CHIAVI.privata_pem:
        raise ErroreChiaviJwt("Chiave privata non configurata: questo servizio non firma token")
    payload = dict(tenant_id) if isinstance(tenant_id, dict) else {
        "tenant_id": tenant_id, "user_id": user_id, "ruolo": ruolo,
        "scopes": scopes or [], **kwargs,
    }
    return firma_token(
        payload,
        chiave_privata_pem=_CHIAVI.privata_pem,
        emittente=getattr(settings, "jwt_issuer", "verumdoc.it"),
        pubblico=getattr(settings, "jwt_audience", "verumdoc-api"),
        durata_secondi=expiry_seconds,
    )
