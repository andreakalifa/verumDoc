# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Correzioni della revisione indipendente R04 (rev4 rilievo 2): un evento
«avvelenato» non ferma più il relay.

Su PostgreSQL reale (outbox partizionata):
  - la prova del revisore: un evento che fallisce sempre + 2000 eventi in attesa
    (metà della stessa chiave, metà di un'altra chiave dello STESSO gruppo) + una
    chiave innocente di un altro gruppo. Prima: 0 pubblicati in 5 giri e
    cortocircuito aperto come «Kafka giù». Ora: la chiave innocente esce al
    primo giro, l'altra chiave dello stesso gruppo esce tutta, si ferma solo la
    chiave avvelenata, il cortocircuito resta chiuso, i log dicono «evento»;
  - dopo OUTBOX_MAX_TENTATIVI la chiave è BLOCCATA (allarme, metrica), e l'età
    del più vecchio non conta gli eventi bloccati;
  - un ritentativo riuscito fa ripartire la chiave nell'ordine;
  - Kafka giù: nessun tentativo contato, nessuna chiave ferma, cortocircuito.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

import libs.shared.outbox as outbox
from libs.shared.db import get_async_session
from libs.shared.db import metriche as metriche_db
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

ORIGINE = "rev"
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


async def _gruppo(url: str, chiave: str) -> int:
    return int((await sql(url, "SELECT (hashtext($1) & 63)::int AS g", chiave))[0]["g"])


async def _chiave_nel_gruppo(url: str, prefisso: str, gruppo: int, diverso: bool) -> str:
    i = 0
    while True:
        k = f"{prefisso}-{i}"
        if (await _gruppo(url, k) != gruppo) == diverso:
            return k
        i += 1


async def _accoda(chiavi_topic: list[tuple[str, str]], tenant) -> None:
    async with get_async_session() as session:
        for n, (chiave, topic) in enumerate(chiavi_topic):
            await outbox.accoda_evento(session, topic=topic, chiave=chiave, payload={"n": n},
                                       tenant_id=tenant, origine=ORIGINE)


def _cfg(**altro) -> outbox.ConfigRelay:
    base = {"max_tentativi": 3, "riprova_secondi": 1.0, "riprova_massima_secondi": 4.0}
    base.update(altro)
    return outbox.ConfigRelay(**base)


async def test_evento_avvelenato_non_ferma_il_relay_prova_del_revisore(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    g_avv = await _gruppo(db_url, "avvelenata")
    innocente = await _chiave_nel_gruppo(db_url, "pdv", g_avv, diverso=True)
    stesso_gruppo = await _chiave_nel_gruppo(db_url, "altro", g_avv, diverso=False)
    eventi = [("avvelenata", "topic-inesistente")]
    eventi += [(stesso_gruppo if n % 2 else "avvelenata", "t") for n in range(1, 2001)]
    eventi += [(innocente, "t")]
    await _accoda(eventi, tenant)

    inviati: list[tuple[str, int]] = []

    async def invia(topic, chiave, payload):
        if topic == "topic-inesistente":
            raise RuntimeError("UNKNOWN_TOPIC_OR_PARTITION")
        inviati.append((chiave, payload["n"]))

    metriche_db.azzera()
    outbox._cortocircuiti.clear()
    with capture_logs() as log:
        primo = await outbox.pubblica_in_sospeso(invia, limite=500, origine=ORIGINE,
                                                 gruppi_per_giro=16, config=_cfg(),
                                                 adesso=T0)
        assert primo > 0
        assert any(ch == innocente for ch, _ in inviati)          # al primo giro
        # anche l'altra chiave dello STESSO gruppo parte al primo giro
        assert any(ch == stesso_gruppo for ch, _ in inviati)
        for _ in range(1, 6):
            await outbox.pubblica_in_sospeso(invia, limite=500, origine=ORIGINE,
                                             gruppi_per_giro=16, config=_cfg(), adesso=T0)
    # Tutta l'altra chiave dello stesso gruppo è uscita, nell'ordine
    altra = [n for ch, n in inviati if ch == stesso_gruppo]
    assert len(altra) == 1000 and altra == sorted(altra)
    # La chiave avvelenata non ha MAI sorpassato il suo primo evento
    assert [n for ch, n in inviati if ch == "avvelenata"] == []
    in_attesa = await sql(db_url, "SELECT count(*) AS n FROM public.outbox_eventi "
                                  "WHERE pubblicato_il IS NULL")
    assert in_attesa[0]["n"] == 1001
    ferme = await sql(db_url, "SELECT chiave, tentativi, bloccato_il FROM "
                              "public.outbox_chiavi_ferme")
    assert [(r["chiave"], r["tentativi"], r["bloccato_il"]) for r in ferme] == [
        ("avvelenata", 1, None)]   # stesso istante: nessun ritentativo prima della pausa
    # Il cortocircuito NON si è aperto e i log parlano dell'evento, non di Kafka
    assert outbox.cortocircuito(ORIGINE, invia, None).aperto_fino == 0.0
    nomi = [r["event"] for r in log]
    assert "outbox_kafka_non_disponibile" not in nomi and "outbox_invio_fallito" in nomi
    assert metriche_db.valore("verumdoc_outbox_errori_evento", origine=ORIGINE) >= 1
    assert metriche_db.valore("verumdoc_outbox_errori_kafka", origine=ORIGINE) is None


async def test_dopo_n_tentativi_la_chiave_e_bloccata_con_allarme(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda([("k", "grande"), ("k", "t"), ("altra", "t")], tenant)

    async def invia(topic, chiave, payload):
        if topic == "grande":
            raise ValueError("MessageSizeTooLargeError: messaggio troppo grande")

    metriche_db.azzera()
    adesso = T0
    with capture_logs() as log:
        for _ in range(6):
            await outbox.pubblica_in_sospeso(invia, limite=100, origine=ORIGINE,
                                             config=_cfg(), adesso=adesso)
            adesso += timedelta(seconds=10)   # oltre la pausa: un ritentativo per giro
    riga = (await sql(db_url, "SELECT tentativi, bloccato_il FROM public.outbox_chiavi_ferme "
                              "WHERE chiave = 'k'"))[0]
    assert riga["tentativi"] == 3 and riga["bloccato_il"] is not None
    bloccati = [r for r in log if r["event"] == "outbox_evento_bloccato"]
    assert len(bloccati) == 1 and bloccati[0]["tentativi"] == 3   # un allarme solo
    assert metriche_db.valore("verumdoc_outbox_eventi_bloccati", origine=ORIGINE) == 1
    arretrato, eta = await outbox.misura_arretrato(ORIGINE)
    assert arretrato == 2                                           # bloccato + successivo
    assert eta == 0.0                                               # nessun evento pubblicabile
    assert metriche_db.valore("verumdoc_outbox_bloccati", origine=ORIGINE) == 1
    assert metriche_db.valore("verumdoc_outbox_chiavi_ferme", origine=ORIGINE) == 0
    # Bloccata: nessun altro tentativo
    tentati = []

    async def conta(topic, chiave, payload):
        tentati.append(topic)

    await outbox.pubblica_in_sospeso(conta, limite=100, origine=ORIGINE, config=_cfg(),
                                     adesso=adesso + timedelta(hours=1))
    assert tentati == []


async def test_ritentativo_riuscito_riparte_nell_ordine(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda([("k", "t"), ("k", "t"), ("k", "t")], tenant)
    guasto = {"si": True}
    inviati: list[int] = []

    async def invia(topic, chiave, payload):
        if guasto["si"] and payload["n"] == 0:
            raise RuntimeError("TopicAuthorizationFailedError")
        inviati.append(payload["n"])

    outbox._cortocircuiti.clear()
    with capture_logs() as log:
        await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=_cfg(), adesso=T0)
    assert inviati == []
    # Nessun evento partito, ma per colpa dell'evento: NON è «Kafka giù»
    assert outbox.cortocircuito(ORIGINE, invia, None).aperto_fino == 0.0
    assert "outbox_kafka_non_disponibile" not in [r["event"] for r in log]
    guasto["si"] = False
    # Prima della pausa: nulla (la chiave è ferma)
    await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=_cfg(), adesso=T0)
    assert inviati == []
    await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=_cfg(),
                                     adesso=T0 + timedelta(seconds=2))
    await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=_cfg(),
                                     adesso=T0 + timedelta(seconds=2))
    assert inviati == [0, 1, 2]
    assert await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme") == []


