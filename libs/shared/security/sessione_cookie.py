# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/sessione_cookie.py — sessione del portale in cookie HttpOnly

Riferimenti: VD-C-079 (R05, agente r05-sicurezza); REGOLE_SVILUPPO §19.7 (token
in cookie HttpOnly + SameSite, non in ``localStorage``), §19.1 (ogni token
verificato); documento: docs/architettura/sicurezza.md.

Il portale (frontend) non vede più il token: dopo l'accesso SPID/CIE il gateway
lo consegna in un cookie

    ``vd_sessione``  HttpOnly; Secure; SameSite=Strict; Path=/api

leggibile solo dal browser verso le API della stessa origine. La protezione
contro la falsificazione delle richieste (CSRF) ha tre livelli:
  1. ``SameSite=Strict``: il browser non manda il cookie da un altro sito;
  2. doppio invio del token: il cookie ``vd_csrf`` (NON HttpOnly, Secure,
     SameSite=Strict, Path=/) contiene un valore casuale che la pagina
     rimanda nell'intestazione ``X-CSRF-Token``; un altro sito non può leggerlo;
  3. controllo dell'origine: ``Origin`` (o, se manca, ``Referer``) deve essere
     l'origine della richiesta stessa o una di ``SESSIONE_ORIGINI_AMMESSE``.
I controlli 2 e 3 valgono per i metodi che modificano (POST, PUT, PATCH,
DELETE); GET e HEAD non cambiano stato (REGOLE §14.11).

``MiddlewareSessioneCookie`` (in ogni servizio raggiunto dal portale):
  - richiesta con ``Authorization`` o chiave API → invariata (sistemi versanti,
    SDK, servizi: il cookie è ignorato);
  - richiesta con il solo cookie → controlli CSRF, poi ``Authorization: Bearer
    <cookie>`` aggiunto alla richiesta: il token passa dalla STESSA verifica di
    sempre (firma, iss, aud, exp, revoca) e imposta l'ambito d'ente;
  - controlli falliti → 403 ``AUTH-040`` (problem+json), nessun accesso;
  - revisione R05 (R12): cookie di una sessione chiusa con «Esci»
    (``sessioni_revocate``, impronta del token) → 401 ``AUTH-003`` in OGNI
    servizio che accetta il cookie, prima di qualunque rotta (anche senza la
    dipendenza di revoca); controllo non eseguibile → 503 ``AUTH-503``. Fa
    eccezione solo l'uscita stessa (``DELETE /api/v1/auth/sessione``, idempotente);
  - percorsi che creano la sessione (``PERCORSI_SENZA_SESSIONE``) → invariati;
  - R06 (VD-C-127): token di una sessione del portale (claim ``ses = portale``,
    nel cookie o come Bearer) inattivo oltre il suo limite, chiuso con «Esci»
    o mai registrato → 401 ``AUTH-004`` in OGNI servizio, subito (nessuna cache:
    ``libs.shared.security.inattivita``); ogni richiesta rinnova la sessione,
    tranne quelle con ``X-Sessione-Rinnovo: no`` (aggiornamenti automatici).
  - revisione R06 (V1): credenziali AMBIGUE → 400 ``AUTH-041`` in OGNI servizio,
    prima di qualunque controllo: più di un'intestazione ``Authorization`` (o
    ``X-Api-Key``, ``X-Api-Key-Id``, ``X-Api-Key-Secret``), più cookie
    ``vd_sessione`` diversi, oppure cookie di sessione e Bearer con token
    diversi. Prima il middleware controllava l'ULTIMA intestazione e le rotte
    autenticavano la PRIMA: un token scaduto per inattività (o chiuso con
    «Esci») seguito da un secondo Bearer qualsiasi passava;
  - revisione R06 (V4): i controlli della sessione sul database (revoca,
    inattività, rinnovo) si fanno solo per un token con FIRMA e scadenza valide
    (chiavi caricate dal processo); un token falso passa alla rotta, che lo
    rifiuta (AUTH-001) senza alcuna scrittura. Processo senza chiavi (solo
    applicazioni di prova): controllo senza rinnovo.
Le chiavi API restano invariate.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from collections.abc import Iterable
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from libs.shared.security.credenziali_uniche import (
    MiddlewareCredenzialiUniche,
    motivo_credenziali_ambigue,
    rispondi_credenziali_ambigue,
)

logger = structlog.get_logger(__name__)

