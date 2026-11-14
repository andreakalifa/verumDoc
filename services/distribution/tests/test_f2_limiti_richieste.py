# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-03, VD-C-099) — limite di richieste al minuto per piano anche su
distribution, con token utente e chiave API, su PostgreSQL e Redis VERI; in
più limiti propri per la ricerca e per la richiesta di un PdD (operazioni
costose: rilettura e impronta di ogni file, ZIP).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
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

PEPPER = b"pepper-di-prova-distribution-f2-0123456789abcdef"


@pytest.fixture(scope="module")
def url_redis():
    with avvia_redis() as url:
        yield url


@pytest.fixture
def limiti(url_redis, monkeypatch):
    from app.auth import dipendenze
    from app.auth.limiti import LIMITATORE
    from app.config import settings

    monkeypatch.setattr(dipendenze, "_PEPPER", PEPPER)
    monkeypatch.setattr(settings, "opensearch_url", "http://127.0.0.1:9")  # nessuna ricerca vera
    monkeypatch.setattr(LIMITATORE, "quote", ImpostazioniQuote(
        richieste_minuto={"starter": 4}, piano_predefinito="starter"))
    monkeypatch.setattr(LIMITATORE.impostazioni, "categorie",
                        {"ricerca": {"starter": 2}, "pdd": {"starter": 1}})
    LIMITATORE.configura(redis_url=url_redis)
    svuota_cache_piani()
    svuota_cache_revoche()
    yield LIMITATORE
    LIMITATORE.configura(redis_url="")
    svuota_cache_piani()
    svuota_cache_revoche()


def _h(tenant: uuid.UUID) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=str(uuid.uuid4()), ruolo="ADMIN_TENANT")}


async def _chiave(url: str, tenant: uuid.UUID) -> str:
    utente = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, 'f2d@example.org', 'Sistema', 'Prova', 'OPERATORE')",
              utente, tenant)
    generata = genera_chiave_api(PEPPER)
    await sql(url, "INSERT INTO public.api_keys (id, tenant_id, user_id, nome, key_hash, "
                   "key_prefix, permessi) VALUES ($1, $2, $3, 'Prova', $4, $5, "
                   "CAST('[\"read\"]' AS jsonb))",
              uuid.uuid4(), tenant, utente, generata.impronta, generata.identificativo)
    return generata.chiave


async def _client() -> AsyncClient:
    from app.routers.distribution import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_limite_al_minuto_token_e_chiave_api(db_url, limiti) -> None:
    """Piano starter (4/min qui): token e chiave API dello stesso ente contano
    insieme; la quinta richiesta riceve 429 QTA-001 con Retry-After."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    chiave = await _chiave(db_url, tenant)
    async with await _client() as c:
        stati = []
        for h in (_h(tenant), {"X-Api-Key": chiave}, _h(tenant), {"X-Api-Key": chiave}):
            stati.append((await c.get("/api/v2/distribution/storico", headers=h)).status_code)
        oltre = await c.get("/api/v2/distribution/storico", headers={"X-Api-Key": chiave})
    assert stati == [200, 200, 200, 200]
    assert oltre.status_code == 429 and oltre.json()["codice"] == "QTA-001"
    assert int(oltre.headers["Retry-After"]) >= 1


async def test_pdd_e_ricerca_hanno_limiti_propri(db_url, limiti) -> None:
    """Richiesta di PdD: 1/min (la seconda 429 anche se l'AIP non esiste: il
    limite si conta prima del lavoro); ricerca: 2/min."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    h = _h(tenant)
    corpo = {"aip_id": str(uuid.uuid4()), "tipo": "ESIBIZIONE", "motivo": "verifica limiti"}
    async with await _client() as c:
        primo = await c.post("/api/v2/distribution/richiedi", json=corpo, headers=h)
        secondo = await c.post("/api/v2/distribution/richiedi", json=corpo, headers=h)
    assert primo.status_code == 404, primo.text
    assert secondo.status_code == 429
    assert secondo.json()["dettagli"]["categoria"] == "pdd"
    altro, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        ricerche = [await c.post("/api/v2/distribution/search", json={}, headers=_h(altro))
                    for _ in range(3)]
    # le prime due arrivano alla ricerca (OpenSearch irraggiungibile: 503), la terza no
    assert [r.status_code for r in ricerche] == [503, 503, 429]
    assert ricerche[-1].json()["dettagli"]["categoria"] == "ricerca"
