# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/tests/test_sprint1_tenant_isolation.py
Sprint 1 FIX-009/010/lifecycle: regression test.

FIX-009: status.py montato, stato_pdv rimosso da versamento.py
FIX-010: input_validation attiva su versamento
FIX-lifecycle: scadenze filtra per tenant_id
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.auth.dependencies import get_redis
from app.auth.jwt_handler import create_access_token
from app.main import app
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401


@pytest.fixture(autouse=True)
def _senza_rate_limit():
    """Il limitatore di richieste usa Redis: fuori da queste prove."""
    app.dependency_overrides[get_redis] = lambda: None
    yield
    app.dependency_overrides.pop(get_redis, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _token(tenant: uuid.UUID, ruolo: str = "OPERATORE") -> dict[str, str]:
    tok = create_access_token(tenant_id=str(tenant), user_id=str(uuid.uuid4()),
                              ruolo=ruolo, scopes=["versamento:ud", "read"])
    return {"Authorization": f"Bearer {tok}"}


async def crea_pdv(url: str, tenant: uuid.UUID, classe: uuid.UUID,
                   stato: str = "RICEVUTO") -> uuid.UUID:
    """PdV reale nel DB migrato (oggetto e classe obbligatori)."""
    pdv = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id, stato) "
              "VALUES ($1, 'Fattura di prova', $2, $3, $4, CAST($5 AS public.pdv_stato))",
              pdv, classe, uuid.uuid4(), tenant, stato)
    return pdv


async def crea_aip(url: str, tenant: uuid.UUID, pdv: uuid.UUID, *, scade_tra_giorni: int,
                   stato: str = "CONSERVATO") -> uuid.UUID:
    aip = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
              "hash_sha256, scadenza_conservazione) VALUES ($1, $2, $3, $4, "
              "CAST($5 AS public.aip_stato), 'k', 'b', $6, $7)",
              aip, pdv, tenant, f"urn:verumdoc:{aip}", stato, "a" * 64,
              datetime.now(UTC) + timedelta(days=scade_tra_giorni))
    return aip


class TestFIX009StatusRouterMounted:
    """FIX-009: status.py è montato in main.py; versamento.py non ha più stato_pdv."""

    async def test_main_includes_status_router(self):
        """GET /api/v1/versamento/{id} è servita da status.router: senza token 401, non 404."""
        async with _client() as c:
            r = await c.get(f"/api/v1/versamento/{uuid.uuid4()}")
        assert r.status_code == 401, r.text
        # rotte pubblicate (OpenAPI: le rotte incluse non espongono .path in FastAPI ≥0.150)
        percorsi = app.openapi()["paths"]
        assert "get" in percorsi["/api/v1/versamento/{pdv_id}"]
        assert "get" in percorsi["/api/v1/versamento/{pdv_id}/rdv"]

    async def test_main_includes_lifecycle_router(self):
        """Le rotte del ciclo di vita sono montate e protette (401 senza token)."""
        async with _client() as c:
            r = await c.get("/api/v1/lifecycle/scadenze")
            r2 = await c.post(f"/api/v1/lifecycle/{uuid.uuid4()}/avvia",
                              json={"scenario": "RETTIFICA", "motivo": "x" * 20})
        assert r.status_code == 401, r.text
        assert r2.status_code == 401, r2.text

    def test_versamento_no_stato_pdv(self):
        """versamento.py non deve più contenere la funzione stato_pdv."""
        from app.routers import versamento
        assert not hasattr(versamento, "stato_pdv"), (
            "stato_pdv deve essere rimosso da versamento.py "
            "(ora servito da status.py)"
        )

    def test_status_module_has_stato_pdv(self):
        """status.py deve avere la funzione stato_pdv."""
        from app.routers import status
        assert hasattr(status, "stato_pdv"), (
            "status.py deve esporre stato_pdv"
        )

    @richiede_pg
    async def test_status_stato_pdv_filters_tenant(self, db_url):
        """Il PdV di un cliente è visibile solo col token di quel cliente (PostgreSQL reale)."""
        tenant_a, classe = await crea_tenant_e_classe(db_url)
        tenant_b, _ = await crea_tenant_e_classe(db_url)
        pdv = await crea_pdv(db_url, tenant_a, classe)
        async with _client() as c:
            proprio = await c.get(f"/api/v1/versamento/{pdv}", headers=_token(tenant_a))
            altrui = await c.get(f"/api/v1/versamento/{pdv}", headers=_token(tenant_b))
        assert proprio.status_code == 200, proprio.text
        assert proprio.json()["pdv_id"] == str(pdv)
        assert proprio.json()["stato"] == "RICEVUTO"
        # altro cliente: 404 identico a «inesistente» (nessuna conferma dell'esistenza)
        assert altrui.status_code == 404, altrui.text
        assert altrui.json()["codice"] == "VRS-404"


