# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Ritentativi limitati, DLQ e offset dopo l'esito (VD-C-009).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiokafka.structs import TopicPartition

from libs.shared.kafka.consumatore_affidabile import (
    ErrorePermanente,
    EsitoElaborazione,
    ciclo_consumer,
    elabora_con_ritentativi,
)


async def _niente(_s: float) -> None:
    return None


async def test_elabora_con_ritentativi_successo_al_terzo_tentativo():
    chiamate = {"n": 0}
    attese: list[float] = []

    async def op():
        chiamate["n"] += 1
        if chiamate["n"] < 3:
            raise ConnectionError("S3 giù")

    async def attendi(s: float) -> None:
        attese.append(s)

    esito = await elabora_con_ritentativi(op, ritardi=[1, 5, 30], attendi=attendi)
    assert esito.riuscito and esito.tentativi == 3
    assert attese == [1, 5]


async def test_elabora_con_ritentativi_esauriti_restituisce_errore_tecnico():
    async def op():
        raise ConnectionError("S3 giù")

    esito = await elabora_con_ritentativi(op, ritardi=[1, 2], attendi=_niente)
    assert not esito.riuscito and esito.tentativi == 3
    assert esito.motivo()["tipo"] == "tecnico"
    assert "ConnectionError" in esito.motivo()["messaggio"]


async def test_elabora_con_ritentativi_errore_permanente_nessun_ritentativo():
    chiamate = {"n": 0}

    async def op():
        chiamate["n"] += 1
        raise ErrorePermanente("AIP-INTEGRITA-002", "impronta diversa", {"x": 1})

    esito = await elabora_con_ritentativi(op, ritardi=[1, 2, 3], attendi=_niente)
    assert chiamate["n"] == 1 and esito.permanente
    assert esito.motivo()["codice"] == "AIP-INTEGRITA-002"


@dataclass
class _Msg:
    topic: str
    partition: int
    offset: int
    value: object


class _ConsumerFinto:
    def __init__(self, messaggi: list[_Msg]):
        self.coda = list(messaggi)
        self.commit_fatti: list[dict] = []
        self.seek_fatti: list[tuple] = []

    async def getmany(self, timeout_ms: int = 0, max_records: int | None = None):
        if not self.coda:
            return {}
        m = self.coda.pop(0)
        return {TopicPartition(m.topic, m.partition): [m]}

    async def commit(self, offsets):
        self.commit_fatti.append(dict(offsets))

    def seek(self, tp, offset):
        self.seek_fatti.append((tp, offset))


async def _gira(consumer, *, gestore, su_fallimento, giri: int):
    fine = asyncio.Event()
    contatore = {"n": 0}
    originale = consumer.getmany

    async def getmany(**kw):
        contatore["n"] += 1
        if contatore["n"] > giri:
            fine.set()
            return {}
        return await originale(**kw)

    consumer.getmany = getmany
    await ciclo_consumer(consumer, gestore=gestore, su_fallimento=su_fallimento,
                         ritardi=[0.1, 0.2], attendi=_niente, fine=fine)


async def test_ciclo_consumer_successo_commit_offset_successivo():
    consumer = _ConsumerFinto([_Msg("pdv.validated", 0, 41, {})])

    async def gestore(msg):
        return None

    async def su_fallimento(msg, esito):
        raise AssertionError("non deve essere chiamato")

    await _gira(consumer, gestore=gestore, su_fallimento=su_fallimento, giri=1)
    assert consumer.commit_fatti == [{TopicPartition("pdv.validated", 0): 42}]


async def test_ciclo_consumer_errore_ritentato_poi_dlq_poi_commit():
    """Errore tecnico: 3 tentativi, poi su_fallimento (DLQ + stato) e solo allora commit."""
    consumer = _ConsumerFinto([_Msg("pdv.validated", 0, 7, {})])
    tentativi = {"n": 0}
    fallimenti: list[EsitoElaborazione] = []

    async def gestore(msg):
        tentativi["n"] += 1
        raise TimeoutError("DB giù")

    async def su_fallimento(msg, esito):
        assert consumer.commit_fatti == []
        fallimenti.append(esito)

    await _gira(consumer, gestore=gestore, su_fallimento=su_fallimento, giri=1)
    assert tentativi["n"] == 3
    assert len(fallimenti) == 1 and not fallimenti[0].permanente
    assert consumer.commit_fatti == [{TopicPartition("pdv.validated", 0): 8}]


async def test_ciclo_consumer_dlq_non_registrata_nessun_commit_e_rilettura():
    """Se nemmeno DLQ/stato si registrano, l'offset NON si conferma e si torna al messaggio."""
    consumer = _ConsumerFinto([_Msg("pdv.validated", 2, 9, {})])

    async def gestore(msg):
        raise ErrorePermanente("X", "dato errato")

    async def su_fallimento(msg, esito):
        raise ConnectionError("Kafka giù")

    await _gira(consumer, gestore=gestore, su_fallimento=su_fallimento, giri=1)
    assert consumer.commit_fatti == []
    assert consumer.seek_fatti == [(TopicPartition("pdv.validated", 2), 9)]
