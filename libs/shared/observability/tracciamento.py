# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/observability/tracciamento.py — tracing distribuito OpenTelemetry (R16, VD-C-176).

Si attiva con ``OTEL_TRACCE_ATTIVE=true`` (predefinito: spento) e si installa da solo in ogni
servizio con ``installa_metriche`` (nessuna riga in più nei ``main.py``). Esporta le tracce in
OTLP/HTTP JSON (``POST <OTEL_EXPORTER_OTLP_ENDPOINT>/v1/traces``, Tempo dello stack locale:
``http://tempo:4318``) con un esportatore proprio sopra il SDK OpenTelemetry (niente protobuf
né gRPC fra le dipendenze). Campionamento ``OTEL_CAMPIONAMENTO`` (0–1, predefinito 0.1)
rispettando la decisione del chiamante (``ParentBased``).

Propagazione del contesto (W3C Trace Context, ``traceparent``):
  - HTTP in ingresso: middleware ASGI che estrae il contesto e apre lo span del server con
    il MODELLO della rotta (mai il percorso effettivo né la query);
  - HTTP in uscita: ``httpx`` (client asincroni e sincroni) apre uno span del client e
    inietta ``traceparent`` SOLO verso gli host interni (``OTEL_HOST_INTERNI``: i servizi
    del cluster e localhost); verso prestatori esterni (CSC, TSA, aggregatore) niente
    intestazioni;
  - Kafka: ``traceparent`` fra le intestazioni del messaggio prodotto
    (``libs.shared.kafka.kafka_produce``) ed estratto dai consumatori
    (``contesto_da_intestazioni``, ``span_consumo``).

Nessun dato personale o segreto: l'esportatore pubblica SOLO gli attributi dell'elenco
``ATTRIBUTI_AMMESSI`` (metodo, modello della rotta, stato, host di destinazione, topic,
nome del servizio, tipo d'errore); ogni altro attributo, comunque impostato, si scarta
prima dell'invio (``attributi_sicuri``), e i nomi degli span non contengono identificativi.
Un collettore irraggiungibile non blocca nulla (si perdono tracce, con un avviso nel log).
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger(__name__)

#: attributi che possono lasciare il processo (tutti gli altri si scartano)
ATTRIBUTI_AMMESSI = frozenset({
    "http.request.method", "http.route", "http.response.status_code", "server.address",
    "url.scheme", "messaging.system", "messaging.destination.name", "messaging.operation.type",
    "error.type", "verumdoc.servizio", "verumdoc.esito",
})
#: host verso cui si propaga ``traceparent`` (servizi del cluster, sviluppo locale)
HOST_INTERNI_PREDEFINITI = (
    "gateway", "validation", "aip-builder", "hashchain", "notification", "distribution",
    "dashboard-api", "signature", "onboarding", "ciclo-vita", "verifica-firme", "sftp-ingest",
    "testimone", "localhost", "127.0.0.1")

_attivo = False
_fornitore: Any = None
_host_interni: frozenset[str] = frozenset()
_httpx_strumentato = False
_lock = threading.Lock()


def _vero(valore: str | None) -> bool:
    return (valore or "").strip().lower() in ("1", "true", "si", "sì", "yes", "on")


def attivo() -> bool:
    return _attivo


def attributi_sicuri(attributi: Mapping[str, Any] | None) -> dict[str, Any]:
    """Solo gli attributi ammessi, con valori semplici (mai oggetti)."""
    return {k: v for k, v in (attributi or {}).items()
            if k in ATTRIBUTI_AMMESSI and isinstance(v, str | int | float | bool)}


def _valore_otlp(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)[:256]}


