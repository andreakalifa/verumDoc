# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Gestori FastAPI del formato unico: app di prova con ogni tipo d'errore."""
from __future__ import annotations

import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from libs.shared.errori import ErroreApi, installa_gestori_errori, risposta_problema
from libs.shared.errori.gestori import campi_da_validazione, errore_da_http


class Corpo(BaseModel):
    nome: str = Field(min_length=3)
    quanti: int


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/errore-api")
    async def errore_api() -> None:
        raise ErroreApi("VRS-102", "Atteso a, calcolato b", dettagli={"file": "x.pdf"})

    @app.get("/vecchio-dict")
    async def vecchio_dict() -> None:
        raise HTTPException(403, {"type": "AUTH-020", "title": "Accesso negato a questo ente",
                                  "detail": "Solo il proprio ente", "id_correlazione": "c0ffee",
                                  "non_validi": ["a"]})

    @app.get("/vecchio-firma")
    async def vecchio_firma() -> None:
        raise HTTPException(422, {"codice": "SIG-422-BASE64", "messaggio": "x non è base64"})

    @app.get("/vecchio-oauth")
    async def vecchio_oauth() -> None:
        raise HTTPException(401, {"error": "invalid_token", "detail": "Voucher PDND non valido"})

    @app.get("/sconosciuto")
    async def sconosciuto() -> None:
        raise HTTPException(409, {"type": "BOH-123", "title": "Qualcosa"})

    @app.get("/stringa")
    async def stringa() -> None:
        raise HTTPException(404, "Documento assente")

    @app.get("/rate")
    async def rate() -> None:
        raise HTTPException(429, {"type": "RATE-001", "title": "Rate limit superato",
                                  "status": 429, "retry_after": 60},
                            headers={"Retry-After": "60"})

    @app.get("/non-disponibile")
    async def non_disponibile() -> None:
        raise ErroreApi("HC-503")

    @app.post("/valida")
    async def valida(corpo: Corpo) -> dict:
        return corpo.model_dump()

    @app.get("/esplode")
    async def esplode() -> None:
        raise RuntimeError("password=segreta /etc/shadow")

    installa_gestori_errori(app)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def _problema(r) -> dict:
    assert r.headers["content-type"] == "application/problem+json"
    corpo = r.json()
    for chiave in ("type", "title", "status", "codice", "esito", "ritentabile", "dettagli",
                   "campi", "id_richiesta", "messaggio"):
        assert chiave in corpo, chiave
    assert corpo["type"] == f"https://docs.verumdoc.it/errori/{corpo['codice']}"
    assert corpo["status"] == r.status_code and corpo["messaggio"] == corpo["title"]
    assert r.headers["x-request-id"] == corpo["id_richiesta"]
    return corpo


def test_errore_api(client):
    r = client.get("/errore-api")
    c = _problema(r)
    assert r.status_code == 400 and c["codice"] == "VRS-102" and c["esito"] == "NON_VALIDO"
    assert c["detail"] == "Atteso a, calcolato b" and c["dettagli"] == {"file": "x.pdf"}
    assert c["instance"] == "/errore-api"


def test_http_exception_vecchio_formato_portata_al_primo_livello(client):
    r = client.get("/vecchio-dict")
    c = _problema(r)
    assert r.status_code == 403 and c["codice"] == "AUTH-020" and c["esito"] == "RIFIUTATO"
    assert c["title"] == "Accesso negato a questo ente" and c["detail"] == "Solo il proprio ente"
    assert c["id_richiesta"] == "c0ffee"            # id di correlazione già assegnato
    assert c["dettagli"] == {"non_validi": ["a"]}
    assert "detail" not in c["dettagli"]


def test_formato_signature_codice_messaggio(client):
    c = _problema(client.get("/vecchio-firma"))
    assert c["codice"] == "SIG-422-BASE64" and c["title"] == "x non è base64"


def test_formato_oauth_pdnd_ricondotto_al_catalogo(client):
    r = client.get("/vecchio-oauth")
    c = _problema(r)
    assert c["codice"] == "PDND-401" and c["dettagli"]["codice_originale"] == "invalid_token"
    assert c["detail"] == "Voucher PDND non valido"


def test_codice_fuori_catalogo_risposta_comunque_corretta_e_log(client):
    with structlog.testing.capture_logs() as log:
        r = client.get("/sconosciuto")
    c = _problema(r)
    assert r.status_code == 409 and c["codice"] == "HTTP-409"
    assert c["dettagli"]["codice_originale"] == "BOH-123"
    assert any(e["event"] == "codice_errore_fuori_catalogo" for e in log)


