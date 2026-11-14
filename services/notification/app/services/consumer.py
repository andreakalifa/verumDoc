# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/services/consumer.py
Consumer Kafka delle notifiche con ritentativi e DLQ — VD-C-024.

Topic: notification.send (messaggi espliciti), pdv.invalid, aip.conserved.
Ogni evento si trasforma in righe della coda di uscita (elaborazione.py).

Semantica (correzione della vecchia DLQ che perdeva i messaggi):
  - l'offset si conferma SOLO dopo che l'evento è stato accodato oppure
    pubblicato sulla DLQ: mai un commit che salta un messaggio fallito;
  - i ritentativi avvengono sul messaggio corrente, con attese crescenti,
    senza leggere oltre (nessun contatore esterno, nessun Redis);
  - se anche la DLQ fallisce, il consumer si ferma senza confermare l'offset:
    al riavvio l'evento viene riletto (at-least-once, deduplica in coda).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.config import settings
from app.domain.pec import TOPIC_PEC
from app.services.coda import CodaUscita
from app.services.elaborazione import (
    TOPIC_EVENTI,
    ScartoEvento,
    accoda_esplicita,
    accoda_evento,
)
from libs.shared.kafka.connessione import parametri_connessione

logger = structlog.get_logger(__name__)

_consumer: AIOKafkaConsumer | None = None
_producer: AIOKafkaProducer | None = None

Attendi = Callable[[float], Awaitable[None]]


def adesso_utc() -> datetime:
    return datetime.now(UTC)


async def elabora_evento(
    topic: str, evento: dict[str, Any], coda: CodaUscita, *, adesso: datetime,
    accoda_pec: Callable[..., Awaitable[int]] | None = None,
) -> int:
    if topic == settings.kafka_topic_in:
        return await accoda_esplicita(evento, coda, adesso=adesso)
    accodati = 0
    if topic in TOPIC_EVENTI:
        accodati += await accoda_evento(topic, evento, coda, adesso=adesso)
    if topic in TOPIC_PEC:
        # R13b (VD-C-078): comunicazione formale per PEC se il caso d'uso è scelto
        # dall'ente (PEC_CASI_USO); errori → ritentativi del messaggio come per la posta
        if accoda_pec is None:
            from app.application.pec import accoda_comunicazione as accoda_pec
        accodati += await accoda_pec(topic, evento)
    elif topic not in TOPIC_EVENTI:
        logger.info("topic_ignorato", topic=topic)
    return accodati


async def gestisci_messaggio(
    topic: str,
    partizione: int,
    offset: int,
    evento: Any,
    elabora: Callable[[str, dict[str, Any]], Awaitable[int]],
    pubblica_dlq: Callable[[dict], Awaitable[None]],
    *,
    ritardi: list[float],
    attendi: Attendi = asyncio.sleep,
) -> str:
    """Elabora un messaggio con ritentativi; poi DLQ. Restituisce «ok» o «dlq».

    Solleva solo se la pubblicazione sulla DLQ fallisce (l'offset non va confermato).
    """
    motivo: str | None = None
    if not isinstance(evento, dict):
        errore = motivo = "evento non è un oggetto JSON"
        tentativi = 0
    else:
        tentativi = 0
        errore = ""
        while True:
            tentativi += 1
            try:
                await elabora(topic, evento)
                return "ok"
            except ScartoEvento as exc:
                # Motivo definitivo (es. cliente non risolvibile): niente
                # ritentativi, niente riga senza cliente: DLQ con il motivo.
                motivo = exc.motivo
                errore = f"ScartoEvento: {exc.motivo}"[:500]
                logger.warning("notifica_evento_scartato", topic=topic, offset=offset,
                               motivo=exc.motivo)
                break
            except Exception as exc:  # noqa: BLE001 — ogni errore porta a ritentativo o DLQ
                errore = f"{type(exc).__name__}: {exc}"[:500]
                logger.warning(
                    "notifica_evento_errore",
                    topic=topic,
                    offset=offset,
                    tentativo=tentativi,
                    errore=errore,
                )
                if tentativi > len(ritardi):
                    break
                await attendi(ritardi[tentativi - 1])
    await pubblica_dlq(
        {
            "original_topic": topic,
            "partition": partizione,
            "offset": offset,
            "event": evento,
            "error": errore,
            "motivo": motivo or "ritentativi esauriti",
            "retries": tentativi,
            "failed_at": adesso_utc().isoformat(),
        }
    )
    logger.error("notification_sent_to_dlq", topic=topic, offset=offset, retries=tentativi)
    return "dlq"


