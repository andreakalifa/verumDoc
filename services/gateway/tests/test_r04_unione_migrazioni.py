# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 — unione dei rami 025/027/028/029 (migrazione ``20261023_030``) e guardia
della 029 sugli indici a cursore di ``audit_global`` partizionata.

Su PostgreSQL REALE, database nuovo e vuoto per ogni test:
  1. lo script ha UNA sola testa (``20261023_030``) che unisce i quattro rami;
  2. 028 PRIMA della 029: ``audit_global`` è già partizionata, la 029 non deve
     tentare ``CREATE INDEX CONCURRENTLY`` (vietato su tabella partizionata) e
     gli indici del cursore esistono comunque (li possiede la 028); il
     downgrade della sola 029 non li tocca;
  3. 029 PRIMA della 028: la 029 crea gli indici sulla tabella semplice, la 028
     li ricrea sulla partizionata; lo schema finale è identico nei due ordini;
  4. ``head`` → ``20261021_024`` → ``head``: reversibile e senza differenze dai
     modelli (``alembic check``).
"""
from __future__ import annotations

import asyncio

import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401

pytestmark = richiede_pg

_INDICI_CURSORE = ("idx_audit_global_cursore", "idx_audit_global_tenant_cursore")


def _upgrade(rev: str) -> None:
    from alembic import command

    command.upgrade(_config(), rev)


def _downgrade(rev: str) -> None:
    from alembic import command

    command.downgrade(_config(), rev)


def _stato_audit(url: str) -> tuple[str, set[str]]:
    righe = asyncio.run(_esegui(
        url,
        "SELECT c.relkind::text AS tipo FROM pg_class c JOIN pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname = 'audit_global'",
    ))
    indici = asyncio.run(_esegui(
        url,
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
        "AND tablename = 'audit_global'",
    ))
    return righe[0]["tipo"], {r["indexname"] for r in indici}


def _schema(url: str) -> set[tuple]:
    """Colonne e indici di ``public`` (impronta dello schema indipendente dall'ordine)."""
    colonne = asyncio.run(_esegui(
        url,
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public'",
    ))
    indici = asyncio.run(_esegui(
        url, "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"))
    return {("c", *tuple(r.values())) for r in colonne} | {("i", *tuple(
        r.values())) for r in indici}


def test_una_sola_testa_che_unisce_i_rami_r04() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_config())
    # R04 correzioni: la testa è 032 (031 → 032 sopra l'unione 030)
    assert script.get_heads() == ["20261114_060"]
    unione = script.get_revision("20261023_030")
    assert set(unione.down_revision) == {
        "20261023_025", "20261023_027", "20261023_028", "20261023_029"}


def test_029_dopo_028_non_usa_concurrently_su_audit_partizionata(db_vuoto: str) -> None:
    _upgrade("20261023_028")
    tipo, indici = _stato_audit(db_vuoto)
    assert tipo == "p"
    assert set(_INDICI_CURSORE) <= indici
    # Senza la guardia questa upgrade fallisce con
    # «cannot create index on partitioned table concurrently».
    _upgrade("20261023_029")
    tipo, indici = _stato_audit(db_vuoto)
    assert tipo == "p" and set(_INDICI_CURSORE) <= indici
    # Downgrade della sola 029: gli indici della 028 restano.
    _downgrade("20261023_029@-1")
    righe = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert {r["version_num"] for r in righe} == {"20261023_028"}
    _, indici = _stato_audit(db_vuoto)
    assert set(_INDICI_CURSORE) <= indici


def test_ordine_dei_rami_indifferente_e_ciclo_completo(db_vuoto: str) -> None:
    from alembic import command

    # Ordine A: 029 sulla tabella semplice, poi 028 la partiziona.
    _upgrade("20261023_029")
    tipo, indici = _stato_audit(db_vuoto)
    assert tipo == "r" and set(_INDICI_CURSORE) <= indici
    _upgrade("20261023_028")
    _upgrade("head")
    schema_a = _schema(db_vuoto)
    command.check(_config(confronto=True))

    # Ritorno a 024 (tutti i rami annullati) e ordine B: 028 poi il resto.
    _downgrade("20261021_024")
    tipo, indici = _stato_audit(db_vuoto)
    assert tipo == "r"
    assert not set(_INDICI_CURSORE) & indici
    _upgrade("20261023_028")
    _upgrade("head")
    assert _schema(db_vuoto) == schema_a
    command.check(_config(confronto=True))
    righe = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in righe] == ["20261114_060"]


@pytest.mark.parametrize("_", [0])
def test_template_corrente_alla_testa(_) -> None:
    """Il template indicato da VD_TEST_DATABASE_URL deve essere alla testa unica."""
    from tests.infra_reale import PG_TEMPLATE_URL

    righe = asyncio.run(_esegui(PG_TEMPLATE_URL, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in righe] == ["20261114_060"]


def test_script_offline_da_024_alla_testa_eseguibile_con_dati(db_vuoto: str) -> None:
    """``alembic upgrade 20261021_024:head --sql`` (finestra di manutenzione)
    gira davvero su un database 024 CON righe vecchie: le partizioni coprono le
    date esistenti (blocco DO della 028) e la 029 non usa CONCURRENTLY su
    ``audit_global``; il risultato è uguale ai modelli."""
    import contextlib
    import io
    import os
    import shutil
    import subprocess
    from urllib.parse import urlsplit

    from alembic import command

    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql non disponibile: lo script offline si esegue con psql")
    _upgrade("20261021_024")
    asyncio.run(_esegui(
        db_vuoto,
        "INSERT INTO public.audit_global (id, evento, dettagli, created_at) "
        "VALUES (gen_random_uuid(), 'prova', '{}', now() - interval '200 days')"))
    asyncio.run(_esegui(
        db_vuoto,
        "INSERT INTO public.outbox_eventi (topic, chiave, payload, creato_il) "
        "VALUES ('t', 'k', '{}', now() - interval '10 days')"))

    cfg = _config()
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    vecchio = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://utente:segreto@localhost/nessuno"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            command.upgrade(cfg, "20261021_024:head", sql=True)
    finally:
        os.environ["DATABASE_URL"] = vecchio
    script = buffer.getvalue()
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_audit_global" not in script

    parti = urlsplit(db_vuoto)
    esito = subprocess.run(
        [psql, "-h", parti.hostname or "127.0.0.1", "-p", str(parti.port or 5432),
         "-U", parti.username or "postgres", "-d", parti.path.lstrip("/"),
         "-v", "ON_ERROR_STOP=1", "-q"],
        input=script, capture_output=True, text=True, timeout=300, check=False)
    assert esito.returncode == 0, esito.stderr[-2000:]
    righe = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in righe] == ["20261114_060"]
    conteggi = asyncio.run(_esegui(
        db_vuoto, "SELECT (SELECT count(*) FROM public.audit_global) AS a, "
                  "(SELECT count(*) FROM public.outbox_eventi) AS o"))
    assert (conteggi[0]["a"], conteggi[0]["o"]) == (1, 1)
    tipo, indici = _stato_audit(db_vuoto)
    assert tipo == "p" and set(_INDICI_CURSORE) <= indici
    command.check(_config(confronto=True))
