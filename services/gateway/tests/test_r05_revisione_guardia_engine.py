# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R13: la guardia del ruolo del database
(VD-C-113) vale per TUTTE le URL del processo, non solo per ``DATABASE_URL``.

Prima della correzione ``DATABASE_URL_LETTURA`` (usata da notification e
dashboard-api) e gli engine aggiuntivi (``DATABASE_URL_DIRETTA``,
``DATABASE_URL_MANUTENZIONE``) non erano controllati: un superutente (o il
proprietario delle tabelle) su quelle URL leggeva ignorando la Row Level
Security e il servizio partiva lo stesso. La manutenzione delle partizioni è
l'unica eccezione dichiarata (può essere proprietaria, mai superutente).
"""
from __future__ import annotations

import pytest

from libs.shared.db import close_db, get_engine, init_db, lettura_su_replica
from libs.shared.db.guardia import RuoloDatabaseNonAmmesso
from libs.shared.db.ruolo_prova import url_con_utente, url_ruolo_servizio
from tests.infra_reale import db_url, richiede_pg  # noqa: F401
from tests.test_r05_guardia_ruolo_db import proprietario  # noqa: F401

pytestmark = richiede_pg


@pytest.fixture
async def in_produzione(db_url, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ENV", "production")
    for variabile in ("DATABASE_URL_LETTURA", "DATABASE_URL_DIRETTA",
                      "DATABASE_URL_MANUTENZIONE"):
        monkeypatch.delenv(variabile, raising=False)
    await close_db()  # il fixture ha già inizializzato il servizio
    return await url_ruolo_servizio(db_url)


async def test_replica_di_lettura_con_superutente_rifiutata(db_url, in_produzione):  # noqa: F811
    with pytest.raises(RuoloDatabaseNonAmmesso, match="superutente"):
        await init_db(in_produzione, database_url_lettura=db_url)
    with pytest.raises(Exception, match="init_db"):
        get_engine()  # niente avvio a metà
    await init_db(in_produzione, database_url_lettura=in_produzione)
    assert lettura_su_replica()


async def test_replica_di_lettura_dal_proprietario_rifiutata(db_url, in_produzione,  # noqa: F811
                                                            proprietario):  # noqa: F811
    with pytest.raises(RuoloDatabaseNonAmmesso, match="proprietario"):
        await init_db(in_produzione,
                      database_url_lettura=url_con_utente(db_url, proprietario))


async def test_url_diretta_con_superutente_rifiutata(db_url, in_produzione,  # noqa: F811
                                                     monkeypatch):
    monkeypatch.setenv("DATABASE_URL_DIRETTA", db_url)
    with pytest.raises(RuoloDatabaseNonAmmesso, match="superutente"):
        await init_db(in_produzione)


async def test_manutenzione_proprietaria_ammessa_superutente_no(db_url, in_produzione,  # noqa: F811
                                                                proprietario,  # noqa: F811
                                                                monkeypatch):
    monkeypatch.setenv("DATABASE_URL_MANUTENZIONE", url_con_utente(db_url, proprietario))
    await init_db(in_produzione)  # eccezione dichiarata: uso «manutenzione»
    await close_db()
    monkeypatch.setenv("DATABASE_URL_MANUTENZIONE", db_url)
    with pytest.raises(RuoloDatabaseNonAmmesso, match="superutente"):
        await init_db(in_produzione)
