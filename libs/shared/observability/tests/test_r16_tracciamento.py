# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R16 (VD-C-176): tracing distribuito OpenTelemetry.

Spento per impostazione; acceso: span del server con il MODELLO della rotta figlio del
``traceparent`` ricevuto, propagazione verso gli host interni (mai verso quelli esterni),
intestazioni Kafka e span del consumatore nella stessa traccia, campionamento configurabile,
nessun attributo fuori dall'elenco ammesso nei dati esportati (niente percorsi effettivi,
query, identificativi, segreti), collettore irraggiungibile senza errori.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared.observability import tracciamento as tr

PADRE = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.fixture
def spans():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    raccolti = InMemorySpanExporter()
    tr.azzera_per_test()
    assert tr.configura("servizio-prova", esportatore=raccolti, ambiente={
        "OTEL_TRACCE_ATTIVE": "true", "OTEL_CAMPIONAMENTO": "1.0"})
    yield raccolti
    tr.azzera_per_test()


def test_spento_per_impostazione() -> None:
    tr.azzera_per_test()
    assert not tr.configura("x", ambiente={})
    app = FastAPI()
    assert not tr.installa(app, "x") or False
    assert tr.intestazioni_kafka() == [] and tr.argomenti_kafka() == {}
    assert tr.inietta({}) == {}


def test_span_del_server_figlio_del_traceparent_con_il_modello_della_rotta(spans) -> None:
    app = FastAPI()

    @app.get("/elementi/{ident}")
    async def elemento(ident: str) -> dict:
        return {"id": ident}

    app.add_middleware(tr.MiddlewareTracciamento, servizio="servizio-prova")
    r = TestClient(app).get("/elementi/codice-fiscale-di-prova?segreto=abc",
                            headers={"traceparent": PADRE})
    assert r.status_code == 200
    from opentelemetry.trace import SpanKind

    (s,) = [x for x in spans.get_finished_spans() if x.kind == SpanKind.SERVER]
    assert s.name == "GET /elementi/{ident}"
    assert format(s.context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert format(s.parent.span_id, "016x") == "00f067aa0ba902b7"
    assert s.attributes["http.route"] == "/elementi/{ident}"
    assert s.attributes["http.response.status_code"] == 200
    testo = json.dumps(tr.corpo_otlp([s], "servizio-prova"))
    assert "codice-fiscale-di-prova" not in testo and "segreto" not in testo


async def test_propagazione_solo_verso_gli_host_interni(spans) -> None:
    viste: dict[str, dict[str, str]] = {}

    def gestore(richiesta: httpx.Request) -> httpx.Response:
        viste[richiesta.url.host] = dict(richiesta.headers)
        return httpx.Response(200)

    trasporto = httpx.MockTransport(gestore)
    with tr.tracer().start_as_current_span("radice") as radice:
        async with httpx.AsyncClient(transport=trasporto) as c:
            await c.get("http://signature/api/v1/signature/status")
            await c.get("https://csc.example/csc/v2/info")
        with httpx.Client(transport=trasporto) as c:
            c.get("http://hashchain/api/v1/health/live")
    traccia = format(radice.get_span_context().trace_id, "032x")
    assert traccia in viste["signature"]["traceparent"]
    assert traccia in viste["hashchain"]["traceparent"]
    assert "traceparent" not in viste["csc.example"]   # prestatore esterno: niente
    client = [s for s in spans.get_finished_spans() if s.name.startswith("HTTP ")]
    assert {s.attributes["server.address"] for s in client} == {"signature", "csc.example",
                                                               "hashchain"}
    assert all(s.attributes["http.response.status_code"] == 200 for s in client)


def test_kafka_intestazioni_e_span_del_consumatore_nella_stessa_traccia(spans) -> None:
    with tr.span_produzione("pdv.ricevuto") as produttore:
        intestazioni = tr.intestazioni_kafka()
        assert tr.argomenti_kafka() == {"headers": intestazioni}
    assert [k for k, _ in intestazioni] == ["traceparent"]
    with tr.span_consumo("pdv.ricevuto", intestazioni) as consumatore:
        assert consumatore is not None
    assert consumatore.get_span_context().trace_id == produttore.get_span_context().trace_id
    nomi = [s.name for s in spans.get_finished_spans()]
    assert nomi == ["pubblica pdv.ricevuto", "consuma pdv.ricevuto"]


def test_solo_attributi_ammessi_nei_dati_esportati(spans) -> None:
    with tr.tracer().start_as_current_span("operazione") as s:
        s.set_attribute("enduser.id", "persona-di-prova")
        s.set_attribute("url.full", "https://op.example/cb?code=segreto")
        s.set_attribute("http.request.header.authorization", "Bearer x")
        s.set_attribute("http.request.method", "POST")
    corpo = tr.corpo_otlp(spans.get_finished_spans(), "servizio-prova")
    testo = json.dumps(corpo)
    assert "persona-di-prova" not in testo and "segreto" not in testo and "Bearer" not in testo
    span = corpo["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert [a["key"] for a in span["attributes"]] == ["http.request.method"]
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    assert corpo["resourceSpans"][0]["resource"]["attributes"][0]["value"] == {
        "stringValue": "servizio-prova"}


def test_campionamento_configurabile() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    raccolti = InMemorySpanExporter()
    tr.azzera_per_test()
    try:
        assert tr.configura("x", esportatore=raccolti, ambiente={
            "OTEL_TRACCE_ATTIVE": "1", "OTEL_CAMPIONAMENTO": "0"})
        assert "0" in tr._fornitore.sampler.get_description()
        for _ in range(20):
            with tr.tracer().start_as_current_span("non campionato"):
                pass
        assert raccolti.get_finished_spans() == ()
        # revisione R17: un genitore REMOTO «campionato» non forza il campionamento
        # (prima lo forzava: un client esterno poteva far tracciare ogni richiesta)
        with tr.span_consumo("t", [("traceparent", PADRE.encode())]):
            pass
        assert raccolti.get_finished_spans() == ()
        # con il rapporto pieno il genitore remoto si segue (stessa traccia)
        tr.azzera_per_test()
        assert tr.configura("x", esportatore=raccolti, ambiente={
            "OTEL_TRACCE_ATTIVE": "1", "OTEL_CAMPIONAMENTO": "1"})
        with tr.span_consumo("t", [("traceparent", PADRE.encode()),
                                   ("tracestate", "x=" + "A" * 200)]):
            pass
        (span,) = raccolti.get_finished_spans()
        assert format(span.context.trace_id, "032x") == PADRE.split("-")[1]
        assert not list(span.context.trace_state.items())   # tracestate esterno mai accolto
    finally:
        tr.azzera_per_test()


def test_collettore_irraggiungibile_non_blocca(spans) -> None:
    from opentelemetry.sdk.trace.export import SpanExportResult

    with tr.tracer().start_as_current_span("x"):
        pass
    esportatore = tr._esportatore("http://127.0.0.1:9", "servizio-prova", 0.5)
    assert esportatore.export(spans.get_finished_spans()) == SpanExportResult.FAILURE
