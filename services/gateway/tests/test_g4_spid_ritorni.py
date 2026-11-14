# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g4-chiusura — gli errori SPID-002, SPID-010 e SPID-011 riportano la persona
alla pagina di accesso del frontend (BASE_URL/login#spid_errore=<codice>, 303)
invece di lasciarla su una risposta JSON del gateway. Il frontend legge già il
frammento (frontend/src/auth/AuthContext.tsx, leggiRitornoIdentita).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.auth import spid
from app.config import settings
from app.main import app
from httpx import ASGITransport, AsyncClient

BASE = "https://app.prova-g4.verumdoc.it"


@pytest.fixture
def base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "base_url", BASE + "/")  # la barra finale non raddoppia
    return BASE


def _ritorno_login(r: httpx.Response) -> str:
    """Codice d'errore del ritorno alla pagina di accesso (verifica la forma)."""
    assert r.status_code == 303, r.text
    url = urlparse(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}" == BASE
    assert url.path == "/login" and url.query == ""
    frammento = {k: v[0] for k, v in parse_qs(url.fragment).items()}
    assert set(frammento) == {"spid_errore"}
    assert "application/json" not in r.headers.get("content-type", "")
    return frammento["spid_errore"]


async def _get(percorso: str, **params: Any) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.verumdoc.it",
                           follow_redirects=False) as c:
        return await c.get(percorso, params=params)


async def test_spid_010_indirizzo_di_ritorno_non_ammesso(base_url, redis_finto) -> None:
    r = await _get("/api/v1/auth/spid/login", redirect_uri="https://malevolo.example/rubo")
    assert _ritorno_login(r) == "SPID-010"
    # Mai verso l'indirizzo non ammesso, né elencando quelli ammessi
    assert "malevolo" not in r.headers["location"]
    assert "app.verumdoc.it/cb" not in r.text
    assert redis_finto.dati == {}  # nessuno state creato


async def test_spid_011_provider_non_ammesso(base_url, redis_finto) -> None:
    r = await _get("/api/v1/auth/spid/login", provider="facebook",
                   redirect_uri="https://app.verumdoc.it/login")
    assert _ritorno_login(r) == "SPID-011"


async def test_spid_002_state_sconosciuto_al_callback(base_url, redis_finto) -> None:
    r = await _get("/api/v1/auth/spid/callback", state="mai-emesso", code="c")
    assert _ritorno_login(r) == "SPID-002"


async def test_spid_002_state_scaduto_dopo_un_login_vero(base_url, redis_finto,
                                                          monkeypatch) -> None:
    """Login avviato davvero (state in Redis), poi lo state scade: il callback
    torna alla pagina di accesso con SPID-002."""
    monkeypatch.setattr(settings, "verumdoc_dev_auth", False)
    monkeypatch.setattr(settings, "spid_aggregator_url", "https://aggregatore.test")
    monkeypatch.setattr(settings, "spid_client_id", "cid")
    monkeypatch.setattr(settings, "spid_issuer", "https://aggregatore.test/oidc")
    monkeypatch.setattr(settings, "spid_jwks_url", "https://aggregatore.test/jwks")
    avvio = await _get("/api/v1/auth/spid/login", redirect_uri="https://app.verumdoc.it/cb")
    assert avvio.status_code in (302, 307), avvio.text
    state = parse_qs(urlparse(avvio.headers["location"]).query)["state"][0]
    redis_finto.dati.pop(f"{spid._STATE_PREFIX}{state}")  # scadenza del TTL
    r = await _get("/api/v1/auth/spid/callback", state=state, code="c")
    assert _ritorno_login(r) == "SPID-002"


async def test_spid_002_anche_nel_mock_di_sviluppo(base_url, redis_finto, login_sviluppo) -> None:
    r = await _get("/api/v1/auth/spid/mock-callback", state="inesistente")
    assert _ritorno_login(r) == "SPID-002"


def test_catalogo_dei_ritorni_completo() -> None:
    """Ogni codice che il gateway può mettere nel frammento ha una descrizione."""
    for codice in ("SPID-002", "SPID-010", "SPID-011"):
        assert codice in spid.ERRORI_RITORNO
