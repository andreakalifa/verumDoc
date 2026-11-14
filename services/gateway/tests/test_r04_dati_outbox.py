# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — outbox con un relay per servizio, a gruppi e in più repliche
(VD-C-076, piano §4.2), su PostgreSQL reale (tabella partizionata per giorno,
migrazione 20261023_028).
"""
from __future__ import annotations

import asyncio
import random
import uuid
from collections import defaultdict

import asyncpg
import pytest

import libs.shared.outbox as outbox
from libs.shared.db import get_async_session
from libs.shared.db import metriche as metriche_db
from tests.infra_reale import (
    _asyncpg_dsn,
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]


async def _accoda(tenant, eventi: list[tuple[str | None, int]], origine: str | None = None):
    async with get_async_session() as session:
        for chiave, i in eventi:
            await outbox.accoda_evento(session, topic="t", chiave=chiave, payload={"i": i},
                                       tenant_id=tenant, origine=origine)


async def test_accoda_evento_origine_del_servizio_e_gruppo_dalla_chiave(db_url):
    """Senza origine esplicita vale quella del servizio (``service_name`` del
    gateway); il gruppo dipende solo dalla chiave (stessa chiave, stesso gruppo)."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda(tenant, [("pdv-1", 0), ("pdv-1", 1), ("pdv-2", 2), (None, 3)])
    righe = await sql(db_url, "SELECT origine, gruppo, chiave FROM public.outbox_eventi "
                              "ORDER BY sequenza")
    assert {r["origine"] for r in righe} == {"gateway"}
    assert righe[0]["gruppo"] == righe[1]["gruppo"]
    assert all(0 <= r["gruppo"] < outbox.NUMERO_GRUPPI for r in righe)
    calcolato = await sql(db_url, "SELECT (hashtext('pdv-2') & 63)::smallint AS g")
    assert righe[2]["gruppo"] == calcolato[0]["g"]


async def test_relay_pubblica_solo_la_propria_origine(db_url):
    """VD-C-076: il relay del gateway non pubblica più gli eventi di onboarding."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda(tenant, [("t1", 0)], origine="onboarding")
    await _accoda(tenant, [("p1", 1)], origine="gateway")
    inviati: list[int] = []

    async def invia(topic, chiave, payload):
        inviati.append(payload["i"])

    assert await outbox.pubblica_in_sospeso(invia, origine="gateway") == 1
    assert inviati == [1]
    assert await outbox.pubblica_in_sospeso(invia, origine="onboarding") == 1
    assert inviati == [1, 0]
    assert await outbox.pubblica_in_sospeso(invia, origine="gateway") == 0


async def test_due_repliche_nessun_doppione_e_nessun_sorpasso_per_chiave(db_url):
    """Due relay concorrenti (connessioni distinte) con 600 eventi su 20 chiavi:
    ogni evento esce una volta sola e, per ogni chiave, nell'ordine di scrittura."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    rnd = random.Random(7)  # noqa: S311 — ordine casuale riproducibile, non crittografia
    eventi = [(f"k{rnd.randrange(20)}", i) for i in range(600)]
    await _accoda(tenant, eventi)
    pubblicati: list[tuple[str, int, str]] = []

    def invio_di(replica: str):
        async def invia(topic, chiave, payload):
            await asyncio.sleep(rnd.random() / 2000)
            pubblicati.append((chiave, payload["i"], replica))
        return invia

    async def replica(nome: str) -> None:
        vuoti = 0
        while vuoti < 3:
            n = await outbox.pubblica_in_sospeso(invio_di(nome), limite=50,
                                                 origine="gateway", gruppi_per_giro=8)
            vuoti = vuoti + 1 if n == 0 else 0

    await asyncio.gather(replica("A"), replica("B"))
    assert sorted(i for _, i, _ in pubblicati) == list(range(600))   # nessun doppione
    per_chiave: dict[str, list[int]] = defaultdict(list)
    for chiave, i, _ in pubblicati:
        per_chiave[chiave].append(i)
    attesi: dict[str, list[int]] = defaultdict(list)
    for chiave, i in eventi:
        attesi[chiave].append(i)
    assert per_chiave == attesi                                       # ordine per chiave
    assert {r for _, _, r in pubblicati} == {"A", "B"}                # lavoro diviso
    restanti = await sql(db_url, "SELECT count(*) AS n FROM public.outbox_eventi "
                                 "WHERE pubblicato_il IS NULL")
    assert restanti[0]["n"] == 0


