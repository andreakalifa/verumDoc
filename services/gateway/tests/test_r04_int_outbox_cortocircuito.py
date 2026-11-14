# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — outbox con Kafka giù: cortocircuito con riprova a intervalli
(richiesta di r04-infra: 21.933 righe di log in 4,5 minuti di prova di carico,
una per evento a ogni versamento).

Su PostgreSQL reale (outbox partizionata, migrazione 028):
  - primo giro con Kafka giù: UN avviso, eventi ancora in attesa;
  - giri successivi entro la pausa (come ``pubblica_dopo_commit`` dopo ogni
    versamento): nessun invio, nessun log;
  - allo scadere della pausa UN giro di prova; fallito → pausa doppia;
  - Kafka di nuovo su: un giro pubblica tutto e il circuito si chiude;
  - producer non attivo (avvio fallito): stesso comportamento, nessuna eccezione.
"""
from __future__ import annotations

import pytest
from structlog.testing import capture_logs

import libs.shared.outbox as outbox
from libs.shared import kafka as kafka_condiviso
from libs.shared.db import get_async_session
from libs.shared.db import metriche as metriche_db
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


class Orologio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def _accoda(tenant, n: int) -> None:
    async with get_async_session() as session:
        for i in range(n):
            await outbox.accoda_evento(session, topic="t", chiave=f"k{i}", payload={"i": i},
                                       tenant_id=tenant, origine="gateway")


async def test_kafka_giu_un_avviso_poi_silenzio_e_prova_a_intervalli(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda(tenant, 40)
    stato = {"su": False, "invii": 0}

    async def invia(topic, chiave, payload):
        stato["invii"] += 1
        if not stato["su"]:
            raise ConnectionError("broker non raggiungibile")

    orologio = Orologio()
    circuito = outbox.cortocircuito("gateway", invia, None)
    circuito.orologio = orologio

    with capture_logs() as log:
        assert await outbox.pubblica_in_sospeso(invia, limite=100, origine="gateway") == 0
        primo_giro = stato["invii"]
        assert primo_giro >= 1
        # 200 «versamenti» entro la pausa: nessun invio, nessun log
        for _ in range(200):
            assert await outbox.pubblica_in_sospeso(invia, limite=20, origine="gateway") == 0
    assert stato["invii"] == primo_giro
    avvisi = [r for r in log if r["log_level"] == "warning"]
    assert [r["event"] for r in avvisi] == ["outbox_kafka_non_disponibile"]
    assert avvisi[0]["prossima_prova_secondi"] == 2.0
    assert circuito.saltati == 200
    attesa = await sql(db_url, "SELECT count(*) AS n FROM public.outbox_eventi "
                               "WHERE pubblicato_il IS NULL")
    assert attesa[0]["n"] == 40  # eventi durevoli, nulla perso

    # pausa scaduta: un giro di prova, ancora giù → pausa doppia
    orologio.t += 2.1
    with capture_logs() as log:
        assert await outbox.pubblica_in_sospeso(invia, limite=100, origine="gateway") == 0
    assert stato["invii"] > primo_giro
    assert [r["prossima_prova_secondi"] for r in log if r["log_level"] == "warning"] == [4.0]
    for _ in range(5):  # la pausa raddoppia fino al massimo (60 s)
        orologio.t += circuito.pausa + 0.1
        await outbox.pubblica_in_sospeso(invia, limite=100, origine="gateway")
    assert circuito.pausa == 60.0

    # Kafka di nuovo su: allo scadere la prova pubblica tutto e chiude il circuito
    stato["su"] = True
    orologio.t += 60.1
    with capture_logs() as log:
        pubblicati = 0
        for _ in range(3):
            pubblicati += await outbox.pubblica_in_sospeso(invia, limite=100, origine="gateway")
    assert pubblicati == 40
    assert "outbox_kafka_ripristinato" in [r["event"] for r in log]
    assert circuito.fallimenti == 0 and not circuito.aperto()
    restano = await sql(db_url, "SELECT count(*) AS n FROM public.outbox_eventi "
                                "WHERE pubblicato_il IS NULL")
    assert restano[0]["n"] == 0


async def test_producer_non_attivo_dopo_commit_nessuna_valanga(db_url, monkeypatch):
    """Il gateway dopo ogni versamento chiama ``pubblica_dopo_commit``: con il
    producer non partito c'è un avviso solo, poi più nulla fino alla prova."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda(tenant, 5)
    monkeypatch.setattr(kafka_condiviso, "_producer", None)
    monkeypatch.setattr(kafka_condiviso, "_bootstrap", None)  # nessuna riprova possibile
    outbox._cortocircuiti.clear()
    with capture_logs() as log:
        for _ in range(100):
            assert await outbox.pubblica_dopo_commit(limite=20, pdv_id="x") == 0
    avvisi = [r["event"] for r in log if r["log_level"] in ("warning", "error")]
    assert avvisi == ["outbox_kafka_non_disponibile"]
    assert metriche_db._gauge[("verumdoc_outbox_kafka_cortocircuito",  # noqa: SLF001
                               (("origine", "gateway"),))] == 1.0
    outbox._cortocircuiti.clear()


async def test_errori_parziali_un_riepilogo_e_circuito_chiuso(db_url):
    """Kafka risponde ma un topic fallisce: niente cortocircuito, un riepilogo per giro."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda(tenant, 6)

    async def invia(topic, chiave, payload):
        if payload["i"] % 2:
            raise ValueError("topic inesistente")

    with capture_logs() as log:
        assert await outbox.pubblica_in_sospeso(invia, limite=100, origine="gateway") == 3
    avvisi = [r for r in log if r["log_level"] == "warning"]
    assert [r["event"] for r in avvisi] == ["outbox_invio_fallito"]
    assert avvisi[0]["falliti"] == 3
    assert not outbox.cortocircuito("gateway", invia, None).aperto()
