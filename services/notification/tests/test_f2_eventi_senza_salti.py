# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-13) — l'elenco eventi a cursore non salta mai un evento, su
PostgreSQL VERO con due connessioni; consumer a lotti con un commit per lotto.

Lo scenario della revisione: un'insert che fa commit molto dopo (lock, fsync,
pausa) ha un id MINORE di eventi già letti da un client in polling. Con il
vecchio cursore sull'id (e 2 s di «visibilità») l'evento era perso per sempre.
Ora il cursore è la posizione assegnata DOPO il commit da un passo seriale:
l'evento lento riceve una posizione maggiore di quelle già lette e il client
lo trova al giro successivo.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest
from app.application.eventi_consumer import ciclo_pubblicazione, elabora_lotto_kafka
from app.domain.eventi import EventoCliente
from app.infrastructure.repo_eventi import elenca_eventi, pubblica_eventi, registra_evento

from tests import infra_reale
from tests.infra_reale import richiede_pg, sql

db_url = infra_reale.db_url  # fixture condivisa

pytestmark = [pytest.mark.integration]


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _ente(url: str) -> uuid.UUID:
    t = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', "
                   "'a@example.org')", t, t.hex[:16], f"t_{t.hex[:20]}")
    return t


_INSERISCI = ("INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, risorsa_id, dati) "
              "VALUES ($1, 'versamento.ricevuto', 'versamento', $2, '{}'::jsonb) RETURNING id")


@richiede_pg
async def test_evento_con_commit_lento_non_si_perde(db_url) -> None:
    ente = await _ente(db_url)
    lenta = await asyncpg.connect(_dsn(db_url))
    try:
        # 1. la transazione «lenta» inserisce per prima (id minore) e non fa commit
        transazione = lenta.transaction()
        await transazione.start()
        id_lento = await lenta.fetchval(_INSERISCI, ente, "lento")
        # 2. un'altra inserisce dopo (id maggiore) e fa commit subito
        await registra_evento(EventoCliente(str(ente), "versamento.ricevuto", "versamento",
                                            "veloce", {}), origine="x")
        assert await pubblica_eventi() == 1          # l'evento lento non è ancora visibile
        pagina, _ = await elenca_eventi(str(ente), dopo=0, limite=10, tipi=[])
        assert [e.risorsa_id for e in pagina] == ["veloce"]
        # proprio il caso che il vecchio cursore (sull'id) perdeva
        assert pagina[0].id > id_lento
        cursore = pagina[0].posizione
        # 3. il commit lento arriva dopo che il client ha letto oltre
        await transazione.commit()
    finally:
        await lenta.close()
    assert await pubblica_eventi() == 1
    seguito, altri = await elenca_eventi(str(ente), dopo=cursore, limite=10, tipi=[])
    assert [e.risorsa_id for e in seguito] == ["lento"] and not altri
    assert seguito[0].posizione > cursore


@richiede_pg
async def test_pubblicazione_seriale_fra_repliche(db_url) -> None:
    """Mentre una replica pubblica (contatore bloccato) l'altra salta il giro;
    le posizioni sono uniche e consecutive."""
    ente = await _ente(db_url)
    for i in range(5):
        await registra_evento(EventoCliente(str(ente), "versamento.ricevuto", "versamento",
                                            f"r{i}", {}), origine="x")
    occupata = await asyncpg.connect(_dsn(db_url))
    try:
        async with occupata.transaction():
            await occupata.execute("SELECT 1 FROM public.eventi_cliente_pubblicazione "
                                   "WHERE id = 1 FOR UPDATE")
            assert await pubblica_eventi() == 0      # SKIP LOCKED: nessuna attesa
    finally:
        await occupata.close()
    primi, secondi = await asyncio.gather(pubblica_eventi(limite=3), pubblica_eventi(limite=3))
    # o la seconda trova il contatore occupato (0), o arriva dopo il commit della prima (2)
    assert sorted([primi, secondi]) in ([0, 3], [2, 3])
    while await pubblica_eventi(limite=3):
        pass
    righe = await sql(db_url, "SELECT id, posizione FROM public.eventi_cliente "
                              "WHERE tenant_id = $1 ORDER BY id", ente)
    posizioni = [r["posizione"] for r in righe]
    assert None not in posizioni and posizioni == sorted(posizioni)
    assert posizioni == list(range(posizioni[0], posizioni[0] + 5))
    contatore = await sql(db_url, "SELECT ultima_posizione FROM "
                                  "public.eventi_cliente_pubblicazione")
    assert contatore[0]["ultima_posizione"] == posizioni[-1]


@richiede_pg
async def test_ciclo_pubblicazione_ripete_subito_a_lotto_pieno(db_url) -> None:
    """A lotto pieno il ciclo non aspetta l'intervallo (smaltisce i picchi)."""
    chiamate: list[int] = []

    async def pubblica(*, limite: int) -> int:
        chiamate.append(limite)
        if len(chiamate) == 3:
            raise asyncio.CancelledError
        return limite if len(chiamate) == 1 else 0

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(ciclo_pubblicazione(intervallo_secondi=0.01, lotto=7,
                                                   pubblica=pubblica), timeout=5)
    assert chiamate == [7, 7, 7]


# ── Consumer a lotti ────────────────────────────────────────────────

@dataclass
class Messaggio:
    topic: str
    partition: int
    offset: int
    value: Any


async def test_elabora_lotto_kafka_partizioni_in_parallelo_in_ordine() -> None:
    visti: list[tuple[int, int]] = []

    async def elabora(topic: str, evento: dict) -> int:
        await asyncio.sleep(0.001 * (3 - evento["p"]))
        visti.append((evento["p"], evento["n"]))
        return 0

    async def dlq(_: dict) -> None:
        raise AssertionError("nessuna DLQ attesa")

    lotti = {p: [Messaggio("pdv.received", p, n, {"p": p, "n": n}) for n in range(4)]
             for p in (0, 1, 2)}
    assert await elabora_lotto_kafka(lotti, elabora, ritardi=[], pubblica=dlq) == 12
    for p in (0, 1, 2):
        assert [n for q, n in visti if q == p] == [0, 1, 2, 3]


async def test_elabora_lotto_kafka_dlq_fallita_non_conferma() -> None:
    """Se un messaggio non è né registrato né sulla DLQ il lotto solleva (gli
    offset non si confermano), dopo che le altre partizioni hanno finito."""
    finiti: list[int] = []

    async def elabora(topic: str, evento: dict) -> int:
        if evento.get("rotto"):
            raise ValueError("errore")
        await asyncio.sleep(0.01)
        finiti.append(evento["n"])
        return 0

    async def dlq(_: dict) -> None:
        raise ConnectionError("DLQ irraggiungibile")

    lotti = {0: [Messaggio("t", 0, 0, {"rotto": True})],
             1: [Messaggio("t", 1, 0, {"n": 1}), Messaggio("t", 1, 1, {"n": 2})]}
    with pytest.raises(ConnectionError):
        await elabora_lotto_kafka(lotti, elabora, ritardi=[], pubblica=dlq)
    assert finiti == [1, 2]
