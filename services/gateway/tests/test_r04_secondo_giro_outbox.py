# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Secondo giro delle correzioni R04 sull'outbox (verifica del revisore):

  - N1: un errore del TOPIC (permesso negato sul topic dopo un cambio delle
    policy IAM per topic, topic inesistente) non ferma le chiavi una per una:
    il topic si SOSPENDE con attesa crescente e un allarme specifico
    (``outbox_topic_sospeso``), nessun tentativo delle chiavi si consuma, e al
    ripristino l'arretrato riparte a lotto pieno. Prova del revisore
    (prova_outbox_errore_sistemico.py): 3.000 chiavi, permesso negato per 20
    giri; prima «outbox svuotata al giro 170 = 300 s reali» e 3.000 chiavi
    ferme; ora pochi giri (secondi) e nessuna chiave ferma;
  - ordine per chiave: un evento successivo della stessa chiave su un ALTRO
    topic non sorpassa l'evento fermo sul topic sospeso;
  - N2: la pubblicazione dopo il commit (dentro la richiesta HTTP) non fa i
    ritentativi delle chiavi ferme: li fa solo il relay.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

import libs.shared.outbox as outbox
from libs.shared.db import get_async_session
from libs.shared.db import metriche as metriche_db
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

ORIGINE = "rev"
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


class TopicAuthorizationFailedError(Exception):
    """Stesso nome dell'errore di aiokafka (la classificazione guarda il nome)."""


async def _accoda(eventi: list[tuple[str, str]]) -> None:
    async with get_async_session() as session:
        for n, (chiave, topic) in enumerate(eventi):
            await outbox.accoda_evento(session, topic=topic, chiave=chiave, payload={"n": n},
                                       origine=ORIGINE)


async def _in_attesa(url: str) -> int:
    return int((await sql(url, "SELECT count(*) AS n FROM public.outbox_eventi "
                               "WHERE pubblicato_il IS NULL"))[0]["n"])


@pytest.mark.integration
@richiede_pg
async def test_errore_del_topic_sospende_il_topic_e_recupero_rapido(db_url):
    """La prova del revisore come test (stessi parametri del relay)."""
    await _accoda([(f"aip-{n}", "aip.conserved") for n in range(3000)])
    stato = {"rotto": True}

    async def invia(topic, chiave, payload):
        if stato["rotto"]:
            raise TopicAuthorizationFailedError("not authorized")

    cfg = outbox.ConfigRelay(max_tentativi=10)
    adesso = T0
    metriche_db.azzera()
    with capture_logs() as log:
        for _ in range(20):
            outbox._cortocircuiti.clear()
            await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, limite=500,
                                             gruppi_per_giro=16, config=cfg, adesso=adesso)
            adesso += timedelta(seconds=30)
    # Nessuna chiave ferma o bloccata, nessun tentativo consumato
    assert await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme") == []
    assert {r["t"] for r in await sql(
        db_url, "SELECT DISTINCT tentativi AS t FROM public.outbox_eventi")} == {0}
    sospeso = await sql(db_url, "SELECT topic, sospensioni, pausa_secondi, ultimo_errore "
                                "FROM public.outbox_topic_sospesi")
    assert [(r["topic"], r["sospensioni"]) for r in sospeso] == [("aip.conserved", 20)]
    assert sospeso[0]["pausa_secondi"] == cfg.pausa_topic_massima_secondi  # 2 → 30 s
    assert "TopicAuthorizationFailedError" in sospeso[0]["ultimo_errore"]
    eventi = [r["event"] for r in log]
    assert eventi.count("outbox_topic_sospeso") == 20
    assert "outbox_invio_fallito" not in eventi and "outbox_evento_bloccato" not in eventi
    assert metriche_db.valore("verumdoc_outbox_topic_sospensioni", origine=ORIGINE) == 20
    await outbox.misura_arretrato(ORIGINE)
    assert metriche_db.valore("verumdoc_outbox_topic_sospesi", origine=ORIGINE) == 1

    # Permesso ripristinato: l'arretrato riparte a lotto pieno
    stato["rotto"] = False
    giri = 0
    with capture_logs() as log:
        while await _in_attesa(db_url) and giri < 50:
            outbox._cortocircuiti.clear()
            await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, limite=500,
                                             gruppi_per_giro=16, config=cfg, adesso=adesso)
            adesso += timedelta(seconds=30)
            giri += 1
    # 3.000 eventi a 500 per giro: 6 giri (12 s reali con OUTBOX_INTERVALLO 2 s), non 150
    assert giri <= 7, giri
    assert await sql(db_url, "SELECT 1 FROM public.outbox_topic_sospesi") == []
    assert [r["event"] for r in log].count("outbox_topic_ripristinato") == 1
    await outbox.misura_arretrato(ORIGINE)
    assert metriche_db.valore("verumdoc_outbox_topic_sospesi", origine=ORIGINE) == 0