COOKIE_SESSIONE = "vd_sessione"
COOKIE_CSRF = "vd_csrf"
INTESTAZIONE_CSRF = "x-csrf-token"
#: Il cookie della sessione serve solo alle API (non alle pagine statiche).
PERCORSO_SESSIONE = "/api"
#: Il cookie anti-CSRF deve essere leggibile dalla pagina.
PERCORSO_CSRF = "/"
METODI_SICURI = frozenset({"GET", "HEAD", "OPTIONS"})
#: Intestazioni con cui un client presenta credenziali proprie: il cookie si ignora.
_CREDENZIALI_ESPLICITE = (b"authorization", b"x-api-key", b"x-api-key-id",
                          b"x-api-key-secret")
VARIABILE_ORIGINI = "SESSIONE_ORIGINI_AMMESSE"
#: Percorsi che CREANO la sessione (accesso SPID/CIE, token di sviluppo): non
#: usano quella esistente, quindi il cookie non vi entra e un nuovo accesso con
#: una sessione vecchia nel browser non viene rifiutato.
PERCORSI_SENZA_SESSIONE: tuple[str, ...] = ("/api/v1/auth/spid/", "/api/v1/auth/dev-token")
#: Uscita dal portale: passa anche con una sessione già chiusa (idempotente)
PERCORSO_USCITA = "/api/v1/auth/sessione"


def nuovo_token_csrf() -> str:
    """Valore casuale del doppio invio (256 bit)."""
    return secrets.token_urlsafe(32)


def imposta_cookie_sessione(risposta: Any, token: str, *, durata_secondi: int,
                            sicuro: bool = True) -> str:
    """Scrive i due cookie su una risposta Starlette; restituisce il token CSRF.

    ``sicuro=False`` solo in sviluppo su http://localhost (il chiamante decide
    con la stessa regola del cookie SPID)."""
    csrf = nuovo_token_csrf()
    risposta.set_cookie(COOKIE_SESSIONE, token, max_age=durata_secondi,
                        path=PERCORSO_SESSIONE, httponly=True, secure=sicuro,
                        samesite="strict")
    risposta.set_cookie(COOKIE_CSRF, csrf, max_age=durata_secondi, path=PERCORSO_CSRF,
                        httponly=False, secure=sicuro, samesite="strict")
    return csrf


def cancella_cookie_sessione(risposta: Any, *, sicuro: bool = True) -> None:
    """Toglie i due cookie (uscita)."""
    risposta.delete_cookie(COOKIE_SESSIONE, path=PERCORSO_SESSIONE, httponly=True,
                           secure=sicuro, samesite="strict")
    risposta.delete_cookie(COOKIE_CSRF, path=PERCORSO_CSRF, secure=sicuro,
                           samesite="strict")


def origini_ammesse_da_ambiente() -> tuple[str, ...]:
    """``SESSIONE_ORIGINI_AMMESSE``: origini (``https://host[:porta]``) separate da
    virgole, oltre a quella della richiesta stessa (predefinito: nessuna)."""
    valore = os.environ.get(VARIABILE_ORIGINI, "")
    return tuple(_normalizza_origine(v) for v in valore.split(",") if v.strip())


def _normalizza_origine(valore: str) -> str:
    parti = urlsplit(valore.strip())
    return f"{parti.scheme.lower()}://{parti.netloc.lower()}" if parti.netloc else ""


def _intestazioni(scope: Scope) -> dict[bytes, bytes]:
    return {n.lower(): v for n, v in scope.get("headers") or ()}


def _cookie(intestazioni: dict[bytes, bytes]) -> dict[str, str]:
    grezzo = intestazioni.get(b"cookie", b"").decode("latin-1")
    if not grezzo:
        return {}
    biscotti = SimpleCookie()
    try:
        biscotti.load(grezzo)
    except CookieError:
        return {}
    return {k: m.value for k, m in biscotti.items()}


def origine_della_richiesta(scope: Scope, intestazioni: dict[bytes, bytes]) -> str:
    """Origine vista dal browser: host della richiesta; schema da
    ``X-Forwarded-Proto`` (ingress che termina TLS) o da quello della connessione."""
    host = intestazioni.get(b"host", b"").decode("latin-1").strip().lower()
    proto = intestazioni.get(b"x-forwarded-proto", b"").decode("latin-1").split(",")[0]
    schema = (proto.strip() or scope.get("scheme") or "http").lower()
    return f"{schema}://{host}" if host else ""


