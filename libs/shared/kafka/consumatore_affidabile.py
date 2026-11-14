# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Elaborazione affidabile di un topic Kafka: ritentativi limitati, DLQ, offset dopo l'esito.

Modulo condiviso (usato da validation e aip-builder; prima era copiato in
entrambi i servizi).
Riferimenti: VD-C-009; REGOLE_SVILUPPO §12.2 (offset solo dopo l'effetto),
§12.4 (ritentativi e DLQ), §18.3 (mai scartare in silenzio).

Regole:
  - ogni messaggio si elabora con ``gestore``; ``ErrorePermanente`` (dato
    errato, integrità violata) non si ritenta; ogni altra eccezione si ritenta
    con le attese di ``ritardi`` (lista costante);
  - esauriti i tentativi, o subito per un errore permanente, si chiama
    ``su_fallimento`` (che pubblica in DLQ e registra lo stato di errore
    esplicito); solo se ``su_fallimento`` riesce l'offset si conferma;
  - se anche ``su_fallimento`` fallisce, l'offset NON si conferma: il consumer
    torna indietro al messaggio (seek) e lo rielabora dopo un'attesa;
  - l'offset si conferma esplicitamente (partizione → offset+1) dopo l'esito.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from aiokafka.structs import TopicPartition

from libs.shared.observability.tracciamento import span_consumo

logger = structlog.get_logger(__name__)


class ErrorePermanente(Exception):
    """Errore che un nuovo tentativo non può risolvere (dato o integrità)."""

    def __init__(self, codice: str, messaggio: str, dettagli: dict | None = None) -> None:
        super().__init__(f"{codice}: {messaggio}")
        self.codice = codice
        self.messaggio = messaggio
        self.dettagli = dettagli or {}


@dataclass(frozen=True)
class EsitoElaborazione:
    """Esito finale dell'elaborazione di un messaggio."""
    riuscito: bool
    tentativi: int
    errore: BaseException | None = None

    @property
    def permanente(self) -> bool:
        return isinstance(self.errore, ErrorePermanente)

    def motivo(self) -> dict[str, Any]:
        """Motivo strutturato per DLQ e stato di errore."""
        if isinstance(self.errore, ErrorePermanente):
            return {"codice": self.errore.codice, "messaggio": self.errore.messaggio,
                    "dettagli": self.errore.dettagli, "tipo": "permanente",
                    "tentativi": self.tentativi}
        return {"codice": "TECNICO", "messaggio": f"{type(self.errore).__name__}: {self.errore}"[
            :500],
                "dettagli": {}, "tipo": "tecnico", "tentativi": self.tentativi}


async def elabora_con_ritentativi(
    operazione: Callable[[], Awaitable[None]],
    *,
    ritardi: Sequence[float],
    attendi: Callable[[float], Awaitable[None]] = asyncio.sleep,
    contesto: dict[str, Any] | None = None,
) -> EsitoElaborazione:
    """Esegue ``operazione`` con al più ``len(ritardi)`` ritentativi."""
    ctx = contesto or {}
    ultimo: BaseException | None = None
    for tentativo in range(1, len(ritardi) + 2):
        try:
            await operazione()
            return EsitoElaborazione(True, tentativo)
        except asyncio.CancelledError:
            raise
        except ErrorePermanente as exc:
            logger.warning("elaborazione_errore_permanente", codice=exc.codice,
                           messaggio=exc.messaggio[:300], **ctx)
            return EsitoElaborazione(False, tentativo, exc)
        except Exception as exc:  # noqa: BLE001 — si ritenta, poi DLQ
            ultimo = exc
            if tentativo <= len(ritardi):
                attesa = ritardi[tentativo - 1]
                logger.warning("elaborazione_ritentativo", tentativo=tentativo,
                               attesa_s=attesa, errore=str(exc)[:300], **ctx)
                await attendi(attesa)
    logger.error("elaborazione_tentativi_esauriti", tentativi=len(ritardi) + 1,
                 errore=str(ultimo)[:300], **ctx)
    return EsitoElaborazione(False, len(ritardi) + 1, ultimo)


async def ciclo_consumer(
    consumer: Any,
    *,
    gestore: Callable[[Any], Awaitable[None]],
    su_fallimento: Callable[[Any, EsitoElaborazione], Awaitable[None]],
    ritardi: Sequence[float],
    attesa_dopo_errore_dlq: float = 30.0,
    attendi: Callable[[float], Awaitable[None]] = asyncio.sleep,
    fine: asyncio.Event | None = None,
    poll_timeout_ms: int = 1000,
) -> None:
    """
    Legge un messaggio alla volta e conferma l'offset solo dopo l'esito persistito.

    ``consumer`` espone getmany/commit/seek come AIOKafkaConsumer.
    """
    while fine is None or not fine.is_set():
        record = await consumer.getmany(timeout_ms=poll_timeout_ms, max_records=1)
        for messaggi in record.values():
            for msg in messaggi:
                ctx = {"topic": msg.topic, "partizione": msg.partition, "offset": msg.offset}
                # R16 (VD-C-176): span del consumatore figlio di quello del produttore
                with span_consumo(msg.topic, getattr(msg, "headers", None)):
                    esito = await elabora_con_ritentativi(
                        lambda m=msg: gestore(m), ritardi=ritardi, attendi=attendi,
                        contesto=ctx)
                if not esito.riuscito:
                    try:
                        await su_fallimento(msg, esito)
                    except Exception as exc:  # noqa: BLE001 — offset non confermato, si rilegge
                        logger.error("dlq_o_stato_errore_non_registrati", errore=str(exc)[:300],
                                     **ctx)
                        consumer.seek(TopicPartition(msg.topic, msg.partition), msg.offset)
                        await attendi(attesa_dopo_errore_dlq)
                        break
                await consumer.commit({TopicPartition(msg.topic, msg.partition): msg.offset + 1})
