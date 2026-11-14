# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (integrazione) — unione dei rami 036/037/038/039 (migrazione ``20261023_040``)
e Row Level Security sulle tabelle nuove dei rami (VD-C-080).

Su PostgreSQL REALE:
  1. lo script ha UNA sola testa (``20261114_060``, R18.2; prima la 059 di R17, la 058
     di R15 rinumerata all'unione, la 057 di R16 e la 056 di R14)
     dopo l'unione ``20261023_040`` dei quattro rami;
  2. le tabelle delle pratiche (037) e della configurazione (036) hanno la
     politica ``vd_ente_isolamento``: con il login dei servizi una query senza
     filtro vede solo il proprio ente; senza ambito nessuna riga;
  3. righe senza ente di ``configurazione_bozze`` e ``impostazioni_storico``
     (livello CONSERVATORE): solo l'ambito conservatore le vede o le scrive;
  4. la vista ``pratiche_canale_versamento`` è ``security_invoker``: non
     aggira la RLS di ``pratiche`` (con i diritti del proprietario mostrerebbe
     le pratiche di tutti gli enti);
  5. ``head`` → ``20261023_035`` → ``head`` reversibile e senza differenze dai
     modelli (``alembic check``).
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from libs.shared.db import get_async_session
from libs.shared.db.ambito import (
    NESSUN_AMBITO,
    ambito_conservatore,
    ambito_ente,
    imposta_ambito,
    ripristina_ambito,
)
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

#: testa dello script (revisione R05: 041 dopo l'unione) e revisione d'unione
TESTA = "20261114_060"
UNIONE = "20261023_040"
RAMI = {"20261023_036", "20261023_037", "20261023_038", "20261023_039"}
TABELLE_R05 = (
    "pratiche", "pratiche_stati", "pratiche_allegati", "pratiche_oggetti",
    "pratiche_esecuzioni", "accordi_versamento", "configurazione_proposte",
    "configurazione_bozze", "impostazioni_storico",
)


@contextmanager
def senza_ambito() -> Iterator[None]:
    token = imposta_ambito(NESSUN_AMBITO)
    try:
        yield
    finally:
        ripristina_ambito(token)


def test_una_sola_testa_che_unisce_i_rami_r05() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == [TESTA]
    assert set(script.get_revision(UNIONE).down_revision) == RAMI
    # 041 (correzioni della revisione 1) dopo l'unione, poi 042 (revisione 2, S3),
    # 043 (revisione 3, T5), 044 (revisione 4, U2)
    assert script.get_revision("20261023_041").down_revision == UNIONE
    assert script.get_revision("20261023_042").down_revision == "20261023_041"
    assert script.get_revision("20261023_043").down_revision == "20261023_042"
    assert script.get_revision("20261024_044").down_revision == "20261023_043"
    # R06: 045 (VD-C-119 e VD-C-127)
    assert script.get_revision("20261030_045").down_revision == "20261024_044"
    assert script.get_revision("20261030_046").down_revision == "20261030_045"   # R07
    assert script.get_revision("20261031_047").down_revision == "20261030_046"   # R08
    assert script.get_revision("20261101_048").down_revision == "20261031_047"   # R09
    assert script.get_revision("20261102_049").down_revision == "20261101_048"   # R10
    assert script.get_revision("20261103_050").down_revision == "20261102_049"   # R11
    assert script.get_revision("20261104_051").down_revision == "20261103_050"   # R12a
    assert script.get_revision("20261105_052").down_revision == "20261104_051"   # R12a
    assert script.get_revision("20261106_053").down_revision == "20261105_052"   # R12b
    assert script.get_revision("20261107_054").down_revision == "20261106_053"   # R13a
    assert script.get_revision("20261108_055").down_revision == "20261107_054"   # R13b
    assert script.get_revision("20261109_056").down_revision == "20261108_055"   # R14
    assert script.get_revision("20261110_057").down_revision == "20261109_056"   # R16
    assert script.get_revision("20261111_058").down_revision == "20261110_057"   # R15
    assert script.get_revision("20261112_059").down_revision == "20261111_058"   # R17
    assert script.get_revision(TESTA).down_revision == "20261112_059"   # R18.2


async def test_tabelle_r05_con_politica_e_vista_security_invoker(db_url):  # noqa: F811
    righe = await sql(db_url, """
        SELECT c.relname, c.relrowsecurity,
               EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                         AND p.polname = 'vd_ente_isolamento') AS politica
        FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace
          AND c.relname = ANY($1::text[])""", list(TABELLE_R05))
    stato = {r["relname"]: (r["relrowsecurity"], r["politica"]) for r in righe}
    assert set(stato) == set(TABELLE_R05)
    assert {t for t, v in stato.items() if v != (True, True)} == set()
    opzioni = await sql(db_url, "SELECT reloptions FROM pg_class "
                                "WHERE oid = 'public.pratiche_canale_versamento'::regclass")
    assert "security_invoker=true" in (opzioni[0]["reloptions"] or [])


async def _pratica(url: str, tenant: uuid.UUID, utente: uuid.UUID) -> uuid.UUID:
    pid = uuid.uuid4()
    await sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, creata_da) "
                   "VALUES ($1, $2, 'RETTIFICA', 'RICHIESTA', 'rettifica di prova', $3)",
              pid, tenant, utente)
    return pid


