# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/db/config
Riferimento piano: F0.4 — Schema Database

Configurazione database tramite Pydantic BaseSettings.
Letta da variabili d'ambiente o file .env del servizio.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Configurazione connessione PostgreSQL async."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="VD_",        # cerca VD_DATABASE_URL, VD_DB_POOL_SIZE, ecc.
        case_sensitive=False,
    )

    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5432/verumdoc_dev"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_pre_ping: bool = True
    db_pool_recycle: int = 3600   # ricicla connessioni ogni ora
    db_pool_timeout: int = 30
    db_echo: bool = False         # True solo per debug query — mai in prod
