# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/application/eventi_consumer.py
Consumer Kafka che alimenta l'elenco degli eventi per i clienti — contratto C3.

Gruppo Kafka proprio (``EVENTI_KAFKA_GROUP_ID``), separato da quello della
posta: un ritardo o un errore di un flusso non ferma l'altro.

Per ogni messaggio di un topic di ``EVENTI_TOPIC``:
  proiezione pura (app.domain.eventi) → UNA istruzione SQL che registra
  l'evento e accoda le consegne webhook (app.infrastructure.repo_eventi).
Stessa semantica del consumer della posta (``gestisci_messaggio``): offset
confermato solo dopo la registrazione o la pubblicazione sulla DLQ
(``notification.dlq``, con ``original_topic``), ritentativi sul messaggio
corrente, ``ScartoEvento`` (evento non trasformabile, cliente inesistente)
subito sulla DLQ con il motivo. La riconsegna di un evento già registrato non
produce righe (indice unico): consumer idempotente (REGOLE §12.2).

R03 f2 (REV-13), carico dell'obiettivo (~4 eventi per documento: ~200
eventi/s nel picco di 50 documenti/s):
  - il consumer legge a lotti (``EVENTI_CONSUMER_LOTTO``), elabora le
    partizioni in parallelo e ognuna in ordine, e conferma gli offset UNA volta
    per lotto (prima: un commit Kafka per messaggio, in serie). Gli offset si
    confermano solo se ogni messaggio del lotto è registrato o sulla DLQ;
  - il passo di pubblicazione (``ciclo_pubblicazione``) assegna la posizione
    agli eventi registrati, ogni ``EVENTI_PUBBLICAZIONE_INTERVALLO_SECONDI``,
    fino a ``EVENTI_PUBBLICAZIONE_LOTTO`` per giro (ripetuto subito a lotto
    pieno): una UPDATE per giro, quindi migliaia di eventi al secondo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer

from app.domain.eventi import EventoCliente, EventoNonValido, proietta
from app.infrastructure.repo_eventi import (
    ClienteInesistente,
    EsitoRegistrazione,
    pubblica_eventi,
    registra_evento,
)
from app.services.consumer import _deserializza, gestisci_messaggio, pubblica_dlq
from app.services.elaborazione import ScartoEvento
from libs.shared.kafka.connessione import parametri_connessione

logger = structlog.get_logger(__name__)

Registra = Callable[..., Awaitable[EsitoRegistrazione]]

_consumer: AIOKafkaConsumer | None = None


async def elabora_evento_cliente(topic: str, evento: Any, *, mappa_topic: Mapping[str, str],
                                 registra: Registra = registra_evento) -> int:
    """Registra l'evento per il cliente; restituisce le consegne webhook accodate."""
    tipo = mappa_topic.get(topic)
    if tipo is None:
        logger.info("eventi_cliente_topic_ignorato", topic=topic)
        return 0
    try:
        ev: EventoCliente = proietta(tipo, evento)
    except EventoNonValido as exc:
        raise ScartoEvento(f"evento {topic} non trasformabile in {tipo}: {exc}") from None
    try:
        esito = await registra(ev, origine=topic)
    except ClienteInesistente:
        raise ScartoEvento(f"cliente inesistente: tenant_id={ev.tenant_id}") from None
    if esito.evento_id is None:
        logger.info("eventi_cliente_gia_registrato", tipo=tipo, tenant_id=ev.tenant_id,
                    risorsa_id=ev.risorsa_id)
        return 0
    logger.info("eventi_cliente_registrato", tipo=tipo, tenant_id=ev.tenant_id,
                evento_id=esito.evento_id, consegne=esito.consegne)
    return esito.consegne


async def avvia_consumer_eventi(*, bootstrap: str, group_id: str, topics: list[str]) -> None:
    global _consumer
    _consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=_deserializza,
        **parametri_connessione(),
    )
    await _consumer.start()
    logger.info("eventi_cliente_consumer_avviato", topics=topics, group_id=group_id)


async def ferma_consumer_eventi() -> None:
    global _consumer
    if _consumer is not None:
        try:
            await _consumer.stop()
        except Exception as exc:  # noqa: BLE001 — chiusura: si registra e si prosegue
            logger.warning("eventi_cliente_consumer_stop_errore", errore=type(exc).__name__)
    _consumer = None


Elabora = Callable[[str, dict[str, Any]], Awaitable[int]]


async def elabora_lotto_kafka(lotti: Mapping[Any, Sequence[Any]], elabora: Elabora, *,
                              ritardi: list[float],
                              pubblica: Callable[[dict], Awaitable[None]] = pubblica_dlq,
                              ) -> int:
    """Un lotto di ``getmany``: partizioni in parallelo, messaggi di una partizione
    in ordine. Restituisce i messaggi elaborati; solleva (dopo aver lasciato
    finire le altre partizioni) se un messaggio non è né registrato né sulla
    DLQ: in quel caso gli offset del lotto NON vanno confermati."""

    async def partizione(messaggi: Sequence[Any]) -> int:
        for msg in messaggi:
            from libs.shared.observability.tracciamento import span_consumo

            with span_consumo(msg.topic, getattr(msg, "headers", None)):
                await gestisci_messaggio(msg.topic, msg.partition, msg.offset, msg.value,
                                         elabora, pubblica, ritardi=ritardi)
        return len(messaggi)

    esiti = await asyncio.gather(*(partizione(m) for m in lotti.values()),
                                 return_exceptions=True)
    for esito in esiti:
        if isinstance(esito, BaseException):
            raise esito
    return sum(int(e) for e in esiti if not isinstance(e, BaseException))


async def ciclo_consumer_eventi(*, mappa_topic: Mapping[str, str], ritardi: list[float],
                                lotto: int = 500) -> None:
    if _consumer is None:
        raise RuntimeError("consumer degli eventi per i clienti non avviato")

    async def _elabora(topic: str, evento: dict[str, Any]) -> int:
        if "__non_decodificabile__" in evento:
            raise ScartoEvento("payload non JSON")
        return await elabora_evento_cliente(topic, evento, mappa_topic=mappa_topic)

    while True:
        lotti = await _consumer.getmany(timeout_ms=1000, max_records=lotto)
        if not lotti:
            continue
        await elabora_lotto_kafka(lotti, _elabora, ritardi=ritardi)
        # Raggiunto solo se ogni evento del lotto è registrato o sulla DLQ
        await _consumer.commit()


async def ciclo_pubblicazione(*, intervallo_secondi: float, lotto: int,
                              pubblica: Callable[..., Awaitable[int]] = pubblica_eventi) -> None:
    """Passo di pubblicazione degli eventi (una replica alla volta per giro)."""
    while True:
        pieno = False
        try:
            pubblicati = await pubblica(limite=lotto)
            pieno = pubblicati >= lotto
            if pubblicati:
                logger.debug("eventi_cliente_pubblicati", n=pubblicati)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — il ciclo non muore: errore nel log
            logger.error("eventi_cliente_pubblicazione_errore", errore=type(exc).__name__)
        if not pieno:
            await asyncio.sleep(intervallo_secondi)
