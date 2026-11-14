# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""ErroreApi e corpo problem+json (RFC 9457) senza FastAPI."""
from __future__ import annotations

import structlog

from libs.shared.errori import ErroreApi, Problema, corpo_problema, errore_registrato, voce
from libs.shared.errori.problema import (
    id_richiesta_corrente,
    imposta_id_richiesta,
    ripristina_id_richiesta,
)


def test_valori_dal_catalogo():
    e = ErroreApi("VRS-404", "PdV x non trovato")
    assert (e.status, e.titolo, e.esito.value, e.ritentabile) == (
        404, "PdV non trovato", "NON_VALIDO", False)
    c = e.corpo(instance="/api/v1/versamento/x", id_richiesta="abc")
    assert c == {
        "type": "https://docs.verumdoc.it/errori/VRS-404",
        "title": "PdV non trovato", "status": 404, "detail": "PdV x non trovato",
        "instance": "/api/v1/versamento/x", "codice": "VRS-404", "esito": "NON_VALIDO",
        "ritentabile": False, "dettagli": {}, "campi": [], "id_richiesta": "abc",
        "messaggio": "PdV non trovato",
    }
    Problema.model_validate(c)


def test_sovrascritture():
    e = ErroreApi("VRS-110", titolo="Nome non valido", status=422,
                  dettagli={"campo": "x"}, campi=[{"campo": "x", "posizione": "body",
                                                   "tipo": "missing", "messaggio": "m"}])
    c = e.corpo(id_richiesta="r1")
    assert c["title"] == c["messaggio"] == "Nome non valido"
    assert c["status"] == 422 and c["dettagli"] == {"campo": "x"}
    assert c["campi"][0]["campo"] == "x"
    assert "detail" not in c and "instance" not in c


def test_retry_after_e_id_nelle_intestazioni():
    assert ErroreApi("RATE-001").intestazioni("r") == {"Retry-After": "60", "X-Request-ID": "r"}
    assert ErroreApi("VRS-501").intestazioni()["Retry-After"] == "30"
    assert "Retry-After" not in ErroreApi("VRS-102").intestazioni()
    # quella dell'errore vince sul catalogo
    e = ErroreApi("RATE-001", headers={"retry-after": "5"})
    assert e.intestazioni() == {"retry-after": "5"}
    assert ErroreApi("HC-503", retry_after=7).intestazioni()["Retry-After"] == "7"


def test_id_richiesta_dal_contesto():
    token = imposta_id_richiesta("dal-contesto")
    try:
        assert id_richiesta_corrente() == "dal-contesto"
        assert ErroreApi("VRS-102").corpo()["id_richiesta"] == "dal-contesto"
    finally:
        ripristina_id_richiesta(token)
    assert id_richiesta_corrente() is None
    generato = ErroreApi("VRS-102").corpo()["id_richiesta"]
    assert len(generato) == 32


def test_corpo_problema_da_voce():
    c = corpo_problema(voce("INTERNAL-001"), id_richiesta="z")
    assert c["status"] == 500 and c["esito"] == "ERRORE" and c["id_richiesta"] == "z"


def test_errore_registrato_eccezione_solo_nel_log():
    with structlog.testing.capture_logs() as log:
        e = errore_registrato("DSH-503", RuntimeError("password=segreta host=db"),
                              evento="lettura", operazione="x")
    c = e.corpo()
    assert "segreta" not in str(c)
    assert c["id_richiesta"] == e.id_richiesta
    riga = next(r for r in log if r["event"] == "lettura")
    assert riga["id_richiesta"] == e.id_richiesta and "segreta" in riga["errore"]
    assert riga["codice"] == "DSH-503" and riga["operazione"] == "x"
