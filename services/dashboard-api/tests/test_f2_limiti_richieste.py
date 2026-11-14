# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-03, VD-C-099) — limite di richieste al minuto per piano anche su
dashboard-api, con token utente e con chiave API, su PostgreSQL e Redis VERI.

Prima: nessun limite fuori dal gateway (una chiave API poteva interrogare senza
fine ricerche ed elenchi). Ora il piano si legge dall'ente (``tenants.piano``)
e i contatori sono su Redis: oltre il limite 429 QTA-001 con Retry-After.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, redis_disponibile
from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.quote import ImpostazioniQuote, svuota_cache_piani
from libs.shared.security.revoca import svuota_cache_revoche
from tests import infra_reale
from tests.infra_reale import crea_tenant_e_classe, richiede_pg, sql

db_url = infra_reale.db_url  # fixture condivisa

pytestmark = [richiede_pg, pytest.mark.skipif(not redis_disponibile(),
                                              reason="redis-server non installato")]

PEPPER = b"pepper-di-prova-dashboard-f2-0123456789abcdef"


@pytest.fixture(scope="module")
def url_redis():
    with avvia_redis() as url:
        yield url


@pytest.fixture
def limiti(url_redis, monkeypatch):
    """Limitatore del servizio su Redis vero, con limiti piccoli per piano."""
    from app.auth import dipendenze
    from app.auth.limiti import LIMITATORE

    monkeypatch.setattr(dipendenze, "_PEPPER", PEPPER)
    quote = ImpostazioniQuote(richieste_minuto={"starter": 3, "enterprise": 5},
                              piano_predefinito="starter")
    monkeypatch.setattr(LIMITATORE, "quote", quote)
    monkeypatch.setattr(LIMITATORE.impostazioni, "categorie", {"ricerca": {"starter": 2}})
    LIMITATORE.configura(redis_url=url_redis)
    svuota_cache_piani()
    svuota_cache_revoche()
    yield LIMITATORE
    LIMITATORE.configura(redis_url="")
    svuota_cache_piani()
    svuota_cache_revoche()


def _token(tenant: uuid.UUID) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=str(uuid.uuid4()), ruolo="OPERATORE")}


async def _chiave(url: str, tenant: uuid.UUID) -> str:
    utente = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, 'f2@example.org', 'Sistema', 'Prova', 'OPERATORE')",
              utente, tenant)
    generata = genera_chiave_api(PEPPER)
    await sql(url, "INSERT INTO public.api_keys (id, tenant_id, user_id, nome, key_hash, "
                   "key_prefix, permessi) VALUES ($1, $2, $3, 'Prova', $4, $5, "
                   "CAST('[\"read\"]' AS jsonb))",
              uuid.uuid4(), tenant, utente, generata.impronta, generata.identificativo)
    return generata.chiave


async def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_limite_al_minuto_token_e_chiave_api_stesso_ente(db_url, limiti) -> None:
    """Piano starter (3/min): token e chiave API dello stesso ente contano insieme;
    la quarta richiesta riceve 429 QTA-001 con Retry-After; un altro ente no."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    chiave = await _chiave(db_url, tenant)
    async with await _client() as c:
        stati = [
            (await c.get("/api/dashboard/versamenti", headers=_token(tenant))).status_code,
            (await c.get("/api/dashboard/aip", headers={"X-Api-Key": chiave})).status_code,
            (await c.get("/api/dashboard/versamenti", headers={"X-Api-Key": chiave})).status_code,
        ]
        oltre = await c.get("/api/dashboard/aip", headers=_token(tenant))
        libero = await c.get("/api/dashboard/versamenti", headers=_token(altro))
    assert stati == [200, 200, 200]
    assert oltre.status_code == 429, oltre.text
    assert oltre.headers["content-type"].startswith("application/problem+json")
    assert oltre.json()["codice"] == "QTA-001"
    assert oltre.json()["dettagli"] == {"limite": 3, "piano": "starter"}
    assert 1 <= int(oltre.headers["Retry-After"]) <= 60
    assert libero.status_code == 200


async def test_limite_segue_il_piano_dell_ente(db_url, limiti) -> None:
    """Il piano viene da ``tenants.piano`` (enterprise = 5 qui), non dal token."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "UPDATE public.tenants SET piano = 'enterprise' WHERE id = $1", tenant)
    async with await _client() as c:
        stati = [(await c.get("/api/dashboard/versamenti", headers=_token(tenant))).status_code
                 for _ in range(6)]
    assert stati == [200] * 5 + [429]


async def test_ricerca_limite_proprio_oltre_a_quello_generale(db_url, limiti) -> None:
    """La ricerca ha un limite in più (categoria «ricerca», 2/min qui)."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    h = _token(tenant)
    async with await _client() as c:
        r = [await c.post("/api/dashboard/search", json={}, headers=h) for _ in range(3)]
    assert [x.status_code for x in r] == [200, 200, 429]
    assert r[-1].json()["dettagli"]["categoria"] == "ricerca"


async def test_senza_credenziali_401_non_conta(db_url, limiti) -> None:
    """Le richieste senza credenziali sono rifiutate prima e non consumano il
    limite dell'ente."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        for _ in range(5):
            assert (await c.get("/api/dashboard/versamenti")).status_code == 401
        assert (await c.get("/api/dashboard/versamenti",
                            headers=_token(tenant))).status_code == 200