def corpo_otlp(spans: Sequence[Any], servizio: str) -> dict[str, Any]:
    """Richiesta OTLP/HTTP JSON (``ExportTraceServiceRequest``) degli span, con i soli
    attributi ammessi."""
    elementi = []
    for s in spans:
        ctx = s.get_span_context()
        genitore = s.parent
        elementi.append({
            "traceId": format(ctx.trace_id, "032x"), "spanId": format(ctx.span_id, "016x"),
            **({"parentSpanId": format(genitore.span_id, "016x")} if genitore else {}),
            "name": str(s.name)[:200], "kind": int(getattr(s.kind, "value", 0)) + 1,
            "startTimeUnixNano": str(s.start_time or 0),
            "endTimeUnixNano": str(s.end_time or 0),
            "attributes": [{"key": k, "value": _valore_otlp(v)}
                           for k, v in attributi_sicuri(dict(s.attributes or {})).items()],
            "status": {"code": int(getattr(s.status.status_code, "value", 0))},
        })
    return {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name",
                                     "value": {"stringValue": servizio}}]},
        "scopeSpans": [{"scope": {"name": "verumdoc"}, "spans": elementi}]}]}


def _esportatore(endpoint: str, servizio: str, timeout: float) -> Any:
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class EsportatoreOtlpJson(SpanExporter):  # type: ignore[misc]
        """OTLP/HTTP con codifica JSON (Tempo, collettore OpenTelemetry)."""

        def __init__(self) -> None:
            self.url = endpoint.rstrip("/") + "/v1/traces"

        def export(self, spans: Sequence[Any]) -> Any:
            import httpx

            try:
                r = httpx.post(self.url, content=json.dumps(corpo_otlp(spans, servizio)),
                               headers={"Content-Type": "application/json"}, timeout=timeout)
                if r.status_code >= 300:
                    logger.warning("tracce_non_esportate", http=r.status_code)
                    return SpanExportResult.FAILURE
            except Exception as exc:  # noqa: BLE001 — il collettore non blocca mai il servizio
                logger.warning("tracce_non_esportate", errore=type(exc).__name__)
                return SpanExportResult.FAILURE
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    return EsportatoreOtlpJson()


def configura(servizio: str, *, ambiente: Mapping[str, str] | None = None,
              esportatore: Any = None) -> bool:
    """Prepara il fornitore delle tracce del processo (idempotente). False se spento."""
    global _attivo, _host_interni, _fornitore
    env = ambiente if ambiente is not None else os.environ
    if not _vero(env.get("OTEL_TRACCE_ATTIVE")):
        return False
    with _lock:
        if _attivo:
            return True
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except ImportError:
            logger.warning("tracce_non_disponibili", motivo="opentelemetry-sdk non installato")
            return False
        try:
            rapporto = min(1.0, max(0.0, float(env.get("OTEL_CAMPIONAMENTO", "0.1"))))
        except ValueError:
            rapporto = 0.1
        # revisione R17: un traceparent arrivato da fuori (anche con il flag «campionato»)
        # non forza il campionamento: per un genitore REMOTO decide il rapporto sull'id
        # della traccia (deterministico, quindi uguale in tutti i servizi interni); un
        # genitore locale (stesso processo) resta rispettato
        campione = TraceIdRatioBased(rapporto)
        fornitore = TracerProvider(resource=Resource.create({"service.name": servizio}),
                                   sampler=ParentBased(
                                       campione, remote_parent_sampled=campione,
                                       remote_parent_not_sampled=campione))
        if esportatore is not None:
            fornitore.add_span_processor(SimpleSpanProcessor(esportatore))
        else:
            endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318")
            fornitore.add_span_processor(BatchSpanProcessor(_esportatore(
                endpoint, servizio, float(env.get("OTEL_TIMEOUT_SECONDI", "5")))))
        # fornitore PROPRIO, non globale: le strumentazioni automatiche di terzi (FastAPI
        # recenti strumentano da sole con il fornitore globale, con il percorso effettivo
        # fra gli attributi) restano spente; propagazione W3C con il propagatore globale
        _fornitore = fornitore
        _host_interni = frozenset(h.strip().lower() for h in (
            env.get("OTEL_HOST_INTERNI") or ",".join(HOST_INTERNI_PREDEFINITI)).split(",")
            if h.strip())
        _attivo = True
    strumenta_httpx()
    logger.info("tracce_attive", servizio=servizio, campionamento=rapporto)
    return True


