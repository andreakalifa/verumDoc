# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R14: Row Level Security anche su
``classi_documentali``, ``impostazioni_valori``, ``pdnd_fruitori`` e
``rsc_nominations`` (migrazione 20261023_041).

Righe GLOBALI (``tenant_id`` NULL: classi di base e valori del conservatore):
leggibili da tutti (politica ``vd_ente_globali_lettura``), scrivibili solo
nell'ambito del conservatore. Ogni altra tabella con ``tenant_id`` senza RLS deve
stare nell'elenco delle esclusioni motivate (docs/architettura/sicurezza.md).
"""
from __future__ import annotations

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

pytestmark = [pytest.mark.integration, richiede_pg]

TABELLE_R14 = ("classi_documentali", "impostazioni_valori", "pdnd_fruitori", "rsc_nominations")
#: Tabelle con ``tenant_id`` FUORI dalla RLS, con il motivo (sicurezza.md)
ESCLUSE = {
    "users": "autenticazione: letta prima che l'ente sia noto",
    "api_keys": "autenticazione: letta prima che l'ente sia noto",
    "contracts": "autenticazione e quote: letta prima che l'ente sia noto",
    "sessioni_revocate": "autenticazione: uscite dal portale (R12)",
    "sessioni_attive": "autenticazione: sessioni del portale e inattività (R06, VD-C-127)",
    # R10 (D08): credenziali locali lette prima che l'ente sia noto; diritti al solo
    # gruppo verumdoc_credenziali_accesso (login del gateway)
    "credenziali_password": "autenticazione: password (R10, D08)",
    "credenziali_totp": "autenticazione: segreto TOTP cifrato (R10, D08)",
    "codici_recupero": "autenticazione: codici di recupero (R10, D08)",
    "credenziali_passkey": "autenticazione: passkey WebAuthn (R10, D08)",
    "outbox_eventi": "coda tecnica del relay (tutti gli enti)",
    "catena_pda": "registro delle impronte: tabella protetta (event trigger della 033)",
    "catena_registro": "registro delle impronte: globale per costruzione",
    "catena_foglie_interne": "registro delle impronte: globale per costruzione",
    "verifiche_registro": "registro protetto con difese proprie (033)",
}


@contextmanager
def senza_ambito() -> Iterator[None]:
    token = imposta_ambito(NESSUN_AMBITO)
    try:
        yield
    finally:
        ripristina_ambito(token)


async def test_ogni_tabella_con_ente_ha_la_rls_o_un_motivo(db_url):  # noqa: F811
    righe = await sql(db_url, """
        SELECT c.relname, c.relrowsecurity,
               EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                         AND p.polname = 'vd_ente_isolamento') AS politica,
               EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                         AND p.polname = 'vd_ente_globali_lettura') AS globali
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
             AND NOT a.attisdropped
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')""")
    senza = {r["relname"] for r in righe if not r["relrowsecurity"]}
    assert senza == set(ESCLUSE)
    per_nome = {r["relname"]: r for r in righe}
    for tabella in TABELLE_R14:
        assert per_nome[tabella]["relrowsecurity"] and per_nome[tabella]["politica"], tabella
    assert {t for t in TABELLE_R14 if per_nome[t]["globali"]} == {
        "classi_documentali", "impostazioni_valori"}


async def _conta(tabella: str, where: str = "", **parametri) -> int:
    async with get_async_session() as s:
        return int((await s.execute(text(
            f"SELECT count(*) FROM public.{tabella} {where}"),  # noqa: S608 — nomi costanti
            parametri)).scalar_one())


async def test_classi_dell_ente_e_globali_mai_quelle_di_un_altro(db_url):  # noqa: F811
    a, classe_a = await crea_tenant_e_classe(db_url, codice_classe="propria_a",
                                             classe_del_tenant=True)
    b, classe_b = await crea_tenant_e_classe(db_url, codice_classe="propria_b",
                                             classe_del_tenant=True)
    globali = await sql(db_url, "SELECT count(*) AS n FROM public.classi_documentali "
                                "WHERE tenant_id IS NULL")
    with ambito_ente(a):
        assert await _conta("classi_documentali", "WHERE tenant_id IS NULL") == \
            globali[0]["n"]
        assert await _conta("classi_documentali", "WHERE id = CAST(:c AS uuid)",
                            c=str(classe_b)) == 0
        # un ente non crea classi globali né classi di un altro ente
        for ente in (None, str(b)):
            with pytest.raises(DBAPIError):
                async with get_async_session() as s:
                    await s.execute(text(
                        "INSERT INTO public.classi_documentali (tenant_id, codice, nome, "
                        "anni_conservazione, formati_ammessi, attiva) VALUES "
                        "(CAST(:t AS uuid), :c, 'x', 10, '[]', true)"),
                        {"t": ente, "c": f"X{uuid.uuid4().hex[:6]}"})
    with ambito_conservatore("test: classi di tutti"):
        assert await _conta("classi_documentali", "WHERE id = ANY(CAST(:c AS uuid[]))",
                            c=[str(classe_a), str(classe_b)]) == 2
    with senza_ambito():
        # senza ambito solo le righe globali (configurazione del conservatore)
        assert await _conta("classi_documentali", "WHERE tenant_id IS NOT NULL") == 0
        assert await _conta("classi_documentali") == globali[0]["n"]


async def test_fruitori_pdnd_e_nomine_solo_del_proprio_ente(db_url):  # noqa: F811
    a, _ = await crea_tenant_e_classe(db_url)
    b, _ = await crea_tenant_e_classe(db_url)
    for ente in (a, b):
        await sql(db_url, "INSERT INTO public.pdnd_fruitori (id, client_id, tenant_id, "
                          "descrizione, attivo) VALUES (gen_random_uuid(), $1, $2, 'prova', true)",
                  f"client-{ente.hex[:8]}", ente)
    with ambito_ente(a):
        assert await _conta("pdnd_fruitori") == 1
        assert await _conta("pdnd_fruitori", "WHERE tenant_id = CAST(:b AS uuid)",
                            b=str(b)) == 0
    with senza_ambito():
        assert await _conta("pdnd_fruitori") == 0


async def test_autenticazione_pdnd_trova_il_fruitore_senza_ambito(db_url):  # noqa: F811
    """La lettura del fruitore per client_id (prima che l'ente sia noto) avviene
    nell'ambito del conservatore con motivo: la RLS non rompe l'accesso PDND."""
    from app.routers.pdnd import _fruitore

    a, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "INSERT INTO public.pdnd_fruitori (id, client_id, tenant_id, descrizione, "
                      "attivo) VALUES (gen_random_uuid(), 'client-r14', $1, 'prova', true)", a)
    with senza_ambito():
        fruitore = await _fruitore({"client_id": "client-r14"})
    assert str(fruitore.tenant_id) == str(a)
