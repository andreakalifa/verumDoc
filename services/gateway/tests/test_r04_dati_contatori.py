# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — contatori per ente al posto di COUNT(*) (piano §4.2) e sessione di
sola lettura (``DATABASE_URL_LETTURA``), su PostgreSQL reale.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import libs.shared.db as db
from libs.shared.db import get_async_session, get_async_session_lettura
from libs.shared.db.contatori import (
    consistenza_per_ente,
    consolida,
    in_attesa,
    per_stato,
    serie_giornaliera,
    valori,
)
from libs.shared.db.manutenzione import giro_contatori
from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]


async def _pdv(url, tenant, classe, stato="RICEVUTO", ricevuto=None) -> uuid.UUID:
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "data_ricezione) VALUES ($1, 'x', $2, $3, $4, $5, $6)",
              pdv, stato, classe, uuid.uuid4(), tenant, ricevuto or datetime.now(UTC))
    return pdv


async def _aip(url, tenant, pdv, stato="IN_BUILD", versato=None, conservato=None) -> uuid.UUID:
    aip = uuid.uuid4()
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256, data_versamento, data_conservazione) VALUES ($1, $2, $3, $4, $5, "
                   "'k', 'b', $6, $7, $8)", aip, pdv, tenant, f"urn:x:{aip}", stato, "a" * 64,
              versato or datetime.now(UTC), conservato)
    return aip


async def _documenti(url, aip, n: int, byte: int = 100) -> None:
    await sql(url, "INSERT INTO public.documenti (aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, s3_key) SELECT $1, 'f', 'PRINCIPALE', "
                   "'application/pdf', $3, 'h', 'k' FROM generate_series(1, $2)", aip, n, byte)


async def _conteggi_veri(url, tenant) -> dict[str, int]:
    """Oracolo: COUNT(*) sulle tabelle (solo nei test)."""
    r = {}
    for riga in await sql(url, "SELECT 'pdv.stato.' || stato AS m, count(*) AS n FROM public.pdv "
                               "WHERE tenant_id = $1 GROUP BY 1 UNION ALL "
                               "SELECT 'aip.stato.' || stato, count(*) FROM public.aip "
                               "WHERE tenant_id = $1 GROUP BY 1", tenant):
        r[riga["m"]] = riga["n"]
    return r


async def test_contatori_esatti_dopo_insert_cambi_di_stato_e_delete(
    db_url, ambito_del_conservatore):
    """I trigger tengono i contatori uguali al COUNT(*) per ogni sequenza di
    operazioni, prima e dopo il consolidamento; gli altri enti non si toccano."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    ieri = datetime.now(UTC) - timedelta(days=1)
    p1 = await _pdv(db_url, tenant, classe, ricevuto=ieri)
    p2 = await _pdv(db_url, tenant, classe)
    await _pdv(db_url, altro, classe)
    a1 = await _aip(db_url, tenant, p1, versato=ieri)
    a2 = await _aip(db_url, tenant, p2)
    await _documenti(db_url, a1, 3, byte=10)
    await _documenti(db_url, a2, 2, byte=7)
    await sql(db_url, "UPDATE public.pdv SET stato = 'VALIDATO' WHERE id = $1", p1)
    await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1", p1)
    await sql(db_url, "UPDATE public.pdv SET oggetto = 'non conta' WHERE id = $1", p2)
    await sql(db_url, "UPDATE public.aip SET stato = 'CONSERVATO', data_conservazione = now() "
                      "WHERE id = $1", a1)
    await sql(db_url, "DELETE FROM public.documenti WHERE aip_id = $1", a2)
    await sql(db_url, "DELETE FROM public.aip WHERE id = $1", a2)

    async def leggi() -> dict[str, int]:
        async with get_async_session() as s:
            return {k: v for k, v in (await valori(s, tenant_id=tenant)).items() if v}

    attesi = await _conteggi_veri(db_url, tenant)
    prima = await leggi()
    assert {k: v for k, v in prima.items() if ".stato." in k} == attesi
    assert prima["documenti.versati"] == 3 and prima["documenti.byte"] == 30
    assert prima["aip.conservati"] == 1 and prima["aip.versati"] == 1
    assert prima["pdv.ricevuti"] == 2
    assert await giro_contatori(lotto=3) > 0                # più lotti
    assert await leggi() == prima
    async with get_async_session() as s:
        assert await in_attesa(s) == 0
        serie = await serie_giornaliera(s, "pdv.ricevuti", tenant_id=tenant,
                                        da=ieri.date(), a=date.today() + timedelta(days=1))
        assert serie == {ieri.date(): 1, datetime.now(UTC).date(): 1}
        assert per_stato(await valori(s, tenant_id=altro), "pdv.stato.") == {"RICEVUTO": 1}
        consistenza = await consistenza_per_ente(s)
    assert consistenza[str(tenant)] == {"numero_pda": 1, "numero_documenti": 3,
                                        "volume_byte": 30}


async def test_consolidamento_concorrente_non_perde_ne_duplica(db_url, ambito_del_conservatore):
    """Due consolidamenti in parallelo (connessioni distinte) mentre arrivano
    altre variazioni: il totale resta esatto (SKIP LOCKED + ON CONFLICT)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    for _ in range(40):
        await _pdv(db_url, tenant, classe)

    async def consolida_finche_serve():
        while True:
            async with get_async_session() as s:
                if await consolida(s, lotto=7) == 0:
                    return

    async def scrivi_altri():
        for _ in range(20):
            await _pdv(db_url, tenant, classe)
            await asyncio.sleep(0)

    await asyncio.gather(consolida_finche_serve(), consolida_finche_serve(), scrivi_altri())
    async with get_async_session() as s:
        assert (await valori(s, tenant_id=tenant))["pdv.stato.RICEVUTO"] == 60


async def test_sessione_lettura_senza_replica_rifiuta_le_scritture(db_url):
    """Senza DATABASE_URL_LETTURA: stessa base dati, transazione READ ONLY."""
    assert db.lettura_su_replica() is False
    async with get_async_session_lettura() as s:
        assert (await s.execute(text("SELECT current_setting('transaction_read_only')"))
                ).scalar_one() == "on"
    with pytest.raises(DBAPIError, match="read-only"):
        async with get_async_session_lettura() as s:
            await s.execute(text("INSERT INTO public.audit_global (evento) VALUES ('x')"))


async def test_sessione_lettura_su_replica_configurata(db_url, monkeypatch):
    """Con DATABASE_URL_LETTURA (qui lo stesso database, come una replica) le
    letture usano un pool distinto in sola lettura; le scritture restano sul
    primario."""
    await db.close_db()
    monkeypatch.setenv("DATABASE_URL_LETTURA", db_url)
    await db.init_db(db_url)
    try:
        assert db.lettura_su_replica() is True
        async with get_async_session() as s:
            await s.execute(text("INSERT INTO public.audit_global (evento) VALUES ('primario')"))
        async with get_async_session_lettura() as s:
            assert (await s.execute(text(
                "SELECT count(*) FROM public.audit_global WHERE evento = 'primario'"))
            ).scalar_one() == 1
        with pytest.raises(DBAPIError, match="read-only"):
            async with get_async_session_lettura() as s:
                await s.execute(text("INSERT INTO public.audit_global (evento) VALUES ('x')"))
    finally:
        await db.close_db()
        monkeypatch.delenv("DATABASE_URL_LETTURA")
        await db.init_db(db_url)
    assert db.lettura_su_replica() is False
