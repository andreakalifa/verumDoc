# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-003 — endpoint lifecycle con autenticazione e ruolo (401/403, mai 500).
VD-C-036 (parte gateway) — la MIGRAZIONE non mette in eliminazione: è rifiutata
esplicitamente senza toccare il database.
R05 (contratto Q2, VD-C-072): l'avvio degli scenari è un RINVIO documentato al
servizio ciclo-vita: 410 LC-410 con il sostituto (prima 501 LC-501 / 503 LC-503).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.auth.dependencies import get_redis
from app.auth.jwt_handler import create_access_token
from app.config import settings
from app.main import app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from libs.shared.security.jwt_rs256 import firma_token

TENANT_A = "00000000-0000-0000-0000-0000000000aa"
AVVIA = f"/api/v1/lifecycle/{uuid.uuid4()}/avvia"
SCADENZE = "/api/v1/lifecycle/scadenze"
CORPO = {"scenario": "SCADENZA_ORDINARIA", "motivo": "Scadenza dei termini di legge"}


class SessioneCheRegistra:
    """Sessione finta: registra le query e restituisce un risultato vuoto."""

    def __init__(self) -> None:
        self.query: list[Any] = []

    async def execute(self, stmt: Any) -> Any:
        self.query.append(stmt)

        class _Risultato:
            def scalars(self) -> Any:
                class _S:
                    def all(self) -> list[Any]:
                        return []
                return _S()
        return _Risultato()


@pytest.fixture(autouse=True)
def senza_redis() -> Iterator[None]:
    """Il rate limit salta con Redis None: i test non richiedono Redis."""
    app.dependency_overrides[get_redis] = lambda: None
    yield
    app.dependency_overrides.pop(get_redis, None)


@pytest.fixture
def sessione(monkeypatch: pytest.MonkeyPatch) -> SessioneCheRegistra:
    from app.routers import lifecycle

    s = SessioneCheRegistra()

    @asynccontextmanager
    async def _get() -> AsyncIterator[SessioneCheRegistra]:
        yield s

    monkeypatch.setattr(lifecycle, "get_async_session", _get)
    return s


@pytest.fixture
def db_vietato(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import lifecycle

    def _vietato() -> None:
        raise AssertionError("il database non deve essere toccato")

    monkeypatch.setattr(lifecycle, "get_async_session", _vietato)


def _bearer(ruolo: str, tenant: str = TENANT_A) -> dict[str, str]:
    tok = create_access_token(tenant_id=tenant, user_id="u-1", ruolo=ruolo, scopes=["*"])
    return {"Authorization": f"Bearer {tok}"}


async def _chiama(metodo: str, url: str, **kw: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(metodo, url, **kw)


class TestAutenticazione:
    @pytest.mark.parametrize("metodo,url,corpo", [
        ("POST", AVVIA, CORPO), ("GET", SCADENZE, None),
    ])
    async def test_senza_token_401(self, metodo: str, url: str, corpo: Any, db_vietato) -> None:
        r = await _chiama(metodo, url, json=corpo)
        assert r.status_code == 401
        assert r.json()["codice"] == "AUTH-000"

    @pytest.mark.parametrize("metodo,url,corpo", [
        ("POST", AVVIA, CORPO), ("GET", SCADENZE, None),
    ])
    async def test_token_firmato_da_altra_chiave_401(
        self, metodo: str, url: str, corpo: Any, db_vietato
    ) -> None:
        altra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = altra.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
        tok = firma_token({"tenant_id": TENANT_A, "ruolo": "SUPER_ADMIN"},
                          chiave_privata_pem=pem, emittente=settings.jwt_issuer,
                          pubblico=settings.jwt_audience, durata_secondi=60)
        r = await _chiama(metodo, url, json=corpo, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401

    @pytest.mark.parametrize("ruolo", ["VIEWER", "OPERATORE", "ADMIN_TENANT", "API_SYSTEM"])
    async def test_avvia_ruolo_non_ammesso_403(self, ruolo: str, db_vietato) -> None:
        r = await _chiama("POST", AVVIA, json=CORPO, headers=_bearer(ruolo))
        assert r.status_code == 403

    @pytest.mark.parametrize("ruolo", ["VIEWER", "OPERATORE"])
    async def test_scadenze_ruolo_non_ammesso_403(self, ruolo: str, db_vietato) -> None:
        r = await _chiama("GET", SCADENZE, headers=_bearer(ruolo))
        assert r.status_code == 403


class TestAvvioRinviatoAlCicloDiVita:
    @pytest.mark.parametrize(("scenario", "sostituto"), [
        ("MIGRAZIONE", "/api/v1/ciclo-vita/pratiche/riversamento-uscita"),
        ("SCADENZA_ORDINARIA", "/api/v1/ciclo-vita/pratiche/scarto/proposta"),
        ("ANNULLAMENTO_VERSANTE", "/api/v1/ciclo-vita/pratiche/annullamento"),
        ("RETTIFICA", "/api/v1/ciclo-vita/pratiche/rettifica"),
    ])
    async def test_scenario_rinviato_410_senza_toccare_il_db(
        self, scenario: str, sostituto: str, db_vietato
    ) -> None:
        corpo = {"scenario": scenario, "motivo": "Motivo sufficientemente lungo"}
        r = await _chiama("POST", AVVIA, json=corpo, headers=_bearer("RSC_CONSERVATORE"))
        assert r.status_code == 410
        assert r.json()["codice"] == "LC-410"
        assert r.json()["dettagli"]["sostituto"] == {"metodo": "POST", "percorso": sostituto}
        assert r.headers["link"] == f'<{sostituto}>; rel="successor-version"'


class TestScadenzeTenant:
    async def test_admin_tenant_filtrato_sul_proprio_cliente(self, sessione) -> None:
        r = await _chiama("GET", SCADENZE, headers=_bearer("ADMIN_TENANT"))
        assert r.status_code == 200
        assert r.json()["total"] == 0
        parametri = sessione.query[0].compile().params
        assert uuid.UUID(TENANT_A) in parametri.values()

    async def test_conservatore_senza_filtro_cliente(self, sessione) -> None:
        r = await _chiama("GET", SCADENZE, headers=_bearer("RSC_CONSERVATORE"))
        assert r.status_code == 200
        parametri = sessione.query[0].compile().params
        assert uuid.UUID(TENANT_A) not in parametri.values()

    async def test_tenant_non_uuid_negato_non_500(self, db_vietato) -> None:
        r = await _chiama("GET", SCADENZE, headers=_bearer("ADMIN_TENANT", tenant="t-demo"))
        assert r.status_code == 403
        assert r.json()["codice"] == "AUTH-021"
