# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Redis in memoria per i test delle quote (R03 b4): ZSET con pipeline, GET,
INCR, EXPIRE con la semantica di Redis per i comandi usati da
``libs.shared.security.quote``. ``guasto=True`` simula Redis non raggiungibile
(solleva ``redis.exceptions.ConnectionError``).

R03 f1: ``eval`` dei due script Lua della quota giornaliera (prenotazione e
rilascio), riconosciuti dal commento iniziale ed eseguiti senza ``await``
intermedi, quindi atomici come in Redis. Gli script veri si provano su un Redis
reale (``test_f1_correzioni_canali.py``, ``libs/shared/security/tests``).
"""
from __future__ import annotations

from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError


class _Pipeline:
    def __init__(self, redis: RedisQuote) -> None:
        self._r = redis
        self._comandi: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, nome: str):
        def _accoda(*args: Any, **kwargs: Any) -> _Pipeline:
            self._comandi.append((nome, args, kwargs))
            return self
        return _accoda

    async def execute(self) -> list[Any]:
        if self._r.guasto:
            raise RedisConnectionError("Redis non raggiungibile (finto)")
        return [getattr(self._r, f"_{n}")(*a, **k) for n, a, k in self._comandi]


class RedisQuote:
    def __init__(self, *, guasto: bool = False) -> None:
        self.guasto = guasto
        self.zset: dict[str, dict[str, float]] = {}
        self.valori: dict[str, str] = {}
        self.scadenze: dict[str, int] = {}

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)

    def _controlla(self) -> None:
        if self.guasto:
            raise RedisConnectionError("Redis non raggiungibile (finto)")

    # comandi in pipeline
    def _zremrangebyscore(self, chiave: str, minimo: float, massimo: float) -> int:
        z = self.zset.setdefault(chiave, {})
        via = [m for m, s in z.items() if minimo <= s <= massimo]
        for m in via:
            del z[m]
        return len(via)

    def _zcard(self, chiave: str) -> int:
        return len(self.zset.get(chiave, {}))

    def _zrange(self, chiave: str, inizio: int, fine: int, withscores: bool = False) -> list:
        voci = sorted(self.zset.get(chiave, {}).items(), key=lambda v: v[1])
        voci = voci[inizio: (fine + 1) if fine >= 0 else None]
        return voci if withscores else [m for m, _ in voci]

    def _zadd(self, chiave: str, mappa: dict[str, float]) -> int:
        self.zset.setdefault(chiave, {}).update(mappa)
        return len(mappa)

    def _expire(self, chiave: str, secondi: int) -> bool:
        self.scadenze[chiave] = secondi
        return True

    def zcard_sync(self, chiave: str) -> int:
        return self._zcard(chiave)

    # comandi diretti
    async def get(self, chiave: str) -> str | None:
        self._controlla()
        return self.valori.get(chiave)

    async def incr(self, chiave: str) -> int:
        self._controlla()
        valore = int(self.valori.get(chiave, "0")) + 1
        self.valori[chiave] = str(valore)
        return valore

    async def expire(self, chiave: str, secondi: int) -> bool:
        self._controlla()
        return self._expire(chiave, secondi)

    async def set(self, chiave: str, valore: str, *, nx: bool = False,
                  ex: int | None = None) -> bool | None:
        """SET con NX/EX (R04: jti dei voucher PDND monouso)."""
        self._controlla()
        if nx and chiave in self.valori:
            return None
        self.valori[chiave] = str(valore)
        if ex is not None:
            self.scadenze[chiave] = int(ex)
        return True

    async def ttl(self, chiave: str) -> int:
        """TTL (R07, rallentamento): -2 se la chiave non c'è."""
        self._controlla()
        return self.scadenze.get(chiave, -1) if chiave in self.valori else -2

    async def delete(self, *chiavi: str) -> int:
        self._controlla()
        tolte = 0
        for c in chiavi:
            tolte += int(self.valori.pop(c, None) is not None)
            self.scadenze.pop(c, None)
        return tolte

    async def eval(self, script: str, numkeys: int, *argomenti: Any) -> int:
        self._controlla()
        chiavi, valori = argomenti[:numkeys], argomenti[numkeys:]
        prima = script.splitlines()[0]
        chiave = str(chiavi[0])
        if "verumdoc:prenota_pdv" in prima:
            valore = int(self.valori.get(chiave, "0")) + 1
            self.scadenze[chiave] = int(valori[1])
            if valore > int(valori[0]):
                self.valori[chiave] = str(valore - 1)
                return -(valore - 1)
            self.valori[chiave] = str(valore)
            return valore
        if "verumdoc:rilascia_pdv" in prima:
            valore = int(self.valori.get(chiave, "0"))
            if valore > 0:
                self.valori[chiave] = str(valore - 1)
                return valore - 1
            return 0
        raise NotImplementedError(f"script non riconosciuto dal Redis finto: {prima!r}")