async def test_gruppo_preso_da_altra_replica_non_si_tocca(db_url):
    """Un gruppo con il lock consultivo tenuto da un'altra transazione non si
    pubblica (nessun sorpasso possibile); gli altri gruppi sì."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda(tenant, [("bloccata", 0), ("libera", 1)])
    gruppo = (await sql(db_url, "SELECT (hashtext('bloccata') & 63)::int AS g"))[0]["g"]
    if gruppo == (await sql(db_url, "SELECT (hashtext('libera') & 63)::int AS g"))[0]["g"]:
        pytest.skip("le due chiavi cadono nello stesso gruppo")
    altra = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        await altra.execute("BEGIN")
        await altra.execute("SELECT pg_advisory_xact_lock($1, $2)",
                            outbox._chiave_lock("gateway"), gruppo)
        inviati: list[int] = []

        async def invia(topic, chiave, payload):
            inviati.append(payload["i"])

        assert await outbox.pubblica_in_sospeso(invia, origine="gateway") == 1
        assert inviati == [1]
        await altra.execute("COMMIT")
        assert await outbox.pubblica_in_sospeso(invia, origine="gateway") == 1
        assert inviati == [1, 0]
    finally:
        await altra.close()


async def test_misura_arretrato_conta_e_eta_del_piu_vecchio(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    metriche_db.azzera()
    assert await outbox.misura_arretrato("gateway") == (0, 0.0)
    await _accoda(tenant, [("a", 0), ("b", 1)])
    await sql(db_url, "UPDATE public.outbox_eventi SET creato_il = creato_il - interval '90 s' "
                      "WHERE chiave = 'a'")
    arretrato, eta = await outbox.misura_arretrato("gateway")
    assert arretrato == 2 and 85 <= eta <= 200
    assert metriche_db.valore("verumdoc_outbox_arretrato", origine="gateway") == 2
    assert metriche_db.valore("verumdoc_outbox_eta_piu_vecchio_secondi", origine="gateway") >= 85


async def test_avvia_relay_servizio_pubblica_e_metriche_sul_registro(db_url, monkeypatch):
    """Riga d'avvio per i servizi (anche hashchain/aip-builder): origine
    configurata, relay attivo, metriche sul registro di ``installa_metriche``."""
    from fastapi import FastAPI
    from prometheus_client import generate_latest

    from libs.shared.observability.metriche import installa_metriche

    tenant, _ = await crea_tenant_e_classe(db_url)
    monkeypatch.setenv("OUTBOX_MISURA_OGNI_SECONDI", "0")
    monkeypatch.setenv("DB_MANUTENZIONE_ATTIVA", "false")
    monkeypatch.setattr(outbox, "_origine", None)
    app = FastAPI()
    metriche = installa_metriche(app, "prova-relay")
    inviati: list[tuple[str, int]] = []

    async def invia(topic, chiave, payload):
        inviati.append((chiave, payload["i"]))

    relay = await outbox.avvia_relay_servizio("hashchain", app=app, invia=invia)
    try:
        assert outbox.origine_corrente() == "hashchain"
        await _accoda(tenant, [("cp-1", 0)])
        for _ in range(100):
            if inviati:
                break
            await asyncio.sleep(0.05)
        assert inviati == [("cp-1", 0)]
        await asyncio.sleep(0.1)
        testo = generate_latest(metriche.registro).decode()
        assert 'verumdoc_outbox_arretrato{origine="hashchain"}' in testo
        assert 'verumdoc_outbox_pubblicati_total{origine="hashchain"}' in testo
    finally:
        await outbox.ferma_relay_servizio(relay, attesa=5)
        monkeypatch.setattr(outbox, "_origine", None)
    assert relay.task.done() and not relay.task.cancelled()
    righe = await sql(db_url, "SELECT origine, pubblicato_il FROM public.outbox_eventi")
    assert [(r["origine"], r["pubblicato_il"] is not None) for r in righe] == [("hashchain", True)]


async def test_relay_disattivato_nessun_task_ma_origine_configurata(db_url, monkeypatch):
    monkeypatch.setenv("OUTBOX_RELAY_ATTIVO", "false")
    monkeypatch.setenv("DB_MANUTENZIONE_ATTIVA", "false")
    monkeypatch.setattr(outbox, "_origine", None)
    relay = await outbox.avvia_relay_servizio("aip-builder")
    try:
        assert relay.task is None and outbox.origine_corrente() == "aip-builder"
    finally:
        await outbox.ferma_relay_servizio(relay)
        monkeypatch.setattr(outbox, "_origine", None)


async def test_evento_su_giorno_senza_partizione_fallisce_esplicitamente(db_url):
    """Nessuna partizione DEFAULT: una riga fuori dalle partizioni non sparisce
    in una tabella di riserva, l'INSERT fallisce (e la manutenzione crea le
    partizioni con 7 giorni d'anticipo)."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await sql(db_url, "INSERT INTO public.outbox_eventi (id, origine, gruppo, topic, payload, "
                          "creato_il) VALUES ($1, 'gateway', 0, 't', '{}', now() + interval "
                          "'400 days')", uuid.uuid4())
    del tenant
