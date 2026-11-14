# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f1 (REV-02) — prenotazione atomica della quota di PdV al giorno.

Gli script Lua si provano su un Redis REALE (``redis-server`` effimero avviato da
``libs.shared.limiti_richieste.redis_di_prova``; il test si salta, con motivo, se
il programma non è installato): N prenotazioni concorrenti
dello stesso ente non superano mai il limite; il rilascio restituisce il posto e
non va mai sotto zero; Redis non raggiungibile → fail-open (None).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, porta_chiusa, redis_disponibile
from libs.shared.security.quote import (
    QuotaSuperata,
    chiave_pdv_giorno,
    prenota_pdv,
    rilascia_pdv,
)

T = "00000000-0000-0000-0000-0000000000aa"
ADESSO = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def url_redis():
    if not redis_disponibile():
        pytest.skip("redis-server non installato: script Lua non provati su Redis reale")
    with avvia_redis() as url:
        yield url


@pytest.fixture
async def redis(url_redis):
    import redis.asyncio as aioredis

    client = aioredis.from_url(url_redis, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


async def test_prenotazioni_concorrenti_non_superano_il_limite(redis):
    async def una():
        try:
            return await prenota_pdv(redis, T, limite=5, piano="starter", adesso=ADESSO)
        except QuotaSuperata as exc:
            return exc

    esiti = await asyncio.gather(*(una() for _ in range(40)))
    prese = [e for e in esiti if not isinstance(e, QuotaSuperata)]
    rifiuti = [e for e in esiti if isinstance(e, QuotaSuperata)]
    assert len(prese) == 5 and len(rifiuti) == 35
    assert all(r.codice == "QTA-002" and r.retry_after >= 1 for r in rifiuti)
    chiave = chiave_pdv_giorno(T, ADESSO, "Europe/Rome")
    assert await redis.get(chiave) == "5"          # i rifiuti non lasciano incrementi
    assert 0 < await redis.ttl(chiave) <= 2 * 86400
    assert sorted(p.totale for p in prese) == [1, 2, 3, 4, 5]


async def test_rilascio_restituisce_il_posto_ed_e_idempotente(redis):
    prima = await prenota_pdv(redis, T, limite=1, piano="starter", adesso=ADESSO)
    with pytest.raises(QuotaSuperata):
        await prenota_pdv(redis, T, limite=1, piano="starter", adesso=ADESSO)
    await rilascia_pdv(redis, prima)
    await rilascia_pdv(redis, prima)            # secondo rilascio: nessun effetto
    chiave = chiave_pdv_giorno(T, ADESSO, "Europe/Rome")
    assert await redis.get(chiave) == "0"
    seconda = await prenota_pdv(redis, T, limite=1, piano="starter", adesso=ADESSO)
    assert seconda is not None and seconda.totale == 1


async def test_rilascio_non_va_mai_sotto_zero(redis):
    prenotazione = await prenota_pdv(redis, T, limite=3, piano="x", adesso=ADESSO)
    await redis.delete(prenotazione.chiave)     # chiave scaduta nel frattempo
    await rilascia_pdv(redis, prenotazione)
    assert await redis.get(prenotazione.chiave) is None


async def test_redis_non_raggiungibile_fail_open():
    import redis.asyncio as aioredis

    client = aioredis.from_url(porta_chiusa(), socket_connect_timeout=0.5)
    try:
        assert await prenota_pdv(client, T, limite=1, piano="x", adesso=ADESSO) is None
    finally:
        await client.aclose()
    assert await prenota_pdv(None, T, limite=0, piano="x") is None
