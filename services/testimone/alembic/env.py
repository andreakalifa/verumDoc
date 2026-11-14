# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Alembic env.py — testimone (database proprio, schema «testimone»).

Perché Alembic DENTRO il servizio (e non DDL versionato a mano né le
migrazioni del gateway): il testimone deve poter girare in un altro account,
con un database che il gateway non vede (K3, PIANO §5.3). Alembic è lo stesso
strumento del resto del progetto (REGOLE §16.2/§16.5): revisioni con
``downgrade``, confronto modelli↔database (``alembic check``), tabella delle
versioni propria (``testimone.alembic_version``) che non si mescola con quella
del gateway anche se, in sviluppo, i due database stanno nello stesso cluster.

Più repliche che partono insieme: la migrazione gira sotto
``pg_advisory_xact_lock`` (chiave fissa del testimone), così una sola replica
applica le revisioni e le altre trovano lo schema già aggiornato.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

_qui = Path(__file__).resolve().parent  # .../services/testimone/alembic o /app/alembic
sys.path.insert(0, str(_qui.parent))
for _p in _qui.parents:
    if (_p / "libs").is_dir():
        sys.path.insert(0, str(_p))
        break

from app.infrastructure.modelli import SCHEMA, Base  # noqa: E402

config = context.config
if config.config_file_name is not None and not config.attributes.get("senza_log"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Chiave dell'advisory lock delle migrazioni del testimone (costante arbitraria).
CHIAVE_LOCK_MIGRAZIONI = 7_465_737_401

database_url = (
    config.attributes.get("database_url")
    or os.environ.get("DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
if not database_url:
    raise SystemExit("DATABASE_URL non impostata: il testimone non sa quale database migrare")


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:  # type: ignore[type-arg]
    """Riflette solo lo schema del testimone."""
    if type_ == "schema":
        return name == SCHEMA
    return True


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHIAVE_LOCK_MIGRAZIONI})
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema=SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