def motivo_rifiuto_csrf(scope: Scope, intestazioni: dict[bytes, bytes],
                        biscotti: dict[str, str], origini: Iterable[str]) -> str | None:
    """Controllo puro: None se la richiesta con il cookie è ammessa, altrimenti il motivo."""
    if str(scope.get("method", "GET")).upper() in METODI_SICURI:
        return None
    atteso = biscotti.get(COOKIE_CSRF, "")
    ricevuto = intestazioni.get(INTESTAZIONE_CSRF.encode(), b"").decode("latin-1").strip()
    if not atteso or not ricevuto or not hmac.compare_digest(atteso, ricevuto):
        return "token anti-CSRF assente o diverso dal cookie"
    ammesse = {o for o in origini if o}
    propria = origine_della_richiesta(scope, intestazioni)
    if propria:
        ammesse.add(propria)
    origine = intestazioni.get(b"origin", b"").decode("latin-1").strip()
    if not origine or origine == "null":
        referer = intestazioni.get(b"referer", b"").decode("latin-1").strip()
        if not referer:
            return "richiesta senza Origin né Referer"
        origine = referer
    if _normalizza_origine(origine) not in ammesse:
        return "origine non ammessa"
    return None


class MiddlewareSessioneCookie:
    """Traduce il cookie della sessione in ``Authorization`` dopo i controlli CSRF.

    Da aggiungere con ``app.add_middleware(MiddlewareSessioneCookie)`` in ogni
    servizio raggiunto dal portale (gateway, onboarding, dashboard-api,
    distribution, notification, hashchain)."""

    def __init__(self, app: ASGIApp, origini_ammesse: Iterable[str] | None = None,
                 percorsi_senza_sessione: Iterable[str] = PERCORSI_SENZA_SESSIONE) -> None:
        self.app = app
        self.origini = tuple(origini_ammesse) if origini_ammesse is not None \
            else origini_ammesse_da_ambiente()
        self.senza_sessione = tuple(percorsi_senza_sessione)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        intestazioni = _intestazioni(scope)
        ambigue = motivo_credenziali_ambigue(scope)
        if ambigue is not None:
            # revisione R06 (V1): mai scegliere una delle credenziali
            logger.warning("credenziali_ambigue_rifiutate", motivo=ambigue,
                           metodo=scope.get("method"), percorso=scope.get("path"))
            await rispondi_credenziali_ambigue(scope, send)
            return
        if str(scope.get("path", "")).startswith(self.senza_sessione):
            await self.app(scope, receive, send)
            return
        if any(n in intestazioni for n in _CREDENZIALI_ESPLICITE):
            # R06 (VD-C-127): lo stesso token del portale presentato come Bearer
            # segue la stessa scadenza per inattività del cookie
            portatore = _bearer(intestazioni)
            if portatore and not _uscita(scope) and _e_portale(portatore):
                # sessione chiusa con «Esci»: AUTH-003 come per il cookie
                esito = await _controlli_sessione(portatore, intestazioni)
                if esito is not None:
                    await _rispondi_errore(scope, send, *esito)
                    return
            await self.app(scope, receive, send)
            return
        biscotti = _cookie(intestazioni)
        token = biscotti.get(COOKIE_SESSIONE, "").strip()
        if not token:
            await self.app(scope, receive, send)
            return
        motivo = motivo_rifiuto_csrf(scope, intestazioni, biscotti, self.origini)
        if motivo is not None:
            logger.warning("sessione_cookie_rifiutata", motivo=motivo,
                           metodo=scope.get("method"), percorso=scope.get("path"))
            await _rispondi_403(scope, send)
            return
        if not _uscita(scope):
            esito = await _controlli_sessione(token, intestazioni)
            if esito is not None:
                await _rispondi_errore(scope, send, *esito)
                return
        nuove = [(n, v) for n, v in scope.get("headers") or () if n.lower() != b"authorization"]
        nuove.append((b"authorization", f"Bearer {token}".encode("latin-1")))
        stato = dict(scope.get("state") or {})
        stato["autenticazione_cookie"] = True
        await self.app({**scope, "headers": nuove, "state": stato}, receive, send)


