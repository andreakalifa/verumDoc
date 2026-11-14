# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza B5 (g5-sicurezza): revoca effettiva dei token, su
PostgreSQL reale. Un token valido di un utente disattivato o di un ente SOSPESO
o DISATTIVATO è rifiutato (401 AUTH-003) da get_current_user, entro la durata
della cache (VD_REVOCA_CACHE_SECONDI, 30 s; svuotabile).
"""
from __future__ import annotations

import uuid

import pytest
from app.auth.dependencies import get_current_user, get_redis
from app.auth.jwt_handler import create_access_token
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from libs.shared.errori import installa_gestori_errori
from libs.shared.security.revoca import svuota_cache_revoche
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/io")
    async def io(user: dict = Depends(get_current_user)) -> dict:
        return {"user_id": user["user_id"]}

    app.dependency_overrides[get_redis] = lambda: None
    installa_gestori_errori(app)  # VD-C-100: errori nel formato problem+json
    return app


@pytest.fixture(autouse=True)
def _cache_pulita():
    svuota_cache_revoche()
    yield
    svuota_cache_revoche()


async def _ente_e_utente(url: str, stato: str = "ATTIVO",
                         attivo: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, "
                   "CAST($4 AS public.tenant_stato), 'e@x.it')",
              tid, tid.hex[:11], f"t_{tid.hex[:20]}", stato)
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, 'u@x.it', 'N', 'C', 'OPERATORE', $3)", uid, tid, attivo)
    return tid, uid


async def _io(tid: uuid.UUID | str, uid: uuid.UUID | str) -> tuple[int, dict]:
    tok = create_access_token(tenant_id=str(tid), user_id=str(uid), ruolo="OPERATORE")
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/io", headers={"Authorization": f"Bearer {tok}"})
    return r.status_code, r.json()


async def test_utente_ed_ente_attivi_passano(db_url) -> None:
    tid, uid = await _ente_e_utente(db_url)
    assert (await _io(tid, uid))[0] == 200


async def test_utente_disattivato_rifiutato(db_url) -> None:
    tid, uid = await _ente_e_utente(db_url, attivo=False)
    stato, corpo = await _io(tid, uid)
    assert stato == 401 and corpo["codice"] == "AUTH-003"


@pytest.mark.parametrize("stato_ente", ["SOSPESO", "DISATTIVATO"])
async def test_ente_bloccato_rifiutato(db_url, stato_ente: str) -> None:
    tid, uid = await _ente_e_utente(db_url, stato=stato_ente)
    stato, corpo = await _io(tid, uid)
    assert stato == 401 and corpo["codice"] == "AUTH-003"


async def test_disattivazione_dopo_il_login_effettiva_alla_scadenza_della_cache(db_url) -> None:
    tid, uid = await _ente_e_utente(db_url)
    assert (await _io(tid, uid))[0] == 200
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", uid)
    # entro la durata della cache la risposta è quella memorizzata ...
    assert (await _io(tid, uid))[0] == 200
    # ... scaduta la voce (qui: svuotata) il token è rifiutato
    svuota_cache_revoche()
    assert (await _io(tid, uid))[0] == 401


async def test_cache_a_zero_revoca_immediata(db_url, monkeypatch) -> None:
    monkeypatch.setenv("VD_REVOCA_CACHE_SECONDI", "0")
    tid, uid = await _ente_e_utente(db_url)
    assert (await _io(tid, uid))[0] == 200
    await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", tid)
    assert (await _io(tid, uid))[0] == 401


async def test_identificativi_non_uuid_non_revocano(db_url) -> None:
    """Es. token interno del versamento PDND: user_id = client_id del fruitore."""
    tid, _ = await _ente_e_utente(db_url)
    assert (await _io(tid, "client-pdnd"))[0] == 200


async def test_database_non_disponibile_503(db_url) -> None:
    tid, uid = await _ente_e_utente(db_url)
    await sql(db_url, "ALTER TABLE public.users RENAME TO utenti_altrove")
    stato, corpo = await _io(tid, uid)
    assert stato == 503 and corpo["codice"] == "AUTH-503"
    assert "utenti_altrove" not in str(corpo) and "users" not in str(corpo)
