# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/observability/metriche.py — metriche Prometheus HTTP (VD-C-033).

Uso (una riga in services/<servizio>/app/main.py, dopo la creazione di ``app``):

    from libs.shared.observability.metriche import installa_metriche
    installa_metriche(app, "<servizio>")

Espone GET /metrics (formato testo Prometheus) e registra per ogni richiesta:
  verumdoc_http_richieste_totali{servizio, metodo, rotta, stato}
  verumdoc_http_durata_secondi{servizio, metodo, rotta}
  verumdoc_http_richieste_in_corso{servizio}
più le metriche del processo (CPU, memoria, file aperti).

«rotta» è il MODELLO della rotta FastAPI (es. /api/v2/distribution/aip/{aip_id}),
mai il percorso effettivo: cardinalità limitata, nessun identificativo nei dati
delle metriche. Le richieste che non corrispondono ad alcuna rotta finiscono in
«non_instradata». Il middleware è ASGI puro (nessun BaseHTTPMiddleware), quindi
non bufferizza le risposte in streaming (download dei pacchetti).

Registro dedicato (non quello globale di prometheus_client): nessun conflitto con
le metriche definite in libs/shared/observability/__init__.py.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

NON_INSTRADATA = "non_instradata"
BUCKET_DURATA = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


class Metriche:
    """Insieme delle metriche HTTP di un servizio, su un registro dedicato."""

    def __init__(self, servizio: str, registro: CollectorRegistry | None = None) -> None:
        self.servizio = servizio
        self.registro = registro or CollectorRegistry()
        self.richieste = Counter(
            "verumdoc_http_richieste_totali", "Richieste HTTP servite",
            ["servizio", "metodo", "rotta", "stato"], registry=self.registro,
        )
        self.durata = Histogram(
            "verumdoc_http_durata_secondi", "Durata delle richieste HTTP in secondi",
            ["servizio", "metodo", "rotta"], buckets=BUCKET_DURATA, registry=self.registro,
        )
        self.in_corso = Gauge(
            "verumdoc_http_richieste_in_corso", "Richieste HTTP in corso",
            ["servizio"], registry=self.registro,
        )
        ProcessCollector(registry=self.registro)
        PlatformCollector(registry=self.registro)

    def esporta(self) -> bytes:
        return generate_latest(self.registro)


class MetricheMiddleware:
    """Middleware ASGI che misura ogni richiesta HTTP."""

    def __init__(self, app: ASGIApp, metriche: Metriche, escludi: tuple[str, ...] = ()) -> None:
        self.app = app
        self.metriche = metriche
        self.escludi = escludi

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.escludi:
            await self.app(scope, receive, send)
            return
        stato = {"codice": 500}

        async def send_misurato(messaggio: dict[str, Any]) -> None:
            if messaggio["type"] == "http.response.start":
                stato["codice"] = int(messaggio["status"])
            await send(messaggio)

        m = self.metriche
        m.in_corso.labels(m.servizio).inc()
        inizio = time.perf_counter()
        try:
            await self.app(scope, receive, send_misurato)
        finally:
            durata = time.perf_counter() - inizio
            m.in_corso.labels(m.servizio).dec()
            rotta = getattr(scope.get("route"), "path", None) or NON_INSTRADATA
            metodo = scope.get("method", "")
            m.richieste.labels(m.servizio, metodo, rotta, str(stato["codice"])).inc()
            m.durata.labels(m.servizio, metodo, rotta).observe(durata)


def installa_metriche(app: Any, servizio: str, percorso: str = "/metrics") -> Metriche:
    """Aggiunge a un'app FastAPI il middleware delle metriche e la rotta ``percorso``."""
    from fastapi import Response

    metriche = Metriche(servizio)

    async def metriche_prometheus() -> Response:
        return Response(content=metriche.esporta(), media_type=CONTENT_TYPE_LATEST)

    app.add_api_route(percorso, metriche_prometheus, methods=["GET"], include_in_schema=False)
    app.add_middleware(MetricheMiddleware, metriche=metriche, escludi=(percorso,))
    # R16 (VD-C-176): tracce OpenTelemetry se OTEL_TRACCE_ATTIVE (predefinito spento)
    from libs.shared.observability.tracciamento import installa

    installa(app, servizio)
    return metriche