def test_dettaglio_stringa_e_rotta_inesistente(client):
    c = _problema(client.get("/stringa"))
    assert c["codice"] == "HTTP-404" and c["detail"] == "Documento assente"
    r = client.get("/non-esiste")
    c = _problema(r)
    assert r.status_code == 404 and c["codice"] == "HTTP-404" and "detail" not in c
    r = client.delete("/errore-api")
    assert _problema(r)["codice"] == "HTTP-405" and r.headers["allow"] == "GET"


def test_retry_after(client):
    r = client.get("/rate")
    c = _problema(r)
    assert r.headers["retry-after"] == "60" and c["ritentabile"] is True
    assert c["dettagli"] == {"retry_after": 60}
    r = client.get("/non-disponibile")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"


def test_validazione_val_422_con_campi(client):
    r = client.post("/valida", json={"nome": "ab"})
    c = _problema(r)
    assert r.status_code == 422 and c["codice"] == "VAL-422" and c["esito"] == "NON_VALIDO"
    per_campo = {x["campo"]: x for x in c["campi"]}
    assert per_campo["nome"]["tipo"] == "string_too_short"
    assert per_campo["nome"]["messaggio"] == "Troppo corto: almeno 3 caratteri."
    assert per_campo["quanti"] == {"campo": "quanti", "posizione": "body", "tipo": "missing",
                                   "messaggio": "Campo obbligatorio."}
    assert c["detail"].startswith("2 campi non validi")


def test_eccezione_non_gestita_500_senza_stacktrace(client):
    with structlog.testing.capture_logs() as log:
        r = client.get("/esplode", headers={"X-Request-ID": "richiesta-123"})
    c = _problema(r)
    assert r.status_code == 500 and c["codice"] == "INTERNAL-001"
    assert "segreta" not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text
    assert c["id_richiesta"] == "richiesta-123"
    riga = next(e for e in log if e["event"] == "eccezione_non_gestita")
    assert riga["id_richiesta"] == "richiesta-123" and riga["tipo"] == "RuntimeError"


def test_id_richiesta_dal_client_o_generato(client):
    r = client.get("/errore-api", headers={"X-Request-ID": "abc-1"})
    assert r.json()["id_richiesta"] == "abc-1" == r.headers["x-request-id"]
    r = client.get("/errore-api", headers={"X-Request-ID": "a b\nc"})   # malformato
    assert r.json()["id_richiesta"] != "a b\nc" and len(r.json()["id_richiesta"]) == 32
    # anche le risposte senza errore portano l'id
    ok = client.post("/valida", json={"nome": "abc", "quanti": 1})
    assert ok.status_code == 200 and ok.headers["x-request-id"]


def test_middleware_rispetta_id_gia_assegnato_da_altro_middleware():
    """Gateway: RequestIdMiddleware (più interno) e il nostro vedono lo stesso id."""
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI()
    visti: list[str] = []

    @app.get("/x")
    async def x() -> None:
        raise ErroreApi("VRS-404")

    async def interno(request, call_next):
        visti.append(request.headers.get("x-request-id"))
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=interno)
    installa_gestori_errori(app)
    r = TestClient(app).get("/x")
    assert visti == [r.json()["id_richiesta"]]


def test_funzioni_di_conversione_dirette():
    e = errore_da_http(HTTPException(503, None))
    assert e.codice == "HTTP-503" and e.dettaglio is None
    e = errore_da_http(HTTPException(400, {"type": "SEC-006", "title": "Estensione '.exe'",
                                           "detail": "Ammesse: .pdf",
                                           "dettagli": {"ammesse": [".pdf"]}}))
    assert (e.codice, e.titolo, e.dettaglio, e.dettagli) == (
        "SEC-006", "Estensione '.exe'", "Ammesse: .pdf", {"ammesse": [".pdf"]})
    campi = campi_da_validazione([{"loc": ("query", "limite"), "type": "greater_than",
                                   "ctx": {"gt": 0}, "msg": "x"},
                                  {"loc": ("body",), "type": "json_invalid", "msg": "x"},
                                  {"loc": ("body", "a"), "type": "value_error",
                                   "msg": "Value error, data futura"}])
    assert campi[0] == {"campo": "limite", "posizione": "query", "tipo": "greater_than",
                        "messaggio": "Deve essere maggiore di 0."}
    assert campi[1]["campo"] == "body" and campi[1]["messaggio"] == "JSON non leggibile."
    assert campi[2]["messaggio"] == "data futura"
    r = risposta_problema(ErroreApi("VRS-102", id_richiesta="q"))
    assert r.status_code == 400 and r.headers["x-request-id"] == "q"
