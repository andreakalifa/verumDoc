# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 servizi — elenchi a cursore della dashboard (VD-C-102: /notifiche, /audit)
e registrazione dei fruitori PDND da API (VD-C-084), su PostgreSQL reale.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


def _token(ruolo: str, tenant=None, user_id: str | None = None) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant or "00000000-0000-0000-0000-0000000000c0"),
        user_id=user_id or str(uuid.uuid4()), ruolo=ruolo)}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _scorri(c: AsyncClient, url: str, h: dict, limite: int) -> list[list[str]]:
    pagine, cursore = [], None
    while True:
        sep = "&" if "?" in url else "?"
        indirizzo = f"{url}{sep}limit={limite}" + (f"&cursore={cursore}" if cursore else "")
        r = await c.get(indirizzo, headers=h)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert "total" not in corpo and "skip" not in corpo
        pagine.append([i["id"] for i in corpo["items"]])
        if not corpo["altri"]:
            assert corpo["cursore"] is None
            return pagine
        cursore = corpo["cursore"]


async def test_notifiche_a_cursore_senza_salti_ne_doppioni(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    base = datetime.now(UTC)
    # stessa data per alcune righe: l'ordine (data, id) non salta né ripete
    for i in range(7):
        await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, "
                          "destinatario, created_at) VALUES ($1, 'x', 'email', $2, $3)",
                  tenant, f"d{i}", base - timedelta(minutes=i // 2))
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario) "
                      "VALUES ($1, 'x', 'email', 'altro')", altro)
    async with _client() as c:
        pagine = await _scorri(c, f"/api/dashboard/notifiche?tenant_id={tenant}",
                               _token("ADMIN_TENANT", tenant), 3)
    assert [len(p) for p in pagine] == [3, 3, 1]
    tutte = [i for p in pagine for i in p]
    assert len(set(tutte)) == 7
    righe = await sql(db_url, "SELECT id::text AS id FROM public.notifications "
                              "WHERE tenant_id = $1 ORDER BY created_at DESC, id DESC", tenant)
    assert tutte == [r["id"] for r in righe]


async def test_notifiche_e_audit_skip_e_cursore_altrui_rifiutati(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "INSERT INTO public.audit_global (tenant_id, evento) VALUES ($1, 'a.b')",
              tenant)
    await sql(db_url, "INSERT INTO public.audit_global (tenant_id, evento) VALUES ($1, 'a.c')",
              tenant)
    h = _token("ADMIN_TENANT", tenant)
    async with _client() as c:
        for url in (f"/api/dashboard/notifiche?tenant_id={tenant}&skip=20",
                    "/api/dashboard/audit?skip=5"):
            r = await c.get(url, headers=h)
            assert r.status_code == 422 and r.json()["codice"] == "DSH-422"
        audit = (await c.get("/api/dashboard/audit?limit=1", headers=h)).json()
        assert audit["altri"] and audit["cursore"]
        # il cursore del registro non vale per le notifiche
        r = await c.get(f"/api/dashboard/notifiche?tenant_id={tenant}&cursore={audit['cursore']}",
                        headers=h)
        assert r.status_code == 422 and r.json()["codice"] == "DSH-104"
        r = await c.get("/api/dashboard/audit?cursore=non-base64!", headers=h)
        assert r.json()["codice"] == "DSH-104"


