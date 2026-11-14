# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Metriche Prometheus del consumer di verifica (esposte su GET /metrics insieme a
quelle HTTP del servizio, vedi ``app.main``).

Servizio: validation. Etichette a cardinalità limitata: nessun identificativo
di PdV, cliente o file. Servono alla scalatura sul lag (KEDA, piano R03 §4.1) e
a vedere se la concorrenza configurata è satura.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

BUCKET_DURATA = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900)


class MetricheValidazione:
    def __init__(self, registro: CollectorRegistry | None = None) -> None:
        self.registro = registro or CollectorRegistry()
        r = self.registro
        self.messaggi = Counter(
            "verumdoc_validation_messaggi_totali",
            "Messaggi pdv.received chiusi per esito (elaborato, dlq)", ["esito"], registry=r)
        self.pdv = Counter(
            "verumdoc_validation_pdv_totali",
            "PdV verificati per esito (VALIDATO, RIFIUTATO, RIPUBBLICATO, SALTATO)", ["esito"],
            registry=r)
        self.durata = Histogram(
            "verumdoc_validation_durata_elaborazione_secondi",
            "Durata dell'elaborazione di un messaggio (ritentativi compresi)",
            buckets=BUCKET_DURATA, registry=r)
        self.in_corso = Gauge(
            "verumdoc_validation_elaborazioni_in_corso",
            "Messaggi in elaborazione in questa replica", registry=r)
        self.concorrenza = Gauge(
            "verumdoc_validation_concorrenza_massima",
            "Limite di elaborazioni parallele configurato", registry=r)
        self.in_attesa_conferma = Gauge(
            "verumdoc_validation_messaggi_non_confermati",
            "Messaggi letti il cui offset non è ancora confermato", registry=r)
        self.offset_confermati = Counter(
            "verumdoc_validation_conferme_offset_totali",
            "Conferme d'offset inviate a Kafka per esito (ok, errore)", ["esito"], registry=r)
        self.dlq_non_registrata = Counter(
            "verumdoc_validation_dlq_non_registrata_totali",
            "Volte in cui DLQ o stato d'errore non si sono potuti registrare", registry=r)
        self.byte_letti = Counter(
            "verumdoc_validation_byte_letti_totali",
            "Byte letti dallo storage per ricalcolare le impronte", registry=r)


#: Istanza del processo (registro proprio, agganciato a /metrics da app.main)
METRICHE = MetricheValidazione()
