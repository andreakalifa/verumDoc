# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/tests/test_conflict_proxy_auth.py
I proxy della dashboard verso i servizi a valle inoltrano il token dell'utente.

g3-test (VD-C-030): prima questi test leggevano il sorgente cercando la stringa
``authorization=authorization``. Ora chiamano gli endpoint veri e osservano la
richiesta che parte davvero verso il servizio a valle (httpx.MockTransport al
posto della rete): URL, metodo, corpo e intestazione Authorization. Provano
anche che senza token (o con ruolo non ammesso) nessuna richiesta parte.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

_ASYNC_CLIENT_VERO = httpx.AsyncClient


def _token(ruolo: str = "OPERATORE", tenant: str | None = None) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    tok = create_access_token(tenant_id=tenant or str(uuid.uuid4()),
                              user_id=str(uuid.uuid4()), ruolo=ruolo)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def a_valle(monkeypatch) -> list[httpx.Request]:
    """Sostituisce la rete del proxy: registra ogni richiesta e risponde 200."""
    from app.routers import dashboard

    richieste: list[httpx.Request] = []

    def gestisci(req: httpx.Request) -> httpx.Response:
        richieste.append(req)
        return httpx.Response(200, json={"ok": True, "id": str(uuid.uuid4())})

    monkeypatch.setattr(dashboard.httpx, "AsyncClient",
                        lambda **kw: _ASYNC_CLIENT_VERO(
                            transport=httpx.MockTransport(gestisci), **kw))
    return richieste


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestConflictProxyAuthForwarding:
    """Dashboard proxy helpers devono forwardare Authorization."""

    def test_get_helper_accepts_authorization(self):
        import inspect

        from app.routers.dashboard import _get
        assert "authorization" in inspect.signature(_get).parameters

    def test_post_helper_accepts_authorization(self):
        import inspect

        from app.routers.dashboard import _post
        assert "authorization" in inspect.signature(_post).parameters

    async def test_distribution_proxy_forwards_auth(self, a_valle):
        """POST /distribution → distribution /richiedi con lo stesso token e canale WEB."""
        from app.config import settings

        h = _token("OPERATORE")
        corpo = {"aip_id": str(uuid.uuid4()), "tipo": "ESIBIZIONE"}
        async with _client() as c:
            senza = await c.post("/api/dashboard/distribution", json=corpo)
            assert senza.status_code == 401
            assert a_valle == []
            r = await c.post("/api/dashboard/distribution", json=corpo, headers=h)
        assert r.status_code == 200, r.text
        assert len(a_valle) == 1
        req = a_valle[0]
        assert req.method == "POST"
        assert str(req.url) == f"{settings.distribution_url}/api/v2/distribution/richiedi"
        assert req.headers["authorization"] == h["Authorization"]
        assert req.headers["x-verumdoc-canale"] == "WEB"
        assert json.loads(req.content) == corpo

    async def test_lista_tenants_forwards_auth(self, a_valle):
        """GET /tenants: solo SUPER_ADMIN; il token arriva a onboarding."""
        from app.config import settings

        h = _token("SUPER_ADMIN")
        async with _client() as c:
            negato = await c.get("/api/dashboard/tenants", headers=_token("ADMIN_TENANT"))
            assert negato.status_code == 403
            assert a_valle == []
            r = await c.get("/api/dashboard/tenants?stato=ATTIVO&limit=5", headers=h)
        assert r.status_code == 200, r.text
        req = a_valle[0]
        assert req.url.path == "/api/v1/onboarding/tenant"
        assert str(req.url).startswith(settings.onboarding_url)
        assert req.url.params["stato"] == "ATTIVO" and req.url.params["limit"] == "5"
        assert req.headers["authorization"] == h["Authorization"]

    async def test_crea_tenant_forwards_auth(self, a_valle):
        """POST /tenants: solo SUPER_ADMIN; corpo e token inoltrati a onboarding."""
        h = _token("SUPER_ADMIN")
        corpo = {"ragione_sociale": "Comune di Prova", "codice_fiscale": "00000000000"}
        async with _client() as c:
            negato = await c.post("/api/dashboard/tenants", json=corpo,
                                  headers=_token("RSC_CONSERVATORE"))
            assert negato.status_code == 403
            assert a_valle == []
            r = await c.post("/api/dashboard/tenants", json=corpo, headers=h)
        assert r.status_code == 201, r.text
        req = a_valle[0]
        assert req.method == "POST" and req.url.path == "/api/v1/onboarding/tenant"
        assert json.loads(req.content) == corpo
        assert req.headers["authorization"] == h["Authorization"]

    async def test_lista_classi_has_auth_and_forwards(self, a_valle):
        """GET /classi-documentali: senza token 401 (nessuna chiamata); con token inoltrato."""
        h = _token("OPERATORE")
        async with _client() as c:
            senza = await c.get("/api/dashboard/classi-documentali")
            falso = await c.get("/api/dashboard/classi-documentali",
                                headers={"Authorization": "Bearer non.un.token"})
            assert senza.status_code == 401 and falso.status_code == 401
            assert a_valle == []
            r = await c.get("/api/dashboard/classi-documentali?attiva=true", headers=h)
        assert r.status_code == 200, r.text
        req = a_valle[0]
        assert req.url.path == "/api/v1/onboarding/classi-documentali"
        assert req.url.params["attiva"] == "true"
        assert req.headers["authorization"] == h["Authorization"]

    async def test_hashchain_proxy_richiede_autenticazione(self, a_valle):
        """GET /hashchain: il proxy esige un token valido; poi interroga lo stato
        della catena con il limite richiesto, INOLTRANDO il token (hashchain lo
        verifica di nuovo: g3-backend). Prova con il servizio vero:
        test_g4_catena_end_to_end.py.
        """
        h = _token("VIEWER")
        async with _client() as c:
            senza = await c.get("/api/dashboard/hashchain")
            assert senza.status_code == 401
            assert a_valle == []
            r = await c.get("/api/dashboard/hashchain?limit=7", headers=h)
        assert r.status_code == 200, r.text
        req = a_valle[0]
        assert req.method == "GET" and req.url.path == "/api/v1/hashchain/status"
        assert req.url.params["limit"] == "7"
        assert req.headers["authorization"] == h["Authorization"]
