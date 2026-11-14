# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza B5 (g5-sicurezza): le API della catena rifiutano il
token valido di un utente disattivato o di un ente sospeso (401 AUTH-003),
su PostgreSQL reale.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from libs.shared.security.revoca import svuota_cache_revoche
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.token_di_prova import TENANT_CONSERVATORE, intestazioni

pytestmark = richiede_pg


@pytest.fixture(autouse=True)
def _cache_pulita():
    svuota_cache_revoche()
    yield
    svuota_cache_revoche()


async def _prepara(url: str, stato_ente: str, attivo: bool) -> str:
    tid, uid = uuid.UUID(TENANT_CONSERVATORE), uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, '12345678901', 'Conservatore', 't_cons', "
                   "CAST($2 AS public.tenant_stato), 'e@x.it')", tid, stato_ente)
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, 'rsc@x.it', 'R', 'S', 'RSC_CONSERVATORE', $3)",
              uid, tid, attivo)
    return str(uid)


async def _stato(uid: str) -> tuple[int, dict]:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/hashchain/status",
                        headers=intestazioni("RSC_CONSERVATORE", user_id=uid))
    return r.status_code, r.json()


@pytest.mark.parametrize("stato_ente,attivo", [("ATTIVO", False), ("SOSPESO", True)])
async def test_token_revocato_401(db_url, stato_ente, attivo) -> None:
    uid = await _prepara(db_url, stato_ente, attivo)
    stato, corpo = await _stato(uid)
    assert stato == 401 and corpo["codice"] == "AUTH-003"


async def test_utente_attivo_passa(db_url) -> None:
    uid = await _prepara(db_url, "ATTIVO", True)
    stato, _ = await _stato(uid)
    assert stato == 200
