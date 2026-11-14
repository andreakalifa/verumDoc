# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test F1.1 — Middleware RequestId e StructuredLogging.
"""
from __future__ import annotations

import pytest
from app.middleware.logging import StructuredLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def minimal_app():
    """App FastAPI minimale con solo i middleware per test isolati."""
    app = FastAPI()
    app.add_middleware(StructuredLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    return app


class TestRequestIdMiddleware:
    def test_aggiunge_x_request_id_response(self, minimal_app):
        client = TestClient(minimal_app)
        r = client.get("/test")
        assert "x-request-id" in r.headers

    def test_propaga_x_request_id_client(self, minimal_app):
        client = TestClient(minimal_app)
        r = client.get("/test", headers={"X-Request-ID": "my-custom-id"})
        assert r.headers["x-request-id"] == "my-custom-id"

    def test_genera_uuid_se_assente(self, minimal_app):
        import uuid
        client = TestClient(minimal_app)
        r = client.get("/test")
        req_id = r.headers["x-request-id"]
        # Deve essere un UUID valido
        uuid.UUID(req_id)  # non solleva = valido
