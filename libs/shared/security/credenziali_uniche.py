# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/credenziali_uniche.py — credenziali ambigue → 400 AUTH-041

Revisione R06 (V1). Una richiesta con più credenziali dello stesso tipo
(intestazioni ``Authorization``, ``X-Api-Key``, ``X-Api-Key-Id`` o
``X-Api-Key-Secret`` ripetute), con più cookie della sessione del portale
diversi, o con cookie della sessione e Bearer diversi, non si autentica: non si
sceglie un'occorrenza. Prima il middleware della sessione leggeva l'ULTIMA
intestazione e le dipendenze delle rotte (FastAPI ``Header``, ``HTTPBearer``)
la PRIMA: un token scaduto per inattività o chiuso con «Esci», seguito da un
secondo Bearer qualsiasi, passava.

Usato da ``MiddlewareSessioneCookie`` (servizi del portale) e, da solo, con
``MiddlewareCredenzialiUniche`` nei servizi che leggono ``Authorization`` senza
la sessione del portale (signature, testimone). Modulo senza dipendenze dal
database né da PyJWT (solo Starlette e structlog).
"""
from __future__ import annotations

import json

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger(__name__)

#: Nome del cookie della sessione del portale (lo stesso di sessione_cookie)
COOKIE_SESSIONE = "vd_sessione"
#: Intestazioni con cui un client presenta credenziali proprie
CREDENZIALI_ESPLICITE = (b"authorization", b"x-api-key", b"x-api-key-id",
                         b"x-api-key-secret")
CODICE = "AUTH-041"
DETTAGLIO = "Presentare una sola credenziale per richiesta."


def _valori_cookie_sessione(scope: Scope) -> set[str]:
    """Tutti i valori di ``vd_sessione`` in TUTTE le intestazioni Cookie (un
    browser in HTTP/2 può dividerle; un attaccante può ripetere il nome)."""
    valori: set[str] = set()
    for nome, valore in scope.get("headers") or ():
        if nome.lower() != b"cookie":
            continue
        for parte in valore.decode("latin-1").split(";"):
            chiave, _, contenuto = parte.strip().partition("=")
            if chiave.strip() == COOKIE_SESSIONE and contenuto.strip().strip('"'):
                valori.add(contenuto.strip().strip('"'))
    return valori


def motivo_credenziali_ambigue(scope: Scope) -> str | None:
    """Revisione R06 (V1): None se le credenziali della richiesta sono univoche,
    altrimenti il motivo (risposta 400 ``AUTH-041``).

    Ambigue: un'intestazione di credenziali ripetuta (le dipendenze leggono la
    prima, il middleware leggeva l'ultima), più cookie di sessione diversi, o
    cookie di sessione e Bearer con token diversi."""
    conteggi: dict[bytes, int] = {}
    autorizzazione = b""
    for nome, valore in scope.get("headers") or ():
        n = nome.lower()
        if n in CREDENZIALI_ESPLICITE:
            conteggi[n] = conteggi.get(n, 0) + 1
            if n == b"authorization":
                autorizzazione = valore
    ripetute = sorted(n.decode() for n, k in conteggi.items() if k > 1)
    if ripetute:
        return f"intestazioni di credenziali ripetute: {', '.join(ripetute)}"
    cookie = _valori_cookie_sessione(scope)
    if len(cookie) > 1:
        return "più cookie di sessione diversi"
    if cookie and autorizzazione:
        testo = autorizzazione.decode("latin-1").strip()
        portatore = testo[7:].strip() if testo[:7].lower() == "bearer " else ""
        if portatore and portatore not in cookie:
            return "cookie di sessione e Bearer con token diversi"
    return None


async def rispondi_credenziali_ambigue(scope: Scope, send: Send) -> None:
    """Risposta problem+json 400 ``AUTH-041`` dal catalogo degli errori."""
    from libs.shared.errori import ErroreApi

    errore = ErroreApi(CODICE, DETTAGLIO)
    stato = scope.get("state") or {}
    corpo = errore.corpo(instance=scope.get("path"), id_richiesta=stato.get("id_richiesta"))
    dati = json.dumps(corpo, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": errore.status,
                "headers": [(b"content-type", b"application/problem+json"),
                            (b"content-length", str(len(dati)).encode())]})
    await send({"type": "http.response.body", "body": dati})


class MiddlewareCredenzialiUniche:
    """Revisione R06 (V1): per i servizi che leggono ``Authorization`` senza la
    sessione del portale (signature, testimone): credenziali ambigue → 400
    ``AUTH-041``. ``MiddlewareSessioneCookie`` fa già lo stesso controllo."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            ambigue = motivo_credenziali_ambigue(scope)
            if ambigue is not None:
                logger.warning("credenziali_ambigue_rifiutate", motivo=ambigue,
                               metodo=scope.get("method"), percorso=scope.get("path"))
                await rispondi_credenziali_ambigue(scope, send)
                return
        await self.app(scope, receive, send)


__all__ = ["CODICE", "CREDENZIALI_ESPLICITE", "MiddlewareCredenzialiUniche",
           "motivo_credenziali_ambigue", "rispondi_credenziali_ambigue"]
