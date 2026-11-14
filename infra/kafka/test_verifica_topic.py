# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""La lista unica dei topic (topics.yaml) coincide con il codice (VD-C-035).

Eseguibile con pytest dalla radice: ``pytest infra/kafka``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "verifica_topic", Path(__file__).with_name("verifica_topic.py"))
verifica_topic = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verifica_topic)


def test_lista_coerente_con_il_codice(capsys) -> None:
    assert verifica_topic.main() == 0, capsys.readouterr().out


def test_ogni_coda_di_scarto_usata_e_dichiarata() -> None:
    dichiarati = verifica_topic.topic_dichiarati()
    dlq_codice = {n for n in verifica_topic.topic_nel_codice() if n.endswith(".dlq")}
    assert {"pdv.received.dlq", "pdv.validated.dlq", "notification.dlq"} <= dlq_codice
    assert dlq_codice <= dichiarati


def test_topic_r03_dell_outbox_e_della_mappa_eventi_riconosciuti() -> None:
    """R03: i topic dell'outbox (costanti TOPIC_*) e quelli della mappa eventi_topic
    di notification sono trovati nel codice e dichiarati."""
    codice = verifica_topic.topic_nel_codice()
    assert {"caricamento.completato", "caricamento.errore", "pdd.pronto"} <= set(codice)
    assert any("(mappa)" in dove for dove in codice["caricamento.completato"])
    assert {"caricamento.completato", "caricamento.errore", "pdd.pronto"} \
        <= verifica_topic.topic_dichiarati()


def test_costante_topic_e_mappa_in_un_servizio_finto(tmp_path) -> None:
    """Il riconoscimento funziona su sorgenti arbitrari (non solo sul repository)."""
    app = tmp_path / "services" / "finto" / "app"
    app.mkdir(parents=True)
    (app / "eventi.py").write_text('TOPIC_X = "x.creato"\nNON_TOPIC = "y"\n', encoding="utf-8")
    (app / "config.py").write_text(
        "class S:\n    eventi_topic: dict[str, str] = {\"a.b\": \"tipo\"}\n", encoding="utf-8")
    trovati = verifica_topic.topic_nel_codice(tmp_path)
    assert set(trovati) == {"x.creato", "a.b"}


def test_partizioni_pipeline_dimensionate() -> None:
    """I topic della pipeline hanno almeno 48 partizioni (obiettivo 1M documenti/giorno)."""
    import re

    testo = verifica_topic.LISTA.read_text(encoding="utf-8")
    voci = dict(re.findall(r"-\s*name:\s*(\S+)\s*\n\s*partitions:\s*(\d+)", testo))
    for nome in ("pdv.received", "pdv.validated", "aip.conserved"):
        assert int(voci[nome]) >= 48, nome
