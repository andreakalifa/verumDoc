# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Alembic env.py — VerumDoc Gateway service
Riferimento piano: F0.4 — Schema Database
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Root monorepo nel path — compatibile sia con monorepo locale che Docker
_here = Path(__file__).resolve().parent  # /app/alembic o .../services/gateway/alembic
for _p in [_here.parent, *_here.parents]:
    if (_p / "libs" / "shared").is_dir():
        sys.path.insert(0, str(_p))
        break

from libs.shared.models import Base  # noqa: E402 — dopo l'aggiunta della radice a sys.path

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# URL: env var ha priorità, poi fallback all'ini, poi default dev.
# DATABASE_URL_MIGRAZIONI (R04 secondo giro, ruoli separati): il ruolo
# amministratore delle migrazioni quando DATABASE_URL è quello del servizio
# (compose: il gateway entra come vd_app, migra come vd_user).
database_url = (
    os.environ.get("DATABASE_URL_MIGRAZIONI")
    or os.environ.get("DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
    or "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5432/verumdoc_dev"
)


def _modalita_confronto() -> bool:
    """True quando Alembic confronta modelli e database (`alembic check`,
    `alembic revision --autogenerate`, o chi imposta
    ``config.attributes["confronto_modelli"] = True``, come i test).

    Perché serve: i modelli dichiarano ``schema="public"`` (REGOLE §16.4), ma
    se ``public`` è nel search_path PostgreSQL riflette tabelle e chiavi
    esterne senza schema e il confronto segnala decine di differenze false
    (FK «da rimuovere e ricreare»). In confronto il search_path viene quindi
    limitato a ``pg_catalog``: ogni oggetto di ``public`` torna qualificato e
    il confronto è esatto. Le migrazioni vere girano con il search_path
    normale.
    """
    if config.attributes.get("confronto_modelli"):
        return True
    opts = getattr(config, "cmd_opts", None)
    if opts is None:
        return False
    if getattr(opts, "autogenerate", False):
        return True
    cmd = getattr(opts, "cmd", None)
    return bool(cmd) and getattr(cmd[0], "__name__", "") == "check"


CONFRONTO = _modalita_confronto()


def include_name(name, type_, parent_names):
    """Riflette solo lo schema public (in confronto lo schema di default è
    pg_catalog e va escluso)."""
    if type_ == "schema":
        return name == "public" or (name is None and not CONFRONTO)
    return True


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        schema = getattr(object, "schema", None)
        return schema == "public" or (schema is None and not CONFRONTO and reflected)
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema="public",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema="public",
        compare_type=True,
        compare_server_default=True,
        # Revisione R05 (S1): una transazione PER REVISIONE, con l'aggiornamento
        # di ``alembic_version`` nella stessa transazione dell'ultimo segmento.
        # Un errore (es. ``lock_timeout``) annulla solo la revisione in corso;
        # le precedenti restano applicate E registrate, e un nuovo ``upgrade``
        # riparte da lì. Dentro una revisione i segmenti chiusi da un
        # ``autocommit_block`` (CONCURRENTLY, VALIDATE) sono resi riprendibili
        # dalla revisione stessa (controlli di catalogo, IF NOT EXISTS).
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # FIX: crea engine direttamente con database_url
    # (async_engine_from_config ignorava set_main_option)
    connect_args = {}
    if CONFRONTO and "asyncpg" in database_url:
        connect_args = {"server_settings": {"search_path": "pg_catalog"}}
    engine = create_async_engine(
        database_url, poolclass=pool.NullPool, connect_args=connect_args
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
