# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Gestori FastAPI che portano OGNI errore al formato problem+json del catalogo.

``installa_gestori_errori(app)`` registra:
- ``ErroreApi`` → il suo corpo;
- ``HTTPException`` (FastAPI e Starlette), anche col vecchio ``detail``
  dizionario ``{type|codice|error, title|messaggio, detail, ...}``: il codice
  si ricava se c'è (anche dagli alias del catalogo, es. ``invalid_token``),
  altrimenti si usa il generico ``HTTP-<status>``;
- ``RequestValidationError`` → ``VAL-422`` con l'elenco ``campi``;
- qualsiasi altra eccezione → ``INTERNAL-001`` (500), mai lo stacktrace al
  client: va nel log strutturato con l'id della richiesta.
e un middleware ASGI che assegna l'id della richiesta (``X-Request-ID`` del
client se ben formato, altrimenti generato) e lo restituisce in risposta.
"""
from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from libs.shared.errori.catalogo import cerca, codice_generico
from libs.shared.errori.problema import (
    INTESTAZIONE_ID_RICHIESTA,
    MEDIA_TYPE_PROBLEMA,
    ErroreApi,
    id_richiesta_corrente,
    imposta_id_richiesta,
    normalizza_id_richiesta,
    nuovo_id_richiesta,
    ripristina_id_richiesta,
)

logger = structlog.get_logger(__name__)

_CHIAVI_CODICE = ("codice", "type", "error", "code")
_CHIAVI_TITOLO = ("title", "messaggio", "message")
_CHIAVI_ID = ("id_richiesta", "id_correlazione", "request_id", "error_id")


class RispostaProblema(JSONResponse):
    media_type = MEDIA_TYPE_PROBLEMA


# ── Id della richiesta ───────────────────────────────────────────────

class MiddlewareIdRichiesta:
    """Assegna l'id della richiesta (stato della richiesta + contextvar) e lo
    rimanda nell'intestazione X-Request-ID. ASGI puro: nessun task in più."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        stato = scope.setdefault("state", {})
        dal_client = None
        for nome, valore in scope.get("headers") or ():
            if nome.lower() == b"x-request-id":
                dal_client = normalizza_id_richiesta(valore.decode("latin-1"))
                break
        idr = (normalizza_id_richiesta(stato.get("id_richiesta"))
               or normalizza_id_richiesta(stato.get("request_id"))
               or dal_client or nuovo_id_richiesta())
        stato["id_richiesta"] = idr
        stato["request_id"] = idr
        if dal_client != idr:
            # Chi legge l'intestazione più avanti (log, altri middleware) vede lo stesso id
            intestazioni = [(n, v) for n, v in scope.get("headers") or ()
                            if n.lower() != b"x-request-id"]
            intestazioni.append((b"x-request-id", idr.encode("latin-1")))
            scope = {**scope, "headers": intestazioni}

        async def invia(messaggio: Message) -> None:
            if messaggio["type"] == "http.response.start":
                h = list(messaggio.get("headers") or [])
                if not any(n.lower() == b"x-request-id" for n, _ in h):
                    h.append((b"x-request-id", idr.encode("latin-1")))
                    messaggio = {**messaggio, "headers": h}
            await send(messaggio)

        token = imposta_id_richiesta(idr)
        try:
            await self.app(scope, receive, invia)
        finally:
            ripristina_id_richiesta(token)


def id_richiesta_di(request: Request | None) -> str:
    """Id della richiesta: stato (middleware), intestazione, contesto o nuovo."""
    if request is not None:
        for chiave in ("id_richiesta", "request_id"):
            valore = normalizza_id_richiesta(getattr(request.state, chiave, None))
            if valore:
                return valore
        valore = normalizza_id_richiesta(request.headers.get(INTESTAZIONE_ID_RICHIESTA))
        if valore:
            return valore
    return id_richiesta_corrente() or nuovo_id_richiesta()


# ── Conversioni ──────────────────────────────────────────────────────

def _frase_standard(status: int) -> str | None:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return None


