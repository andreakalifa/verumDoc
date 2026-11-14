# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/db/metriche.py — metriche Prometheus dei lavori sul database (R04).

Stato in memoria del processo, aggiornato da chi misura (relay dell'outbox,
manutenzione delle partizioni, consolidamento dei contatori) e letto da UN
collettore registrato sul registro del servizio (quello di
``libs.shared.observability.metriche.installa_metriche``):

  verumdoc_outbox_arretrato{origine}                  eventi in attesa di pubblicazione
  verumdoc_outbox_eta_piu_vecchio_secondi{origine}    età dell'evento più vecchio in attesa
  verumdoc_outbox_pubblicati_total{origine}           eventi pubblicati da questo processo
  verumdoc_outbox_errori_total{origine}               invii falliti da questo processo
  verumdoc_outbox_kafka_cortocircuito{origine}        1 = Kafka giù, pubblicazione sospesa (R04)
  verumdoc_outbox_errori_kafka_total{origine}         invii falliti per Kafka non raggiungibile
  verumdoc_outbox_errori_evento_total{origine}        invii falliti per colpa dell'evento
  verumdoc_outbox_chiavi_ferme{origine}               chiavi ferme in riprova (errore dell'evento)
  verumdoc_outbox_bloccati{origine}                   chiavi BLOCCATE dopo OUTBOX_MAX_TENTATIVI
  verumdoc_outbox_eventi_bloccati_total{origine}      eventi bloccati da questo processo
  verumdoc_outbox_topic_sospesi{origine}              topic sospesi per un errore del topic (N1)
  verumdoc_outbox_topic_sospensioni_total{origine}    sospensioni di topic da questo processo
  verumdoc_partizioni_future{tabella}                 periodi futuri già pronti
  verumdoc_partizioni_bloccate{tabella}               partizioni scadute non staccabili
  verumdoc_partizioni_staccate_total{tabella}         partizioni staccate da questo processo
  verumdoc_contatori_delta_in_attesa                  variazioni dei contatori da consolidare
  verumdoc_parametri_scartati_total{codice,livello,motivo}  righe di parametri ignorate (R07 W3)

Allarmi suggeriti (r04-infra): arretrato in crescita per 10 minuti; età del più
vecchio > 300 s; partizioni future < 2; partizioni bloccate > 0.
L'arretrato di ogni origine è misurato da ogni replica del suo relay: in
Prometheus si legge con ``max by (origine)``.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

_blocco = threading.Lock()
_gauge: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_contatori: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

_DESCRIZIONI = {
    "verumdoc_outbox_arretrato": "Eventi dell'outbox in attesa di pubblicazione",
    "verumdoc_outbox_eta_piu_vecchio_secondi": "Età in secondi dell'evento più vecchio in attesa",
    "verumdoc_outbox_pubblicati": "Eventi dell'outbox pubblicati da questo processo",
    "verumdoc_outbox_errori": "Invii dell'outbox falliti in questo processo",
    "verumdoc_outbox_kafka_cortocircuito": "1 se il cortocircuito verso Kafka è aperto",
    "verumdoc_outbox_errori_kafka": "Invii falliti perché Kafka non è raggiungibile",
    "verumdoc_outbox_errori_evento": "Invii falliti per un errore del singolo evento",
    "verumdoc_outbox_chiavi_ferme": "Chiavi dell'outbox ferme in riprova per un errore dell'evento",
    "verumdoc_outbox_bloccati": "Chiavi dell'outbox bloccate (da gestire nelle code di scarto)",
    "verumdoc_outbox_eventi_bloccati": "Eventi dell'outbox bloccati da questo processo",
    "verumdoc_outbox_topic_sospesi": "Topic dell'outbox sospesi per un errore del topic "
                                     "o dell'autorizzazione",
    "verumdoc_outbox_topic_sospensioni": "Sospensioni di topic dell'outbox da questo processo",
    "verumdoc_partizioni_future": "Periodi futuri con la partizione già pronta",
    "verumdoc_partizioni_bloccate": "Partizioni scadute che non si possono staccare",
    "verumdoc_partizioni_staccate": "Partizioni staccate ed eliminate da questo processo",
    "verumdoc_contatori_delta_in_attesa": "Variazioni dei contatori per ente non consolidate",
    "verumdoc_parametri_scartati": "Righe di parametri ignorate (livello non ammesso o valore "
                                   "non valido; R07 W3)",
    "verumdoc_programmate_avvisi_non_inviati": "E-mail delle modifiche programmate non "
                                               "inviate (autore senza indirizzo o senza ente)",
}


def _chiave(nome: str, etichette: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return nome, tuple(sorted(etichette.items()))


def imposta(nome: str, valore: float, **etichette: str) -> None:
    """Valore istantaneo (gauge)."""
    with _blocco:
        _gauge[_chiave(nome, etichette)] = float(valore)


def incrementa(nome: str, quanto: float = 1.0, **etichette: str) -> None:
    """Contatore monotono del processo (esportato con il suffisso ``_total``)."""
    with _blocco:
        k = _chiave(nome, etichette)
        _contatori[k] = _contatori.get(k, 0.0) + float(quanto)


def valore(nome: str, **etichette: str) -> float | None:
    """Lettura per i test e per lo stato onesto (``/info``)."""
    with _blocco:
        k = _chiave(nome, etichette)
        return _gauge.get(k, _contatori.get(k))


def azzera() -> None:
    """Solo per i test."""
    with _blocco:
        _gauge.clear()
        _contatori.clear()


class CollettoreDB:
    """Collettore Prometheus che espone lo stato in memoria (vedi sopra)."""

    def collect(self) -> Iterator[Any]:
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        with _blocco:
            gauge = dict(_gauge)
            contatori = dict(_contatori)
        famiglie: dict[str, Any] = {}
        for (nome, etichette), v in sorted(gauge.items()):
            fam = famiglie.get(nome)
            if fam is None:
                fam = famiglie[nome] = GaugeMetricFamily(
                    nome, _DESCRIZIONI.get(nome, nome), labels=[k for k, _ in etichette])
            fam.add_metric([x for _, x in etichette], v)
        for (nome, etichette), v in sorted(contatori.items()):
            fam = famiglie.get(nome)
            if fam is None:
                fam = famiglie[nome] = CounterMetricFamily(
                    nome, _DESCRIZIONI.get(nome, nome), labels=[k for k, _ in etichette])
            fam.add_metric([x for _, x in etichette], v)
        yield from famiglie.values()


_registrati: set[int] = set()


def registra_metriche_db(registro: Any) -> None:
    """Registra (una volta per registro) il collettore sul registro del servizio."""
    if registro is None or id(registro) in _registrati:
        return
    registro.register(CollettoreDB())
    _registrati.add(id(registro))


def registro_da_app(app: Any) -> Any:
    """Registro Prometheus di ``installa_metriche`` (middleware delle metriche
    dell'app), o None se il servizio non le ha installate."""
    for m in getattr(app, "user_middleware", []) or []:
        metriche = (getattr(m, "kwargs", None) or {}).get("metriche")
        if metriche is not None and hasattr(metriche, "registro"):
            return metriche.registro
    return None