async def init_notification_consumer(tentativi: int = 10) -> None:
    """Avvio con ritentativi: Kafka può non essere pronto quando parte il servizio."""
    global _consumer, _producer
    # R13b: anche i topic delle comunicazioni PEC (pratica.cambio_stato)
    topics = list(dict.fromkeys([settings.kafka_topic_in, *TOPIC_EVENTI, *TOPIC_PEC]))
    for n in range(1, tentativi + 1):
        try:
            _consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=settings.kafka_bootstrap,
                group_id=settings.kafka_group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                value_deserializer=_deserializza,
                **parametri_connessione(),
            )
            _producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",
                **parametri_connessione(),
            )
            await _consumer.start()
            await _producer.start()
            logger.info("notification_consumer_started", topics=topics)
            return
        except Exception as exc:  # noqa: BLE001 — si ritenta l'avvio
            logger.warning(
                "notification_consumer_avvio_fallito", tentativo=n, errore=type(exc).__name__
            )
            await close_notification_consumer()
            if n == tentativi:
                raise
            await asyncio.sleep(min(2**n, 30))


async def init_producer_dlq(tentativi: int = 10) -> None:
    """R12b (VD-C-161): SOLO il producer della coda di scarto, per il lavoratore della
    posta (``app.lavoratore_posta``), che non consuma topic: i messaggi non partiti
    vanno su ``notification.dlq`` come prima."""
    global _producer
    for n in range(1, tentativi + 1):
        try:
            _producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",
                **parametri_connessione(),
            )
            await _producer.start()
            logger.info("notification_producer_dlq_avviato")
            return
        except Exception as exc:  # noqa: BLE001 — si ritenta l'avvio
            logger.warning("notification_producer_dlq_avvio_fallito", tentativo=n,
                           errore=type(exc).__name__)
            await close_notification_consumer()
            if n == tentativi:
                raise
            await asyncio.sleep(min(2**n, 30))


def _deserializza(v: bytes) -> Any:
    try:
        return json.loads(v.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"__non_decodificabile__": v[:200].decode("utf-8", "replace")}


async def close_notification_consumer() -> None:
    global _consumer, _producer
    for c in (_consumer, _producer):
        if c is not None:
            try:
                await c.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("notification_stop_errore", errore=type(exc).__name__)
    _consumer = _producer = None


async def pubblica_dlq(evento: dict) -> None:
    if _producer is None:
        raise RuntimeError("producer Kafka non inizializzato")
    chiave = str(evento.get("notifica_id") or evento.get("offset") or "")
    from libs.shared.observability.tracciamento import argomenti_kafka

    await _producer.send_and_wait(settings.kafka_topic_dlq, key=chiave, value=evento,
                                  **argomenti_kafka())


async def consume_loop(coda: CodaUscita) -> None:
    if _consumer is None or _producer is None:
        raise RuntimeError("Notification consumer non inizializzato")

    async def _elabora(topic: str, evento: dict[str, Any]) -> int:
        if "__non_decodificabile__" in evento:
            raise ValueError("payload non JSON")
        return await elabora_evento(topic, evento, coda, adesso=adesso_utc())

    async for msg in _consumer:
        await gestisci_messaggio(
            msg.topic,
            msg.partition,
            msg.offset,
            msg.value,
            _elabora,
            pubblica_dlq,
            ritardi=settings.kafka_ritardi_ritentativi_secondi,
        )
        # Raggiunto solo se l'evento è in coda o sulla DLQ
        await _consumer.commit()