def errore_da_http(exc: StarletteHTTPException) -> ErroreApi:
    """Converte una HTTPException (anche del vecchio formato) in ErroreApi."""
    status = exc.status_code
    headers = dict(exc.headers or {})
    dettaglio_grezzo: Any = exc.detail
    titolo: str | None = None
    dettaglio: str | None = None
    dettagli: dict[str, Any] = {}
    id_richiesta: str | None = None
    grezzo: str | None = None

    if isinstance(dettaglio_grezzo, Mapping):
        resto = dict(dettaglio_grezzo)
        for chiave in _CHIAVI_CODICE:
            valore = resto.pop(chiave, None)
            if grezzo is None and isinstance(valore, str) and valore:
                grezzo = valore
        for chiave in _CHIAVI_TITOLO:
            valore = resto.pop(chiave, None)
            if titolo is None and isinstance(valore, str) and valore.strip():
                titolo = valore.strip()
        spiegazione = resto.pop("detail", None)
        if isinstance(spiegazione, str):
            dettaglio = spiegazione
        elif spiegazione is not None:
            dettagli["detail"] = spiegazione
        for chiave in _CHIAVI_ID:
            valore = normalizza_id_richiesta(resto.pop(chiave, None))
            id_richiesta = id_richiesta or valore
        resto.pop("status", None)
        annidati = resto.pop("dettagli", None)
        if isinstance(annidati, Mapping):
            dettagli.update(annidati)
        dettagli.update(resto)
    elif isinstance(dettaglio_grezzo, str):
        if dettaglio_grezzo != _frase_standard(status):
            dettaglio = dettaglio_grezzo
    elif dettaglio_grezzo is not None:
        dettagli["detail"] = dettaglio_grezzo

    voce = cerca(grezzo)
    if voce is None:
        if grezzo:
            # Il codice deve stare nel catalogo: lo si segnala nel log e si
            # risponde comunque in forma corretta (mai un 500 per questo).
            logger.warning("codice_errore_fuori_catalogo", codice=grezzo, status=status)
            dettagli["codice_originale"] = grezzo
        codice = codice_generico(status)
    else:
        codice = voce.codice
        if grezzo != codice:
            dettagli.setdefault("codice_originale", grezzo)
    return ErroreApi(codice, dettaglio, titolo=titolo, status=status, dettagli=dettagli,
                     headers=headers, id_richiesta=id_richiesta)


_POSIZIONI = ("body", "query", "path", "header", "cookie")

_MESSAGGI_VALIDAZIONE: dict[str, str] = {
    "missing": "Campo obbligatorio.",
    "string_pattern_mismatch": "Formato non valido.",
    "string_too_short": "Troppo corto: almeno {min_length} caratteri.",
    "string_too_long": "Troppo lungo: al massimo {max_length} caratteri.",
    "too_short": "Troppo pochi elementi: almeno {min_length}.",
    "too_long": "Troppi elementi: al massimo {max_length}.",
    "greater_than": "Deve essere maggiore di {gt}.",
    "greater_than_equal": "Deve essere almeno {ge}.",
    "less_than": "Deve essere minore di {lt}.",
    "less_than_equal": "Deve essere al massimo {le}.",
    "int_parsing": "Deve essere un numero intero.",
    "int_type": "Deve essere un numero intero.",
    "float_parsing": "Deve essere un numero.",
    "bool_parsing": "Deve essere vero o falso.",
    "string_type": "Deve essere un testo.",
    "uuid_parsing": "Identificativo non valido.",
    "date_parsing": "Data non valida.",
    "datetime_parsing": "Data e ora non valide.",
    "enum": "Valore non ammesso: scegliere fra {expected}.",
    "literal_error": "Valore non ammesso: scegliere fra {expected}.",
    "json_invalid": "JSON non leggibile.",
    "model_attributes_type": "Oggetto non valido.",
    "dict_type": "Deve essere un oggetto.",
    "list_type": "Deve essere un elenco.",
    "extra_forbidden": "Campo non previsto.",
    "url_parsing": "Indirizzo non valido.",
}


