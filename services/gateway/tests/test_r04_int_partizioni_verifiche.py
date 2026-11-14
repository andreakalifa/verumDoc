# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — la manutenzione condivisa delle partizioni
(``libs/shared/db/partizioni.py``, eseguita da ogni servizio con relay) gestisce
anche ``verifiche_registro`` (migrazione 027): crea i mesi futuri fino
all'anticipo e non stacca MAI nulla (il registro delle verifiche è evidenza,
nessuna retention).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.shared.db import engine_manutenzione  # R05: ruolo proprietario
from libs.shared.db.partizioni import (
    POLITICHE,
    esegui_manutenzione_partizioni,
    inizio_periodo,
    nome_partizione,
    periodo_successivo,
)
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def _partizioni(url: str) -> list[str]:
    righe = await sql(url, "SELECT c.relname FROM pg_inherits i JOIN pg_class c "
                           "ON c.oid = i.inhrelid WHERE i.inhparent = "
                           "'public.verifiche_registro'::regclass ORDER BY 1")
    return [r["relname"] for r in righe]


def test_politica_verifiche_registro_senza_retention() -> None:
    politica = {p.tabella: p for p in POLITICHE}["verifiche_registro"]
    assert politica.periodo == "mese" and politica.anticipo == 3
    assert politica.codice_retenzione is None and not politica.protezione_audit


async def test_manutenzione_condivisa_crea_i_mesi_futuri_del_registro(db_url):
    prima = await _partizioni(db_url)
    adesso = datetime.now(UTC) + timedelta(days=200)  # oltre i 3 mesi creati dalla 027
    esito = await esegui_manutenzione_partizioni(engine_manutenzione(), adesso, POLITICHE)
    assert esito is not None
    assert esito.create["verifiche_registro"] >= 1
    assert esito.future["verifiche_registro"] == 3
    assert esito.staccate["verifiche_registro"] == 0
    ultimo = inizio_periodo("mese", adesso)
    for _ in range(3):
        ultimo = periodo_successivo("mese", ultimo)
    dopo = await _partizioni(db_url)
    assert nome_partizione("verifiche_registro", "mese", ultimo) in dopo
    assert set(prima) <= set(dopo)  # nessun distacco
    # una riga nel mese futuro finisce nella sua partizione
    tenant, _ = await crea_tenant_e_classe(db_url)
    istante = ultimo + timedelta(days=2)
    await sql(db_url, "INSERT INTO public.verifiche_registro (verificato_il, aip_id, tenant_id, "
                      "componente, esito, esecutore) VALUES ($1, $2, $3, 'PDA', 'OK', 'prova')",
              istante, uuid.uuid4(), tenant)
    dove = await sql(db_url, "SELECT tableoid::regclass::text AS t FROM public.verifiche_registro")
    assert [r["t"] for r in dove] == [
        f"partizioni.{nome_partizione('verifiche_registro', 'mese', ultimo)}"]
