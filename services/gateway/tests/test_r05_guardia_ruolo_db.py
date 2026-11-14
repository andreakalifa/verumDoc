# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (agente r05-sicurezza) — VD-C-113: in esercizio un servizio non parte se
``DATABASE_URL`` è di un superutente o del proprietario delle tabelle (la Row
Level Security non varrebbe). Provato su PostgreSQL vero con ruoli creati nel
test; la guardia non ha interruttori: i test d'integrazione entrano con il
login dei servizi (``libs/shared/db/ruolo_prova.py``).
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from libs.shared.db import close_db, get_engine, init_db
from libs.shared.db.guardia import (
    EsitoRuolo,
    RuoloDatabaseNonAmmesso,
    guardia_attiva,
    problemi_ruolo,
)
from libs.shared.db.ruolo_prova import url_con_utente, url_ruolo_servizio
from tests.infra_reale import _asyncpg_dsn, _con_database, db_url, richiede_pg  # noqa: F401


def test_guardia_attiva_fuori_da_sviluppo_e_test(monkeypatch):
    for env in ("production", "PRODUCTION", "staging", "prod", "qualsiasi"):
        assert guardia_attiva(env)
    for env in ("development", "test", " Test "):
        assert not guardia_attiva(env)
    monkeypatch.delenv("ENV", raising=False)
    assert guardia_attiva()  # predefinito dei servizi: production


@pytest.mark.parametrize(("esito", "uso", "atteso"), [
    (EsitoRuolo("app", False, False, None, 900_000), "servizio", 0),
    (EsitoRuolo("postgres", True, False, "public.pdv", 900_000), "servizio", 2),
    (EsitoRuolo("master", False, True, None, 900_000), "servizio", 1),
    (EsitoRuolo("proprietario", False, False, "public.pdv", 900_000), "servizio", 1),
    (EsitoRuolo("manutenzione", False, False, "public.outbox_eventi"), "manutenzione", 0),
    (EsitoRuolo("postgres", True, False, None), "manutenzione", 1),
    # Revisione 2 di R05 (S7): idle_in_transaction_session_timeout del login
    (EsitoRuolo("app", False, False, None, 0), "servizio", 1),
    (EsitoRuolo("app", False, False, None, None), "servizio", 1),
    (EsitoRuolo("app", False, False, None, 7_200_000), "servizio", 1),
])
def test_problemi_ruolo(esito, uso, atteso):
    assert len(problemi_ruolo(esito, uso)) == atteso


@pytest.fixture
async def proprietario(db_url):  # noqa: F811
    """Login NON superutente proprietario di una tabella di public (eliminato alla fine)."""
    ruolo = f"vd_prop_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        await admin.execute(f"CREATE ROLE {ruolo} LOGIN")
        await admin.execute("CREATE TABLE public.prova_proprieta_r05 (id int)")
        await admin.execute(f"ALTER TABLE public.prova_proprieta_r05 OWNER TO {ruolo}")
        yield ruolo
    finally:
        await admin.execute("DROP TABLE IF EXISTS public.prova_proprieta_r05")
        await admin.close()
        fuori = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
        try:
            await fuori.execute(f"DROP ROLE IF EXISTS {ruolo}")
        finally:
            await fuori.close()


@richiede_pg
async def test_in_produzione_superutente_e_proprietario_rifiutati(db_url, proprietario,  # noqa: F811
                                                                 monkeypatch):
    monkeypatch.setenv("ENV", "production")
    # il fixture imposta la manutenzione sul superutente (R13: anche lei si controlla)
    monkeypatch.delenv("DATABASE_URL_MANUTENZIONE", raising=False)
    await close_db()  # il fixture ha già inizializzato il servizio
    with pytest.raises(RuoloDatabaseNonAmmesso, match="superutente"):
        await init_db(db_url)  # superutente del server di prova
    with pytest.raises(RuoloDatabaseNonAmmesso, match="proprietario di public.prova_proprieta_r05"):
        await init_db(url_con_utente(db_url, proprietario))
    # il processo della manutenzione possiede tabelle per costruzione: ammesso
    await init_db(url_con_utente(db_url, proprietario), uso="manutenzione")
    await close_db()
    with pytest.raises(RuoloDatabaseNonAmmesso, match="superutente"):
        await init_db(db_url, uso="manutenzione")
    # il login dei servizi (membro di verumdoc_applicazione) parte
    await init_db(await url_ruolo_servizio(db_url))
    assert get_engine() is not None


@richiede_pg
async def test_in_sviluppo_e_test_la_guardia_non_si_applica(db_url, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ENV", "test")
    await close_db()
    await init_db(db_url)  # superutente: ammesso solo fuori dall'esercizio
    assert get_engine() is not None


@richiede_pg
async def test_in_produzione_login_senza_idle_in_transaction_rifiutato(db_url,  # noqa: F811
                                                                      monkeypatch):
    """Revisione 2 di R05 (S7): un login dei servizi senza
    ``idle_in_transaction_session_timeout`` non parte in esercizio (una transazione
    dimenticata tratterrebbe l'export dell'audit senza limite); con il valore
    impostato sul ruolo parte."""
    ruolo = f"vd_idle_{uuid.uuid4().hex[:8]}"
    fuori = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
    try:
        await fuori.execute(f"CREATE ROLE {ruolo} LOGIN IN ROLE verumdoc_applicazione")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("DATABASE_URL_MANUTENZIONE", raising=False)
        await close_db()
        with pytest.raises(RuoloDatabaseNonAmmesso, match="idle_in_transaction_session_timeout"):
            await init_db(url_con_utente(db_url, ruolo))
        await fuori.execute(f"ALTER ROLE {ruolo} SET idle_in_transaction_session_timeout "
                            "= '3h'")
        with pytest.raises(RuoloDatabaseNonAmmesso, match="10800000 ms"):
            await init_db(url_con_utente(db_url, ruolo))
        await fuori.execute(f"ALTER ROLE {ruolo} SET idle_in_transaction_session_timeout "
                            "= '15min'")
        await init_db(url_con_utente(db_url, ruolo))
        assert get_engine() is not None
        await close_db()
    finally:
        await fuori.execute(f"DROP ROLE IF EXISTS {ruolo}")
        await fuori.close()
