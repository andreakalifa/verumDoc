# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/observability — telemetria condivisa tra i servizi.

Moduli usati dai servizi:
  - ``metriche``: ``installa_metriche(app, servizio)`` espone ``GET /metrics``
    (Prometheus) con un registro per processo (VD-C-033);
  - ``prontezza``, ``prontezza_db``, ``prontezza_kafka``, ``prontezza_s3``:
    ``/health/ready`` che controlla davvero le dipendenze.

R04 servizi (VD-C-086): tolti i contatori Prometheus globali, le funzioni di
tracing OpenTelemetry e il middleware delle metriche definiti qui e mai usati
da alcun servizio (il tracing non è attivo: lo dichiara STATO_PROGETTO).
"""