async def _conta(tabella: str, where: str = "", **parametri) -> int:
    async with get_async_session() as s:
        return int((await s.execute(text(
            f"SELECT count(*) FROM public.{tabella} {where}"),  # noqa: S608 — nomi costanti del test
            parametri)).scalar_one())


async def test_pratiche_e_vista_del_canale_solo_del_proprio_ente(db_url):  # noqa: F811
    a, _ = await crea_tenant_e_classe(db_url)
    b, _ = await crea_tenant_e_classe(db_url)
    ua, ub = uuid.uuid4(), uuid.uuid4()
    pa, pb = await _pratica(db_url, a, ua), await _pratica(db_url, b, ub)
    with ambito_ente(a):
        assert await _conta("pratiche") == 1
        assert await _conta("pratiche", "WHERE id = CAST(:p AS uuid)", p=str(pb)) == 0
        # La vista del canale (letta dal gateway con l'ambito del token di canale)
        assert await _conta("pratiche_canale_versamento") == 1
        assert await _conta("pratiche_canale_versamento",
                            "WHERE responsabile_id = CAST(:u AS uuid)", u=str(ub)) == 0
        # Scrittura verso un altro ente: WITH CHECK
        with pytest.raises(DBAPIError):
            async with get_async_session() as s:
                await s.execute(text(
                    "INSERT INTO public.pratiche (tenant_id, tipo, stato, motivo) VALUES "
                    "(CAST(:t AS uuid), 'SCARTO', 'BOZZA', 'scarto di prova')"), {"t": str(b)})
    with ambito_conservatore("test: tutte le pratiche"):
        assert await _conta("pratiche") == 2
        assert await _conta("pratiche_canale_versamento") == 2
    with senza_ambito():
        assert await _conta("pratiche") == 0
        assert await _conta("pratiche_canale_versamento") == 0
    assert pa != pb


async def test_configurazione_righe_senza_ente_solo_al_conservatore(db_url):  # noqa: F811
    a, _ = await crea_tenant_e_classe(db_url)
    b, _ = await crea_tenant_e_classe(db_url)
    for tenant, livello in ((None, "CONSERVATORE"), (a, "ENTE"), (b, "ENTE")):
        await sql(db_url, "INSERT INTO public.configurazione_bozze (risorsa, livello, tenant_id, "
                          "modifiche, motivo) VALUES ('impostazioni', $1, $2, '[]', 'prova')",
                  livello, tenant)
        await sql(db_url, "INSERT INTO public.impostazioni_storico (livello, tenant_id, codice, "
                          "motivo) VALUES ($1, $2, 'X', 'prova')", livello, tenant)
    await sql(db_url, "INSERT INTO public.accordi_versamento (tenant_id, codice, versione, "
                      "valido_dal, motivo) VALUES ($1, 'ACC', 1, CURRENT_DATE, 'prova'), "
                      "($2, 'ACC', 1, CURRENT_DATE, 'prova')", a, b)
    with ambito_ente(a):
        for tabella in ("configurazione_bozze", "impostazioni_storico", "accordi_versamento"):
            assert await _conta(tabella) == 1, tabella
        assert await _conta("configurazione_bozze", "WHERE tenant_id IS NULL") == 0
        # un ente non scrive righe del livello del conservatore
        with pytest.raises(DBAPIError):
            async with get_async_session() as s:
                await s.execute(text(
                    "INSERT INTO public.impostazioni_storico (livello, codice, motivo) "
                    "VALUES ('CONSERVATORE', 'Y', 'prova')"))
    with ambito_conservatore("test: configurazione di tutti"):
        assert await _conta("configurazione_bozze") == 3
        assert await _conta("impostazioni_storico") == 3
        assert await _conta("accordi_versamento") == 2
        async with get_async_session() as s:
            await s.execute(text(
                "INSERT INTO public.impostazioni_storico (livello, codice, motivo) "
                "VALUES ('CONSERVATORE', 'Y', 'prova')"))


def test_unione_reversibile_fino_a_035(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "head")
    command.downgrade(_config(), "20261023_035")
    righe = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in righe] == ["20261023_035"]
    vista = asyncio.run(_esegui(db_vuoto, "SELECT to_regclass('public.pratiche') AS t"))
    assert vista[0]["t"] is None
    command.upgrade(_config(), "head")
    righe = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in righe] == [TESTA]
    command.check(_config(confronto=True))
