# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-03, VD-C-099) — limite di richieste al minuto per piano anche su
notification (elenco eventi con chiave API o token, gestione dei webhook con
token), su PostgreSQL e Redis VERI. Prima nessun limite: un sistema poteva
interrogare l'elenco eventi senza fine.
"""
from __future__ import annotations

import base64
import os
import uuid

import httpx
import pytest
from app.api import autenticazione
from app.application import configurazione
from app.infrastructure.cifratura import Cifratore

from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, redis_disponibile
from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.quote import ImpostazioniQuote, svuota_cache_piani
from libs.shared.security.revoca import svuota_cache_revoche
from tests import infra_reale
from tests.b3_supporto import token_utente
from tests.infra_reale import richiede_pg, sql

db_url = infra_reale.db_url  # fixture condivisa

pytestmark = [pytest.mark.integration, richiede_pg,
              pytest.mark.skipif(not redis_disponibile(), reason="redis-server non installato")]

PEPPER = b"q" * 40


@pytest.fixture(scope="module")
def url_redis():
    with avvia_redis() as url:
        yield url


@pytest.fixture
def limiti(url_redis, monkeypatch):
    from app.api.limiti import LIMITATORE

    configurazione.imposta_cifratore(
        Cifratore.da_impostazioni(base64.b64encode(os.urandom(32)).decode(), "k1"))
    autenticazione.imposta_pepper(PEPPER)
    monkeypatch.setattr(LIMITATORE, "quote", ImpostazioniQuote(
        richieste_minuto={"starter": 3}, piano_predefinito="starter"))
    LIMITATORE.configura(redis_url=url_redis)
    svuota_cache_piani()
    svuota_cache_revoche()
    yield LIMITATORE
    LIMITATORE.configura(redis_url="")
    configurazione.imposta_cifratore(None)
    autenticazione.imposta_pepper(None)
    svuota_cache_piani()
    svuota_cache_revoche()


@pytest.fixture
async def client(db_url):
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://notification") as c:
        yield c


async def _ente(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    t, u = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', "
                   "'a@example.org')", t, t.hex[:16], f"t_{t.hex[:20]}")
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, $3, 'N', 'C', 'ADMIN_TENANT')", u, t, f"{u.hex[:8]}@e.org")
    return t, u


async def test_eventi_e_webhook_limite_al_minuto(client, db_url, limiti) -> None:
    """Piano starter (3/min qui): chiave API sugli eventi e token sui webhook dello
    stesso ente contano insieme; la quarta riceve 429 QTA-001 con Retry-After."""
    tenant, admin = await _ente(db_url)
    chiave = genera_chiave_api(PEPPER)
    await sql(db_url, "INSERT INTO public.api_keys (tenant_id, user_id, nome, key_hash, "
                      "key_prefix, permessi) VALUES ($1, $2, 'sistema', $3, $4, '[\"read\"]')",
              tenant, admin, chiave.impronta, chiave.identificativo)
    token = {"Authorization": f"Bearer {token_utente(tenant, 'ADMIN_TENANT', admin)}"}
    stati = [
        (await client.get("/api/v1/eventi", headers={"X-Api-Key": chiave.chiave})).status_code,
        (await client.get("/api/v1/webhook", headers=token)).status_code,
        (await client.get("/api/v1/eventi", headers=token)).status_code,
    ]
    oltre = await client.get("/api/v1/eventi", headers={"X-Api-Key": chiave.chiave})
    assert stati == [200, 200, 200]
    assert oltre.status_code == 429 and oltre.json()["codice"] == "QTA-001"
    assert oltre.json()["dettagli"] == {"limite": 3, "piano": "starter"}
    assert int(oltre.headers["Retry-After"]) >= 1
    # un altro ente non è toccato
    altro, admin2 = await _ente(db_url)
    r = await client.get("/api/v1/eventi", headers={
        "Authorization": f"Bearer {token_utente(altro, 'ADMIN_TENANT', admin2)}"})
    assert r.status_code == 200
