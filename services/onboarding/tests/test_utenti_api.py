# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test API utenti tenant — F3-03.
Verifica GET lista utenti, POST crea utente, regole RBAC per ADMIN_TENANT.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient

TENANT_ID   = "00000000-0000-0000-0000-000000000001"
TENANT_ID_B = "bbbbbbbb-0000-0000-0000-000000000002"
USER_ID     = "00000000-0000-0000-0000-000000000010"


def _tok(ruolo: str, tenant_id: str = TENANT_ID,
         scopes: list[str] | None = None) -> str:
    return create_access_token(
        tenant_id=tenant_id, user_id=USER_ID,
        ruolo=ruolo, scopes=scopes or ["read", "admin:tenant"],
    )


def _mock_db_utenti(rows=None):
    session = AsyncMock()
    res = MagicMock()  # il risultato di execute() espone fetchall() sincrono
    res.fetchall.return_value = rows or []
    session.execute.return_value = res
    session.commit = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__  = AsyncMock(return_value=False)
    return cm


class TestListaUtenti:

    @pytest.mark.asyncio
    async def test_lista_utenti_super_admin_200(self):
        """SUPER_ADMIN può vedere utenti di qualsiasi tenant."""
        with patch("app.routers.utenti.get_async_session",
                   return_value=_mock_db_utenti()):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                r = await c.get(
                    f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti",
                    headers={"Authorization": f"Bearer {_tok('SUPER_ADMIN', scopes=['*'])}"},
                )
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "total" in body

    @pytest.mark.asyncio
    async def test_lista_utenti_admin_tenant_proprio_tenant_200(self):
        """ADMIN_TENANT può vedere utenti del proprio tenant."""
        with patch("app.routers.utenti.get_async_session",
                   return_value=_mock_db_utenti()):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                r = await c.get(
                    f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti",
                    headers={"Authorization": f"Bearer {_tok('ADMIN_TENANT')}"},
                )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_lista_utenti_senza_token_401(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_lista_utenti_viewer_403(self):
        """VIEWER non può vedere la lista utenti."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti",
                headers={"Authorization": f"Bearer {_tok('VIEWER', scopes=['read'])}"},
            )
        assert r.status_code == 403

class TestCreaUtente:

    @pytest.mark.asyncio
    async def test_crea_utente_admin_tenant_non_puo_creare_in_altro_tenant(self):
        """
        ADMIN_TENANT non può creare utenti in un tenant diverso dal proprio.
        Questo è un requisito di sicurezza fondamentale.
        """
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                # Tenant B — diverso da TENANT_ID del token
                f"/api/v1/onboarding/tenant/{TENANT_ID_B}/utenti",
                json={
                    "email": "hacker@example.com",
                    "nome": "Tentativo non autorizzato",
                    "ruolo": "OPERATORE",
                },
                headers={
                    "Authorization": f"Bearer {_tok('ADMIN_TENANT', tenant_id=TENANT_ID)}"
                },
            )
        # 404 fra clienti diversi (REGOLE §14.4): l'indirizzo non rivela se esiste
        assert r.status_code == 404, (
            "ADMIN_TENANT non deve poter creare utenti in un tenant diverso dal proprio!"
        )

    @pytest.mark.asyncio
    async def test_crea_utente_ruolo_non_valido_422(self):
        """Ruolo non OPERATORE/VIEWER → 422 (Pydantic validation)."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti",
                json={
                    "email": "test@example.com",
                    "nome": "Test",
                    "ruolo": "SUPER_ADMIN",  # non consentito
                },
                headers={"Authorization": f"Bearer {_tok('ADMIN_TENANT')}"},
            )
        assert r.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_crea_utente_senza_token_401(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti",
                json={"email": "t@t.com", "nome": "T", "ruolo": "VIEWER"},
            )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_crea_utente_viewer_403(self):
        """VIEWER non può creare utenti."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(
                f"/api/v1/onboarding/tenant/{TENANT_ID}/utenti",
                json={"email": "t@t.com", "nome": "T", "ruolo": "VIEWER"},
                headers={"Authorization": f"Bearer {_tok('VIEWER', scopes=['read'])}"},
            )
        assert r.status_code == 403


# I casi di successo (201, struttura della lista, 409, codice fiscale) sono su
# PostgreSQL reale in test_g2_utenti_chiavi.py: i mock della sessione non
# provavano né i vincoli né le colonne vere (R02, g2-auth).
