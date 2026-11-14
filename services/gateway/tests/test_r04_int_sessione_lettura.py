# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — ``get_async_session_lettura``: gli oggetti ORM letti restano
utilizzabili dopo il blocco (prima il ROLLBACK finale li «scadeva»:
DetachedInstanceError nell'elenco dei PdA della dashboard) e una scrittura nel
blocco resta vietata (transazione in sola lettura).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from libs.shared.db import get_async_session_lettura
from libs.shared.models import Tenant
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_oggetti_letti_utilizzabili_dopo_il_blocco(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with get_async_session_lettura() as session:
        letto = (await session.execute(select(Tenant).where(Tenant.id == tenant))).scalar_one()
    assert letto.id == tenant and letto.ragione_sociale  # nessun DetachedInstanceError


async def test_scrittura_rifiutata_nella_sessione_di_lettura(db_url):
    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with get_async_session_lettura() as session:
            await session.execute(text("UPDATE public.tenants SET ragione_sociale = 'x'"))
