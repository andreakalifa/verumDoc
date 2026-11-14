# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/limiti.py — limiti condivisi fra le repliche del servizio
di firma (R16, VD-C-168, VD-C-166).

Le prove di connessione dei fornitori (``FIRMA_PROVE_PER_ORA``) e i tentativi di firma
personale con OTP (``FIRMA_PERSONALE_TENTATIVI``, per persona) consumano firme o OTP del
prestatore: il limite è una RISERVA ATOMICA in Redis (``libs.shared.security.riserva``,
§31 R10 (7), R12b revisione (1)), uguale per tutte le repliche. Fino a R14 il limite delle
prove era per processo (``deque``): con N repliche valeva N volte.

Redis: ``REDIS_URL``; obbligatorio in produzione (il servizio non parte senza,
``registro_fornitori.problemi_avvio``). Fuori produzione, senza ``REDIS_URL``, vale il
contatore del processo (sviluppo e test unitari). Redis configurato ma non raggiungibile:
l'operazione si rifiuta (``FIRMA-503-LIMITE``), perché senza contatore il limite non c'è.
"""
from __future__ import annotations

import hashlib
import time
from collections import deque
from typing import Any

import structlog

from app.config import settings
from libs.shared.errori import ErroreApi
from libs.shared.security.riserva import LimiteNonVerificabile, restituisci, riserva

logger = structlog.get_logger(__name__)

PREFISSO = "vd:firma:"
FINESTRA_PROVE = 3600
FINESTRA_PERSONALE = 900

_locali: dict[str, deque[float]] = {}
_redis: Any = None


def imposta_redis(cliente: Any) -> None:
    """Sostituisce il client Redis (test); None = di nuovo da ``REDIS_URL``."""
    global _redis
    _redis = cliente


def _cliente() -> Any:
    global _redis
    if _redis is None and settings.redis_url:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.redis_url, socket_timeout=2,
                                   socket_connect_timeout=2)
    return _redis


def _locale(chiave: str, limite: int, finestra: int) -> bool:
    """Contatore del processo (solo senza Redis, fuori produzione)."""
    adesso = time.monotonic()
    coda = _locali.setdefault(chiave, deque())
    while coda and coda[0] < adesso - finestra:
        coda.popleft()
    if len(coda) >= max(1, limite):
        return False
    coda.append(adesso)
    return True


def azzera_locali() -> None:
    _locali.clear()


async def _riserva(chiave: str, limite: int, finestra: int, codice: str, messaggio: str
                   ) -> None:
    r = _cliente()
    if r is None:
        if settings.in_produzione:
            raise ErroreApi("FIRMA-503-LIMITE", "Limite non verificabile: REDIS_URL assente")
        ammesso = _locale(chiave, limite, finestra)
    else:
        try:
            ammesso = await riserva(r, PREFISSO + chiave, limite=limite,
                                    finestra_secondi=finestra)
        except LimiteNonVerificabile as exc:
            logger.error("limite_firma_non_verificabile", errore=str(exc)[:100])
            raise ErroreApi("FIRMA-503-LIMITE") from None
    if not ammesso:
        raise ErroreApi(codice, messaggio, headers={"Retry-After": str(finestra)})


async def riserva_prova() -> None:
    """Una prova di connessione (tutte le repliche insieme, finestra di un'ora)."""
    await _riserva("prove", settings.firma_prove_per_ora, FINESTRA_PROVE, "FIRMA-429-PROVA",
                   "Troppe prove di connessione: riprovare più tardi")


def _persona(user_id: str) -> str:
    # mai l'identificativo in chiaro nelle chiavi di Redis
    return hashlib.sha256(user_id.encode()).hexdigest()[:32]


async def riserva_personale(user_id: str, operazione: str) -> None:
    """Un tentativo di firma personale (``otp`` o ``firma``) della persona."""
    await _riserva(f"personale:{operazione}:{_persona(user_id)}",
                   settings.firma_personale_tentativi, FINESTRA_PERSONALE,
                   "FIRMA-429-PERSONALE", "Troppi tentativi di firma personale: riprovare "
                   "fra qualche minuto")


async def restituisci_personale(user_id: str, operazione: str) -> None:
    """Esito che non conta (firma riuscita): il tentativo si restituisce."""
    r = _cliente()
    if r is None:
        coda = _locali.get(f"personale:{operazione}:{_persona(user_id)}")
        if coda:
            coda.pop()
        return
    await restituisci(r, PREFISSO + f"personale:{operazione}:{_persona(user_id)}")


__all__ = ["azzera_locali", "imposta_redis", "restituisci_personale", "riserva_personale",
           "riserva_prova"]
