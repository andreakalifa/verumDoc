# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/dependencies.py — dipendenze FastAPI d'autenticazione.

Riferimenti: F1.1.2; R03 b4 — contratto C2 (identità di canale), VD-C-099
(chiave API e quote per piano). Regole: REGOLE_SVILUPPO §19.1, §19.8, §14.3.

``get_current_user`` accetta tre credenziali:
  1. ``Authorization: Bearer <JWT utente>`` (iss, aud «verumdoc-api», firma, exp);
  2. ``Authorization: Bearer <token di canale>`` di un servizio ammesso
     (aud «verumdoc-canale», ``app.auth.canali``), con controllo sul database
     dell'utente responsabile (esistente, attivo, dell'ente, stesso ruolo);
  3. chiave API (``X-Api-Key`` oppure ``X-Api-Key-Id`` + ``X-Api-Key-Secret``).
Poi: revoca (utente disattivato, ente sospeso, ruolo cambiato), piano dell'ente
letto dal DATABASE (mai dal token) e limite di richieste al minuto del piano
(QTA-001).

L'utente restituito ai router e ai casi d'uso contiene sempre:
``tenant_id``, ``user_id``, ``ruolo``, ``scopes``, ``canale`` (REST per JWT e
chiave API, il canale del token per i token di canale; le rotte di un canale
diverso usano ``app.auth.canali.canale_effettivo``), ``piano`` e
``auth_method`` (jwt | api_key | token_canale).
"""
from __future__ import annotations

from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.api_key import autentica_chiave_api  # all'avvio: verifica API_KEY_PEPPER
from app.auth.canali import CANALE_PREDEFINITO, payload_da_token_canale
from app.auth.jwt_handler import verify_access_token
from app.auth.rate_limiter import IMPOSTAZIONI_QUOTE, check_rate_limit
from app.config import settings
from libs.shared.errori import ErroreApi
from libs.shared.security.jwt_rs256 import ErroreTokenNonValido
from libs.shared.security.quote import chiave_richieste, limiti_ente
from libs.shared.security.revoca import RevocaNonVerificabile, richiedi_non_revocato
from libs.shared.security.token_canale import verifica_utente_del_canale

_bearer = HTTPBearer(auto_error=False)
_redis_pool: aioredis.Redis | None = None

_WWW = {"WWW-Authenticate": "Bearer"}


def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis_pool


async def _da_canale(token: str, errore_se_non_canale: Exception) -> dict[str, Any]:
    """Token di canale (contratto C2) con il controllo stretto dell'utente responsabile."""
    try:
        canale = payload_da_token_canale(token)
    except ErroreTokenNonValido as exc:
        if exc.codice == "AUTH-002":
            raise ErroreApi("AUTH-002", exc.dettaglio, headers=_WWW) from None
        raise errore_se_non_canale from None
    try:
        await verifica_utente_del_canale(canale)
    except ErroreTokenNonValido as exc:
        raise ErroreApi("AUTH-003", exc.dettaglio, headers=_WWW) from None
    except RevocaNonVerificabile:
        raise ErroreApi("AUTH-503", "Riprovare tra poco.") from None
    return canale


async def _da_bearer(token: str) -> dict[str, Any]:
    """JWT utente; se non lo è, token di canale. Errore: quello del token utente,
    salvo un token di canale scaduto (AUTH-002)."""
    try:
        user = verify_access_token(token)
    except HTTPException as errore_utente:
        return await _da_canale(token, errore_utente)
    if str(user.get("sub") or "").startswith("servizio:"):
        # Token di servizio che passa anche la verifica utente (firmato con la
        # chiave del gateway, aud verumdoc-api): mai trattato come un utente.
        # Vale solo come token di canale ammesso; in esercizio i servizi hanno
        # una chiave propria e quella del gateway non è ammessa: rifiutato.
        return await _da_canale(token, ErroreApi(
            "AUTH-001", "Token di servizio non ammesso.", headers=_WWW))
    await richiedi_non_revocato(user)
    return {**user, "auth_method": user.get("auth_method") or "jwt"}


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_api_key_id: Annotated[str | None, Header(alias="X-Api-Key-Id")] = None,
    x_api_key_secret: Annotated[str | None, Header(alias="X-Api-Key-Secret")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    if credentials:
        user = await _da_bearer(credentials.credentials)
    else:
        chiave = await autentica_chiave_api(x_api_key, x_api_key_id, x_api_key_secret)
        if chiave is None:
            raise ErroreApi(
                "AUTH-000", "Fornire un Bearer token oppure la chiave API (X-Api-Key)",
                headers=_WWW,
            )
        # Revisione B5: responsabile disattivato o ente sospeso → 401 anche qui
        await richiedi_non_revocato(chiave)
        user = chiave
    tenant_id = str(user.get("tenant_id") or "anonymous")
    # VD-C-099: il piano viene dall'ente nel database (cache breve), mai dal token;
    # R03 i3: con le quote personalizzate dell'ente, che prevalgono sul piano
    limiti = await limiti_ente(user.get("tenant_id"), IMPOSTAZIONI_QUOTE)
    user = {**user, "piano": limiti.piano, "canale": user.get("canale") or CANALE_PREDEFINITO}
    await check_rate_limit(redis, chiave_richieste(tenant_id), limiti)
    return user


def require_role(*roles: str):
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if user.get("ruolo") not in roles:
            raise HTTPException(
                403,
                {"type": "AUTH-010", "title": "Permessi insufficienti",
                 "detail": f"Ruolo richiesto: {roles}"},
            )
        return user
    return _check


def require_scope(scope: str):
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        scopes = user.get("scopes", [])
        if scope not in scopes and "*" not in scopes:
            if user.get("ruolo") not in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
                raise HTTPException(
                    403,
                    {"type": "AUTH-011", "title": "Scope non autorizzato",
                     "detail": f"Scope {scope!r} richiesto"},
                )
        return user
    return _check

def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict | None:
    """Versione opzionale di get_current_user — ritorna None se non autenticato."""
    if not credentials:
        return None
    try:
        return verify_access_token(credentials.credentials)
    except HTTPException:
        return None
