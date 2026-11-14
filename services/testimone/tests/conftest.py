# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixture dei test del testimone (R04, K3)."""

from __future__ import annotations

import os

# Prima di importare l'app (come negli altri servizi).
os.environ["ENV"] = "test"

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import urlsplit, urlunsplit  # noqa: E402

import pytest  # noqa: E402

from libs.catena import nota as nota_lib  # noqa: E402

PG_URL = os.environ.get("VD_TEST_DATABASE_URL", "")
SERVIZIO = Path(__file__).resolve().parents[1]

richiede_pg = pytest.mark.skipif(
    not PG_URL, reason="VD_TEST_DATABASE_URL non impostata: serve un PostgreSQL reale"
)

ORIGINE = "verumdoc.it/registro/v1/test"
TOKEN_REGISTRO = "token-di-prova-del-registro-" + "x" * 16


def _con_database(url: str, nome: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, f"/{nome}", p.query, p.fragment))


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def migra(url: str, revisione: str = "head") -> None:
    """``alembic upgrade`` del SOLO testimone sul database ``url``."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(SERVIZIO / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVIZIO / "alembic"))
    cfg.attributes["database_url"] = url
    cfg.attributes["senza_log"] = True
    command.upgrade(cfg, revisione)


@pytest.fixture
async def db_vuoto() -> AsyncIterator[str]:
    """Database NUOVO e vuoto (nessuno schema del gateway), eliminato a fine test."""
    import asyncpg

    nome = f"vdtst_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(_con_database(PG_URL, "postgres")))
    try:
        await admin.execute(f'CREATE DATABASE "{nome}"')
    finally:
        await admin.close()
    url = _con_database(PG_URL, nome)
    try:
        yield url
    finally:
        admin = await asyncpg.connect(_dsn(_con_database(PG_URL, "postgres")))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest.fixture
async def db_url(db_vuoto: str) -> AsyncIterator[str]:
    """Database del testimone migrato dal servizio stesso, con il pool inizializzato."""
    from libs.shared.db import close_db, init_db

    await asyncio.to_thread(migra, db_vuoto)
    await init_db(db_vuoto)
    try:
        yield db_vuoto
    finally:
        await close_db()


async def sql(url: str, istruzione: str, *argomenti: object) -> list:  # type: ignore[type-arg]
    import asyncpg

    conn = await asyncpg.connect(_dsn(url))
    try:
        return await conn.fetch(istruzione, *argomenti)
    finally:
        await conn.close()


@pytest.fixture
def chiavi() -> dict[str, str]:
    """Coppie di chiavi generate per il test (mai salvate nel repository)."""
    priv_reg, pub_reg = nota_lib.genera_chiave("verumdoc-registro")
    priv_tst, pub_tst = nota_lib.genera_chiave("verumdoc-testimone")
    return {
        "registro_privata": priv_reg,
        "registro": pub_reg,
        "testimone_privata": priv_tst,
        "testimone": pub_tst,
    }


@pytest.fixture
def configurazione(chiavi: dict[str, str]):  # type: ignore[no-untyped-def]
    from app.avvio import ConfigurazioneAvvio

    return ConfigurazioneAvvio(
        env="test",
        database_url="postgresql+asyncpg://ignorato/ignorato",
        chiave_privata=chiavi["testimone_privata"],
        nome_chiave="verumdoc-testimone",
        chiavi_registro=[chiavi["registro"]],
        origini=[ORIGINE],
        impronte_token=[hashlib.sha256(TOKEN_REGISTRO.encode()).hexdigest()],
        max_corpo_byte=65536,
        max_righe_prova=128,
    )


@pytest.fixture
async def client(db_url: str, configurazione):  # type: ignore[no-untyped-def]
    """Client HTTP sull'app con contesto caricato e database reale."""
    from app.avvio import carica_contesto
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    app.state.testimone = carica_contesto(configurazione, app.state.metriche_testimone)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.state.testimone = None


@pytest.fixture
def registro(chiavi: dict[str, str]):  # type: ignore[no-untyped-def]
    from tests.supporto import RegistroDiProva

    return RegistroDiProva(ORIGINE, chiavi["registro_privata"])


def intestazioni(token: str = TOKEN_REGISTRO) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "text/plain; charset=utf-8"}
