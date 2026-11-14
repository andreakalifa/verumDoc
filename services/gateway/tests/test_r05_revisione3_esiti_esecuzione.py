# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 3 di R05 — rilievo T5: migrazione ``20261023_043`` (esiti di
esecuzione scritti solo dal lavoratore di ciclo-vita).

Su PostgreSQL vero, database vuoto → testa: ``verumdoc_applicazione`` (login
delle API, anche quella di ciclo-vita) può solo LEGGERE ``esiti_esecuzione``;
``verumdoc_ciclo_vita_lavoratore`` legge e inserisce; nessuno modifica o
cancella (privilegi e trigger); Row Level Security per ente. Downgrade e
nuovo upgrade riportano gli stessi privilegi.
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

RUOLI = ("verumdoc_applicazione", "verumdoc_ciclo_vita_lavoratore")


def _diritti(url: str) -> set[tuple[str, str]]:
    righe = asyncio.run(_esegui(url, """
        SELECT r.rolname, p FROM pg_roles r,
               unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
        WHERE r.rolname = ANY($1::text[])
          AND has_table_privilege(r.rolname, 'public.esiti_esecuzione', p)""", list(RUOLI)))
    return {(r["rolname"], r["p"]) for r in righe}


def test_043_privilegi_trigger_e_rls(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "head")
    atteso = {("verumdoc_applicazione", "SELECT"),
              ("verumdoc_ciclo_vita_lavoratore", "SELECT"),
              ("verumdoc_ciclo_vita_lavoratore", "INSERT")}
    assert _diritti(db_vuoto) == atteso
    rls = asyncio.run(_esegui(db_vuoto, """
        SELECT c.relrowsecurity, EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                                          AND p.polname = 'vd_ente_isolamento') AS politica
        FROM pg_class c WHERE c.oid = 'public.esiti_esecuzione'::regclass"""))[0]
    assert rls["relrowsecurity"] and rls["politica"]
    # trigger per riga contro UPDATE/DELETE (provati con righe nel test della
    # hashchain test_r05_revisione3_riscontro_esiti.py) e per istruzione contro TRUNCATE
    trigger = asyncio.run(_esegui(db_vuoto, """
        SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.esiti_esecuzione'::regclass
          AND NOT tgisinternal ORDER BY 1"""))
    assert [t["tgname"] for t in trigger] == ["trg_esiti_esecuzione_append_only",
                                              "trg_esiti_esecuzione_no_truncate"]
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        asyncio.run(_esegui(db_vuoto, "TRUNCATE public.esiti_esecuzione"))
    # downgrade e nuovo upgrade: stessi privilegi (GRANT/REVOKE idempotenti)
    command.downgrade(_config(), "20261023_042")
    assert asyncio.run(_esegui(db_vuoto, "SELECT to_regclass('public.esiti_esecuzione') "
                                         "AS t"))[0]["t"] is None
    command.upgrade(_config(), "head")
    assert _diritti(db_vuoto) == atteso