class TestFIX010InputValidation:
    """FIX-010: input_validation attiva nel router versamento."""

    async def test_versamento_imports_sanitize(self):
        """Il versamento applica sanitize_string e validate_file_extension (eseguito)."""
        import json
        import uuid

        from app.services.versamento_service import RifiutoVersamento, versa_unita_documentaria

        def indice(nome: str) -> str:
            return json.dumps({"classe_documentale": "fattura", "documenti": [{
                "id": "d1", "tipo": "ALLEGATO", "nome_file": nome,
                "hash": {"impronta": "a" * 64, "algoritmo": "SHA-256"}}]})

        utente = {"tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())}
        with pytest.raises(RifiutoVersamento) as xss:
            await versa_unita_documentaria(
                indice_json=indice("<script>x.pdf"), files=[], user=utente)
        assert xss.value.codice == "SEC-003"
        with pytest.raises(RifiutoVersamento) as est:
            await versa_unita_documentaria(
                indice_json=indice("programma.exe"), files=[], user=utente)
        assert est.value.codice == "SEC-006"

    def test_sanitize_string_blocks_xss(self):
        """sanitize_string blocca input con pattern XSS."""
        from libs.shared.security.input_validation import sanitize_string
        with pytest.raises(HTTPException) as exc_info:
            sanitize_string("<script>alert('xss')</script>")
        assert exc_info.value.status_code == 400
        assert "XSS" in exc_info.value.detail["type"] or "SEC" in exc_info.value.detail["type"]

    def test_sanitize_string_blocks_sql_injection(self):
        """sanitize_string blocca input con pattern SQL injection."""
        from libs.shared.security.input_validation import sanitize_string
        with pytest.raises(HTTPException) as exc_info:
            sanitize_string("fattura; DROP TABLE aip; --")
        assert exc_info.value.status_code == 400

    def test_sanitize_string_allows_normal_input(self):
        """sanitize_string accetta input normali."""
        from libs.shared.security.input_validation import sanitize_string
        result = sanitize_string("fattura_2026")
        assert result == "fattura_2026"

    def test_validate_file_extension_blocks_exe(self):
        """validate_file_extension blocca estensioni non ammesse."""
        from libs.shared.security.input_validation import validate_file_extension
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("malware.exe")
        assert exc_info.value.status_code == 400

    def test_validate_file_extension_allows_pdf(self):
        """validate_file_extension accetta PDF."""
        from libs.shared.security.input_validation import validate_file_extension
        result = validate_file_extension("fattura_2026.pdf")
        assert result == "fattura_2026.pdf"

    def test_validate_file_extension_allows_p7m(self):
        """validate_file_extension accetta P7M (firma CAdES)."""
        from libs.shared.security.input_validation import validate_file_extension
        result = validate_file_extension("contratto.p7m")
        assert result == "contratto.p7m"

    def test_validate_file_extension_allows_xml(self):
        """validate_file_extension accetta XML."""
        from libs.shared.security.input_validation import validate_file_extension
        result = validate_file_extension("indice.xml")
        assert result == "indice.xml"

    def test_validate_file_extension_allows_tiff(self):
        """validate_file_extension accetta TIFF."""
        from libs.shared.security.input_validation import validate_file_extension
        result = validate_file_extension("scansione.tiff")
        assert result == "scansione.tiff"

    def test_sanitize_string_blocks_too_long(self):
        """sanitize_string blocca stringhe troppo lunghe."""
        from libs.shared.security.input_validation import sanitize_string
        with pytest.raises(HTTPException) as exc_info:
            sanitize_string("a" * 60, max_length=50)
        assert exc_info.value.status_code == 400

    def test_sanitize_string_blocks_path_traversal(self):
        """sanitize_string blocca path traversal."""
        from libs.shared.security.input_validation import sanitize_string
        with pytest.raises(HTTPException) as exc_info:
            sanitize_string("../../etc/passwd")
        assert exc_info.value.status_code == 400


@richiede_pg
class TestLifecycleTenantFilter:
    """Sprint 1: le scadenze filtrano per cliente (PostgreSQL reale)."""

    async def _scenario(self, url):
        tenant_a, classe = await crea_tenant_e_classe(url)
        tenant_b, _ = await crea_tenant_e_classe(url)
        aip_a = await crea_aip(
            url, tenant_a, await crea_pdv(url, tenant_a, classe), scade_tra_giorni=30)
        aip_b = await crea_aip(
            url, tenant_b, await crea_pdv(url, tenant_b, classe), scade_tra_giorni=30)
        lontano = await crea_aip(url, tenant_a, await crea_pdv(url, tenant_a, classe),
                                 scade_tra_giorni=400)
        return tenant_a, tenant_b, aip_a, aip_b, lontano

    async def test_scadenze_filters_by_tenant(self, db_url):
        """ADMIN_TENANT vede solo i propri AIP; SUPER_ADMIN e RSC vedono tutti i clienti."""
        tenant_a, _tb, aip_a, aip_b, _l = await self._scenario(db_url)
        async with _client() as c:
            admin = await c.get("/api/v1/lifecycle/scadenze?giorni=90",
                                headers=_token(tenant_a, "ADMIN_TENANT"))
            sa = await c.get("/api/v1/lifecycle/scadenze?giorni=90",
                             headers=_token(tenant_a, "SUPER_ADMIN"))
            rsc = await c.get("/api/v1/lifecycle/scadenze?giorni=90",
                              headers=_token(tenant_a, "RSC_CONSERVATORE"))
            operatore = await c.get("/api/v1/lifecycle/scadenze",
                                    headers=_token(tenant_a, "OPERATORE"))
        assert admin.status_code == 200, admin.text
        assert {i["aip_id"] for i in admin.json()["items"]} == {str(aip_a)}
        for r in (sa, rsc):
            assert r.status_code == 200, r.text
            assert {str(aip_a), str(aip_b)} <= {i["aip_id"] for i in r.json()["items"]}
        assert operatore.status_code == 403

    async def test_scadenze_has_tenant_where_clause(self, db_url):
        """La soglia in giorni si applica e l'altro cliente resta escluso anche con soglia ampia."""
        tenant_a, _tb, aip_a, aip_b, lontano = await self._scenario(db_url)
        async with _client() as c:
            corto = await c.get("/api/v1/lifecycle/scadenze?giorni=90",
                                headers=_token(tenant_a, "ADMIN_TENANT"))
            lungo = await c.get("/api/v1/lifecycle/scadenze?giorni=500",
                                headers=_token(tenant_a, "ADMIN_TENANT"))
        assert str(lontano) not in {i["aip_id"] for i in corto.json()["items"]}
        ids = {i["aip_id"] for i in lungo.json()["items"]}
        assert ids == {str(aip_a), str(lontano)}
        assert str(aip_b) not in ids
        assert lungo.json()["total"] == 2


class TestCORSWhitelist:
    """Sprint 1: CORS non usa più wildcard in dev."""

    async def test_main_no_wildcard_cors(self):
        """Origine non in elenco: nessun Access-Control-Allow-Origin; mai «*»."""
        from app.config import settings

        consentita = settings.cors_origins[0]
        async with _client() as c:
            estranea = await c.options("/api/v1/info", headers={
                "Origin": "https://attaccante.example",
                "Access-Control-Request-Method": "GET"})
            ok = await c.options("/api/v1/info", headers={
                "Origin": consentita, "Access-Control-Request-Method": "GET"})
            semplice = await c.get("/api/v1/info", headers={"Origin": "https://attaccante.example"})
        assert "access-control-allow-origin" not in estranea.headers
        assert "access-control-allow-origin" not in semplice.headers
        assert ok.headers.get("access-control-allow-origin") == consentita
        for r in (estranea, ok, semplice):
            assert r.headers.get("access-control-allow-origin") != "*"