async def _controlli_sessione(token: str, intestazioni: dict[bytes, bytes]
                              ) -> tuple[str, str] | None:
    """Revoca e inattività, SOLO per un token con firma e scadenza valide
    (revisione R06, V4). Token non valido: nessun lavoro sul database, la
    rotta lo rifiuta. Processo senza chiavi: controllo senza rinnovo."""
    from libs.shared.security.jwt_rs256 import ErroreChiaviJwt, verifica_firma_e_scadenza

    rinnovo_ammesso = True
    try:
        if verifica_firma_e_scadenza(token) is None:
            return None
    except ErroreChiaviJwt:
        rinnovo_ammesso = False
    return (await _sessione_chiusa(token)
            or await _sessione_inattiva(token, intestazioni, rinnovo_ammesso=rinnovo_ammesso))


def _uscita(scope: Scope) -> bool:
    return (str(scope.get("method", "")).upper() == "DELETE"
            and str(scope.get("path", "")) == PERCORSO_USCITA)


def _e_portale(token: str) -> bool:
    from libs.shared.security.inattivita import e_sessione_portale

    return e_sessione_portale(token)


def _bearer(intestazioni: dict[bytes, bytes]) -> str:
    valore = intestazioni.get(b"authorization", b"").decode("latin-1").strip()
    return valore[7:].strip() if valore[:7].lower() == "bearer " else ""


async def _sessione_inattiva(token: str, intestazioni: dict[bytes, bytes], *,
                             rinnovo_ammesso: bool = True) -> tuple[str, str] | None:
    """R06 (VD-C-127): (codice, dettaglio) se la sessione del portale è scaduta
    per inattività o chiusa; None se si prosegue (o il token non è del portale)."""
    from libs.shared.security.inattivita import (
        INTESTAZIONE_RINNOVO,
        SCADUTA,
        controlla_sessione,
        e_sessione_portale,
    )
    from libs.shared.security.revoca import RevocaNonVerificabile

    if not e_sessione_portale(token):
        return None
    rinnova = rinnovo_ammesso and \
        intestazioni.get(INTESTAZIONE_RINNOVO.encode(), b"").strip().lower() != b"no"
    try:
        esito = await controlla_sessione(token, rinnova=rinnova)
    except RevocaNonVerificabile:
        return "AUTH-503", "Riprovare tra poco."
    if esito == SCADUTA:
        logger.info("sessione_portale_scaduta")
        return "AUTH-004", "Sessione scaduta per inattività: accedere di nuovo."
    return None


async def _sessione_chiusa(token: str) -> tuple[str, str] | None:
    """(codice, dettaglio) se la sessione del cookie è stata chiusa con «Esci» o
    il controllo non si può fare; None se si prosegue (revisione R05, R12)."""
    from libs.shared.security.revoca import (
        RevocaNonVerificabile,
        impronta_token,
        sessione_revocata,
    )

    try:
        if await sessione_revocata(impronta=impronta_token(token)):
            logger.warning("sessione_cookie_chiusa_rifiutata")
            return "AUTH-003", "La sessione è stata chiusa: accedere di nuovo."
    except RevocaNonVerificabile:
        return "AUTH-503", "Riprovare tra poco."
    return None


async def _rispondi_403(scope: Scope, send: Send) -> None:
    await _rispondi_errore(scope, send, "AUTH-040", "Ripetere l'operazione dal portale.")


async def _rispondi_errore(scope: Scope, send: Send, codice: str, dettaglio: str) -> None:
    from libs.shared.errori import ErroreApi

    errore = ErroreApi(codice, dettaglio)
    stato = scope.get("state") or {}
    corpo = errore.corpo(instance=scope.get("path"), id_richiesta=stato.get("id_richiesta"))
    dati = json.dumps(corpo, ensure_ascii=False).encode()
    intestazioni = [(b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(dati)).encode())]
    intestazioni += [(k.lower().encode(), v.encode())
                     for k, v in errore.intestazioni().items()]
    await send({"type": "http.response.start", "status": errore.status,
                "headers": intestazioni})
    await send({"type": "http.response.body", "body": dati})


__all__ = [
    "COOKIE_CSRF", "COOKIE_SESSIONE", "INTESTAZIONE_CSRF", "METODI_SICURI",
    "PERCORSI_SENZA_SESSIONE", "PERCORSO_USCITA",
    "MiddlewareCredenzialiUniche", "MiddlewareSessioneCookie", "VARIABILE_ORIGINI",
    "cancella_cookie_sessione", "imposta_cookie_sessione", "motivo_credenziali_ambigue",
    "motivo_rifiuto_csrf", "nuovo_token_csrf",
    "origine_della_richiesta", "origini_ammesse_da_ambiente",
]
