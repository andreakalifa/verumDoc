# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Regressione del seed con il ruolo dei servizi (avvio docker, R13a).

Il seed (``app.seed.run_seed``) gira con il login del gateway, membro di
``verumdoc_applicazione``: non proprietario delle tabelle e senza BYPASSRLS. Le
classi di base (tenant_id NULL) e il cliente dimostrativo sono dati del
conservatore: la Row Level Security li accetta solo con l'ambito
«conservatore» impostato nella transazione del seed. Senza, l'avvio del
gateway falliva. Il test lo prova su una copia del database a head, dopo aver
tolto classi di base e cliente dimostrativo (il seed deve ricrearli tutti).
"""
from __future__ import annotations

import pytest
from app import seed as modulo_seed

from libs.shared.db.ruolo_prova import assicura_ruolo_servizio, url_con_utente
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = [richiede_pg, pytest.mark.asyncio]

#: Login di prova SOLO del gruppo dei servizi (più il gruppo del portale che
#: ``assicura_ruolo_servizio`` concede a ogni login di prova).
LOGIN_SEED = "vd_prova_seed_servizi"


async def _prepara(url: str) -> str:
    codici = [c.codice for c in modulo_seed.CLASSI_BASE]
    await sql(url, "DELETE FROM public.classi_documentali WHERE tenant_id IS NULL "
                      "AND codice = ANY($1::text[])", codici)
    await sql(url, "DELETE FROM public.tenants WHERE id = $1::uuid",
              modulo_seed.DEMO_TENANT_ID)
    await assicura_ruolo_servizio(url, LOGIN_SEED)
    return url_con_utente(url, LOGIN_SEED).replace("postgresql+asyncpg://",
                                                       "postgresql://", 1)


async def test_seed_con_login_dei_servizi_crea_classi_e_cliente_demo(db_url, monkeypatch):  # noqa: F811  # noqa: F811
    dsn = await _prepara(db_url)
    ruolo = (await sql(db_url, "SELECT rolsuper, rolbypassrls, "
                               "pg_has_role($1, 'verumdoc_applicazione', 'MEMBER') AS membro "
                               "FROM pg_roles WHERE rolname = $1", LOGIN_SEED))[0]
    assert ruolo["membro"] and not ruolo["rolsuper"] and not ruolo["rolbypassrls"]
    proprietari = await sql(db_url, "SELECT tableowner FROM pg_tables WHERE schemaname = "
                                    "'public' AND tablename IN ('classi_documentali', 'tenants')")
    assert all(r["tableowner"] != LOGIN_SEED for r in proprietari)
    monkeypatch.setattr(modulo_seed, "_crea_bucket_sviluppo", lambda *a, **k: {})

    esito = await modulo_seed.run_seed(dsn, env="test")

    assert esito == {"classi_create": 9, "demo": True, "tenant_demo_creato": True}
    righe = await sql(db_url, "SELECT codice FROM public.classi_documentali "
                              "WHERE tenant_id IS NULL AND codice = ANY($1::text[])",
                      [c.codice for c in modulo_seed.CLASSI_BASE])
    assert len(righe) == 9
    demo = await sql(db_url, "SELECT ragione_sociale FROM public.tenants WHERE id = $1::uuid",
                     modulo_seed.DEMO_TENANT_ID)
    assert len(demo) == 1
    # idempotente: un secondo avvio non crea nulla e non fallisce
    assert await modulo_seed.run_seed(dsn, env="test") == {
        "classi_create": 0, "demo": True, "tenant_demo_creato": False}


async def test_senza_ambito_conservatore_la_rls_rifiuta_le_classi_di_base(db_url):  # noqa: F811  # noqa: F811
    """Controllo che il test sopra sia significativo: con lo stesso login e senza
    l'ambito del conservatore l'INSERT di una classe di base è rifiutato."""
    import asyncpg

    dsn = await _prepara(db_url)
    conn = await asyncpg.connect(dsn)
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with conn.transaction():
                await modulo_seed.seed_classi_base(conn)
    finally:
        await conn.close()
