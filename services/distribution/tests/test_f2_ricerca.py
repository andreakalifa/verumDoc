# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-06) — ricerca di distribution che regge milioni di PdA per ente:
niente paginazione per posizione (``from``), conteggio dei risultati limitato
(``track_total_hits``), ``skip`` > 0 rifiutato; i profili del conservatore
possono indicare l'ente (come per lo storico e la ricerca del portale).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests import infra_reale, pda_reale
from tests.infra_reale import richiede_pg, sql
from tests.pda_reale import crea_pda

# fixture condivise
db_url = infra_reale.db_url
s3_server = infra_reale.s3_server
s3 = pda_reale.s3


pytestmark = richiede_pg

ADESSO = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def opensearch(monkeypatch):
    from app.config import settings

    from tests.opensearch_finto import OpenSearchFinto

    finto = OpenSearchFinto().avvia()
    monkeypatch.setattr(settings, "opensearch_url", finto.url)
    yield finto
    finto.ferma()


def _h(tenant, ruolo: str = "VIEWER") -> dict:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=str(uuid.uuid4()), ruolo=ruolo)}


async def _client() -> AsyncClient:
    from app.routers.distribution import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _indicizzati(db_url, s3, n: int, tenant=None) -> list[dict]:
    from app.services.indicizzazione import elabora_lotto

    pda = []
    for i in range(n):
        p = await crea_pda(db_url, s3, tenant=tenant or (pda[0]["tenant"] if pda else None),
                           data_versamento=ADESSO - timedelta(days=i + 1))
        pda.append(p)
        await sql(db_url, "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id, "
                          "prossimo_tentativo) VALUES ($1, $2, $3)",
                  p["aip"], p["tenant"], ADESSO - timedelta(seconds=1))
    await elabora_lotto(adesso=ADESSO)
    return pda


async def test_ricerca_conteggio_limitato_e_skip_rifiutato(db_url, s3, opensearch,
                                                          monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ricerca_conteggio_massimo", 2)
    pda = await _indicizzati(db_url, s3, 3)
    tenant = pda[0]["tenant"]
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/search", headers=_h(tenant), json={"limit": 1})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["total"] == 2 and corpo["total_esatto"] is False
        assert corpo["altri"] is True and len(corpo["items"]) == 1
        assert "testo_completo" in corpo["campi_testo"]
        skip = await c.post("/api/v2/distribution/search", headers=_h(tenant),
                            json={"skip": 20})
        assert skip.status_code == 422 and skip.json()["codice"] == "DST-422"
    corpo_os, _ = opensearch.ricerche[0]
    assert corpo_os["track_total_hits"] == 2 and "from" not in corpo_os


async def test_ricerca_ente_scelto_solo_dal_conservatore(db_url, s3, opensearch) -> None:
    """SUPER_ADMIN/RSC_CONSERVATORE cercano nell'ente indicato; per gli altri
    profili ``tenant_id`` è ignorato (si cerca nel proprio ente)."""
    pda = await _indicizzati(db_url, s3, 1)
    tenant = pda[0]["tenant"]
    proprio = uuid.uuid4()
    async with await _client() as c:
        rsc = await c.post("/api/v2/distribution/search", headers=_h(proprio, "RSC_CONSERVATORE"),
                           json={"tenant_id": str(tenant)})
        viewer = await c.post("/api/v2/distribution/search", headers=_h(proprio, "VIEWER"),
                              json={"tenant_id": str(tenant)})
        errato = await c.post("/api/v2/distribution/search", headers=_h(proprio, "SUPER_ADMIN"),
                              json={"tenant_id": "non-un-uuid"})
    assert [i["aip_id"] for i in rsc.json()["items"]] == [str(pda[0]["aip"])]
    assert viewer.status_code == 200 and viewer.json()["items"] == []
    assert errato.status_code == 422 and errato.json()["codice"] == "DST-422"
    assert [p["routing"] for _, p in opensearch.ricerche] == [str(tenant), str(proprio)]
