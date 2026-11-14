# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/middleware/security.py — F5-05
Security headers AGID + OWASP Top 10 protection.
"""
from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Aggiunge security headers a tutte le risposte.
    Conformità: OWASP Security Headers, AGID Linee Guida Sicurezza.
    """
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Sicurezza HTTP
        response.headers["X-Content-Type-Options"]    = "nosniff"
        response.headers["X-Frame-Options"]           = "DENY"
        response.headers["X-XSS-Protection"]          = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"

        # HSTS (solo in produzione)
        from app.config import settings
        if settings.env.lower() == "production":  # MED-001 FIX: case-insensitive
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src \'self\'; "
                "script-src \'self\'; "
                "style-src \'self\' \'unsafe-inline\'; "
                "img-src \'self\' data:; "
                "connect-src \'self\';"
            )

        # Rimozione header che espongono info sul server
        # MutableHeaders non ha .pop() — usa del con guard
        if "server" in response.headers:
            del response.headers["server"]
        if "x-powered-by" in response.headers:
            del response.headers["x-powered-by"]

        return response
