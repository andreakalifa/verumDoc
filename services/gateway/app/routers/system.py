# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Router system: health/live, health/ready, info
Riferimento piano: F1.1.4
"""
from __future__ import annotations

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auth.dependencies import get_current_user
from app.config import settings
from libs.shared.db import get_engine
from libs.shared.errori import RISPOSTE_ERRORE

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["System"])


@router.get("/api/v1/health/live", include_in_schema=False)
async def liveness() -> dict:
    return {"status": "alive", "service": settings.service_name}


@router.get("/api/v1/health/ready")
async def readiness() -> JSONResponse:
    """
    Pronto solo se il database risponde (VD-C-030).

    - DB non inizializzato (lifespan non eseguito) o non raggiungibile → 503
      ``not_ready``: un gateway senza DB non può accettare versamenti, quindi
      dichiararlo «ready» sarebbe falso.
    - Redis e OPA sono riportati in ``checks`` ma non bloccano.
    - Il testo delle eccezioni va solo nel log, mai al client (§18).
    """
    checks: dict[str, str] = {}
    ok = True

    try:
        engine = get_engine()
    except RuntimeError:
        engine = None
    if engine is None:
        checks["database"] = "non_inizializzato"
        ok = False
    else:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            logger.error("readiness_db_ko", errore=str(exc))
            checks["database"] = "errore"
            ok = False

    # Check Redis
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=1)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = "errore"
        logger.warning("redis_check_failed", error=str(exc))

    # R07 (D11, VD-C-034): niente controllo di OPA, motore di policy tolto
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not_ready",
            "service": settings.service_name,
            "checks": checks,
        },
    )


@router.get("/api/v1/info")
async def info() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "env": settings.env,
        "api_version": "v1",
        "formati_ammessi": ["application/pdf", "application/pkcs7-mime",
                            "application/xml", "image/tiff"],
    }


@router.get("/api/v1/me", summary="Identità della richiesta: ente, piano, chiave API",
            responses=RISPOSTE_ERRORE)
async def me(user: dict = Depends(get_current_user)) -> dict:
    """
    Chi sta chiamando. Con Bearer (utente o token di canale) **e con la chiave
    API** (R03 i1: un sistema versante ricava qui il proprio ente):

    - ``tenant_id``, ``user_id``, ``ruolo``, ``scopes`` (come prima);
    - ``piano`` (letto dall'ente nel database), ``auth_method``, ``canale``;
    - ``ente``: ``{id, ragione_sociale, stato}``;
    - ``chiave_api`` (solo con la chiave): ``{id, responsabile: {user_id, nome,
      cognome}}`` — l'utente responsabile della chiave (niente email: §19.9).

    401 AUTH-000 senza credenziali; database non raggiungibile: 503 AUTH-503,
    come la revoca.
    """
    ente, responsabile = await _ente_e_responsabile(
        user.get("tenant_id"), user.get("user_id") if user.get("api_key_id") else None)
    return {
        "tenant_id": user.get("tenant_id"),
        "user_id":   user.get("user_id"),
        "ruolo":     user.get("ruolo"),
        "scopes":    user.get("scopes", []),
        "piano":     user.get("piano"),
        "auth_method": user.get("auth_method") or "jwt",
        "canale":    user.get("canale"),
        "ente":      ente,
        "chiave_api": ({"id": str(user["api_key_id"]), "responsabile": responsabile}
                       if user.get("api_key_id") else None),
    }


async def _ente_e_responsabile(tenant_id: object, responsabile_id: object,
                               ) -> tuple[dict | None, dict | None]:
    """Ente e (per la chiave API) responsabile, in una sola query."""
    import uuid

    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import get_async_session
    from libs.shared.errori import ErroreApi

    try:
        tid = uuid.UUID(str(tenant_id))
    except (TypeError, ValueError):
        return None, None
    try:
        uid = uuid.UUID(str(responsabile_id)) if responsabile_id else None
    except (TypeError, ValueError):
        uid = None
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT t.id, t.ragione_sociale, t.stato, u.id AS uid, u.nome, u.cognome "
                "FROM public.tenants t LEFT JOIN public.users u "
                "  ON u.id = CAST(:u AS uuid) AND u.tenant_id = t.id "
                "WHERE t.id = CAST(:t AS uuid)"
            ), {"t": str(tid), "u": str(uid) if uid else None})).mappings().first()
    except SQLAlchemyError:
        logger.warning("me_lettura_ente_non_riuscita", tenant_id=str(tid))
        raise ErroreApi("AUTH-503", "Riprovare tra poco.") from None
    if riga is None:
        return None, None
    ente = {"id": str(riga["id"]), "ragione_sociale": riga["ragione_sociale"],
            "stato": str(riga["stato"]) if riga["stato"] is not None else None}
    responsabile = ({"user_id": str(riga["uid"]), "nome": riga["nome"],
                     "cognome": riga["cognome"]} if riga["uid"] else None)
    return ente, responsabile

