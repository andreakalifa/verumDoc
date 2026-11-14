# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/tests/test_deepreview.py
Code review finale 20/03/2026 — regression per CRIT-001/002, HIGH-004/005, MED-005.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from tests.dati_prova import BUCKET, client, crea_aip, token
from tests.infra_reale import (  # noqa: F401
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)


@pytest.fixture
def s3(s3_server, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    c = client_s3(s3_server)
    try:
        c.create_bucket(Bucket=BUCKET)
    except c.exceptions.BucketAlreadyOwnedByYou:
        pass
    return c

# g3-test (VD-C-030): i test che leggevano il sorgente (inspect.getsource) sono
# ora prove di comportamento sugli endpoint veri, con PostgreSQL reale e S3 (moto).


# ── CRIT-001: download.py must require auth + tenant filter ──────────

class TestCRIT001DownloadAuth:
    """Tutti e 5 gli endpoint download devono avere auth + tenant."""

    def test_lista_file_has_authorization(self):
        from app.routers.download import lista_file_aip
        sig = inspect.signature(lista_file_aip)
        assert "authorization" in sig.parameters

    def test_download_manifest_has_authorization(self):
        from app.routers.download import download_manifest
        sig = inspect.signature(download_manifest)
        assert "authorization" in sig.parameters

    def test_download_sincro_has_authorization(self):
        from app.routers.download import download_sincro
        sig = inspect.signature(download_sincro)
        assert "authorization" in sig.parameters

    def test_download_rdv_has_authorization(self):
        from app.routers.download import download_rdv
        sig = inspect.signature(download_rdv)
        assert "authorization" in sig.parameters

    def test_download_completo_has_authorization(self):
        from app.routers.download import download_completo
        sig = inspect.signature(download_completo)
        assert "authorization" in sig.parameters

    @richiede_pg
    async def test_all_queries_filter_tenant(self, db_url, s3):
        """I 5 scaricamenti di un AIP di un altro ente danno 404 (la query filtra per
        ente) senza toccare l'archivio; il proprietario ottiene i file."""
        t_a, classe = await crea_tenant_e_classe(db_url)
        t_b, _ = await crea_tenant_e_classe(db_url)
        aip = await crea_aip(db_url, t_a, classe, s3=s3)
        percorsi = ["files", "download/manifest", "download/sincro", "download/rdv",
                    "download/completo"]
        async with client() as c:
            for ruolo in ("ADMIN_TENANT", "OPERATORE", "SUPER_ADMIN", "RSC_CONSERVATORE"):
                for p in percorsi:
                    r = await c.get(f"/api/dashboard/aip/{aip}/{p}", headers=token(ruolo, t_b))
                    # senza tenant_id si legge solo l'ente del token, anche per i profili globali
                    assert r.status_code == 404, (ruolo, p, r.text)
                    assert r.json()["codice"] == "DL-404", (ruolo, p)
            proprio = token("OPERATORE", t_a)
            assert (
                await c.get(f"/api/dashboard/aip/{aip}/files", headers=proprio)).status_code == 200
            m = await c.get(f"/api/dashboard/aip/{aip}/download/manifest", headers=proprio)
            assert m.status_code == 200 and m.content == b'{"m":1}'
            z = await c.get(f"/api/dashboard/aip/{aip}/download/completo", headers=proprio)
            assert z.status_code == 200, z.text
            assert z.headers["content-type"].startswith("application/zip")
            # RdV: l'AIP esiste ma il RdV non è emesso → 404 distinto
            rdv = await c.get(f"/api/dashboard/aip/{aip}/download/rdv", headers=proprio)
            assert rdv.json()["codice"] == "DL-404-RDV"

    def test_require_download_auth_rejects_none(self):
        from app.routers.download import _require_download_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_download_auth(None)
        assert exc.value.status_code == 401

    def test_require_download_auth_rejects_empty(self):
        from app.routers.download import _require_download_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_download_auth("Bearer ")
        assert exc.value.status_code == 401


# ── CRIT-002: sla.py report+throughput must require auth ────────────

class TestCRIT002SLAAuth:
    def test_sla_report_has_authorization(self):
        from app.routers.sla import sla_report
        sig = inspect.signature(sla_report)
        assert "authorization" in sig.parameters

    def test_sla_throughput_has_authorization(self):
        from app.routers.sla import sla_throughput
        sig = inspect.signature(sla_throughput)
        assert "authorization" in sig.parameters

    @richiede_pg
    async def test_sla_report_calls_auth(self, db_url):
        """Report SLA: 401 senza token o con token non firmato, 403 per i profili
        dell'ente, 200 per SUPER_ADMIN e RSC_CONSERVATORE."""
        async with client() as c:
            assert (await c.get("/api/dashboard/sla/report")).status_code == 401
            assert (await c.get("/api/dashboard/sla/report",
                                headers={"Authorization": "Bearer a.b.c"})).status_code == 401
            for ruolo in ("ADMIN_TENANT", "OPERATORE", "VIEWER"):
                r = await c.get("/api/dashboard/sla/report", headers=token(ruolo))
                assert r.status_code == 403, ruolo
            for ruolo in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
                r = await c.get("/api/dashboard/sla/report", headers=token(ruolo))
                assert r.status_code == 200, (ruolo, r.text)
                assert "conformita" in r.json()


# ── HIGH-004/005: dashboard.py must have /contratti + /nomine-rsc ────

class TestHIGH004005MissingEndpoints:
    def test_contratti_endpoint_exists(self):
        from app.routers.dashboard import lista_contratti
        sig = inspect.signature(lista_contratti)
        assert "authorization" in sig.parameters

    def test_nomine_rsc_endpoint_exists(self):
        from app.routers.dashboard import nomine_rsc
        sig = inspect.signature(nomine_rsc)
        assert "authorization" in sig.parameters

    @richiede_pg
    async def test_contratti_requires_auth(self, db_url):
        """Contratti: 401 senza token; ogni profilo vede solo i contratti del proprio
        ente, anche indicando tenant_id di un altro ente."""
        t_a, _ = await crea_tenant_e_classe(db_url)
        t_b, _ = await crea_tenant_e_classe(db_url)
        for tid, numero in ((t_a, "C-A-1"), (t_b, "C-B-1")):
            await sql(db_url, "INSERT INTO public.contracts (tenant_id, numero_contratto, "
                              "data_stipula, piano) VALUES ($1, $2, CURRENT_DATE, 'business')",
                      tid, f"{numero}-{uuid.uuid4().hex[:6]}")
        async with client() as c:
            assert (await c.get("/api/dashboard/contratti")).status_code == 401
            r = await c.get(f"/api/dashboard/contratti?tenant_id={t_b}",
                            headers=token("ADMIN_TENANT", t_a))
            g = await c.get(f"/api/dashboard/contratti?tenant_id={t_b}",
                            headers=token("SUPER_ADMIN"))
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1
        assert {i["tenant_id"] for i in r.json()["items"]} == {str(t_a)}
        assert {i["tenant_id"] for i in g.json()["items"]} == {str(t_b)}

    @richiede_pg
    async def test_nomine_rsc_requires_role(self, db_url):
        """Nomine RSC: solo SUPER_ADMIN e RSC_CONSERVATORE (403 per gli altri)."""
        t_a, _ = await crea_tenant_e_classe(db_url)
        async with client() as c:
            assert (await c.get("/api/dashboard/nomine-rsc")).status_code == 401
            for ruolo in ("ADMIN_TENANT", "OPERATORE", "VIEWER"):
                r = await c.get("/api/dashboard/nomine-rsc", headers=token(ruolo, t_a))
                assert r.status_code == 403, ruolo
            for ruolo in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
                r = await c.get(f"/api/dashboard/nomine-rsc?tenant_id={t_a}",
                                headers=token(ruolo))
                assert r.status_code == 200, (ruolo, r.text)
                assert r.json() == {"tenant_id": str(t_a), "nomina_corrente": None,
                                    "storico": []}
