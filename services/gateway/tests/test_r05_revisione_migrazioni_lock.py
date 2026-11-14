# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R6: le migrazioni 038 e 039 non fanno
scansioni lunghe sotto ACCESS EXCLUSIVE.

Prima della correzione il passo 1d della 038 eseguiva ``VALIDATE CONSTRAINT``
della chiave esterna di ``documenti`` (e delle altre tabelle) nella STESSA
transazione in cui aveva appena fatto ``SET NOT NULL`` (ACCESS EXCLUSIVE fino al
commit): una scansione dell'intera tabella con letture e scritture bloccate; la
039 faceva lo stesso su ``catena_registro``. Ora ogni ``VALIDATE`` gira in
autocommit, senza alcun lock esclusivo tenuto dalla sessione, e i DDL con lock
esclusivo hanno ``lock_timeout``.

Prova su PostgreSQL vero: database vuoto → 035 → ``upgrade head`` con un
ascoltatore che, a ogni ``VALIDATE CONSTRAINT``, legge i lock esclusivi tenuti
dalla sessione in ``pg_locks`` (la prova riguarda chi tiene il lock, non la
durata della scansione: vale con tabelle vuote come piene).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

_VALIDATE = re.compile(r"ALTER TABLE public\.(\w+) VALIDATE CONSTRAINT (\w+)")
_LOCK_ESCLUSIVI = (
    "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
    "WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation' "
    "AND l.mode = 'AccessExclusiveLock' AND l.granted ORDER BY 1")


def test_validate_mai_sotto_lock_esclusivo(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "20261023_035")
    osservate: list[tuple[str, str, list[str], str]] = []

    def prima(conn: Any, cursore: Any, istruzione: str, parametri: Any, contesto: Any,
              multi: bool) -> None:
        trovato = _VALIDATE.search(istruzione)
        if trovato is None:
            return
        lettore = conn.connection.dbapi_connection.cursor()
        lettore.execute(_LOCK_ESCLUSIVI)
        tenuti = [r[0] for r in lettore.fetchall()]
        lettore.execute("SHOW lock_timeout")
        osservate.append((trovato.group(1), trovato.group(2), tenuti,
                          str(lettore.fetchall()[0][0])))

    event.listen(Engine, "before_cursor_execute", prima)
    try:
        command.upgrade(_config(), "head")
    finally:
        event.remove(Engine, "before_cursor_execute", prima)
    tabelle = {t for t, _, _, _ in osservate}
    assert {"documenti", "metadati_allegato5", "lifecycle_events", "fascicoli_aip",
            "catena_registro"} <= tabelle
    vincoli = {v for _, v, _, _ in osservate}
    assert {"fk_documenti_tenant", "ck_documenti_tenant_presente",
            "ck_catena_registro_tipo", "ck_catena_registro_pda"} <= vincoli
    con_lock = [(t, v, tenuti) for t, v, tenuti, _ in osservate if tenuti]
    assert con_lock == [], f"VALIDATE con lock esclusivi tenuti: {con_lock}"


def test_ddl_con_lock_esclusivo_hanno_lock_timeout(db_vuoto: str) -> None:  # noqa: F811
    """I DDL delle migrazioni R05 che prendono ACCESS EXCLUSIVE su tabelle
    esistenti girano con ``lock_timeout`` (predefinito 5 s)."""
    from alembic import command

    command.upgrade(_config(), "20261023_035")
    osservati: list[tuple[str, str]] = []
    esclusivi = re.compile(r"ALTER TABLE public\.(documenti|catena_registro|tenants|"
                           r"classi_documentali) (ALTER COLUMN|ENABLE ROW|ADD CONSTRAINT)")

    def prima(conn: Any, cursore: Any, istruzione: str, *argomenti: Any) -> None:
        trovato = esclusivi.search(istruzione)
        if trovato is None:
            return
        lettore = conn.connection.dbapi_connection.cursor()
        lettore.execute("SHOW lock_timeout")
        osservati.append((trovato.group(0), str(lettore.fetchall()[0][0])))

    event.listen(Engine, "before_cursor_execute", prima)
    try:
        command.upgrade(_config(), "head")
    finally:
        event.remove(Engine, "before_cursor_execute", prima)
    assert osservati
    senza = [i for i, valore in osservati if valore in ("0", "0ms")]
    assert senza == [], senza
    righe = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in righe] == ["20261114_060"]
