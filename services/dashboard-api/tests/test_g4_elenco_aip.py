# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
GET /api/dashboard/aip — isolamento per ente e parametri non validi, su
PostgreSQL reale. Sostituisce tests/e2e/test_multitenant_isolation.py della
radice (B-01/B-16), che usava un DB simulato e non veniva mai eseguito
(g4-chiusura).
"""
from __future__ import annotations

import pytest

from tests import infra_reale
from tests.dati_prova import client, crea_aip, token
from tests.infra_reale import crea_tenant_e_classe, richiede_pg

# Fixture condivise (tests/infra_reale.py), registrate in questo modulo
db_url = infra_reale.db_url

pytestmark = richiede_pg


@pytest.fixture
async def due_enti(db_url):
    a, classe = await crea_tenant_e_classe(db_url)
    b, _ = await crea_tenant_e_classe(db_url)
    aip_a = await crea_aip(db_url, a, classe)
    aip_b = await crea_aip(db_url, b, classe)
    return a, b, str(aip_a), str(aip_b)


async def test_ogni_ente_vede_solo_i_propri_pacchetti(due_enti) -> None:
    a, b, aip_a, aip_b = due_enti
    async with client() as c:
        propri = await c.get(f"/api/dashboard/aip?tenant_id={a}", headers=token("ADMIN_TENANT", a))
        altrui = await c.get(f"/api/dashboard/aip?tenant_id={b}", headers=token("ADMIN_TENANT", a))
        globale = await c.get(f"/api/dashboard/aip?tenant_id={b}",
                              headers=token("RSC_CONSERVATORE", a))
    assert propri.status_code == 200, propri.text
    assert [i["aip_id"] for i in propri.json()["items"]] == [aip_a]
    assert propri.json()["altri"] is False
    assert altrui.status_code == 403
    assert aip_b not in altrui.text
    # Profilo globale: metadati di qualsiasi ente (VD-C-005), solo di quell'ente
    assert [i["aip_id"] for i in globale.json()["items"]] == [aip_b]


async def test_parametri_non_validi(due_enti) -> None:
    """Ente non UUID → 400 DSH-400; stato fuori dall'enumerazione → 422 DSH-422
    (la vecchia prova della radice si aspettava 400: il contratto attuale è 422)."""
    a, _, _, _ = due_enti
    async with client() as c:
        ente = await c.get("/api/dashboard/aip?tenant_id=NON-UN-UUID",
                           headers=token("ADMIN_TENANT", a))
        stato = await c.get(f"/api/dashboard/aip?tenant_id={a}&stato=VALORE_INESISTENTE",
                            headers=token("ADMIN_TENANT", a))
        filtro = await c.get(f"/api/dashboard/aip?tenant_id={a}&stato=CONSERVATO",
                             headers=token("ADMIN_TENANT", a))
    assert ente.status_code == 400 and ente.json()["codice"] == "DSH-400"
    assert stato.status_code == 422 and stato.json()["codice"] == "DSH-422"
    assert filtro.status_code == 200 and len(filtro.json()["items"]) == 1
    async with client() as c:
        skip = await c.get(f"/api/dashboard/aip?tenant_id={a}&skip=5",
                           headers=token("ADMIN_TENANT", a))
        cursore = await c.get(f"/api/dashboard/aip?tenant_id={a}&cursore=abc",
                              headers=token("ADMIN_TENANT", a))
    assert skip.status_code == 422 and skip.json()["codice"] == "DSH-422"
    assert cursore.status_code == 422 and cursore.json()["codice"] == "DSH-104"


async def test_elenco_a_cursore_senza_ripetizioni(db_url) -> None:
    """R03 f2 (REV-06): pagine a cursore (data di versamento, id) decrescenti;
    anche PdA con la stessa data di versamento non si ripetono né si perdono."""
    from datetime import UTC, datetime

    a, classe = await crea_tenant_e_classe(db_url)
    ids = [str(await crea_aip(db_url, a, classe)) for _ in range(5)]
    stessa = datetime(2026, 5, 1, tzinfo=UTC)
    await infra_reale.sql(db_url, "UPDATE public.aip SET data_versamento = $1 "
                                  "WHERE tenant_id = $2", stessa, a)
    visti, cursore = [], None
    async with client() as c:
        while True:
            q = f"&cursore={cursore}" if cursore else ""
            r = await c.get(f"/api/dashboard/aip?tenant_id={a}&limit=2{q}",
                            headers=token("ADMIN_TENANT", a))
            assert r.status_code == 200, r.text
            visti += [i["aip_id"] for i in r.json()["items"]]
            if not r.json()["altri"]:
                break
            cursore = r.json()["cursore"]
    assert sorted(visti) == sorted(ids) and len(visti) == len(set(visti))
    assert visti == sorted(ids, reverse=True)
