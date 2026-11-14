# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Consumo parallelo con conferma degli offset in ordine (R03 b5, REGOLE §12.2, §12.4).

Consumer Kafka finto con la stessa interfaccia usata dal ciclo (getmany/commit):
si controlla che l'elaborazione sia davvero parallela entro il limite, che un
offset non si confermi mai prima che TUTTI i precedenti della partizione siano
chiusi, che la stessa chiave non corra in parallelo e che DLQ e ribilanciamento
rispettino le regole del ciclo seriale.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from aiokafka.structs import TopicPartition
from app.services.consumo_parallelo import ConsumoParallelo, CoordinatoreOffset
from app.services.metriche import MetricheValidazione
from prometheus_client import CollectorRegistry

from libs.shared.kafka.consumatore_affidabile import ErrorePermanente

TP0 = TopicPartition("pdv.received", 0)
TP1 = TopicPartition("pdv.received", 1)


@dataclass
class Msg:
    partition: int
    offset: int
    value: dict
    topic: str = "pdv.received"
    key: bytes | None = None


@dataclass
class ConsumerFinto:
    messaggi: list[Msg]
    commit_fatti: list[tuple[TopicPartition, int]] = field(default_factory=list)
    fine: asyncio.Event = field(default_factory=asyncio.Event)
    letti: int = 0

    async def getmany(self, *, timeout_ms: int, max_records: int):
        lotto = self.messaggi[self.letti:self.letti + max_records]
        self.letti += len(lotto)
        if not lotto:
            await asyncio.sleep(0.005)
            return {}
        risultato: dict[TopicPartition, list[Msg]] = {}
        for m in lotto:
            risultato.setdefault(TopicPartition(m.topic, m.partition), []).append(m)
        return risultato

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        for tp, off in offsets.items():
            self.commit_fatti.append((tp, off))

    def ultimo(self, tp: TopicPartition) -> int | None:
        valori = [o for t, o in self.commit_fatti if t == tp]
        return valori[-1] if valori else None


async def _senza_attesa(_s: float) -> None:
    await asyncio.sleep(0)


def _ciclo(consumer, gestore, *, concorrenza=4, max_in_sospeso=100, su_fallimento=None,
           metriche=None, ritardi=(0.0,)):
    async def registra(_msg, _esito):
        return None

    return ConsumoParallelo(
        consumer, gestore=gestore, su_fallimento=su_fallimento or registra,
        ritardi=list(ritardi), concorrenza=concorrenza, max_in_sospeso=max_in_sospeso,
        chiave_di=lambda m: m.value.get("pdv_id"), attesa_dopo_errore_dlq=0.0,
        attendi=_senza_attesa, poll_timeout_ms=5, metriche=metriche,
    )


async def _esegui_fino(ciclo, condizione, timeout=5.0):
    fine = asyncio.Event()
    compito = asyncio.create_task(ciclo.esegui(fine))
    try:
        async with asyncio.timeout(timeout):
            while not condizione():
                await asyncio.sleep(0.002)
    finally:
        fine.set()
        await compito


def test_coordinatore_conferma_solo_il_prefisso_chiuso():
    c = CoordinatoreOffset()
    for o in (10, 11, 12):
        c.registra(TP0, o)
    assert c.completa(TP0, 12) is None      # 10 e 11 ancora aperti
    assert c.completa(TP0, 10) == 11
    assert c.completa(TP0, 11) == 13        # 11 e 12 chiusi: avanza fino a 13
    assert c.in_sospeso() == 0
    c.registra(TP1, 0)
    c.dimentica([TP1])
    assert c.completa(TP1, 0) is None       # partizione revocata


async def test_parallelo_entro_il_limite_e_offset_in_ordine():
    """Il messaggio 0 è lento: gli altri finiscono prima, ma l'offset resta fermo a 0
    finché il primo non è chiuso; mai più di `concorrenza` insieme."""
    messaggi = [Msg(0, i, {"pdv_id": f"p{i}"}) for i in range(10)]
    consumer = ConsumerFinto(messaggi)
    attivi, massimo, chiusi = 0, 0, []
    rilascia_primo = asyncio.Event()

    async def gestore(m):
        nonlocal attivi, massimo
        attivi += 1
        massimo = max(massimo, attivi)
        try:
            if m.offset == 0:
                await rilascia_primo.wait()
            else:
                await asyncio.sleep(0.001)
            chiusi.append(m.offset)
        finally:
            attivi -= 1

    ciclo = _ciclo(consumer, gestore, concorrenza=3)

    async def controlla():
        await asyncio.sleep(0.05)
        # tutti gli altri chiusi (o in corso), ma nessuna conferma oltre l'offset 0
        assert consumer.ultimo(TP0) is None
        rilascia_primo.set()

    controllo = asyncio.create_task(controlla())
    await _esegui_fino(ciclo, lambda: consumer.ultimo(TP0) == 10)
    await controllo
    assert massimo == 3
    assert chiusi[-1] == 0  # il lento chiude per ultimo, eppure le conferme restano in ordine
    conferme = [o for _, o in consumer.commit_fatti]
    assert conferme == sorted(conferme) and conferme[-1] == 10