def tracer() -> Any:
    if _fornitore is not None:
        return _fornitore.get_tracer("verumdoc")
    from opentelemetry import trace

    return trace.get_tracer("verumdoc")  # fornitore globale (nessuna traccia se spento)


# ── propagazione W3C ────────────────────────────────────────────────────

def inietta(intestazioni: dict[str, str]) -> dict[str, str]:
    """Aggiunge ``traceparent`` (contesto corrente) a ``intestazioni`` se le tracce sono
    attive."""
    if _attivo:
        from opentelemetry.propagate import inject

        inject(intestazioni)
    return intestazioni


def contesto_da(intestazioni: Mapping[str, str]) -> Any:
    from opentelemetry.propagate import extract

    return extract({k.lower(): v for k, v in intestazioni.items()})


def intestazioni_kafka() -> list[tuple[str, bytes]]:
    """Intestazioni di un messaggio Kafka con ``traceparent`` (vuoto se spento)."""
    return [(k, v.encode()) for k, v in inietta({}).items()]


def argomenti_kafka() -> dict[str, Any]:
    """``headers=`` per ``send_and_wait`` dei produttori propri dei servizi (vuoto se spento:
    nessun argomento in più per i produttori di prova)."""
    intestazioni = intestazioni_kafka()
    return {"headers": intestazioni} if intestazioni else {}


def contesto_da_intestazioni(intestazioni: Iterable[tuple[str, bytes]] | None) -> Any:
    """Contesto di un messaggio Kafka letto (intestazioni come le dà aiokafka)."""
    return contesto_da({k: (v.decode("latin-1") if isinstance(v, bytes) else str(v))
                        for k, v in (intestazioni or ()) if k.lower() == "traceparent"})


@contextmanager
def span_consumo(topic: str, intestazioni: Iterable[tuple[str, bytes]] | None = None) -> Any:
    """Span del consumatore di un messaggio Kafka, figlio dello span del produttore."""
    if not _attivo:
        yield None
        return
    from opentelemetry.trace import SpanKind

    with tracer().start_as_current_span(
            f"consuma {topic}", context=contesto_da_intestazioni(intestazioni),
            kind=SpanKind.CONSUMER, attributes={"messaging.system": "kafka",
                                                "messaging.destination.name": topic,
                                                "messaging.operation.type": "process"}) as s:
        yield s


@contextmanager
def span_produzione(topic: str) -> Any:
    if not _attivo:
        yield None
        return
    from opentelemetry.trace import SpanKind

    with tracer().start_as_current_span(
            f"pubblica {topic}", kind=SpanKind.PRODUCER,
            attributes={"messaging.system": "kafka", "messaging.destination.name": topic,
                        "messaging.operation.type": "publish"}) as s:
        yield s


# ── HTTP in uscita (httpx) ──────────────────────────────────────────────

def _host_interno(url: Any) -> bool:
    host = (urlsplit(str(url)).hostname or "").lower()
    return host in _host_interni


