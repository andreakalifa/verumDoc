# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test HTTP delle API di firma, marca e verifica — VD-C-010.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from app.main import app
from app.routers.signature import reimposta_servizio
from app.services.firma_cms import ServizioFirma
from app.services.sviluppo import FornitoreFirmaSviluppo, FornitoreMarcaSviluppo, a_asn1
from httpx import ASGITransport, AsyncClient

CONTENUTO = b"documento da firmare via API"


@pytest.fixture
def servizio_sviluppo(chiavi):
    s = ServizioFirma(
        FornitoreFirmaSviluppo(chiavi),
        FornitoreMarcaSviluppo(chiavi),
        produzione=False,
        radici_sviluppo=[a_asn1(chiavi.ca_cert)],
    )
    reimposta_servizio(s)
    yield s
    reimposta_servizio(None)


@pytest.fixture
def servizio_vuoto():
    reimposta_servizio(ServizioFirma(None, None, produzione=False))
    yield
    reimposta_servizio(None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_api_firma_e_verifica_round_trip(servizio_sviluppo, token_servizio):
    h = {"Authorization": f"Bearer {token_servizio}"}
    async with _client() as c:
        r = await c.post(
            "/api/v1/signature/firma",
            json={"content_b64": base64.b64encode(CONTENUTO).decode()},
            headers=h,
        )
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["qualificata"] is False
        assert any("NON QUALIFICATA" in a for a in corpo["avvisi"])
        v = await c.post(
            "/api/v1/signature/verifica", json={"busta_b64": corpo["busta_b64"]}, headers=h
        )
        assert v.status_code == 200
        assert v.json()["esito"] == "valida"
        busta = bytearray(base64.b64decode(corpo["busta_b64"]))
        pos = bytes(busta).find(CONTENUTO)
        busta[pos] ^= 0x01
        v2 = await c.post(
            "/api/v1/signature/verifica",
            json={"busta_b64": base64.b64encode(bytes(busta)).decode()},
            headers=h,
        )
        assert v2.json()["esito"] == "non_valida"


@pytest.mark.asyncio
async def test_api_marca_e_verifica(servizio_sviluppo, token_servizio):
    h = {"Authorization": f"Bearer {token_servizio}"}
    impronta = hashlib.sha256(CONTENUTO).hexdigest()
    async with _client() as c:
        r = await c.post(
            "/api/v1/signature/marca", json={"impronta_sha256_hex": impronta}, headers=h
        )
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["avviso"].startswith("MARCA DI SVILUPPO NON QUALIFICATA")
        v = await c.post(
            "/api/v1/signature/marca/verifica",
            json={"token_b64": corpo["token_b64"], "impronta_sha256_hex": impronta},
            headers=h,
        )
        assert v.json()["valida"] is True


@pytest.mark.asyncio
async def test_api_senza_token_401(servizio_sviluppo):
    async with _client() as c:
        for metodo, url in (
            ("post", "/api/v1/signature/firma"),
            ("post", "/api/v1/signature/verifica"),
            ("post", "/api/v1/signature/marca"),
            ("get", "/api/v1/signature/status"),
        ):
            kwargs = {"json": {}} if metodo == "post" else {}
            r = await getattr(c, metodo)(url, **kwargs)
            assert r.status_code in (401, 422), url
        r = await c.get("/api/v1/signature/status")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_api_base64_non_valido_422(servizio_sviluppo, token_servizio):
    async with _client() as c:
        r = await c.post(
            "/api/v1/signature/firma",
            json={"content_b64": "@@@"},
            headers={"Authorization": f"Bearer {token_servizio}"},
        )
    assert r.status_code == 422
    assert r.json()["codice"] == "SIG-422-BASE64"


@pytest.mark.asyncio
async def test_api_firma_non_configurata_503(servizio_vuoto, token_servizio):
    async with _client() as c:
        r = await c.post(
            "/api/v1/signature/firma",
            json={"content_b64": base64.b64encode(b"x").decode()},
            headers={"Authorization": f"Bearer {token_servizio}"},
        )
        s = await c.get(
            "/api/v1/signature/status", headers={"Authorization": f"Bearer {token_servizio}"}
        )
    assert r.status_code == 503
    assert r.json()["codice"] == "FIRMA-503-NON-CONFIGURATA"
    assert s.json()["firma_qualificata"] == "non configurata"
    assert s.json()["production_ready"] is False
