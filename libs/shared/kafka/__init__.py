# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/kafka — Producer Kafka con retry.

``kafka_produce`` NON scarta più in silenzio: senza producer attivo solleva
``KafkaNonDisponibile`` e gli errori del broker si propagano. Chi chiama decide
(outbox: l'evento resta in sospeso; altri: errore esplicito nel log).
BUG FIX: rimosso enable_idempotence=True (causa NodeNotReadyError al startup).
         Aggiunto retry esponenziale — il servizio parte anche se Kafka è lento.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aiokafka import AIOKafkaProducer

from libs.shared.kafka.connessione import parametri_connessione

logger = logging.getLogger(__name__)
_producer: AIOKafkaProducer | None = None
#: R04 int: ultimo bootstrap con cui si è tentato l'avvio (per ``riprova_producer``).
_bootstrap: str | None = None
#: Riprova del producer non partito: prossimo tentativo (monotonic) e pausa
#: corrente (raddoppia fino al massimo; si azzera alla riuscita).
_prossima_riprova = 0.0
_pausa_riprova = 0.0
PAUSA_RIPROVA_MINIMA_SECONDI = 2.0
PAUSA_RIPROVA_MASSIMA_SECONDI = 60.0


class KafkaNonDisponibile(RuntimeError):
    """Il producer Kafka del processo non è attivo: il messaggio NON è stato inviato."""


def producer_attivo() -> bool:
    return _producer is not None


async def init_kafka_producer(bootstrap_servers: str, retries: int = 5) -> None:
    global _producer, _bootstrap
    _bootstrap = bootstrap_servers
    _producer = AIOKafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        acks="all",
        # RIMOSSO enable_idempotence=True — incompatibile con Kafka non fully-ready
        compression_type="gzip",
        request_timeout_ms=15_000,
        # R04: PLAINTEXT in locale, SASL_SSL + OAUTHBEARER IAM su MSK (KAFKA_AUTENTICAZIONE)
        **parametri_connessione(),
    )
    for attempt in range(1, retries + 1):
        try:
            await _producer.start()
            logger.info(f"kafka_producer_started bootstrap={bootstrap_servers}")
            return
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning(
                f"kafka_producer_retry attempt={attempt}/{retries} wait={wait}s error={exc}")
            if attempt == retries:
                logger.error("kafka_producer_definitively_failed — servizio parte senza Kafka")
                _producer = None
                return
            await asyncio.sleep(wait)


async def riprova_producer(adesso: float | None = None) -> bool:
    """True se il producer è attivo, avviandolo se il primo avvio era fallito.

    Senza riprova un servizio partito con Kafka giù non pubblicava più nulla
    fino al riavvio. Un tentativo SOLO (nessuna attesa interna) e al più uno ogni
    pausa (2 s che raddoppiano fino a 60 s): chi lo chiama a ogni giro del relay
    non genera né carico né una valanga di log."""
    global _prossima_riprova, _pausa_riprova
    if _producer is not None:
        return True
    if not _bootstrap:
        return False
    ora = time.monotonic() if adesso is None else adesso
    if ora < _prossima_riprova:
        return False
    await init_kafka_producer(_bootstrap, retries=1)
    if _producer is not None:
        _pausa_riprova = 0.0
        _prossima_riprova = 0.0
        return True
    _pausa_riprova = min(PAUSA_RIPROVA_MASSIMA_SECONDI,
                         max(PAUSA_RIPROVA_MINIMA_SECONDI, _pausa_riprova * 2))
    _prossima_riprova = ora + _pausa_riprova
    return False


async def close_kafka() -> None:
    global _producer
    if _producer is not None:
        try:
            await _producer.stop()
        except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
            logger.warning("kafka_producer_arresto_non_riuscito: %s", type(exc).__name__)
        _producer = None
        logger.info("kafka_producer_stopped")


async def kafka_produce(topic: str, value: dict[str, Any], key: str | None = None) -> None:
    """Invia e attende la conferma del broker (acks=all).

    Solleva ``KafkaNonDisponibile`` se il producer non è attivo e lascia
    propagare gli errori di invio: un messaggio non inviato non deve mai
    sembrare inviato.
    """
    if _producer is None:
        raise KafkaNonDisponibile(f"Producer Kafka non attivo: messaggio per {topic!r} non inviato")
    # R16 (VD-C-176): contesto della traccia nelle intestazioni (vuote se le tracce sono spente)
    from libs.shared.observability.tracciamento import intestazioni_kafka, span_produzione

    with span_produzione(topic):
        intestazioni = intestazioni_kafka()
        if intestazioni:
            await _producer.send_and_wait(topic, value=value, key=key, headers=intestazioni)
        else:
            await _producer.send_and_wait(topic, value=value, key=key)