def strumenta_httpx() -> None:
    """Span del client e ``traceparent`` verso gli host interni, per ogni client httpx."""
    global _httpx_strumentato
    import httpx

    with _lock:
        if _httpx_strumentato:
            return
        _httpx_strumentato = True
    invia_asincrono = httpx.AsyncClient.send
    invia_sincrono = httpx.Client.send

    def _apri(richiesta: Any) -> Any:
        from opentelemetry.trace import SpanKind

        host = richiesta.url.host or ""
        span = tracer().start_span(f"HTTP {richiesta.method}", kind=SpanKind.CLIENT,
                                   attributes={"http.request.method": richiesta.method,
                                               "server.address": host,
                                               "url.scheme": richiesta.url.scheme})
        if _host_interno(richiesta.url) and "traceparent" not in richiesta.headers:
            from opentelemetry import trace
            from opentelemetry.propagate import inject

            portatore: dict[str, str] = {}
            inject(portatore, context=trace.set_span_in_context(span))
            richiesta.headers.update(portatore)
        return span

    def _chiudi(span: Any, risposta: Any = None, errore: BaseException | None = None) -> None:
        if risposta is not None:
            span.set_attribute("http.response.status_code", int(risposta.status_code))
        if errore is not None:
            span.set_attribute("error.type", type(errore).__name__)
        span.end()

    async def send_asincrono(self: Any, request: Any, *a: Any, **kw: Any) -> Any:
        if not _attivo:
            return await invia_asincrono(self, request, *a, **kw)
        span = _apri(request)
        try:
            risposta = await invia_asincrono(self, request, *a, **kw)
        except BaseException as exc:
            _chiudi(span, errore=exc)
            raise
        _chiudi(span, risposta)
        return risposta

    def send_sincrono(self: Any, request: Any, *a: Any, **kw: Any) -> Any:
        if not _attivo or str(request.url).endswith("/v1/traces"):
            return invia_sincrono(self, request, *a, **kw)
        span = _apri(request)
        try:
            risposta = invia_sincrono(self, request, *a, **kw)
        except BaseException as exc:
            _chiudi(span, errore=exc)
            raise
        _chiudi(span, risposta)
        return risposta

    httpx.AsyncClient.send = send_asincrono  # type: ignore[method-assign]
    httpx.Client.send = send_sincrono  # type: ignore[method-assign]


# ── HTTP in ingresso (ASGI) ─────────────────────────────────────────────

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class MiddlewareTracciamento:
    """Span del server per ogni richiesta HTTP, figlio del ``traceparent`` ricevuto. Nome e
    attributi con il MODELLO della rotta, mai il percorso effettivo né la query."""

    def __init__(self, app: Any, servizio: str) -> None:
        self.app = app
        self.servizio = servizio

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not _attivo or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return
        from opentelemetry import context as contesto
        from opentelemetry.trace import SpanKind, Status, StatusCode

        # revisione R17: solo traceparent; tracestate (testo libero del chiamante) mai
        # accolto né propagato
        intestazioni = {k.decode("latin-1"): v.decode("latin-1")
                        for k, v in scope.get("headers") or []
                        if k.lower() == b"traceparent"}
        token = contesto.attach(contesto_da(intestazioni))
        stato = {"codice": 500}

        async def invia(messaggio: dict[str, Any]) -> None:
            if messaggio.get("type") == "http.response.start":
                stato["codice"] = int(messaggio.get("status", 500))
            await send(messaggio)

        metodo = str(scope.get("method", "GET"))
        try:
            with tracer().start_as_current_span(
                    f"{metodo} richiesta", kind=SpanKind.SERVER,
                    attributes={"http.request.method": metodo,
                                "verumdoc.servizio": self.servizio}) as span:
                try:
                    await self.app(scope, receive, invia)
                finally:
                    rotta = getattr(scope.get("route"), "path", None) or "non_instradata"
                    span.update_name(f"{metodo} {rotta}")
                    span.set_attribute("http.route", rotta)
                    span.set_attribute("http.response.status_code", stato["codice"])
                    if stato["codice"] >= 500:
                        span.set_status(Status(StatusCode.ERROR))
        finally:
            contesto.detach(token)


def installa(app: Any, servizio: str) -> bool:
    """Middleware delle tracce se ``OTEL_TRACCE_ATTIVE`` (chiamato da ``installa_metriche``)."""
    if not configura(servizio):
        return False
    app.add_middleware(MiddlewareTracciamento, servizio=servizio)
    return True


def azzera_per_test() -> None:
    """Spegne le tracce del processo (test)."""
    global _attivo
    _attivo = False


__all__ = ["ATTRIBUTI_AMMESSI", "MiddlewareTracciamento", "attivo", "attributi_sicuri",
           "azzera_per_test", "configura", "contesto_da_intestazioni", "corpo_otlp",
           "inietta", "installa", "intestazioni_kafka", "span_consumo", "span_produzione"]
