# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""VD-C-100: l'app del servizio risponde agli errori nel formato unico
application/problem+json del catalogo (libs/shared/errori)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

CASI = [
    ("GET", "/percorso-inesistente", 404, "HTTP-404"),
    # R03 f2: il limite di richieste del router autentica prima di validare i parametri
    ("GET", "/api/dashboard/overview", 401, "AUTH-000"),
    ("GET", "/api/dashboard/hashchain", 401, "AUTH-000"),
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    # senza «with»: nessun avvio di database/Kafka, si prova solo il formato
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(("metodo", "percorso", "stato", "codice"), CASI)
def test_errore_nel_formato_problem_json(client, metodo, percorso, stato, codice) -> None:
    r = client.request(metodo, percorso, headers={"X-Request-ID": "prova-a1"})
    assert r.status_code == stato, r.text
    assert r.headers["content-type"] == "application/problem+json"
    corpo = r.json()
    assert corpo["codice"] == codice
    assert corpo["type"] == f"https://docs.verumdoc.it/errori/{codice}"
    assert corpo["status"] == stato and corpo["esito"] in ("NON_VALIDO", "RIFIUTATO", "ERRORE")
    assert corpo["id_richiesta"] == "prova-a1" == r.headers["x-request-id"]
    assert corpo["instance"] == percorso and "detail" not in corpo.get("dettagli", {})


def test_id_richiesta_generato_se_assente(client) -> None:
    r = client.get("/percorso-inesistente")
    assert len(r.json()["id_richiesta"]) == 32 and r.headers[
        "x-request-id"] == r.json()["id_richiesta"]