async def test_kafka_giu_nessun_tentativo_contato_ne_chiave_ferma(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _accoda([(f"k{i}", "t") for i in range(10)], tenant)

    async def invia(topic, chiave, payload):
        raise outbox.KafkaNonDisponibile("producer Kafka non attivo")

    metriche_db.azzera()
    outbox._cortocircuiti.clear()
    with capture_logs() as log:
        assert await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=_cfg(),
                                                adesso=T0) == 0
    assert [r["event"] for r in log].count("outbox_kafka_non_disponibile") == 1
    assert "outbox_invio_fallito" not in [r["event"] for r in log]
    assert await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme") == []
    righe = await sql(db_url, "SELECT tentativi FROM public.outbox_eventi")
    assert {r["tentativi"] for r in righe} == {0}
    assert metriche_db.valore("verumdoc_outbox_errori_kafka", origine=ORIGINE) >= 1


def test_classificazione_errori_connessione_o_evento():
    import aiokafka.errors as e

    for exc in (outbox.KafkaNonDisponibile("x"), ConnectionError("x"), TimeoutError(),
                e.KafkaConnectionError(), e.KafkaTimeoutError(), e.RequestTimedOutError(),
                e.NodeNotReadyError(), e.NotLeaderForPartitionError()):
        assert outbox.errore_di_connessione(exc), exc
    for exc in (e.UnknownTopicOrPartitionError(), e.MessageSizeTooLargeError(),
                e.TopicAuthorizationFailedError(), TypeError("non serializzabile"),
                RuntimeError("altro")):
        assert not outbox.errore_di_connessione(exc), exc
