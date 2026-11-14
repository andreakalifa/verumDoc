# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza N1 (g6-ultime) — responsabile di una chiave API, su
PostgreSQL reale.

Il responsabile indicato (user_id) deve essere un utente ATTIVO dello stesso
ente con ruolo NON superiore a quello di chi genera la chiave: un
ADMIN_TENANT o un RSC_CONSERVATORE non attribuisce una chiave (e quindi
versamenti e audit) a un SUPER_ADMIN. Altrimenti 403 e nessuna chiave creata;
un utente di un altro ente resta 404 (l'indirizzo non rivela altri enti).
"""
from __future__ import annotations

import uuid

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient

from libs.shared.security.revoca import svuota_cache_revoche
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


@pytest.fixture(autouse=True)
def _pepper_e_cache(monkeypatch):
    from app.routers import utenti

    monkeypatch.setattr(utenti, "_PEPPER", b"pepper-di-prova-" * 4)
    svuota_cache_revoche()
    yield
    svuota_cache_revoche()


async def _ente(url: str) -> uuid.UUID:
    tid = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', 'a@b.it')",
              tid, tid.hex[:11], f"t_{tid.hex[:20]}")
    return tid


async def _utente(url: str, tid: uuid.UUID, ruolo: str, attivo: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, $3, 'Nome', 'Cognome', CAST($4 AS public.user_ruolo), $5)",
              uid, tid, f"{uid.hex[:8]}@x.it", ruolo, attivo)
    return uid


async def _genera(tid: uuid.UUID, chiamante: uuid.UUID, ruolo: str,
                  responsabile: uuid.UUID) -> tuple[int, dict]:
    tok = create_access_token(tenant_id=str(tid), user_id=str(chiamante), ruolo=ruolo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/onboarding/tenant/{tid}/api-keys",
                         json={"descrizione": "Protocollo", "user_id": str(responsabile)},
                         headers={"Authorization": f"Bearer {tok}"})
    return r.status_code, r.json() if r.content else {}


async def _chiavi(url: str, tid: uuid.UUID) -> int:
    return len(await sql(url, "SELECT 1 FROM public.api_keys WHERE tenant_id = $1", tid))


@pytest.mark.parametrize("gestore,superiore", [
    ("ADMIN_TENANT", "SUPER_ADMIN"),
    ("ADMIN_TENANT", "RSC_CONSERVATORE"),
    ("RSC_CONSERVATORE", "SUPER_ADMIN"),
])
async def test_responsabile_di_ruolo_superiore_403(db_url, gestore, superiore) -> None:
    tid = await _ente(db_url)
    chi = await _utente(db_url, tid, gestore)
    alto = await _utente(db_url, tid, superiore)
    stato, corpo = await _genera(tid, chi, gestore, alto)
    assert stato == 403, corpo
    assert corpo["codice"] == "AUTH-010"
    assert await _chiavi(db_url, tid) == 0


@pytest.mark.parametrize("gestore,responsabile", [
    ("ADMIN_TENANT", "OPERATORE"),
    ("ADMIN_TENANT", "VIEWER"),
    ("ADMIN_TENANT", "ADMIN_TENANT"),        # pari ruolo: non superiore
    ("RSC_CONSERVATORE", "ADMIN_TENANT"),
    ("SUPER_ADMIN", "SUPER_ADMIN"),
    ("SUPER_ADMIN", "OPERATORE"),
])
async def test_responsabile_di_ruolo_non_superiore_201(db_url, gestore, responsabile) -> None:
    tid = await _ente(db_url)
    chi = await _utente(db_url, tid, gestore)
    resp = await _utente(db_url, tid, responsabile)
    stato, corpo = await _genera(tid, chi, gestore, resp)
    assert stato == 201, corpo
    righe = await sql(db_url, "SELECT user_id FROM public.api_keys WHERE id = $1",
                      uuid.UUID(corpo["id"]))
    assert righe[0]["user_id"] == resp


async def test_responsabile_non_attivo_403(db_url) -> None:
    tid = await _ente(db_url)
    chi = await _utente(db_url, tid, "ADMIN_TENANT")
    spento = await _utente(db_url, tid, "OPERATORE", attivo=False)
    stato, corpo = await _genera(tid, chi, "ADMIN_TENANT", spento)
    assert stato == 403 and corpo["codice"] == "AUTH-010"
    assert await _chiavi(db_url, tid) == 0


async def test_responsabile_di_altro_ente_resta_404(db_url) -> None:
    a, b = await _ente(db_url), await _ente(db_url)
    chi = await _utente(db_url, a, "ADMIN_TENANT")
    altro = await _utente(db_url, b, "OPERATORE")
    stato, _ = await _genera(a, chi, "ADMIN_TENANT", altro)
    assert stato == 404
    assert await _chiavi(db_url, a) == 0
