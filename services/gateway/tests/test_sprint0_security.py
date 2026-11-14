# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/tests/test_sprint0_security.py
Sprint 0 FIX-004: vedi test_vdc002_dev_auth.py.
Sprint 0 FIX-005: PDND voucher verifica firma JWT.
Sprint 0 FIX-006: stato_pdv filtra per tenant_id.
"""
from __future__ import annotations

import json
import time

import pytest

from libs.shared.errori import ErroreApi
from tests.infra_reale import db_url, richiede_pg  # noqa: F401

# FIX-004 (dev-token): i vecchi test confrontavano stringhe fisse senza toccare
# il codice. Sostituiti dai test reali in test_vdc002_dev_auth.py (VD-C-002).


class TestPDNDVoucherValidation:
    """FIX-005: _validate_pdnd_voucher verifica firma JWT.

    R03 (VD-C-092/100): il rifiuto è un ``ErroreApi`` del catalogo (PDND-401),
    non più un ``HTTPException`` con il corpo in stile OAuth."""

    def test_no_token_returns_401(self):
        from app.routers.pdnd import _validate_pdnd_voucher
        with pytest.raises(ErroreApi) as exc_info:
            _validate_pdnd_voucher(None)
        assert exc_info.value.status == 401

    def test_empty_bearer_returns_401(self):
        from app.routers.pdnd import _validate_pdnd_voucher
        with pytest.raises(ErroreApi) as exc_info:
            _validate_pdnd_voucher("Bearer ")
        assert exc_info.value.status == 401

    def test_valid_gateway_token_accepted_in_dev(self):
        """In dev: token firmato con chiave gateway viene accettato."""
        from app.auth.jwt_handler import create_access_token
        from app.routers.pdnd import _validate_pdnd_voucher
        token = create_access_token(
            tenant_id="t-pdnd", user_id="ente-test",
            ruolo="API_SYSTEM", scopes=["versamento:ud"],
        )
        payload = _validate_pdnd_voucher(f"Bearer {token}")
        assert payload["tenant_id"] == "t-pdnd"

    def test_dev_pdnd_test_token_non_firmato_rifiutato(self):
        """VD-C-021 (R03): il token non firmato con sub=dev-pdnd-test, prima
        accettato fuori produzione, ora è rifiutato (REGOLE §19.1)."""
        import base64
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload_data = {
            "sub": "dev-pdnd-test",
            "client_id": "ente-test-001",
            "exp": int(time.time()) + 3600,
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b"=").decode()
        token = f"{header}.{payload}.fake-sig"
        from app.routers.pdnd import _validate_pdnd_voucher
        with pytest.raises(ErroreApi) as exc_info:
            _validate_pdnd_voucher(f"Bearer {token}")
        assert exc_info.value.status == 401

    def test_random_dev_token_without_sub_rejected(self):
        """Token dev senza sub=dev-pdnd-test rifiutato anche in dev."""
        import base64
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload_data = {
            "sub": "hacker",
            "exp": int(time.time()) + 3600,
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b"=").decode()
        token = f"{header}.{payload}.fake-sig"
        from app.routers.pdnd import _validate_pdnd_voucher
        with pytest.raises(ErroreApi) as exc_info:
            _validate_pdnd_voucher(f"Bearer {token}")
        assert exc_info.value.status == 401


@richiede_pg
class TestStatoPdvTenantFilter:
    """FIX-006: stato e RdV di un PdV filtrano per cliente (PostgreSQL reale)."""

    async def test_query_includes_tenant_id(self, db_url):
        """Un PdV di un altro cliente dà la stessa risposta di un PdV inesistente,
        sia per lo stato sia per il RdV (nessuna conferma dell'esistenza)."""
        import uuid

        from app.auth.dependencies import get_redis
        from app.auth.jwt_handler import create_access_token
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        from tests.infra_reale import crea_tenant_e_classe, sql

        tenant_a, classe = await crea_tenant_e_classe(db_url)
        tenant_b, _ = await crea_tenant_e_classe(db_url)
        pdv = uuid.uuid4()
        await sql(db_url,
                  "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                  "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant_a)

        def intestazioni(tenant):
            tok = create_access_token(tenant_id=str(tenant), user_id=str(uuid.uuid4()),
                                      ruolo="OPERATORE", scopes=["read"])
            return {"Authorization": f"Bearer {tok}"}

        app.dependency_overrides[get_redis] = lambda: None
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                altrui = await c.get(f"/api/v1/versamento/{pdv}", headers=intestazioni(tenant_b))
                inesistente = await c.get(f"/api/v1/versamento/{uuid.uuid4()}",
                                          headers=intestazioni(tenant_b))
                rdv_altrui = await c.get(f"/api/v1/versamento/{pdv}/rdv",
                                         headers=intestazioni(tenant_b))
                rdv_proprio = await c.get(f"/api/v1/versamento/{pdv}/rdv",
                                          headers=intestazioni(tenant_a))
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert altrui.status_code == inesistente.status_code == 404
        assert altrui.json()["codice"] == inesistente.json()["codice"] == "VRS-404"
        assert rdv_altrui.status_code == 404
        # il proprio PdV esiste ma il RdV non è ancora emesso: 409, non 404
        assert rdv_proprio.status_code == 409, rdv_proprio.text
        assert rdv_proprio.json()["codice"] == "VRS-409"
