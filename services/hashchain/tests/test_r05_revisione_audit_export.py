# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R7: l'export dell'audit nel WORM non
perde le righe delle transazioni lunghe.

``audit_global.created_at`` vale ``now()`` = INIZIO della transazione che scrive.
Prima della correzione un periodo si esportava dopo un margine fisso (600 s)
dalla sua fine: una transazione più lunga del margine committava una riga in un
periodo GIÀ esportato, che non finiva in nessun segmento (e ``verifica-audit``
la segnalava come manomissione). Ora il limite è anche l'inizio della
transazione aperta più vecchia (``vd_transazioni_aperte``, migrazione 041).
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import asyncpg
import pytest
from app.config import settings
from app.registro import audit_worm

from tests.infra_reale import _asyncpg_dsn, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.test_r05_audit_worm import s3  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_transazione_lunga_aperta_ferma_l_export_del_suo_periodo(db_url, s3,  # noqa: F811
                                                                      monkeypatch) -> None:
    lenta = await asyncpg.connect(_asyncpg_dsn(db_url))
    tr = lenta.transaction()
    await tr.start()
    inizio_tr = await lenta.fetchval("SELECT now()")
    ora = inizio_tr.replace(minute=0, second=0, microsecond=0)
    monkeypatch.setattr(settings, "audit_export_inizio", ora.isoformat())
    id_ = uuid.uuid4()
    # come registra_audit: created_at dal default now() (inizio della transazione)
    await lenta.execute("INSERT INTO public.audit_global (id, evento, dettagli) VALUES "
                        "($1, 'prova.transazione_lunga', CAST($2 AS jsonb))", id_,
                        json.dumps({"x": 1}))
    try:
        # 71 minuti dopo l'inizio dell'ora: oltre il margine, ma la transazione è aperta
        esito = await audit_worm.esporta_periodi_chiusi(adesso=ora + timedelta(minutes=71))
        assert not esito.problemi
        assert esito.attesa is not None and "transazione aperta" in esito.attesa
        coperti = await sql(db_url, "SELECT 1 FROM public.audit_esportazioni "
                                    "WHERE periodo_a > $1", inizio_tr)
        assert coperti == []  # il periodo della riga NON è stato esportato
    finally:
        await tr.commit()
        await lenta.close()
    # finita la transazione, il periodo si esporta CON la riga
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ora + timedelta(minutes=135))
    assert esito.attesa is None and esito.esportati >= 1
    righe = await sql(db_url, "SELECT worm_chiave FROM public.audit_esportazioni "
                              "WHERE periodo_da <= $1 AND periodo_a > $1", inizio_tr)
    assert len(righe) == 1
    corpo = s3.get_object(Bucket=settings.s3_bucket_aip,
                          Key=righe[0]["worm_chiave"])["Body"].read().decode()
    assert str(id_) in corpo


async def test_orizzonte_non_verificabile_sospende_l_export(db_url, s3,  # noqa: F811
                                                           monkeypatch) -> None:
    """Fail-closed: se non si vede l'inizio di tutte le transazioni (proprietario
    della funzione senza pg_read_all_stats) non si esporta nulla."""
    async def cieco() -> tuple[None, int]:
        return None, 3

    await sql(db_url, "INSERT INTO public.audit_global (evento, dettagli) "
                      "VALUES ('prova.cieco', '{}'::jsonb)")
    adesso = (await sql(db_url, "SELECT now() AS t"))[0]["t"] + timedelta(hours=3)
    monkeypatch.setattr(audit_worm, "orizzonte_transazioni", cieco)
    esito = await audit_worm.esporta_periodi_chiusi(adesso=adesso)
    assert esito.esportati == 0 and "pg_read_all_stats" in (esito.attesa or "")
    assert await sql(db_url, "SELECT 1 FROM public.audit_esportazioni") == []


async def test_funzione_vede_le_transazioni_degli_altri_ruoli(db_url) -> None:  # noqa: F811
    """La funzione SECURITY DEFINER, chiamata con il login dei servizi (non
    superutente), vede l'inizio di una transazione del superutente."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session

    altra = await asyncpg.connect(_asyncpg_dsn(db_url))
    tr = altra.transaction()
    await tr.start()
    inizio = await altra.fetchval("SELECT now()")
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT inizio_piu_vecchio, non_visibili, current_user AS chi "
                "FROM public.vd_transazioni_aperte()"))).one()
    finally:
        await tr.rollback()
        await altra.close()
    assert riga.chi != "postgres"
    assert riga.non_visibili == 0
    assert riga.inizio_piu_vecchio is not None and riga.inizio_piu_vecchio <= inizio