def messaggio_validazione(errore: Mapping[str, Any]) -> str:
    """Messaggio in italiano per un errore di validazione Pydantic."""
    tipo = str(errore.get("type") or "")
    ctx = errore.get("ctx") if isinstance(errore.get("ctx"), Mapping) else {}
    if tipo in ("value_error", "assertion_error"):
        testo = str(errore.get("msg") or "")
        for prefisso in ("Value error, ", "Assertion failed, "):
            testo = testo.removeprefix(prefisso)
        return testo or "Valore non valido."
    modello = _MESSAGGI_VALIDAZIONE.get(tipo)
    if modello is None:
        return "Valore non valido."
    try:
        return modello.format(**{k: str(v) for k, v in ctx.items()})
    except (KeyError, IndexError):
        return modello.split(":")[0].rstrip(".") + "."


def campi_da_validazione(errori: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    campi: list[dict[str, Any]] = []
    for e in errori:
        loc = [str(x) for x in e.get("loc") or ()]
        posizione = loc[0] if loc and loc[0] in _POSIZIONI else None
        percorso = loc[1:] if posizione else loc
        campi.append({
            "campo": ".".join(percorso) or (posizione or ""),
            "posizione": posizione,
            "tipo": str(e.get("type") or ""),
            "messaggio": messaggio_validazione(e),
        })
    return campi


def errore_da_validazione(exc: RequestValidationError) -> ErroreApi:
    campi = campi_da_validazione(list(exc.errors()))
    nomi = ", ".join(c["campo"] for c in campi[:10] if c["campo"])
    dettaglio = (f"{len(campi)} campo non valido: {nomi}" if len(campi) == 1
                 else f"{len(campi)} campi non validi: {nomi}")
    return ErroreApi("VAL-422", dettaglio, campi=campi)


# ── Risposte ─────────────────────────────────────────────────────────

def risposta_problema(errore: ErroreApi, request: Request | None = None) -> JSONResponse:
    """Risposta application/problem+json per un ErroreApi."""
    idr = errore.id_richiesta or id_richiesta_di(request)
    corpo = errore.corpo(instance=request.url.path if request is not None else None,
                         id_richiesta=idr)
    return RispostaProblema(status_code=errore.status, content=corpo,
                            headers=errore.intestazioni(idr))


def _registra(errore: ErroreApi, request: Request) -> None:
    dati = {"codice": errore.codice, "status": errore.status, "percorso": request.url.path,
            "metodo": request.method, "id_richiesta": errore.id_richiesta}
    if errore.status >= 500:
        logger.error("risposta_errore", **dati)
    else:
        logger.info("risposta_errore", **dati)


async def gestisci_errore_api(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ErroreApi)
    risposta = risposta_problema(exc, request)
    _registra(exc, request)
    return risposta


async def gestisci_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return await gestisci_errore_api(request, errore_da_http(exc))


async def gestisci_validazione(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return await gestisci_errore_api(request, errore_da_validazione(exc))


async def gestisci_imprevisto(request: Request, exc: Exception) -> JSONResponse:
    """Eccezione non gestita: 500 INTERNAL-001; lo stacktrace solo nel log."""
    idr = id_richiesta_di(request)
    logger.exception("eccezione_non_gestita", id_richiesta=idr, percorso=request.url.path,
                     metodo=request.method, tipo=type(exc).__name__)
    return risposta_problema(ErroreApi("INTERNAL-001", id_richiesta=idr), request)


def installa_gestori_errori(app: FastAPI, *, middleware: bool = True) -> None:
    """
    Registra i gestori del formato unico e (predefinito) il middleware dell'id
    della richiesta. Chiamarla DOPO gli ``add_middleware`` del servizio, così
    il middleware dell'id è il più esterno e copre anche i log degli altri.
    """
    app.add_exception_handler(ErroreApi, gestisci_errore_api)
    app.add_exception_handler(StarletteHTTPException, gestisci_http)
    app.add_exception_handler(RequestValidationError, gestisci_validazione)
    app.add_exception_handler(Exception, gestisci_imprevisto)
    if middleware:
        app.add_middleware(MiddlewareIdRichiesta)
