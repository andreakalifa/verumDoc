# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza M2 (g5-sicurezza): il servizio di firma firma e marca
SOLO con il token di servizio di hashchain (audience «verumdoc-signature»,
scope «firma:servizio»). Ogni token utente, di qualunque ruolo e ente, riceve
403 e non ottiene alcuna busta; un token di servizio non vale come token utente.
"""
from __future__ import annotations

import base64
import hashlib

import pytest
from app.auth.jwt_handler import (
    chiavi_jwt,
    create_access_token,
    create_service_token,
    verify_access_token,
)
from app.main import app
from app.routers.signature import reimposta_servizio
from app.services.firma_cms import ServizioFirma
from app.services.sviluppo import FornitoreFirmaSviluppo, FornitoreMarcaSviluppo
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from libs.shared.security.jwt_rs256 import firma_token, firma_token_servizio

pytestmark = pytest.mark.asyncio

CONTENUTO = b"RdV falso da far firmare al conservatore"
IMPRONTA = hashlib.sha256(CONTENUTO).hexdigest()


@pytest.fixture
def servizio_sviluppo(chiavi):
    reimposta_servizio(ServizioFirma(FornitoreFirmaSviluppo(chiavi),
                                     FornitoreMarcaSviluppo(chiavi), produzione=False))
    yield
    reimposta_servizio(None)


async def _firma_e_marca(token: str) -> tuple:
    h = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        f = await c.post("/api/v1/signature/firma",
                         json={"content_b64": base64.b64encode(CONTENUTO).decode()}, headers=h)
        m = await c.post("/api/v1/signature/marca", json={"impronta_sha256_hex": IMPRONTA},
                         headers=h)
    return f, m


@pytest.mark.parametrize("ruolo", ["VIEWER", "OPERATORE", "API_SYSTEM", "ADMIN_TENANT",
                                   "SUPER_ADMIN", "RSC_CONSERVATORE"])
async def test_token_utente_non_firma_ne_marca_403(servizio_sviluppo, ruolo) -> None:
    tok = create_access_token(tenant_id="t-001", user_id="u-001", ruolo=ruolo, scopes=["*"])
    f, m = await _firma_e_marca(tok)
    assert (f.status_code, m.status_code) == (403, 403)
    assert "busta_b64" not in f.text and "token_b64" not in m.text


async def test_token_di_servizio_firma_e_marca(servizio_sviluppo) -> None:
    f, m = await _firma_e_marca(create_service_token("hashchain"))
    assert f.status_code == 200 and f.json()["busta_b64"]
    assert m.status_code == 200 and m.json()["token_b64"]


async def test_token_di_servizio_senza_scope_403(servizio_sviluppo) -> None:
    tok = firma_token_servizio("hashchain", chiave_privata_pem=chiavi_jwt().privata_pem,
                               emittente="verumdoc.it", durata_secondi=60, scopes=["read"])
    f, m = await _firma_e_marca(tok)
    assert (f.status_code, m.status_code) == (403, 403)


async def test_token_servizio_con_audience_utente_401(servizio_sviluppo) -> None:
    """Un token «di servizio» firmato con l'audience dei token utente non vale."""
    tok = firma_token({"servizio": "hashchain", "scopes": ["firma:servizio"]},
                      chiave_privata_pem=chiavi_jwt().privata_pem, emittente="verumdoc.it",
                      pubblico="verumdoc-api", durata_secondi=60)
    f, m = await _firma_e_marca(tok)
    assert (f.status_code, m.status_code) == (401, 401)


async def test_token_servizio_scaduto_o_di_altro_emittente_401(servizio_sviluppo) -> None:
    privata = chiavi_jwt().privata_pem
    scaduto = firma_token_servizio("hashchain", chiave_privata_pem=privata,
                                   emittente="verumdoc.it", durata_secondi=1, adesso=1_000_000)
    altro = firma_token_servizio("hashchain", chiave_privata_pem=privata,
                                 emittente="altro.it", durata_secondi=60)
    for tok in (scaduto, altro, "non-un-token"):
        f, m = await _firma_e_marca(tok)
        assert (f.status_code, m.status_code) == (401, 401)


async def test_token_servizio_vale_per_stato_e_verifica(servizio_sviluppo) -> None:
    h = {"Authorization": f"Bearer {create_service_token('hashchain')}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/signature/status", headers=h)).status_code == 200


async def test_token_servizio_non_vale_come_token_utente() -> None:
    with pytest.raises(HTTPException) as exc:
        verify_access_token(create_service_token("hashchain"))
    assert exc.value.status_code == 401
    # nessun messaggio della libreria JWT al client (B2)
    assert "audience" not in str(exc.value.detail).lower()


async def test_durata_massima_del_token_di_servizio() -> None:
    with pytest.raises(ValueError):
        firma_token_servizio("hashchain", chiave_privata_pem=chiavi_jwt().privata_pem,
                             emittente="verumdoc.it", durata_secondi=91 * 86400)
