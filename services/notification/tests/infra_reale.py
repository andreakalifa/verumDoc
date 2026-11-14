# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Infrastruttura reale per i test d'integrazione (REGOLE_SVILUPPO §23.3).

- PostgreSQL: ``VD_TEST_DATABASE_URL`` punta a un database già migrato con
  ``alembic upgrade head`` (usato come TEMPLATE, mai scritto dai test). Ogni
  test riceve una copia nuova (CREATE DATABASE ... TEMPLATE), poi eliminata.
  Senza la variabile i test d'integrazione si saltano (con motivo).
- S3: server moto in un thread su una porta libera (bucket effimeri).

Copia identica in gateway, validation, aip-builder, hashchain e notification (tests/infra_reale.py).
"""
from __future__ import annotations

import os
import socket
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

PG_TEMPLATE_URL = os.environ.get("VD_TEST_DATABASE_URL", "")

richiede_pg = pytest.mark.skipif(
    not PG_TEMPLATE_URL,
    reason="VD_TEST_DATABASE_URL non impostata: serve un PostgreSQL migrato (alembic upgrade head)",
)


def _con_database(url: str, nome: str) -> str:
    parti = urlsplit(url)
    return urlunsplit((parti.scheme, parti.netloc, f"/{nome}", parti.query, parti.fragment))


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def db_url():
    """URL SQLAlchemy di un database nuovo copiato dal template migrato."""
    import asyncpg

    from libs.shared.db import close_db, init_db
    from libs.shared.db.ruolo_prova import url_ruolo_servizio

    template = urlsplit(PG_TEMPLATE_URL).path.lstrip("/")
    nome = f"vdt_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_asyncpg_dsn(_con_database(PG_TEMPLATE_URL, "postgres")))
    try:
        await admin.execute(f'CREATE DATABASE "{nome}" TEMPLATE "{template}"')
    finally:
        await admin.close()
    url = _con_database(PG_TEMPLATE_URL, nome)
    # R05 (VD-C-080, VD-C-113): il codice dei servizi entra con un login NON
    # superutente membro di verumdoc_applicazione, come in esercizio (la RLS
    # vale); il superutente resta a sql() e alla manutenzione delle partizioni.
    precedente = os.environ.get("DATABASE_URL_MANUTENZIONE")
    os.environ["DATABASE_URL_MANUTENZIONE"] = url
    await init_db(await url_ruolo_servizio(url))
    try:
        yield url
    finally:
        await close_db()
        if precedente is None:
            os.environ.pop("DATABASE_URL_MANUTENZIONE", None)
        else:
            os.environ["DATABASE_URL_MANUTENZIONE"] = precedente
        admin = await asyncpg.connect(_asyncpg_dsn(_con_database(PG_TEMPLATE_URL, "postgres")))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
        finally:
            await admin.close()


async def sql(url: str, istruzione: str, *argomenti):
    """Esegue SQL diretto (asyncpg) sul database di test e restituisce le righe."""
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        return await conn.fetch(istruzione, *argomenti)
    finally:
        await conn.close()


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def s3_server():
    """Server S3 (moto) locale; restituisce l'endpoint."""
    from moto.server import ThreadedMotoServer

    porta = _porta_libera()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=porta, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{porta}"
    server.stop()


def client_s3(endpoint: str):
    import boto3

    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id="test",
        aws_secret_access_key="test", region_name="us-east-1",
    )


async def crea_tenant_e_classe(url: str, *, codice_classe: str = "fattura",
                               formati: str = '["application/pdf"]',
                               classe_del_tenant: bool = False,
                               attiva: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """Crea un cliente e (se serve) una classe; restituisce (tenant_id, classe_id)."""
    tenant = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "stato, "
              "email_referente) VALUES ($1, $2, 'Cliente di prova', $3, 'ATTIVO', "
              "'prova@example.org')",
              tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}")
    righe = await sql(url, "SELECT id FROM public.classi_documentali WHERE codice = $1 "
                           "AND tenant_id IS NULL", codice_classe)
    if righe and not classe_del_tenant:
        classe = righe[0]["id"]
        await sql(url, "UPDATE public.classi_documentali SET attiva = $2, formati_ammessi = "
                       "CAST($3 AS jsonb) WHERE id = $1", classe, attiva, formati)
        return tenant, classe
    classe = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.classi_documentali (id, tenant_id, codice, nome, "
              "formati_ammessi, attiva) "
              "VALUES ($1, $2, $3, $3, CAST($4 AS jsonb), $5)",
              classe, tenant if classe_del_tenant else None, codice_classe, formati, attiva)
    return tenant, classe
