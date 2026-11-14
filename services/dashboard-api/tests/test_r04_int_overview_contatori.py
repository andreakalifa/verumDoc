# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — ``GET /api/dashboard/overview``: PdA per stato dai contatori
per ente (niente 8 ``COUNT(*)`` su ``aip``, richiesta di r04-dati), letti con la
sessione di sola lettura. Oracolo: ``COUNT(*)`` nel test.
Anche gli elenchi dell'archivio usano la sessione di lettura.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

import libs.shared.db as db
from tests.infra_reale import richiede_pg, sql
from tests.test_g2_dati import _aip, _client, _token, crea_tenant_e_classe, db_url  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_overview_per_stato_dai_contatori_uguale_al_conteggio(db_url, monkeypatch):
    tenant, classe = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    await _aip(db_url, tenant, classe, conservato=datetime.now(UTC))
    await _aip(db_url, tenant, classe, conservato=datetime.now(UTC))
    secondo = await _aip(db_url, tenant, classe, stato="IN_BUILD")
    await _aip(db_url, altro, classe)
    await sql(db_url, "UPDATE public.aip SET stato = 'SU_STORAGE' WHERE id = $1", secondo)

    letture: list[str] = []
    originale = db.get_async_session_lettura

    def spia():
        letture.append("lettura")
        return originale()

    monkeypatch.setattr(db, "get_async_session_lettura", spia)
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/overview?tenant_id={tenant}",
                        headers=_token(str(tenant), "ADMIN_TENANT"))
    assert r.status_code == 200, r.text
    stats = r.json()["aip_stats"]
    vero = {x["stato"]: x["n"] for x in await sql(
        db_url, "SELECT stato::text AS stato, count(*) AS n FROM public.aip "
                "WHERE tenant_id = $1 GROUP BY stato", tenant)}
    assert {k: v for k, v in stats["per_stato"].items() if v} == vero
    assert vero == {"CONSERVATO": 2, "SU_STORAGE": 1}
    assert stats["totale"] == 3 and stats["conservati"] == 2
    assert set(stats["per_stato"]) >= {"CONSERVATO", "SU_STORAGE", "IN_BUILD"}
    assert letture == ["lettura"]


async def test_elenchi_archivio_sulla_sessione_di_lettura(db_url, monkeypatch):
    """Una scrittura dentro la sessione di lettura fallirebbe: gli elenchi la
    usano e funzionano (transazione in sola lettura)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    await _aip(db_url, tenant, classe)
    import app.services.archivio as archivio

    usate: list[str] = []
    originale = archivio.get_async_session_lettura

    def spia():
        usate.append("lettura")
        return originale()

    monkeypatch.setattr(archivio, "get_async_session_lettura", spia)
    async with await _client() as c:
        for percorso in ("/api/dashboard/versamenti", "/api/dashboard/aip"):
            r = await c.get(f"{percorso}?tenant_id={tenant}",
                            headers=_token(str(tenant), "ADMIN_TENANT"))
            assert r.status_code == 200, (percorso, r.text)
            assert len(r.json()["items"]) == 1
    assert usate == ["lettura", "lettura"]
