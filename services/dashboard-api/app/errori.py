# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Errori verso il client della dashboard: sottile adattatore sul catalogo
``libs/shared/errori`` (VD-C-100, REGOLE §18).

Mai 200 con un campo ``error``, mai il testo di un'eccezione (SQL, S3, rete)
nella risposta: l'eccezione va nel log con l'id della richiesta, il client
riceve il problem+json del catalogo con lo stesso ``id_richiesta`` (anche
nell'intestazione ``X-Request-ID``), così l'operatore ritrova la riga di log.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from libs.shared.errori import (
    INTESTAZIONE_ID_RICHIESTA,
    ErroreApi,
    cerca,
    errore_registrato,
    nuovo_id_richiesta,
)

#: Compatibilità: l'identificativo di correlazione è ora l'id della richiesta.
INTESTAZIONE_CORRELAZIONE = INTESTAZIONE_ID_RICHIESTA
nuovo_id_correlazione = nuovo_id_richiesta

__all__ = ["ErroreApi", "errore_interno", "errore_db", "errore_da_servizio_a_valle",
           "errore_servizio_irraggiungibile", "INTESTAZIONE_CORRELAZIONE"]


def errore_interno(
    stato: int, codice: str, titolo: str, exc: BaseException | None = None, **contesto: Any,
) -> ErroreApi:
    """Errore del catalogo con id della richiesta; l'eccezione va solo nel log."""
    return errore_registrato(codice, exc, titolo=titolo, status=stato, **contesto)


def errore_db(operazione: str, exc: BaseException, codice: str = "GOV-503") -> ErroreApi:
    """Database non raggiungibile o query fallita: 503 esplicito, mai «vuoto»."""
    return errore_registrato(
        codice, exc, titolo=f"Database non disponibile: {operazione} non eseguita",
        status=503, operazione=operazione,
    )


def errore_da_servizio_a_valle(
    servizio: str, risposta: httpx.Response,
) -> ErroreApi | HTTPException:
    """
    Ripassa al client l'errore di un servizio interno con il suo codice HTTP.
    Un problem+json del catalogo si ripassa così com'è (codice, titolo,
    dettaglio, dati); un corpo del vecchio formato ``{detail: {...}}`` passa
    dal gestore, che lo converte; altrimenti messaggio generico. Un 5xx a
    valle diventa 502 (il testo resta nel log).
    """
    stato = risposta.status_code
    if stato >= 500:
        return errore_registrato(
            "DSH-502", None, titolo=f"Il servizio {servizio} ha risposto con un errore ({stato})",
            servizio=servizio, stato_a_valle=stato, corpo=risposta.text[:500],
        )
    try:
        corpo = risposta.json()
    except ValueError:
        corpo = None
    if isinstance(corpo, dict):
        voce = cerca(corpo.get("codice") if isinstance(corpo.get("codice"), str) else None)
        if voce is not None and "type" in corpo:
            return ErroreApi(
                voce.codice,
                corpo.get("detail") if isinstance(corpo.get("detail"), str) else None,
                titolo=corpo.get("title") if isinstance(corpo.get("title"), str) else None,
                status=stato,
                dettagli=corpo.get("dettagli") if isinstance(corpo.get("dettagli"), dict) else None,
                campi=corpo.get("campi") if isinstance(corpo.get("campi"), list) else None,
            )
        if "detail" in corpo:
            return HTTPException(stato, corpo["detail"])
    return ErroreApi("DSH-UPSTREAM", titolo=f"Richiesta rifiutata dal servizio {servizio}",
                     status=stato)


def errore_servizio_irraggiungibile(servizio: str, exc: BaseException) -> ErroreApi:
    return errore_registrato(
        "DSH-502", exc, titolo=f"Il servizio {servizio} non è raggiungibile", servizio=servizio,
    )
