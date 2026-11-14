# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 servizi (VD-C-084) — voucher PDND monouso (jti su Redis fino alla scadenza).

Redis reale effimero (``redis-server``) quando c'è; altrimenti il test che lo
richiede si salta con il motivo. Fruitori su PostgreSQL reale.
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError

from libs.shared.errori import ErroreApi, installa_gestori_errori
from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, redis_disponibile
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.test_pdnd import ISSUER, _voucher, chiave_pdnd  # noqa: F401 — fixture


class RedisSet:
    """SET NX EX in memoria (semantica di Redis), con guasto simulato."""

    def __init__(self, guasto: bool = False) -> None:
        self.dati: dict[str, tuple[str, int]] = {}
        self.guasto = guasto

    async def set(self, chiave: str, valore: str, *, nx: bool = False, ex: int | None = None):
        if self.guasto:
            raise RedisConnectionError("Redis non raggiungibile")
        if nx and chiave in self.dati:
            return None
        self.dati[chiave] = (valore, int(ex or 0))
        return True


async def test_consuma_jti_seconda_volta_rifiutato_con_ttl_alla_scadenza(chiave_pdnd) -> None:
    from app.routers.pdnd import PREFISSO_JTI, consuma_jti

    redis = RedisSet()
    ora = time.time()
    voucher = {"iss": ISSUER, "jti": "abc-1", "exp": int(ora) + 600, "client_id": "c"}
    await consuma_jti(voucher, redis, adesso=ora)
    [(chiave, (_, ttl))] = redis.dati.items()
    assert chiave.startswith(PREFISSO_JTI) and "abc-1" not in chiave   # impronta, non il jti
    assert 600 <= ttl <= 631
    with pytest.raises(ErroreApi) as exc:
        await consuma_jti(voucher, redis, adesso=ora)
    assert exc.value.codice == "PDND-401-RIUSATO" and exc.value.status == 401
    assert exc.value.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    # stesso jti di un altro emittente: chiave diversa
    await consuma_jti({**voucher, "iss": "altro"}, redis, adesso=ora)


async def test_consuma_jti_senza_jti_o_redis_guasto(chiave_pdnd) -> None:
    from app.routers.pdnd import consuma_jti

    with pytest.raises(ErroreApi) as exc:
        await consuma_jti({"iss": ISSUER, "exp": int(time.time()) + 60}, RedisSet())
    assert exc.value.codice == "PDND-401-RIUSATO"
    with pytest.raises(ErroreApi) as exc:
        await consuma_jti({"iss": ISSUER, "jti": "x", "exp": int(time.time()) + 60},
                          RedisSet(guasto=True))
    assert exc.value.codice == "PDND-503-REPLAY" and exc.value.status == 503


async def test_consuma_jti_disattivabile(chiave_pdnd, monkeypatch) -> None:
    from app.routers.pdnd import consuma_jti

    monkeypatch.setenv("PDND_VOUCHER_MONOUSO", "false")
    redis = RedisSet()
    voucher = {"iss": ISSUER, "jti": "x", "exp": int(time.time()) + 60}
    await consuma_jti(voucher, redis)
    await consuma_jti(voucher, redis)
    assert redis.dati == {}


@richiede_pg
@pytest.mark.skipif(not redis_disponibile(), reason="redis-server non installato")
async def test_voucher_riusato_sull_endpoint_con_redis_vero(db_url, chiave_pdnd) -> None:
    """Sull'e-service 2 vero: il primo uso passa, lo stesso voucher ripresentato no,
    un voucher nuovo dello stesso fruitore sì."""
    import redis.asyncio as aioredis
    from app.auth.dependencies import get_redis
    from app.routers.pdnd import router

    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "INSERT INTO public.pdnd_fruitori (client_id, tenant_id) "
                      "VALUES ('client-jti', $1)", tenant)
    aip = uuid.uuid4()
    with avvia_redis() as url:
        redis = aioredis.from_url(url, decode_responses=True)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_redis] = lambda: redis
        installa_gestori_errori(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            voucher = _voucher("client-jti")
            primo = await c.get(f"/api/pdnd/v1/aip/{aip}",
                                headers={"Authorization": f"Bearer {voucher}"})
            secondo = await c.get(f"/api/pdnd/v1/aip/{aip}",
                                  headers={"Authorization": f"Bearer {voucher}"})
            nuovo = await c.get(f"/api/pdnd/v1/aip/{aip}",
                                headers={"Authorization": f"Bearer {_voucher('client-jti')}"})
        await redis.aclose()
    assert primo.json()["codice"] == "PDND-404"            # voucher accettato, AIP inesistente
    assert secondo.status_code == 401 and secondo.json()["codice"] == "PDND-401-RIUSATO"
    assert nuovo.json()["codice"] == "PDND-404"
