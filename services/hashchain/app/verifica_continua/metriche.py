# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Metriche Prometheus del lavoratore di verifica continua (registro dedicato,
esposto da ``python -m app.verifica_continua`` su ``VERIFICA_METRICHE_PORTA``).

  verumdoc_verifica_oggetti_totali{componente, esito}   oggetti riletti per esito
  verumdoc_verifica_pda_totali{esito}                   PdA con verifica conclusa
  verumdoc_verifica_byte_letti_totali                   byte riletti da storage
  verumdoc_verifica_anomalie_totali{esito}              oggetti in anomalia (allarme)
  verumdoc_verifica_errori_lettura_ripetuti_totali      PdA con ERRORE_LETTURA oltre la soglia
  verumdoc_verifica_lease_persi_totali                  esiti scartati per lease scaduto
  verumdoc_verifica_ritardo_secondi                     attesa del PdA più in ritardo
  verumdoc_verifica_anomalie_aperte                     PdA con un'anomalia aperta
  verumdoc_verifica_ultimo_giro_timestamp               ultimo giro (allarme se fermo, §22.3)
  verumdoc_verifica_attesa_budget_secondi_totali{budget} attese imposte dai budget

Allarmi suggeriti (r04-infra): anomalie aperte > 0; aumento di
``anomalie_totali``; ritardo > 1 giorno (capacità insufficiente per il ciclo);
ultimo giro più vecchio di 10 minuti.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge


class MetricheVerifica:
    def __init__(self, registro: CollectorRegistry | None = None) -> None:
        self.registro = registro or CollectorRegistry()
        r = self.registro
        self.oggetti = Counter("verumdoc_verifica_oggetti", "Oggetti riletti da storage per esito",
                               ["componente", "esito"], registry=r)
        self.pda = Counter("verumdoc_verifica_pda", "PdA con verifica conclusa per esito",
                           ["esito"], registry=r)
        self.byte_letti = Counter("verumdoc_verifica_byte_letti", "Byte riletti da storage",
                                  registry=r)
        self.anomalie = Counter("verumdoc_verifica_anomalie",
                                "Oggetti in anomalia d'integrità (allarme)", ["esito"], registry=r)
        self.errori_ripetuti = Counter("verumdoc_verifica_errori_lettura_ripetuti",
                                       "PdA con errori di lettura oltre la soglia", registry=r)
        self.lease_persi = Counter("verumdoc_verifica_lease_persi",
                                   "Esiti scartati perché il lease era scaduto", registry=r)
        self.ritardo = Gauge("verumdoc_verifica_ritardo_secondi",
                             "Attesa in secondi del PdA più in ritardo sulla rotazione", registry=r)
        self.anomalie_aperte = Gauge("verumdoc_verifica_anomalie_aperte",
                                     "PdA con un'anomalia d'integrità aperta", registry=r)
        self.ultimo_giro = Gauge("verumdoc_verifica_ultimo_giro_timestamp",
                                 "Istante (epoch) dell'ultimo giro del lavoratore", registry=r)
        self.attesa_budget = Counter("verumdoc_verifica_attesa_budget_secondi",
                                     "Secondi di attesa imposti dai budget di lettura",
                                     ["budget"], registry=r)
