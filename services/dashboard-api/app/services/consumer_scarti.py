# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/services/consumer_scarti.py — consumer delle code di scarto.

Riferimenti: VD-C-075 (R04 servizi); REGOLE_SVILUPPO §12.2 (effetto e offset:
prima la scrittura, poi la conferma), §12.4 (una DLQ è consumata da qualcosa).

Processo dedicato (non dentro le repliche dell'API):

    python -m app.services.consumer_scarti

  1. consuma le DLQ di ``SCARTI_CODE_KAFKA`` (gruppo ``SCARTI_KAFKA_GROUP_ID``,
     commit manuale): ogni lotto di ``getmany`` diventa righe di
     ``public.scarti`` in UNA query idempotente (``ON CONFLICT DO NOTHING`` sulla
     chiave ``kafka:<coda>:<partizione>:<offset>``), poi gli offset si
     confermano. Crash fra le due: il lotto si rilegge e non si duplica;
  2. ogni ``SCARTI_INTERVALLO_TABELLE_SECONDI`` riporta nelle code di scarto le
     righe in ERRORE di ``indicizzazione_aip`` (idempotente);
  3. fa girare il relay dell'outbox con origine ``dashboard-api``: gli eventi
     che «Rielabora» scrive nell'outbox (dalle repliche dell'API) partono da qui.

Scalabilità: le DLQ sono a basso volume (3 partizioni ciascuna, retention 30
giorni); più repliche del processo si dividono le partizioni (stesso gruppo).
Un messaggio non decodificabile non ferma nulla: si registra con i primi byte.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import structlog

from app.config import settings
from app.services import scarti
from libs.shared.db import get_async_session
from libs.shared.db.ambito import con_ambito_conservatore

logger = structlog.get_logger(__name__)


def righe_da_lotto(lotti: Mapping[Any, Sequence[Any]], code: Mapping[str, str],
                   adesso: datetime) -> list[scarti.RigaScarto]:
    """Righe di scarto dei messaggi di un lotto ``getmany`` (funzione pura)."""
    righe: list[scarti.RigaScarto] = []
    for messaggi in lotti.values():
        for msg in messaggi:
            valore = msg.value if isinstance(msg.value, dict) else scarti.valore_da_byte(msg.value)
            ricevuto = (datetime.fromtimestamp(msg.timestamp / 1000, UTC)
                        if getattr(msg, "timestamp", None) else adesso)
            righe.append(scarti.riga_da_messaggio(
                coda=msg.topic, servizio=code.get(msg.topic, "sconosciuto"),
                partizione=int(msg.partition), posizione=int(msg.offset), valore=valore,
                ricevuto_il=ricevuto))
    return righe


@con_ambito_conservatore("code di scarto: messaggi delle DLQ di tutti gli enti")
async def elabora_lotto(lotti: Mapping[Any, Sequence[Any]], *, conferma: Any,
                        code: Mapping[str, str] | None = None,
                        adesso: datetime | None = None) -> int:
    """Registra il lotto (una transazione) e SOLO DOPO conferma gli offset.
    Se la scrittura fallisce l'eccezione si propaga e gli offset non avanzano."""
    righe = righe_da_lotto(lotti, code or settings.scarti_code_kafka, adesso or datetime.now(UTC))
    nuove = 0
    if righe:
        async with get_async_session() as session:
            nuove = await scarti.registra(session, righe)
    await conferma()
    if righe:
        logger.info("scarti_registrati", messaggi=len(righe), nuovi=nuove)
    return nuove


@con_ambito_conservatore("code di scarto: rilevamento dalle tabelle di tutti gli enti")
async def rileva_tabelle(limite: int | None = None) -> int:
    """Un giro sulle code in tabella: ``indicizzazione_aip`` e, da R04 correzioni,
    le chiavi BLOCCATE dell'outbox di tutti i servizi (``outbox_chiavi_ferme``)."""
    async with get_async_session() as session:
        nuove = await scarti.rileva_indicizzazione(session, limite or settings.scarti_lotto)
        nuove += await scarti.rileva_outbox_bloccati(session, limite or settings.scarti_lotto)
    if nuove:
        logger.info("scarti_tabelle_registrati", nuovi=nuove)
    return nuove


def _deserializza(grezzo: bytes | None) -> dict[str, Any]:
    return scarti.valore_da_byte(grezzo)


async def ciclo(consumer: Any, *, fine: asyncio.Event) -> None:
    """Ciclo del processo: DLQ Kafka a lotti e, a intervalli, le code in tabella."""
    ultima_tabelle = 0.0
    while not fine.is_set():
        try:
            lotti = await consumer.getmany(timeout_ms=1000, max_records=settings.scarti_lotto)
            await elabora_lotto(lotti, conferma=consumer.commit)
        except Exception as exc:  # noqa: BLE001 — offset non confermati: il lotto si rilegge
            logger.error("scarti_lotto_non_registrato", errore=type(exc).__name__,
                         messaggio=str(exc)[:300])
            await _pausa(fine, 5.0)
            with contextlib.suppress(Exception):
                await _riparti_dall_ultimo_confermato(consumer)
        if time.monotonic() - ultima_tabelle >= settings.scarti_intervallo_tabelle_secondi:
            ultima_tabelle = time.monotonic()
            try:
                await rileva_tabelle()
            except Exception as exc:  # noqa: BLE001 — si riprova al giro dopo
                logger.error("scarti_tabelle_non_lette", errore=type(exc).__name__)


async def _riparti_dall_ultimo_confermato(consumer: Any) -> None:
    """Dopo un errore di scrittura: rilettura dagli ultimi offset confermati."""
    for partizione in consumer.assignment():
        confermato = await consumer.committed(partizione)
        if confermato is not None:
            consumer.seek(partizione, confermato)


async def _pausa(fine: asyncio.Event, secondi: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(fine.wait(), timeout=secondi)


def crea_consumer() -> Any:
    """Consumer delle code di scarto (PLAINTEXT in locale, SASL_SSL IAM su MSK)."""
    from aiokafka import AIOKafkaConsumer

    from libs.shared.kafka.connessione import parametri_connessione

    return AIOKafkaConsumer(
        *sorted(settings.scarti_code_kafka), bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.scarti_kafka_group_id, enable_auto_commit=False,
        auto_offset_reset="earliest", value_deserializer=_deserializza,
        **parametri_connessione())


async def _main() -> None:  # pragma: no cover — processo (provato da elabora_lotto e ciclo)
    from libs.shared.db import close_db, init_db
    from libs.shared.outbox import avvia_relay_servizio, ferma_relay_servizio

    await init_db(settings.database_url)
    relay = await avvia_relay_servizio(scarti.ORIGINE_OUTBOX, bootstrap=settings.kafka_bootstrap,
                                       manutenzione=False)
    consumer = crea_consumer()
    await consumer.start()
    fine = asyncio.Event()
    logger.info("consumer_scarti_avviato", code=sorted(settings.scarti_code_kafka))
    try:
        await ciclo(consumer, fine=fine)
    finally:
        await consumer.stop()
        await ferma_relay_servizio(relay)
        await close_db()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