@pytest.mark.integration
@richiede_pg
async def test_topic_sospeso_attesa_crescente_e_altri_topic_proseguono(db_url):
    """Durante la sospensione il topic non si tenta (nessun invio), gli altri
    topic proseguono; allo scadere UN tentativo; la pausa raddoppia fino al massimo."""
    await _accoda([("a-1", "rotto"), ("a-2", "rotto"), ("b-1", "sano"), ("b-2", "sano")])
    inviati: list[str] = []

    async def invia(topic, chiave, payload):
        inviati.append(topic)
        if topic == "rotto":
            raise TopicAuthorizationFailedError("not authorized")

    cfg = outbox.ConfigRelay(pausa_topic_secondi=2.0, pausa_topic_massima_secondi=8.0)
    assert await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=cfg,
                                            adesso=T0) == 2
    pause = []
    for secondi in (1, 2.5, 3, 6.5, 7, 14.5, 15, 23):  # dentro e fuori dalla pausa
        inviati.clear()
        await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=cfg,
                                         adesso=T0 + timedelta(seconds=secondi))
        riga = (await sql(db_url, "SELECT pausa_secondi FROM public.outbox_topic_sospesi"))[0]
        pause.append((secondi, inviati.count("rotto"), riga["pausa_secondi"]))
    # tentativi solo allo scadere: 2 s, poi 4, poi 8 (massimo), poi ancora 8
    assert pause == [(1, 0, 2.0), (2.5, 1, 4.0), (3, 0, 4.0), (6.5, 1, 8.0), (7, 0, 8.0),
                     (14.5, 1, 8.0), (15, 0, 8.0), (23, 1, 8.0)]
    assert await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme") == []


@pytest.mark.integration
@richiede_pg
async def test_ordine_per_chiave_dietro_un_topic_sospeso(db_url):
    """Stessa chiave: evento 1 sul topic sospeso, evento 2 su un altro topic. Il 2
    non sorpassa l'1 né mentre il topic è sospeso né nel giro della sospensione."""
    await _accoda([("k", "rotto"), ("k", "sano"), ("altra", "sano")])
    stato = {"rotto": True}
    ordine: list[tuple[str, str]] = []

    async def invia(topic, chiave, payload):
        if topic == "rotto" and stato["rotto"]:
            raise TopicAuthorizationFailedError("not authorized")
        ordine.append((chiave, topic))

    cfg = outbox.ConfigRelay()
    for secondi in (0, 1):  # giro della sospensione e giro durante la sospensione
        await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=cfg,
                                         adesso=T0 + timedelta(seconds=secondi))
    assert ordine == [("altra", "sano")]
    stato["rotto"] = False
    await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=cfg,
                                     adesso=T0 + timedelta(seconds=60))
    # topic ripristinato: k riparte nell'ordine (prima «rotto», poi «sano»)
    assert ordine == [("altra", "sano"), ("k", "rotto"), ("k", "sano")]


def test_classificazione_errori_del_topic():
    import aiokafka.errors as e

    for exc in (e.TopicAuthorizationFailedError(), e.ClusterAuthorizationFailedError(),
                e.UnknownTopicOrPartitionError(), e.InvalidTopicError(),
                TopicAuthorizationFailedError("x")):
        assert outbox.errore_del_topic(exc), exc
        assert not outbox.errore_di_connessione(exc), exc
    for exc in (e.MessageSizeTooLargeError(), TypeError("non serializzabile"),
                e.KafkaConnectionError(), outbox.KafkaNonDisponibile("x")):
        assert not outbox.errore_del_topic(exc), exc


@pytest.mark.integration
@richiede_pg
async def test_pubblicazione_dopo_il_commit_senza_ritentativi(db_url, monkeypatch):
    """N2: una chiave ferma con il ritentativo scaduto NON si ritenta nella
    richiesta del cliente (pubblica_dopo_commit); il relay sì."""
    await _accoda([("ferma", "t"), ("nuova", "t")])
    inviati: list[str] = []

    async def invia_ko(topic, chiave, payload):
        raise ValueError("payload non valido")

    async def invia(topic, chiave, payload):
        inviati.append(chiave)

    cfg = outbox.ConfigRelay(max_tentativi=5)
    # la chiave «ferma» si ferma; poi arriva un evento nuovo della chiave «nuova2»
    await outbox.pubblica_in_sospeso(invia_ko, origine=ORIGINE, config=cfg, adesso=T0)
    assert len(await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme")) == 2
    await sql(db_url, "UPDATE public.outbox_chiavi_ferme SET prossimo_tentativo_il = $1",
              T0 - timedelta(hours=1))
    await _accoda([("nuova2", "t")])
    originale = outbox.pubblica_in_sospeso

    async def con_invio_finto(**argomenti):
        return await originale(invia, origine=ORIGINE, config=cfg, adesso=T0, **argomenti)

    monkeypatch.setattr(outbox, "pubblica_in_sospeso", con_invio_finto)
    assert await outbox.pubblica_dopo_commit(limite=20) == 1
    assert inviati == ["nuova2"]  # niente ritentativi delle chiavi ferme
    monkeypatch.setattr(outbox, "pubblica_in_sospeso", originale)
    assert await outbox.pubblica_in_sospeso(invia, origine=ORIGINE, config=cfg,
                                            adesso=T0) == 2
    assert sorted(inviati) == ["ferma", "nuova", "nuova2"]
