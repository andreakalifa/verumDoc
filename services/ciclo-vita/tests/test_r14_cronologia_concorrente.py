# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R14 (VD-C-121) — concorrenza della cronologia delle pratiche nel DATABASE.

Prima di R14 due righe di ``pratiche_stati`` scritte insieme sulla stessa base passavano
entrambe il trigger di continuità (ognuna non vede la riga non confermata dell'altra): la
sola difesa era il lock applicativo della pratica, e un ramo doppio si scopriva dopo,
dalla verifica, come discontinuità. Ora (PostgreSQL vero) il trigger scrive
``precedente_id`` (id dell'ultima riga, 0 per la prima, ignorando il valore del chiamante)
e l'indice unico ``uq_pratiche_stati_precedente`` fa fallire la seconda transazione con
``unique_violation`` appena la prima conferma: nessun ramo doppio, nemmeno da un processo
che salta il lock (provato con due connessioni dirette, senza alcun lock applicativo).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.infra_reale import _asyncpg_dsn, richiede_pg, sql
from tests.supporto import crea_ente

pytestmark = [pytest.mark.integration, richiede_pg]

_RIGA = ("INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, a_stato, ruolo, "
         "metodo, motivo, precedente_id) VALUES ($1, $2, $3, $4, 'ADMIN_TENANT', 'API', "
         "'prova di concorrenza', $5) RETURNING id, precedente_id")


async def _pratica(db_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = await crea_ente(db_url)
    pid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo) VALUES "
                      "($1, $2, 'SCARTO', 'BOZZA', 'pratica della prova di concorrenza')",
              pid, tenant)
    return pid, tenant


async def test_due_righe_sulla_stessa_base_la_seconda_fallisce(db_url) -> None:
    import asyncpg

    pid, tenant = await _pratica(db_url)
    [prima] = await sql(db_url, _RIGA, pid, tenant, None, "BOZZA", 999)
    assert prima["precedente_id"] == 0          # il valore del chiamante è ignorato
    uno = await asyncpg.connect(_asyncpg_dsn(db_url))
    due = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        t1, t2 = uno.transaction(), due.transaction()
        await t1.start()
        await t2.start()
        r1 = await uno.fetchrow(_RIGA, pid, tenant, "BOZZA", "PROPOSTA", None)
        assert r1["precedente_id"] == prima["id"]
        # la seconda vede ancora BOZZA come ultimo stato (continuità rispettata) e resta
        # in attesa sull'indice unico finché la prima non conferma
        seconda = asyncio.create_task(due.fetchrow(_RIGA, pid, tenant, "BOZZA", "RESPINTA",
                                                   None))
        await asyncio.sleep(0.3)
        assert not seconda.done()
        await t1.commit()
        with pytest.raises(asyncpg.UniqueViolationError):
            await seconda
        await t2.rollback()
    finally:
        await uno.close()
        await due.close()
    righe = await sql(db_url, "SELECT da_stato, a_stato, precedente_id FROM public.pratiche_stati"
                              " WHERE pratica_id = $1 ORDER BY id", pid)
    assert [(r["da_stato"], r["a_stato"]) for r in righe] == [(None, "BOZZA"),
                                                              ("BOZZA", "PROPOSTA")]


async def test_righe_in_sequenza_nella_stessa_transazione(db_url) -> None:
    """Più transizioni nella stessa transazione (passi automatici) restano ammesse."""
    import asyncpg

    pid, tenant = await _pratica(db_url)
    conn = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        async with conn.transaction():
            a = await conn.fetchrow(_RIGA, pid, tenant, None, "BOZZA", None)
            b = await conn.fetchrow(_RIGA, pid, tenant, "BOZZA", "PROPOSTA", None)
            c = await conn.fetchrow(_RIGA, pid, tenant, "PROPOSTA", "ESEGUIBILE", None)
    finally:
        await conn.close()
    assert (a["precedente_id"], b["precedente_id"], c["precedente_id"]) == (0, a["id"], b["id"])
