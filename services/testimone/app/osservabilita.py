# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/osservabilita.py — metriche proprie del testimone.

Riferimenti: contratto K3 di R04 (allarme di fork: metrica e log), VD-C-033
(metriche Prometheus su GET /metrics). Le metriche si registrano sullo stesso
registro dedicato di ``libs.shared.observability.metriche`` (una sola rotta
/metrics per servizio).

Etichetta ``origine``: valori presi SOLO dall'elenco configurato delle origini
ammesse (cardinalità limitata), mai dal testo ricevuto.

Regole d'allarme suggerite (Prometheus):
  - ``verumdoc_testimone_compromesso == 1``                → pagina subito;
  - ``increase(verumdoc_testimone_allarmi_totali[10m]) > 0`` → avviso;
  - ``time() - verumdoc_testimone_ultima_controfirma_secondi > 600`` → il
    registro non manda più checkpoint (stessa soglia di
    CATENA_CONTROFIRMA_MAX_SECONDI lato registro).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge


class MetricheTestimone:
    """Contatori e indicatori del testimone."""

    def __init__(self, registro: CollectorRegistry | None = None) -> None:
        self.registro = registro or CollectorRegistry()
        self.controfirme = Counter(
            "verumdoc_testimone_controfirme_totali",
            "Checkpoint controfirmati",
            ["origine"],
            registry=self.registro,
        )
        self.rifiuti = Counter(
            "verumdoc_testimone_rifiuti_totali",
            "Richieste di controfirma rifiutate, per motivo",
            ["motivo"],
            registry=self.registro,
        )
        self.allarmi = Counter(
            "verumdoc_testimone_allarmi_totali",
            "Incoerenze del registro rilevate",
            ["origine", "tipo"],
            registry=self.registro,
        )
        self.compromesso = Gauge(
            "verumdoc_testimone_compromesso",
            "1 se il testimone è bloccato per un fork del registro",
            ["origine"],
            registry=self.registro,
        )
        self.dimensione = Gauge(
            "verumdoc_testimone_dimensione_controfirmata",
            "Dimensione dell'ultimo checkpoint controfirmato",
            ["origine"],
            registry=self.registro,
        )
        self.ultima_controfirma = Gauge(
            "verumdoc_testimone_ultima_controfirma_secondi",
            "Istante (epoch) dell'ultima controfirma",
            ["origine"],
            registry=self.registro,
        )

    def aggiorna_stato(
        self,
        origine: str,
        *,
        compromesso: bool,
        dimensione: int | None,
        ultima_controfirma: float | None,
    ) -> None:
        self.compromesso.labels(origine).set(1 if compromesso else 0)
        if dimensione is not None:
            self.dimensione.labels(origine).set(dimensione)
        if ultima_controfirma is not None:
            self.ultima_controfirma.labels(origine).set(ultima_controfirma)


__all__ = ["MetricheTestimone"]
