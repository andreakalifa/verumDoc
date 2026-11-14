# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test reali del modulo metriche (VD-C-033): app FastAPI vera, /metrics vero."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from libs.shared.observability.metriche import NON_INSTRADATA, installa_metriche


def _app() -> tuple[FastAPI, object]:
    app = FastAPI()

    @app.get("/api/v1/aip/{aip_id}")
    async def leggi(aip_id: str) -> dict:
        return {"id": aip_id}

    @app.get("/api/v1/errore")
    async def errore() -> dict:
        raise HTTPException(status_code=409, detail="conflitto")

    metriche = installa_metriche(app, "prova")
    return app, metriche


def _campioni(testo: str, nome: str) -> list:
    return [
        s for fam in text_string_to_metric_families(testo) for s in fam.samples if s.name == nome
    ]


def test_rotta_registrata_col_modello_non_col_percorso() -> None:
    app, _ = _app()
    with TestClient(app) as c:
        c.get("/api/v1/aip/123")
        c.get("/api/v1/aip/456")
        testo = c.get("/metrics").text
    righe = _campioni(testo, "verumdoc_http_richieste_totali_total") or _campioni(
        testo, "verumdoc_http_richieste_totali"
    )
    per_rotta = {(s.labels["rotta"], s.labels["stato"]): s.value for s in righe}
    assert per_rotta[("/api/v1/aip/{aip_id}", "200")] == 2
    assert not any("123" in r for r, _ in per_rotta)


def test_stato_di_errore_e_rotte_sconosciute() -> None:
    app, _ = _app()
    with TestClient(app) as c:
        c.get("/api/v1/errore")
        c.get("/non/esiste")
        testo = c.get("/metrics").text
    righe = _campioni(testo, "verumdoc_http_richieste_totali_total") or _campioni(
        testo, "verumdoc_http_richieste_totali"
    )
    chiavi = {(s.labels["rotta"], s.labels["stato"]) for s in righe}
    assert ("/api/v1/errore", "409") in chiavi
    assert (NON_INSTRADATA, "404") in chiavi


def test_metrics_esclusa_e_formato_prometheus() -> None:
    app, _ = _app()
    with TestClient(app) as c:
        c.get("/metrics")
        r2 = c.get("/metrics")
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("text/plain")
    righe = _campioni(r2.text, "verumdoc_http_richieste_totali_total")
    assert not [s for s in righe if s.labels["rotta"] == "/metrics"]


def test_durata_e_in_corso() -> None:
    app, metriche = _app()
    with TestClient(app) as c:
        c.get("/api/v1/aip/1")
        testo = c.get("/metrics").text
    conteggi = {
        s.labels["rotta"]: s.value for s in _campioni(testo, "verumdoc_http_durata_secondi_count")
    }
    assert conteggi["/api/v1/aip/{aip_id}"] == 1
    in_corso = _campioni(testo, "verumdoc_http_richieste_in_corso")
    assert in_corso and in_corso[0].value == 0


def test_due_servizi_non_si_pestano() -> None:
    """Registro dedicato: due installazioni nello stesso processo non vanno in conflitto."""
    _app()
    _app()


@pytest.mark.parametrize("percorso", ["/metriche-interne"])
def test_percorso_personalizzato(percorso: str) -> None:
    app = FastAPI()
    installa_metriche(app, "prova", percorso=percorso)
    with TestClient(app) as c:
        assert c.get(percorso).status_code == 200
        assert c.get("/metrics").status_code == 404
