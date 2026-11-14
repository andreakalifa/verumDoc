# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — SLA e piano di cessazione dai contatori per ente (piano §4.2: mai
COUNT(*) su tabelle grandi), su PostgreSQL reale e con le API HTTP.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.test_g2_dati import _aip, _client, _token, crea_tenant_e_classe, db_url  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_throughput_e_totali_dai_contatori_uguali_al_conteggio(db_url):
    """Throughput per giorno e totale dei PdA uguali ai COUNT(*) (oracolo nel
    test), anche dopo un cambio di stato e prima del consolidamento."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    adesso = datetime.now(UTC)
    ieri = adesso - timedelta(days=1)
    await _aip(db_url, tenant, classe, versato=ieri, conservato=ieri)
    await _aip(db_url, tenant, classe, versato=adesso, stato="IN_BUILD")
    terzo = await _aip(db_url, altro, classe, versato=adesso, stato="IN_BUILD")
    await sql(db_url, "UPDATE public.aip SET stato = 'CONSERVATO', data_conservazione = now() "
                      "WHERE id = $1", terzo)
    async with await _client() as c:
        r = await c.get("/api/dashboard/sla/throughput?giorni=2",
                        headers=_token(None, "SUPER_ADMIN"))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["totale_aip"] == 3
        assert corpo["per_giorno"] == [{"data": str(ieri.date()), "aip": 1},
                                       {"data": str(adesso.date()), "aip": 2}]
        r = await c.get("/api/dashboard/admin/tenants-stats", headers=_token(None, "SUPER_ADMIN"))
        assert r.status_code == 200 and r.json()["totale_aip"] == 3
        r = await c.get("/api/dashboard/sla/report", headers=_token(None, "SUPER_ADMIN"))
        assert r.status_code == 200
        vero = await sql(db_url, "SELECT count(*) AS n FROM public.aip WHERE stato IN "
                                 "('CONSERVATO', 'IN_CATENA') AND data_conservazione >= "
                                 "date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
        assert r.json()["metriche"]["aip_conservati"] == vero[0]["n"]


async def test_piano_cessazione_consistenza_dai_contatori(db_url, ambito_del_conservatore):
    """Elenchi di consistenza (PdA, documenti, byte) per produttore dai
    contatori: stessi numeri delle tabelle; un ente senza PdA ha zeri."""
    from app.services.piano_cessazione import leggi_consistenza

    tenant, classe = await crea_tenant_e_classe(db_url)
    vuoto, _ = await crea_tenant_e_classe(db_url)
    await _aip(db_url, tenant, classe)
    await _aip(db_url, tenant, classe)
    righe, errore = await leggi_consistenza()
    assert errore is None
    per_cf = {r["codice_fiscale"]: r for r in righe}
    vero = await sql(db_url, "SELECT count(*) AS n, sum(d.dimensione_bytes) AS b FROM "
                             "public.documenti d JOIN public.aip a ON a.id = d.aip_id "
                             "WHERE a.tenant_id = $1", tenant)
    pieno = per_cf[tenant.hex[:16]]
    assert (pieno["numero_pda"], pieno["numero_documenti"], pieno["volume_byte"]) == (
        2, vero[0]["n"], vero[0]["b"])
    zero = per_cf[vuoto.hex[:16]]
    assert (zero["numero_pda"], zero["numero_documenti"], zero["volume_byte"]) == (0, 0, 0)
