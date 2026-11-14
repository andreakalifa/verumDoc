# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Consumo di un topic Kafka con elaborazione PARALLELA dei messaggi e conferma
degli offset IN ORDINE.

Servizio: validation (obiettivo 1 milione di documenti al giorno: piano R03
§4.1-4.2). Riferimenti: REGOLE_SVILUPPO §12.2 (offset solo dopo l'effetto,
consumer idempotenti), §12.4 (ritentativi e DLQ), §18.3.

Stesse regole del ciclo seriale ``libs.shared.kafka.consumatore_affidabile``
(che resta per gli altri servizi), con in più:

- fino a ``concorrenza`` messaggi in elaborazione contemporanea, anche della
  stessa partizione (la chiave dei messaggi è il PdV: l'ordine fra PdV diversi
  non conta);
- due messaggi con la stessa chiave (riconsegne dello stesso PdV) NON corrono
  insieme: il secondo aspetta la fine del primo, poi trova l'esito salvato;
- l'offset di una partizione si conferma solo fino al primo messaggio non
  ancora chiuso (``CoordinatoreOffset``): un messaggio veloce dopo uno lento
  non fa confermare quello lento; dopo un crash si rielabora da lì
  (l'elaborazione è idempotente: il PdV si blocca e si rilegge lo stato);
- un messaggio è «chiuso» quando il gestore è riuscito oppure, esauriti i
  ritentativi (o subito per ``ErrorePermanente``), ``su_fallimento`` ha
  registrato DLQ e stato d'errore; se anche questo fallisce il messaggio resta
  aperto, si attende e si rielabora da capo (come il seek del ciclo seriale);
- oltre ``max_in_sospeso`` messaggi letti e non confermati si smette di leggere;
- revoca di partizioni (ribilanciamento): si attendono i messaggi in corso di
  quelle partizioni, si conferma ciò che è chiuso e si dimentica il resto (il
  nuovo assegnatario li rielabora).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

import structlog
from aiokafka.structs import TopicPartition

from libs.shared.kafka.consumatore_affidabile import EsitoElaborazione, elabora_con_ritentativi

logger = structlog.get_logger(__name__)


class CoordinatoreOffset:
    """
    Offset ricevuti per partizione, in ordine, e loro stato (aperto/chiuso).

    ``completa`` restituisce il nuovo offset da confermare (ultimo chiuso
    contiguo + 1) quando la «testa» della partizione avanza, altrimenti None.
    """

    def __init__(self) -> None:
        self._partizioni: dict[TopicPartition, OrderedDict[int, bool]] = {}

    def registra(self, tp: TopicPartition, offset: int) -> None:
        self._partizioni.setdefault(tp, OrderedDict())[offset] = False

    def completa(self, tp: TopicPartition, offset: int) -> int | None:
        coda = self._partizioni.get(tp)
        if coda is None or offset not in coda:
            return None  # partizione revocata nel frattempo
        coda[offset] = True
        da_confermare = None
        while coda:
            primo, chiuso = next(iter(coda.items()))
            if not chiuso:
                break
            coda.popitem(last=False)
            da_confermare = primo + 1
        return da_confermare

    def in_sospeso(self) -> int:
        return sum(len(c) for c in self._partizioni.values())

    def dimentica(self, partizioni: Iterable[TopicPartition]) -> None:
        for tp in partizioni:
            self._partizioni.pop(tp, None)


class ConsumoParallelo:
    """Ciclo di consumo parallelo; vedi la docstring del modulo."""

    def __init__(
        self,
        consumer: Any,
        *,
        gestore: Callable[[Any], Awaitable[None]],
        su_fallimento: Callable[[Any, EsitoElaborazione], Awaitable[None]],
        ritardi: Sequence[float],
        concorrenza: int,
        max_in_sospeso: int,
        chiave_di: Callable[[Any], str | None] = lambda m: None,
        attesa_dopo_errore_dlq: float = 30.0,
        attendi: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_timeout_ms: int = 1000,
        metriche: Any = None,
    ) -> None:
        if concorrenza < 1 or max_in_sospeso < 1:
            raise ValueError("concorrenza e max_in_sospeso devono essere positivi")
        self.consumer = consumer
        self.gestore = gestore
        self.su_fallimento = su_fallimento
        self.ritardi = list(ritardi)
        self.concorrenza = concorrenza
        self.max_in_sospeso = max(max_in_sospeso, concorrenza)
        self.chiave_di = chiave_di
        self.attesa_dopo_errore_dlq = attesa_dopo_errore_dlq
        self.attendi = attendi
        self.poll_timeout_ms = poll_timeout_ms
        self.metriche = metriche
        self.offset = CoordinatoreOffset()
        self._attivi: dict[asyncio.Task[None], TopicPartition] = {}
        self._per_chiave: dict[str, asyncio.Task[None]] = {}
        #: offset chiuso più alto (+1) per partizione, da confermare
        self._watermark: dict[TopicPartition, int] = {}
        #: offset già confermati a Kafka per partizione
        self._confermati: dict[TopicPartition, int] = {}
        self._lock_conferma = asyncio.Lock()
        if metriche is not None:
            metriche.concorrenza.set(concorrenza)

    # ── ciclo principale ──────────────────────────────────────────────
    async def esegui(self, fine: asyncio.Event | None = None) -> None:
        try:
            while fine is None or not fine.is_set():
                liberi = min(self.concorrenza - len(self._attivi),
                             self.max_in_sospeso - self.offset.in_sospeso())
                if liberi <= 0:
                    await self._attendi_un_completamento()
                    continue
                record = await self.consumer.getmany(
                    timeout_ms=self.poll_timeout_ms, max_records=liberi)
                for tp, messaggi in record.items():
                    for msg in messaggi:
                        self._avvia(tp, msg)
                self._aggiorna_metriche()
                # Cede il turno ai messaggi avviati anche se getmany ritorna subito
                await asyncio.sleep(0)
            # Arresto ordinato: si chiudono i messaggi già avviati
            if self._attivi:
                await asyncio.gather(*self._attivi, return_exceptions=True)
        except asyncio.CancelledError:
            for task in list(self._attivi):
                task.cancel()
            raise

    async def _attendi_un_completamento(self) -> None:
        if not self._attivi:
            # Tutto in sospeso è chiuso ma non confermabile (partizioni revocate)
            await self.attendi(self.poll_timeout_ms / 1000)
            return
        await asyncio.wait(set(self._attivi), timeout=self.poll_timeout_ms / 1000,
                           return_when=asyncio.FIRST_COMPLETED)

    def _avvia(self, tp: TopicPartition, msg: Any) -> None:
        self.offset.registra(tp, msg.offset)
        chiave = self.chiave_di(msg)
        precedente = self._per_chiave.get(chiave) if chiave else None
        task = asyncio.create_task(self._elabora(tp, msg, precedente))
        self._attivi[task] = tp
        if chiave:
            self._per_chiave[chiave] = task
        task.add_done_callback(lambda t, c=chiave: self._rimuovi(t, c))

    def _rimuovi(self, task: asyncio.Task[None], chiave: str | None) -> None:
        self._attivi.pop(task, None)
        if chiave and self._per_chiave.get(chiave) is task:
            del self._per_chiave[chiave]
        self._aggiorna_metriche()

    # ── un messaggio ──────────────────────────────────────────────────
    async def _elabora(self, tp: TopicPartition, msg: Any,
                       precedente: asyncio.Task[None] | None) -> None:
        if precedente is not None and not precedente.done():
            # Stessa chiave: mai due elaborazioni dello stesso PdV insieme
            await asyncio.wait({precedente})
        ctx = {"topic": msg.topic, "partizione": msg.partition, "offset": msg.offset}
        inizio = time.perf_counter()
        while True:
            # R16 (VD-C-176): span del consumatore figlio di quello del produttore
            from libs.shared.observability.tracciamento import span_consumo

            with span_consumo(msg.topic, getattr(msg, "headers", None)):
                esito = await elabora_con_ritentativi(
                    lambda: self.gestore(msg), ritardi=self.ritardi, attendi=self.attendi,
                    contesto=ctx)
            if esito.riuscito:
                self._conta("elaborato")
                break
            try:
                await self.su_fallimento(msg, esito)
                self._conta("dlq")
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — offset non confermato, si rielabora
                logger.error("dlq_o_stato_errore_non_registrati", errore=str(exc)[:300], **ctx)
                if self.metriche is not None:
                    self.metriche.dlq_non_registrata.inc()
                await self.attendi(self.attesa_dopo_errore_dlq)
        if self.metriche is not None:
            self.metriche.durata.observe(time.perf_counter() - inizio)
        nuovo = self.offset.completa(tp, msg.offset)
        if nuovo is not None:
            self._watermark[tp] = max(nuovo, self._watermark.get(tp, -1))
            await self._conferma(tp)

    def _conta(self, esito: str) -> None:
        if self.metriche is not None:
            self.metriche.messaggi.labels(esito).inc()

    async def _conferma(self, tp: TopicPartition) -> None:
        """Conferma l'offset più alto chiuso della partizione (conferme in serie,
        mai all'indietro)."""
        async with self._lock_conferma:
            da_confermare = self._watermark.get(tp)
            if da_confermare is None or self._confermati.get(tp, -1) >= da_confermare:
                return
            try:
                await self.consumer.commit({tp: da_confermare})
            except Exception as exc:  # noqa: BLE001 — si rielaborerà (idempotente)
                logger.warning("conferma_offset_non_riuscita", partizione=tp.partition,
                               offset=da_confermare, errore=str(exc)[:300])
                if self.metriche is not None:
                    self.metriche.offset_confermati.labels("errore").inc()
                return
            self._confermati[tp] = da_confermare
            if self.metriche is not None:
                self.metriche.offset_confermati.labels("ok").inc()

    def _aggiorna_metriche(self) -> None:
        if self.metriche is not None:
            self.metriche.in_corso.set(len(self._attivi))
            self.metriche.in_attesa_conferma.set(self.offset.in_sospeso())

    # ── ribilanciamento ───────────────────────────────────────────────
    async def partizioni_revocate(self, revocate: Iterable[TopicPartition]) -> None:
        revocate = set(revocate)
        in_corso = [t for t, tp in self._attivi.items() if tp in revocate]
        if in_corso:
            logger.info("revoca_attesa_messaggi_in_corso", messaggi=len(in_corso))
            await asyncio.gather(*in_corso, return_exceptions=True)
        self.offset.dimentica(revocate)
        for tp in revocate:
            self._confermati.pop(tp, None)
            self._watermark.pop(tp, None)
        self._aggiorna_metriche()

    def partizioni_assegnate(self, assegnate: Iterable[TopicPartition]) -> None:
        # Si riparte dall'offset confermato dal gruppo: nessuno stato vecchio
        assegnate = set(assegnate)
        self.offset.dimentica(assegnate)
        for tp in assegnate:
            self._confermati.pop(tp, None)
            self._watermark.pop(tp, None)