async def test_audit_a_cursore_con_filtro_e_perimetro(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    for i in range(5):
        await sql(db_url, "INSERT INTO public.audit_global (tenant_id, evento) VALUES ($1, $2)",
                  t1, f"contratto.registrato.{i}")
    await sql(db_url, "INSERT INTO public.audit_global (tenant_id, evento) VALUES ($1, "
                      "'contratto.registrato')", t2)
    async with _client() as c:
        pagine = await _scorri(c, "/api/dashboard/audit?evento=contratto",
                               _token("ADMIN_TENANT", t1), 2)
        assert [len(p) for p in pagine] == [2, 2, 1]
        globale = await _scorri(c, "/api/dashboard/audit?evento=contratto",
                                _token("SUPER_ADMIN"), 4)
        assert sum(len(p) for p in globale) == 6


# ── Fruitori PDND (VD-C-084) ─────────────────────────────────────────

async def test_fruitori_pdnd_registrazione_e_disattivazione(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    sa = _token("SUPER_ADMIN", user_id=str(uuid.uuid4()))
    async with _client() as c:
        r = await c.post("/api/dashboard/pdnd/fruitori", headers=sa, json={
            "client_id": "pdnd-client-001", "tenant_id": str(tenant),
            "descrizione": "Comune di prova"})
        assert r.status_code == 201, r.text
        fid = r.json()["id"]
        assert r.json()["attivo"] is True and r.json()["ente"] == "Cliente di prova"
        doppio = await c.post("/api/dashboard/pdnd/fruitori", headers=sa, json={
            "client_id": "pdnd-client-001", "tenant_id": str(tenant)})
        assert doppio.status_code == 409 and doppio.json()["codice"] == "GOV-409-FRUITORE"
        inesistente = await c.post("/api/dashboard/pdnd/fruitori", headers=sa, json={
            "client_id": "pdnd-client-002", "tenant_id": str(uuid.uuid4())})
        assert inesistente.status_code == 404 and inesistente.json()["codice"] == "GOV-404"
        r = await c.patch(f"/api/dashboard/pdnd/fruitori/{fid}", headers=sa,
                          json={"attivo": False})
        assert r.status_code == 200 and r.json()["attivo"] is False
        assert (await c.patch("/api/dashboard/pdnd/fruitori/xyz", headers=sa,
                              json={"attivo": True})).json()["codice"] == "GOV-404-FRUITORE"
        assert (await c.patch(f"/api/dashboard/pdnd/fruitori/{fid}", headers=sa,
                              json={})).status_code == 422
        elenco = (await c.get(f"/api/dashboard/pdnd/fruitori?tenant_id={tenant}",
                              headers=sa)).json()
    assert [f["client_id"] for f in elenco["items"]] == ["pdnd-client-001"]
    [riga] = await sql(db_url, "SELECT attivo FROM public.pdnd_fruitori WHERE client_id = $1",
                       "pdnd-client-001")
    assert riga["attivo"] is False
    eventi = [r["evento"] for r in await sql(
        db_url, "SELECT evento FROM public.audit_global WHERE evento LIKE 'pdnd.fruitore%' "
                "ORDER BY created_at")]
    assert eventi == ["pdnd.fruitore_registrato", "pdnd.fruitore_modificato"]
    [ultimo] = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                                 "WHERE evento = 'pdnd.fruitore_modificato'")
    assert json.loads(ultimo["dettagli"])["dopo"] == {"attivo": False}


@pytest.mark.parametrize("ruolo", ["RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE"])
async def test_fruitori_pdnd_solo_super_admin(db_url, ruolo) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with _client() as c:
        r = await c.post("/api/dashboard/pdnd/fruitori", headers=_token(ruolo, tenant), json={
            "client_id": "pdnd-x", "tenant_id": str(tenant)})
        assert r.status_code == 403 and r.json()["codice"] == "AUTH-010"
        assert (await c.get("/api/dashboard/pdnd/fruitori",
                            headers=_token(ruolo, tenant))).status_code == 403
    assert await sql(db_url, "SELECT 1 FROM public.pdnd_fruitori") == []


async def test_fruitori_pdnd_elenco_a_cursore(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    sa = _token("SUPER_ADMIN")
    async with _client() as c:
        for i in range(5):
            assert (await c.post("/api/dashboard/pdnd/fruitori", headers=sa, json={
                "client_id": f"c-{i}", "tenant_id": str(tenant)})).status_code == 201
        p1 = (await c.get("/api/dashboard/pdnd/fruitori?limit=3", headers=sa)).json()
        p2 = (await c.get(f"/api/dashboard/pdnd/fruitori?limit=3&cursore={p1['cursore']}",
                          headers=sa)).json()
    assert len(p1["items"]) == 3 and p1["altri"] and len(p2["items"]) == 2 and not p2["altri"]
    assert {f["client_id"] for f in p1["items"] + p2["items"]} == {f"c-{i}" for i in range(5)}
