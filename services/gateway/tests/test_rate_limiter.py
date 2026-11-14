# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test del limite di richieste al minuto per piano (F1.1.3; R03 b4, VD-C-099).

Cambio voluto in R03: oltre il limite l'errore è QTA-001 del catalogo
(problem+json, Retry-After calcolato sulla finestra) invece di RATE-001; i
limiti per piano sono impostazioni (QUOTE_RICHIESTE_MINUTO); predefiniti R03 i3. Redis è
un finto in memoria con la stessa semantica
dei comandi usati (ZSET + pipeline), non un mock che ignora i comandi.
"""
from __future__ import annotations

import pytest
from app.auth.rate_limiter import RATE_LIMITS, check_rate_limit, get_limit_for_piano

from libs.shared.errori import ErroreApi
from tests.redis_finto import RedisQuote


def test_limiti_predefiniti_r03():
    """R03 i3: predefiniti alzati (60/300/1000 → 120/600/6000) per l'obiettivo di
    un milione di documenti al giorno (proposta f2, da confermare)."""
    assert RATE_LIMITS["starter"] == 120
    assert RATE_LIMITS["business"] == 600
    assert RATE_LIMITS["enterprise"] == 6000
    assert get_limit_for_piano("piano_inesistente") == 120


async def test_check_rate_limit_con_limiti_dell_ente():
    from libs.shared.security.quote import LimitiEnte

    redis = RedisQuote()
    limiti = LimitiEnte("enterprise", richieste_minuto_ente=2)
    await check_rate_limit(redis, "rl:rpm:e", limiti)
    await check_rate_limit(redis, "rl:rpm:e", limiti)
    with pytest.raises(ErroreApi) as errore:
        await check_rate_limit(redis, "rl:rpm:e", limiti)
    assert errore.value.codice == "QTA-001"
    assert errore.value.dettagli == {"limite": 2, "piano": "enterprise"}


async def test_check_rate_limit_redis_none_non_blocca():
    await check_rate_limit(None, "rl:rpm:t", "starter")


async def test_check_rate_limit_dentro_limite_non_blocca():
    redis = RedisQuote()
    for _ in range(5):
        await check_rate_limit(redis, "rl:rpm:t", 5)
    assert redis.zcard_sync("rl:rpm:t") == 5


async def test_check_rate_limit_oltre_limite_qta_001_con_retry_after():
    redis = RedisQuote()
    for _ in range(3):
        await check_rate_limit(redis, "rl:rpm:t", 3)
    with pytest.raises(ErroreApi) as errore:
        await check_rate_limit(redis, "rl:rpm:t", 3)
    assert errore.value.codice == "QTA-001" and errore.value.status == 429
    secondi = int(errore.value.intestazioni()["Retry-After"])
    assert 1 <= secondi <= 60


async def test_check_rate_limit_enti_separati():
    redis = RedisQuote()
    await check_rate_limit(redis, "rl:rpm:a", 1)
    await check_rate_limit(redis, "rl:rpm:b", 1)
    with pytest.raises(ErroreApi):
        await check_rate_limit(redis, "rl:rpm:a", 1)


async def test_check_rate_limit_piano_sconosciuto_usa_predefinito():
    redis = RedisQuote()
    for _ in range(120):  # R03 i3: predefinito starter 120
        await check_rate_limit(redis, "rl:rpm:t", "piano_inesistente")
    with pytest.raises(ErroreApi) as errore:
        await check_rate_limit(redis, "rl:rpm:t", "piano_inesistente")
    assert errore.value.dettagli["limite"] == 120


async def test_check_rate_limit_redis_giu_lascia_passare():
    """Redis non raggiungibile: la richiesta passa (avviso nel log)."""
    redis = RedisQuote(guasto=True)
    await check_rate_limit(redis, "rl:rpm:t", 1)
    await check_rate_limit(redis, "rl:rpm:t", 1)