async def test_partizioni_indipendenti():
    """Un messaggio lento sulla partizione 0 non ferma le conferme della partizione 1."""
    blocca = asyncio.Event()
    consumer = ConsumerFinto([Msg(0, 0, {"pdv_id": "a"}), Msg(1, 0, {"pdv_id": "b"}),
                              Msg(1, 1, {"pdv_id": "c"})])

    async def gestore(m):
        if m.partition == 0:
            await blocca.wait()

    ciclo = _ciclo(consumer, gestore)
    fine = asyncio.Event()
    compito = asyncio.create_task(ciclo.esegui(fine))
    async with asyncio.timeout(5):
        while consumer.ultimo(TP1) != 2:
            await asyncio.sleep(0.002)
    assert consumer.ultimo(TP0) is None
    blocca.set()
    async with asyncio.timeout(5):
        while consumer.ultimo(TP0) != 1:
            await asyncio.sleep(0.002)
    fine.set()
    await compito


async def test_stessa_chiave_mai_in_parallelo():
    """Riconsegne dello stesso PdV: la seconda parte solo dopo la fine della prima."""
    consumer = ConsumerFinto([Msg(0, 0, {"pdv_id": "x"}), Msg(0, 1, {"pdv_id": "x"}),
                              Msg(0, 2, {"pdv_id": "y"})])
    in_corso: set[str] = set()
    sovrapposizioni = []
    ordine = []

    async def gestore(m):
        chiave = m.value["pdv_id"]
        if chiave in in_corso:
            sovrapposizioni.append(chiave)
        in_corso.add(chiave)
        await asyncio.sleep(0.01 if m.offset == 0 else 0)
        ordine.append(m.offset)
        in_corso.discard(chiave)

    await _esegui_fino(_ciclo(consumer, gestore), lambda: consumer.ultimo(TP0) == 3)
    assert sovrapposizioni == []
    assert ordine.index(0) < ordine.index(1)


async def test_errore_permanente_dlq_poi_conferma():
    consumer = ConsumerFinto([Msg(0, 0, {"pdv_id": "a"})])
    registrati = []

    async def gestore(_m):
        raise ErrorePermanente("VLD-EVT-001", "dato errato")

    async def su_fallimento(msg, esito):
        registrati.append((msg.offset, esito.permanente))

    metriche = MetricheValidazione(CollectorRegistry())
    ciclo = _ciclo(consumer, gestore, su_fallimento=su_fallimento, metriche=metriche)
    await _esegui_fino(ciclo, lambda: consumer.ultimo(TP0) == 1)
    assert registrati == [(0, True)]
    assert metriche.messaggi.labels("dlq")._value.get() == 1


async def test_dlq_non_registrata_niente_conferma_e_rielaborazione():
    """Se DLQ/stato d'errore non si registrano l'offset NON avanza; si rielabora da capo."""
    consumer = ConsumerFinto([Msg(0, 0, {"pdv_id": "a"}), Msg(0, 1, {"pdv_id": "b"})])
    tentativi_dlq = 0
    elaborazioni = []

    async def gestore(m):
        elaborazioni.append(m.offset)
        if m.offset == 0:
            raise ConnectionError("S3 giù")

    async def su_fallimento(_msg, _esito):
        nonlocal tentativi_dlq
        tentativi_dlq += 1
        if tentativi_dlq < 3:
            raise ConnectionError("Kafka giù")

    metriche = MetricheValidazione(CollectorRegistry())
    ciclo = _ciclo(consumer, gestore, su_fallimento=su_fallimento, metriche=metriche)
    await _esegui_fino(ciclo, lambda: consumer.ultimo(TP0) == 2)
    assert tentativi_dlq == 3
    # nessuna conferma prima che la DLQ del messaggio 0 sia registrata
    assert [o for _, o in consumer.commit_fatti] == [2]
    assert elaborazioni.count(0) == 3 * 2  # (1 + 1 ritentativo) per ogni giro
    assert metriche.dlq_non_registrata._value.get() == 2


async def test_limite_messaggi_in_sospeso_ferma_la_lettura():
    consumer = ConsumerFinto([Msg(0, i, {"pdv_id": f"p{i}"}) for i in range(20)])
    rilascia = asyncio.Event()

    async def gestore(m):
        if m.offset == 0:
            await rilascia.wait()

    ciclo = _ciclo(consumer, gestore, concorrenza=2, max_in_sospeso=5)
    fine = asyncio.Event()
    compito = asyncio.create_task(ciclo.esegui(fine))
    await asyncio.sleep(0.1)
    assert consumer.letti == 5  # il primo è fermo: oltre 5 non confermati non si legge
    rilascia.set()
    async with asyncio.timeout(5):
        while consumer.ultimo(TP0) != 20:
            await asyncio.sleep(0.002)
    fine.set()
    await compito


async def test_revoca_attende_i_messaggi_in_corso_e_conferma():
    consumer = ConsumerFinto([Msg(0, 0, {"pdv_id": "a"})])
    iniziato, rilascia = asyncio.Event(), asyncio.Event()

    async def gestore(_m):
        iniziato.set()
        await rilascia.wait()

    ciclo = _ciclo(consumer, gestore)
    fine = asyncio.Event()
    compito = asyncio.create_task(ciclo.esegui(fine))
    await iniziato.wait()
    revoca = asyncio.create_task(ciclo.partizioni_revocate([TP0]))
    await asyncio.sleep(0.02)
    assert not revoca.done()  # la revoca aspetta il messaggio in corso
    rilascia.set()
    await revoca
    assert consumer.ultimo(TP0) == 1
    assert ciclo.offset.in_sospeso() == 0
    fine.set()
    await compito


def test_parametri_non_validi():
    with pytest.raises(ValueError):
        _ciclo(ConsumerFinto([]), lambda m: None, concorrenza=0)
