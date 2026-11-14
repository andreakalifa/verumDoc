# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/tests/test_sprint3a_audit.py
Sprint 3A FIX-018 + FIX-021: audit endpoint e classi tenant_id.
"""
from __future__ import annotations

import base64
import json
import uuid

from tests.dati_prova import client, token
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

# g3-test (VD-C-030): i test che leggevano il sorgente di lista_audit e di
# _sla_require_admin ora chiamano gli endpoint veri su PostgreSQL reale.


async def _eventi(url):
    """Due enti con eventi di audit; restituisce (ente A, ente B)."""
    t_a, _ = await crea_tenant_e_classe(url)
    t_b, _ = await crea_tenant_e_classe(url)
    for tid, evento, dettagli in ((t_a, "contratto.registrato", {"numero": "C-1"}),
                                  (t_a, "tenant.attivato_via_dashboard", {}),
                                  (t_b, "contratto.registrato", {"numero": "C-2"})):
        await sql(url, "INSERT INTO public.audit_global (tenant_id, evento, dettagli) "
                       "VALUES ($1, $2, CAST($3 AS jsonb))", tid, evento, json.dumps(dettagli))
    return t_a, t_b


@richiede_pg
class TestAuditEndpoint:
    """FIX-021: endpoint /api/dashboard/audit esiste ed è protetto."""

    async def test_dashboard_has_audit_endpoint(self, db_url):
        """GET /api/dashboard/audit è pubblicato e risponde con la forma attesa."""
        from app.main import app

        assert "get" in app.openapi()["paths"]["/api/dashboard/audit"]
        async with client() as c:
            r = await c.get("/api/dashboard/audit", headers=token("SUPER_ADMIN"))
        assert r.status_code == 200, r.text
        assert set(r.json()) >= {"items", "limit", "cursore", "altri"}  # R04 (VD-C-102): a cursore

    async def test_audit_requires_auth(self, db_url):
        """Senza token, con token non firmato o scaduto → 401."""
        async with client() as c:
            assert (await c.get("/api/dashboard/audit")).status_code == 401
            r = await c.get("/api/dashboard/audit", headers={"Authorization": "Bearer x.y.z"})
            assert r.status_code == 401

    async def test_audit_filters_by_tenant(self, db_url):
        """ADMIN_TENANT vede solo il proprio ente, anche chiedendo un altro tenant_id;
        SUPER_ADMIN e RSC_CONSERVATORE vedono tutti o l'ente indicato."""
        t_a, t_b = await _eventi(db_url)
        async with client() as c:
            admin = await c.get(f"/api/dashboard/audit?tenant_id={t_b}",
                                headers=token("ADMIN_TENANT", t_a))
            tutti = await c.get("/api/dashboard/audit", headers=token("SUPER_ADMIN"))
            solo_b = await c.get(f"/api/dashboard/audit?tenant_id={t_b}",
                                 headers=token("RSC_CONSERVATORE"))
        assert {i["tenant_id"] for i in admin.json()["items"]} == {str(t_a)}
        assert len(admin.json()["items"]) == 2
        assert len(tutti.json()["items"]) == 3
        assert {i["tenant_id"] for i in solo_b.json()["items"]} == {str(t_b)}

    async def test_audit_queries_audit_global(self, db_url):
        """Le righe vengono da audit_global, con evento e dettagli."""
        t_a, _ = await _eventi(db_url)
        async with client() as c:
            r = await c.get("/api/dashboard/audit", headers=token("ADMIN_TENANT", t_a))
        voci = {i["evento"]: i for i in r.json()["items"]}
        assert set(voci) == {"contratto.registrato", "tenant.attivato_via_dashboard"}
        assert voci["contratto.registrato"]["dettagli"] == {"numero": "C-1"}

    async def test_audit_supports_event_filter(self, db_url):
        """Filtro per evento: sottostringa letterale («_» e «%» non sono jolly)."""
        t_a, _ = await _eventi(db_url)
        h = token("ADMIN_TENANT", t_a)
        async with client() as c:
            contratti = await c.get("/api/dashboard/audit?evento=contratto", headers=h)
            jolly = await c.get("/api/dashboard/audit?evento=%25", headers=h)
            sottolineato = await c.get("/api/dashboard/audit?evento=contratto_registrato",
                                       headers=h)
        assert [i["evento"] for i in contratti.json()["items"]] == ["contratto.registrato"]
        assert jolly.json()["items"] == []
        assert sottolineato.json()["items"] == []


class TestClassiTenantId:
    """FIX-018: ClasseDocumentale deve avere tenant_id."""

    def test_model_has_tenant_id(self):
        """Il modello ClasseDocumentale deve avere la colonna tenant_id."""
        from libs.shared.models import ClasseDocumentale
        assert hasattr(ClasseDocumentale, "tenant_id"), (
            "ClasseDocumentale deve avere la colonna tenant_id"
        )

    def test_tenant_id_is_nullable(self):
        """tenant_id deve essere nullable (classi globali = NULL)."""
        from libs.shared.models import ClasseDocumentale
        col = ClasseDocumentale.__table__.columns["tenant_id"]
        assert col.nullable is True, (
            "tenant_id deve essere nullable per classi globali"
        )

    @richiede_pg
    async def test_migration_exists(self, db_url):
        """Nel database migrato (alembic upgrade head) classi_documentali.tenant_id
        esiste, è nullable e ammette classi globali (NULL) e del cliente.

        g3-test: prima era ``assert True``.
        """
        righe = await sql(db_url,
                          "SELECT is_nullable FROM information_schema.columns WHERE "
                          "table_schema = 'public' AND table_name = 'classi_documentali' "
                          "AND column_name = 'tenant_id'")
        assert [r["is_nullable"] for r in righe] == ["YES"]
        tenant, classe = await crea_tenant_e_classe(
            db_url, codice_classe=f"c{uuid.uuid4().hex[:8]}",
                                                    classe_del_tenant=True)
        riga = await sql(db_url, "SELECT tenant_id FROM public.classi_documentali WHERE id = $1",
                         classe)
        assert riga[0]["tenant_id"] == tenant


class TestSLAAuthFix:
    """Sprint 3A: SLA router usa verify_access_token."""

    async def test_sla_no_base64_decode(self):
        """Un token NON firmato con payload SUPER_ADMIN leggibile in base64 è rifiutato:
        il ruolo si legge solo da un token verificato (RS256)."""
        def b64(d: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        carico = {"tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
                  "ruolo": "SUPER_ADMIN", "iss": "verumdoc.it", "aud": "verumdoc-api",
                  "exp": 4102444800, "iat": 1700000000}
        falsi = [
            f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(carico)}.",
            f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(carico)}.{b64({'firma': 'finta'})}",
        ]
        async with client() as c:
            for falso in falsi:
                for percorso in ("/api/dashboard/sla/report", "/api/dashboard/sla/throughput"):
                    r = await c.get(percorso, headers={"Authorization": f"Bearer {falso}"})
                    assert r.status_code == 401, (percorso, r.text)
