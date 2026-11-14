# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 4 di R05 — rilievo U2: migrazione ``20261024_044`` (catena delle
autorizzazioni dello scarto verificabile dal lavoratore).

Su PostgreSQL vero, database vuoto → testa: trigger del piano congelato, della
cronologia continua e dell'impronta immutabile presenti; ``esiti_esecuzione``
accetta ``AUTORIZZAZIONI_VERIFICATE`` senza PdA e rifiuta ``ELIMINATO`` senza
PdA; downgrade alla 043 e nuovo upgrade. Il comportamento con le righe è
provato nel servizio (``services/ciclo-vita/tests/test_r05_revisione4_scarto.py``).
"""
from __future__ import annotations

import asyncio

import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

TRIGGER = {("pratiche", "trg_pratiche_autorizzazioni"),
           ("pratiche_oggetti", "trg_pratiche_oggetti_piano"),
           ("pratiche_stati", "trg_pratiche_stati_continuita")}


def _trigger(url: str) -> set[tuple[str, str]]:
    righe = asyncio.run(_esegui(url, """
        SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE NOT t.tgisinternal AND t.tgname = ANY($1::text[])""",
        [n for _, n in TRIGGER]))
    return {(r["relname"], r["tgname"]) for r in righe}


def _vincoli(url: str) -> dict[str, tuple[str, bool]]:
    righe = asyncio.run(_esegui(url, """
        SELECT conname, pg_get_constraintdef(oid) AS def, convalidated FROM pg_constraint
        WHERE conrelid = 'public.esiti_esecuzione'::regclass AND contype = 'c'"""))
    return {r["conname"]: (r["def"], r["convalidated"]) for r in righe}


def test_044_trigger_colonne_e_vincoli(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "head")
    assert _trigger(db_vuoto) == TRIGGER
    colonne = asyncio.run(_esegui(db_vuoto, """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'pratiche_oggetti'
          AND column_name IN ('esito_piano', 'motivo_piano') ORDER BY 1"""))
    assert [c["column_name"] for c in colonne] == ["esito_piano", "motivo_piano"]
    vincoli = _vincoli(db_vuoto)
    assert "AUTORIZZAZIONI_VERIFICATE" in vincoli["ck_esiti_esecuzione_esito"][0]
    assert vincoli["ck_esiti_esecuzione_esito"][1] and vincoli["ck_esiti_esecuzione_aip"][1]
    nullable = asyncio.run(_esegui(db_vuoto, """
        SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'public'
          AND table_name = 'esiti_esecuzione' AND column_name = 'aip_id'"""))[0]
    assert nullable["is_nullable"] == "YES"
    # downgrade alla 043 (nessuna riga AUTORIZZAZIONI_VERIFICATE) e nuovo upgrade
    command.downgrade(_config(), "20261023_043")
    assert _trigger(db_vuoto) == set()
    assert "ck_esiti_esecuzione_aip" not in _vincoli(db_vuoto)
    command.upgrade(_config(), "head")
    assert _trigger(db_vuoto) == TRIGGER
