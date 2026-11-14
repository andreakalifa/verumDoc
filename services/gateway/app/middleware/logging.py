# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Middleware: structured logging con structlog per ogni request."""
from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        request_id = getattr(request.state, "request_id", "-")
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=request.method, path=request.url.path
        )
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info("http_request",
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    client_ip=_ip(request))
        return response


def _ip(request: Request) -> str:
    """Revisione R10 (V1): IP reale dietro l'ALB, stessa regola del limite d'accesso
    (``X-Forwarded-For`` solo dai ``PROXY_FIDATI``)."""
    from app.config import settings
    from libs.shared.security.ip_cliente import ip_cliente

    return ip_cliente(request.client.host if request.client else None, request.headers,
                      settings.proxy_fidati)
