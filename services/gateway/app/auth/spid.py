# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Integrazione SPID/CIE tramite aggregatore tecnologico (OpenID Connect).
Riferimento piano: F2.4 — SPID/CIE (tramite aggregatore — no SPID diretto)

Sprint 2B FIX-016: state SPID salvato in Redis (condiviso tra repliche K8s),
    letto e cancellato in modo atomico (GETDEL): uno state vale una sola volta.
Sprint 2B FIX-017: whitelist redirect_uri — previene Open Redirect.

VD-C-006 (REGOLE §19.1): l'id_token dell'aggregatore è VERIFICATO prima di
usarne i claim: firma con la chiave pubblicata nel JWKS configurato
(SPID_JWKS_URL, scelta per `kid`), emittente (SPID_ISSUER), pubblico
(SPID_CLIENT_ID), scadenza, e `nonce` uguale a quello salvato con lo state di
QUESTA sessione; lo state è inoltre legato al browser che ha avviato il login
(cookie HttpOnly il cui hash è salvato con lo state).

Accesso reale (R02, g2-auth): persona SPID/CIE → utente → token VerumDoc.
  1. /login       salva state+nonce (Redis) e imposta il cookie di sessione;
                  redirige all'aggregatore (o al mock in sviluppo).
  2. /callback    verifica l'id_token, ricava il codice fiscale (claim
                  fiscal_number, prefisso TINIT- tolto), controlla che esista
                  almeno un utente attivo con quel codice in un cliente non
                  sospeso, crea un CODICE MONOUSO (Redis, 120 s, legato allo
                  stesso cookie) e redirige al frontend con il codice nel
                  FRAMMENTO dell'URL (#spid_codice=…): il frammento non arriva
                  ai server né nei Referer, e il codice da solo non vale nulla
                  senza il cookie HttpOnly del browser che ha avviato il login.
                  Il token NON passa mai nell'URL. Gli errori tornano allo
                  stesso indirizzo come #spid_errore=<codice>.
  3. POST /scambio {codice, tenant_id?}: consuma il codice (GETDEL), rilegge
                  gli utenti, emette il token e lo consegna nel cookie HttpOnly
                  della sessione del portale (R05, VD-C-079: mai nel corpo). Se la
                  persona è utente di più
                  clienti e non ha scelto, risponde 200 con esito «scelta_ente»,
                  l'elenco degli enti e un NUOVO codice monouso (REGOLE §14.12:
                  mai «il primo»).

Mock (solo sviluppo): /mock-callback esiste solo con VERUMDOC_DEV_AUTH=1 e
ENV development/test (stessa regola del dev-token, VD-C-002) e percorre la
stessa associazione codice fiscale → utente del login reale.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from app.auth import oidc
from app.auth.emissione import UtenteAccesso, emetti_accesso
from app.auth.fornitori import ConfigSpid, configurazione_spid, metodi_ammessi_ente
from app.auth.ruoli import STATI_CLIENTE_BLOCCATI
from app.config import login_sviluppo_consentito, settings
from libs.shared.db import get_async_session
from libs.shared.models import Tenant, User
from libs.shared.security.codice_fiscale import (
    CodiceFiscaleNonValido,
    maschera_codice_fiscale,
    valida_codice_fiscale,
)
from libs.verumdoc_rules.parametri_servizio import METODO_SPID_CIE

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth/spid", tags=["SPID/CIE"])


# ── Sprint 2B FIX-017: whitelist redirect_uri ────────────────────────
# Previene Open Redirect: il client non può passare URI arbitrari.
# Gli URI localhost valgono solo in development/test.

ALLOWED_REDIRECT_URIS_DEV = {
    "http://localhost:5173/cb",
    "http://localhost:5173/login",
    "http://localhost:3000/cb",
    "http://localhost:3000/login",
}

ALLOWED_REDIRECT_URIS_PROD = {
    "https://app.verumdoc.it/cb",
    "https://app.verumdoc.it/login",
}

#: Provider ammessi dall'aggregatore.
PROVIDER_AMMESSI = frozenset({"spid", "cie"})


def _get_allowed_uris() -> set[str]:
    """Restituisce la whitelist di redirect_uri in base all'ambiente."""
    extra = set(getattr(settings, "spid_allowed_redirects", []))
    if login_sviluppo_consentito(settings.env):
        return ALLOWED_REDIRECT_URIS_DEV | ALLOWED_REDIRECT_URIS_PROD | extra
    return ALLOWED_REDIRECT_URIS_PROD | extra


class RitornoNonAmmesso(Exception):  # noqa: N818 — nome di dominio
    """redirect_uri fuori dalla whitelist (SPID-010)."""


def _validate_redirect_uri(redirect_uri: str) -> None:
    """Verifica che redirect_uri sia nella whitelist (RitornoNonAmmesso altrimenti).

    g4-chiusura: l'elenco degli indirizzi ammessi non si restituisce più al
    browser; resta nel log l'indirizzo rifiutato."""
    if redirect_uri not in _get_allowed_uris():
        logger.warning("spid_redirect_uri_rejected", redirect_uri=redirect_uri[:200])
        raise RitornoNonAmmesso(redirect_uri)


def _ritorno_login(codice: str) -> RedirectResponse:
    """Errore del flusso senza un indirizzo di ritorno verificato (SPID-002,
    SPID-010, SPID-011): 303 alla pagina di accesso del frontend, BASE_URL/login,
    con il codice nel frammento come gli altri errori (#spid_errore=...).
    BASE_URL è configurazione del servizio, mai un dato della richiesta."""
    base = str(settings.base_url).rstrip("/")
    return RedirectResponse(
        url=f"{base}/login#{urllib.parse.urlencode({'spid_errore': codice})}", status_code=303
    )


def _mock_abilitato() -> bool:
    return settings.login_sviluppo_abilitato() and bool(settings.spid_mock_enabled)


# ── State e codici monouso in Redis (atomici) ────────────────────────

_STATE_PREFIX = "spid:state:"
_STATE_TTL = 300  # 5 minuti
_CODICE_PREFIX = "spid:codice:"
_CODICE_TTL = 120  # 2 minuti: il frontend lo scambia subito al ritorno
#: Cookie che lega state e codice monouso al browser che ha avviato il login.
COOKIE_SESSIONE = "vd_spid_sessione"
_COOKIE_PATH = "/api/v1/auth/spid"


async def _get_redis() -> Any:
    """Client Redis (decode_responses=True)."""
    import redis.asyncio as aioredis

    _redis_url = getattr(settings, "redis_url", "redis://localhost:6379/0")
    return aioredis.from_url(_redis_url, encoding="utf-8", decode_responses=True)


async def _salva(chiave: str, ttl: int, data: dict[str, Any]) -> None:
    redis = await _get_redis()
    try:
        await redis.setex(chiave, ttl, json.dumps(data))
    finally:
        await redis.aclose()


async def _consuma(chiave: str) -> dict[str, Any] | None:
    """Legge e rimuove in un solo comando (GETDEL): niente riuso."""
    redis = await _get_redis()
    try:
        raw = await redis.getdel(chiave)
        if raw is None:
            return None
        dati: dict[str, Any] = json.loads(raw)
        return dati
    finally:
        await redis.aclose()


async def _set_state(state: str, data: dict[str, Any]) -> None:
    """Salva state SPID in Redis con TTL."""
    await _salva(f"{_STATE_PREFIX}{state}", _STATE_TTL, data)


async def _get_state(state: str) -> dict[str, Any] | None:
    """Recupera e rimuove lo state in un solo comando (GETDEL): niente riuso."""
    return await _consuma(f"{_STATE_PREFIX}{state}")


async def _nuovo_codice(data: dict[str, Any]) -> str:
    codice = secrets.token_urlsafe(32)
    await _salva(f"{_CODICE_PREFIX}{_hash_sessione(codice)}", _CODICE_TTL, data)
    return codice


async def _consuma_codice(codice: str) -> dict[str, Any] | None:
    # In Redis c'è solo l'impronta del codice: chi legge Redis non può usarlo.
    return await _consuma(f"{_CODICE_PREFIX}{_hash_sessione(codice)}")


def _hash_sessione(valore: str) -> str:
    return hashlib.sha256(valore.encode()).hexdigest()


def _imposta_cookie(risposta: RedirectResponse, sessione: str, request: Request) -> None:
    # Secure sempre, tranne in development/test su http (localhost senza TLS).
    sicuro = not (login_sviluppo_consentito(settings.env) and request.url.scheme == "http")
    risposta.set_cookie(
        COOKIE_SESSIONE,
        sessione,
        max_age=_STATE_TTL + _CODICE_TTL,
        path=_COOKIE_PATH,
        httponly=True,
        secure=sicuro,
        samesite="lax",
    )


def _sessione_corrisponde(request: Request, atteso: str) -> bool:
    sessione = request.cookies.get(COOKIE_SESSIONE, "")
    return bool(sessione and atteso and hmac.compare_digest(_hash_sessione(sessione), atteso))


# ── Verifica id_token (JWKS) ─────────────────────────────────────────

#: Trasporto HTTP verso l'aggregatore. None = rete reale; i test iniettano
#: un httpx.MockTransport (nessuna rete esterna nei test).
_trasporto_http: httpx.AsyncBaseTransport | None = None
_ALGORITMI_ID_TOKEN = ["RS256", "PS256", "ES256"]
_cache_jwks: dict[str, tuple[float, dict[str, Any]]] = {}


class IdTokenNonValido(Exception):
    """id_token rifiutato; il messaggio è sicuro da registrare (niente claim personali)."""


def _client_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, transport=_trasporto_http)


async def _scarica_jwks(url: str, forza: bool = False) -> dict[str, Any]:
    adesso = time.monotonic()
    in_cache = _cache_jwks.get(url)
    if in_cache and not forza and in_cache[0] > adesso:
        return in_cache[1]
    async with _client_http() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        jwks: dict[str, Any] = resp.json()
    if not isinstance(jwks.get("keys"), list):
        raise IdTokenNonValido("JWKS dell'aggregatore senza 'keys'")
    _cache_jwks[url] = (adesso + max(int(settings.spid_jwks_cache_seconds), 0), jwks)
    return jwks


_cache_catena: dict[str, tuple[float, dict[str, Any]]] = {}


async def jwks_op(cfg: ConfigSpid) -> dict[str, Any]:
    """R13b (VD-C-069): chiavi dell'OP. Con la federazione configurata vengono dalla
    trust chain VERIFICATA fino al Trust Anchor (chiavi fissate in console); altrimenti
    dal JWKS configurato, come prima."""
    if not cfg.federazione:
        return await _scarica_jwks(cfg.jwks_url)
    from libs.shared.security.oidc_federazione import CatenaNonValida, chiavi_fissate, risolvi

    entita = str(cfg.federazione.get("entita_op") or cfg.issuer)
    voce = _cache_catena.get(entita)
    if voce and voce[0] > time.time():
        return voce[1]

    from libs.shared.security.oidc_federazione import DIMENSIONE_MASSIMA

    if not settings.spid_host_ammessi and settings.env not in ("development", "test"):
        # fuori sviluppo la catena si scarica solo da host fissati al deploy (rilievo 4)
        raise IdTokenNonValido("federazione senza SPID_HOST_AMMESSI")

    async def scarica(url: str) -> str:
        # niente redirect, solo 200, al più DIMENSIONE_MASSIMA byte letti dallo stream
        async with _client_http() as client, client.stream(
                "GET", url, follow_redirects=False) as resp:
            if resp.status_code != 200:
                raise CatenaNonValida(f"risposta {resp.status_code} da un indirizzo di "
                                      "federazione")
            corpo = bytearray()
            async for pezzo in resp.aiter_bytes():
                corpo.extend(pezzo)
                if len(corpo) > DIMENSIONE_MASSIMA:
                    raise CatenaNonValida("risposta di federazione troppo grande")
            return corpo.decode("utf-8", errors="replace")

    async def invia(url: str, campi: Any) -> str:
        # R16 (VD-C-173): stato dei trust mark (POST di un modulo), stessi vincoli
        async with _client_http() as client, client.stream(
                "POST", url, data=dict(campi), follow_redirects=False) as resp:
            if resp.status_code != 200:
                raise CatenaNonValida(f"risposta {resp.status_code} dallo stato del trust mark")
            corpo = bytearray()
            async for pezzo in resp.aiter_bytes():
                corpo.extend(pezzo)
                if len(corpo) > DIMENSIONE_MASSIMA:
                    raise CatenaNonValida("risposta di stato troppo grande")
            return corpo.decode("utf-8", errors="replace")

    richiesti = cfg.federazione.get("trust_marks_richiesti") or {}
    try:
        catena = await risolvi(entita, trust_anchor=str(cfg.federazione.get("trust_anchor")),
                               chiavi_trust_anchor=chiavi_fissate(
                                   cfg.federazione.get("chiavi_trust_anchor")),
                               scarica=scarica, host_ammessi=settings.spid_host_ammessi,
                               trust_marks_richiesti=richiesti if isinstance(richiesti, dict)
                               else {}, invia=invia,
                               verifica_stato_trust_marks=cfg.federazione.get(
                                   "verifica_stato_trust_marks") is True)
    except httpx.HTTPError:
        raise IdTokenNonValido("trust chain dell'OP non scaricabile") from None
    except CatenaNonValida as exc:
        raise IdTokenNonValido(f"trust chain dell'OP non valida: {exc}") from None
    jwks = catena.metadati.get("jwks") if isinstance(catena.metadati.get("jwks"), dict) \
        else catena.jwks
    if catena.metadati.get("issuer") not in (None, cfg.issuer):
        raise IdTokenNonValido("issuer dei metadati dell'OP diverso da quello configurato")
    _cache_catena[entita] = (min(time.time() + max(int(settings.spid_jwks_cache_seconds), 0),
                                 float(catena.scade)), jwks)
    return jwks


def _scegli_chiave(jwks: dict[str, Any], kid: str | None) -> jwt.PyJWK | None:
    try:
        insieme = jwt.PyJWKSet.from_dict(jwks)
    except jwt.PyJWTError as exc:
        raise IdTokenNonValido("JWKS dell'aggregatore non leggibile") from exc
    chiavi = [k for k in insieme.keys if k.public_key_use in (None, "sig")]
    if kid is None:
        # Senza kid si accetta solo un JWKS con un'unica chiave di firma
        return chiavi[0] if len(chiavi) == 1 else None
    for chiave in chiavi:
        if chiave.key_id == kid:
            return chiave
    return None


async def verifica_id_token(id_token: str, nonce_atteso: str,
                            cfg: ConfigSpid | None = None) -> dict[str, Any]:
    """
    Verifica l'id_token OIDC dell'aggregatore e restituisce i claim.
    Controlli: firma (JWKS per kid, con un nuovo scaricamento se il kid è
    sconosciuto, per la rotazione delle chiavi), iss, aud = client_id, exp, iat,
    sub, nonce = quello legato allo state di questa sessione.
    """
    if not id_token:
        raise IdTokenNonValido("id_token assente nella risposta dell'aggregatore")
    if not nonce_atteso:
        raise IdTokenNonValido("nonce della sessione assente")
    try:
        intestazione = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise IdTokenNonValido("id_token malformato") from exc
    alg = intestazione.get("alg")
    if alg not in _ALGORITMI_ID_TOKEN:
        raise IdTokenNonValido(f"algoritmo {alg!r} non ammesso")
    kid = intestazione.get("kid")
    # R10 (D08): parametri dell'aggregatore dalla console (o dalle variabili SPID_*)
    cfg = cfg or await configurazione_spid()

    jwks = await jwks_op(cfg)
    chiave = _scegli_chiave(jwks, kid)
    if chiave is None and not cfg.federazione:
        jwks = await _scarica_jwks(cfg.jwks_url, forza=True)
        chiave = _scegli_chiave(jwks, kid)
    if chiave is None:
        raise IdTokenNonValido("chiave di firma dell'id_token non presente nel JWKS")

    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            chiave.key,
            algorithms=[alg],
            audience=cfg.client_id,
            issuer=cfg.issuer,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise IdTokenNonValido("id_token scaduto") from exc
    except jwt.PyJWTError as exc:
        raise IdTokenNonValido(f"id_token non valido: {type(exc).__name__}") from exc

    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce_atteso):
        raise IdTokenNonValido("nonce non corrispondente alla sessione")
    return claims


def _spid_configurato(cfg: ConfigSpid) -> bool:
    """R10 (D08): aggregatore completo E fornitore attivo (il conservatore lo può spegnere)."""
    return cfg.configurato


# ── Identità → utente ────────────────────────────────────────────────

#: Claim in cui gli aggregatori consegnano il codice fiscale: nome dell'attributo
#: nelle specifiche OIDC SPID/CIE (URI), forme brevi usate dagli aggregatori.
CLAIM_CODICE_FISCALE = (
    "https://attributes.eid.gov.it/fiscal_number",
    "fiscal_number",
    "fiscalNumber",
    "fiscal_code",
)


def codice_fiscale_dai_claim(claims: dict[str, Any]) -> str:
    """Codice fiscale verificato dai claim dell'id_token, o CodiceFiscaleNonValido."""
    for nome in CLAIM_CODICE_FISCALE:
        valore = claims.get(nome)
        if valore:
            return valida_codice_fiscale(str(valore))
    raise CodiceFiscaleNonValido("L'identità digitale non contiene il codice fiscale")


#: R10: un utente verificato è lo stesso per ogni metodo d'accesso (app.auth.emissione)
UtenteSpid = UtenteAccesso


def _valore(enum_o_testo: Any) -> str:
    return str(getattr(enum_o_testo, "value", enum_o_testo))


async def utenti_per_codice_fiscale(codice_fiscale: str) -> list[UtenteSpid]:
    """
    Utenti che possono accedere con questa identità: stesso codice fiscale,
    utente attivo, cliente non SOSPESO/DISATTIVATO, ruolo di persona (mai
    API_SYSTEM). Ordine stabile per ragione sociale (mai «il primo» come scelta:
    con più risultati decide la persona, REGOLE §14.12).
    """
    async with get_async_session() as session:
        righe = (await session.execute(
            select(User, Tenant)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.codice_fiscale == codice_fiscale, User.attivo.is_(True))
            .order_by(Tenant.ragione_sociale, Tenant.id)
        )).all()
    utenti: list[UtenteSpid] = []
    for utente, cliente in righe:
        ruolo = _valore(utente.ruolo)
        if ruolo == "API_SYSTEM" or _valore(cliente.stato) in STATI_CLIENTE_BLOCCATI:
            continue
        utenti.append(UtenteSpid(
            user_id=str(utente.id), tenant_id=str(cliente.id), ruolo=ruolo,
            nome=utente.nome, cognome=utente.cognome,
            ragione_sociale=cliente.ragione_sociale, piano=cliente.piano,
        ))
    return utenti


async def utenti_ammessi_spid(utenti: list[UtenteAccesso]) -> list[UtenteAccesso]:
    """R10 (D08): solo gli enti che ammettono SPID/CIE fra i metodi d'accesso."""
    return [u for u in utenti if METODO_SPID_CIE in await metodi_ammessi_ente(u.tenant_id)]


async def _registra_accesso(user_id: str) -> None:
    async with get_async_session() as session:
        await session.execute(
            update(User).where(User.id == uuid.UUID(user_id)).values(ultimo_login=datetime.now(UTC))
        )


def _errore_spid(codice: str, titolo: str, dettaglio: str = "") -> dict[str, str]:
    voce = {"type": codice, "title": titolo}
    if dettaglio:
        voce["detail"] = dettaglio
    return voce


#: Esiti che tornano al frontend come #spid_errore=<codice> (catalogo per l'interfaccia).
ERRORI_RITORNO = {
    "SPID-001": "Aggregatore SPID/CIE non configurato",
    "SPID-002": "Accesso scaduto o già concluso (state non valido)",
    "SPID-003": "Accesso annullato o rifiutato presso il gestore dell'identità",
    "SPID-004": "Errore nello scambio con l'aggregatore",
    "SPID-005": "Identità digitale non verificata",
    "SPID-006": "Accesso iniziato in un altro browser o scaduto",
    "SPID-007": "Codice fiscale assente o non valido nell'identità digitale",
    "SPID-008": "Nessun utente attivo associato a questo codice fiscale",
    "SPID-009": "Livello di autenticazione insufficiente (richiesto almeno SPID L2)",
    "SPID-010": "Indirizzo di ritorno non ammesso",
    "SPID-011": "Provider non ammesso (valori: spid, cie)",
}


_ACR_LIVELLO = re.compile(r"SpidL([123])/?$")


#: Messaggio nel log quando l'id_token non porta `acr` (accesso rifiutato SPID-009).
ACR_ASSENTE = ("acr assente nell'id_token: verificare la configurazione dell'aggregatore "
               "(deve restituire il livello SPID/CIE nel claim acr)")


def livello_da_acr(acr: Any) -> int | None:
    """Livello SPID dichiarato dal gestore nell'`acr` dell'id_token verificato
    (es. «https://www.spid.gov.it/SpidL2»; la CIE usa gli stessi valori).
    None se assente o non riconosciuto."""
    valori = acr if isinstance(acr, list) else [acr]
    livelli = [int(m.group(1)) for v in valori if isinstance(v, str)
               and (m := _ACR_LIVELLO.search(v.strip()))]
    return min(livelli) if livelli else None


def _livello_minimo(cfg: ConfigSpid | None = None) -> int:
    if cfg is not None:
        return max(2, int(cfg.livello_minimo))
    return max(2, int(settings.spid_livello_minimo))


def _ritorno(redirect_uri: str, **frammento: str) -> RedirectResponse:
    """Redirect 303 al frontend con i parametri nel frammento (#), mai in query."""
    return RedirectResponse(
        url=f"{redirect_uri}#{urllib.parse.urlencode(frammento)}", status_code=303
    )


async def _completa_login(
    state_data: dict[str, Any], codice_fiscale: str, metodo: str
) -> RedirectResponse:
    """Identità verificata → controllo utenti → codice monouso → ritorno al frontend."""
    redirect_uri = str(state_data["redirect_uri"])
    utenti = await utenti_per_codice_fiscale(codice_fiscale)
    if not utenti:
        logger.warning("spid_utente_non_associato", provider=state_data.get("provider"),
                       codice_fiscale=maschera_codice_fiscale(codice_fiscale))
        return _ritorno(redirect_uri, spid_errore="SPID-008")
    codice = await _nuovo_codice({
        "codice_fiscale": codice_fiscale,
        "provider": state_data.get("provider", "spid"),
        "livello": state_data.get("livello", 2),
        "metodo": metodo,
        "sessione_hash": state_data.get("sessione_hash", ""),
    })
    logger.info("spid_identita_verificata", provider=state_data.get("provider"),
                utenti=len(utenti), codice_fiscale=maschera_codice_fiscale(codice_fiscale))
    return _ritorno(redirect_uri, spid_codice=codice)


# ── Endpoint ────────────────────────────────────────────────────────

@router.get("/login", summary="Avvia flusso SPID/CIE")
async def spid_login(
    request: Request,
    provider: str = Query("spid", description="spid | cie"),
    redirect_uri: str = Query(..., description="URI di ritorno del frontend (whitelist)"),
    livello: int = Query(2, ge=1, le=3, description="Livello SPID richiesto (1/2/3); "
                         "mai sotto il minimo configurato (SPID_LIVELLO_MINIMO, almeno 2)"),
) -> RedirectResponse:
    """Avvia il flusso di autenticazione SPID o CIE tramite aggregatore.

    Revisione B3: il livello richiesto al gestore non scende sotto il minimo;
    quello che conta è l'`acr` dell'id_token verificato (callback)."""
    cfg = await configurazione_spid()
    livello = max(livello, _livello_minimo(cfg))
    # g4-chiusura: errori di avvio come redirect alla pagina di accesso, non JSON
    try:
        _validate_redirect_uri(redirect_uri)
    except RitornoNonAmmesso:
        return _ritorno_login("SPID-010")
    if provider not in PROVIDER_AMMESSI:
        logger.warning("spid_provider_non_ammesso", provider=provider[:32])
        return _ritorno_login("SPID-011")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    sessione = secrets.token_urlsafe(32)

    if _mock_abilitato():
        await _set_state(state, {
            "provider": provider, "redirect_uri": redirect_uri, "livello": livello,
            "nonce": nonce, "sessione_hash": _hash_sessione(sessione),
        })
        mock_url = (
            f"/api/v1/auth/spid/mock-callback"
            f"?state={state}&provider={provider}&livello={livello}"
        )
        risposta = RedirectResponse(url=mock_url)
        _imposta_cookie(risposta, sessione, request)
        return risposta

    if not _spid_configurato(cfg):
        # Indirizzo di ritorno già verificato: l'interfaccia mostra l'errore per esteso.
        logger.warning("spid_aggregatore_non_configurato")
        return _ritorno(redirect_uri, spid_errore="SPID-001")

    verifier, challenge = oidc.coppia_pkce()
    await _set_state(state, {
        "provider": provider, "redirect_uri": redirect_uri, "livello": livello,
        "nonce": nonce, "sessione_hash": _hash_sessione(sessione), "pkce": verifier,
    })
    params: dict[str, Any] = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "scope": "openid profile fiscal_code",
        "state": state,
        "nonce": nonce,
        "redirect_uri": f"{settings.base_url}/api/v1/auth/spid/callback",
        "acr_values": f"https://www.spid.gov.it/SpidL{livello}",
        "provider": provider,
        # R13b (VD-C-069): PKCE S256 sempre (RFC 7636; obbligatorio in SPID/CIE OIDC)
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if cfg.prompt:
        params["prompt"] = cfg.prompt
    if cfg.attributi_da_userinfo:
        params["claims"] = oidc.richiesta_attributi()
    if cfg.request_object:
        try:
            firmato = oidc.oggetto_richiesta(
                params, client_id=cfg.client_id, audience=cfg.issuer,
                chiave=_chiave_cliente(), kid=settings.spid_chiave_client_kid)
        except oidc.ErroreOidc as exc:
            logger.error("spid_request_object_non_firmato", motivo=str(exc))
            return _ritorno(redirect_uri, spid_errore="SPID-001")
        params = {"client_id": cfg.client_id, "response_type": "code",
                  "scope": params["scope"], "request": firmato}
    auth_url = f"{cfg.url_authorize}?{urllib.parse.urlencode(params)}"
    risposta = RedirectResponse(url=auth_url)
    _imposta_cookie(risposta, sessione, request)
    return risposta


@router.get(
    "/callback",
    summary="Callback SPID/CIE — verifica l'identità e torna al frontend con un codice monouso",
)
async def spid_callback(
    request: Request,
    state: str = Query(...),
    code: str | None = Query(None),
    error: str | None = Query(None, description="Errore OIDC dell'aggregatore"),
) -> RedirectResponse:
    """Riceve il code dall'aggregatore, lo scambia e VERIFICA l'id_token."""
    state_data = await _get_state(state)
    if state_data is None:
        # Senza state non si conosce un indirizzo di ritorno verificato: si torna
        # alla pagina di accesso del frontend (BASE_URL/login).
        logger.warning("spid_state_non_valido")
        return _ritorno_login("SPID-002")
    redirect_uri = str(state_data.get("redirect_uri", ""))

    if not _sessione_corrisponde(request, str(state_data.get("sessione_hash", ""))):
        logger.warning("spid_state_sessione_non_corrispondente")
        return _ritorno(redirect_uri, spid_errore="SPID-006")

    if error or not code:
        logger.info("spid_annullato_o_rifiutato", errore=(error or "code assente")[:64])
        return _ritorno(redirect_uri, spid_errore="SPID-003")

    cfg = await configurazione_spid()
    if not _spid_configurato(cfg):
        return _ritorno(redirect_uri, spid_errore="SPID-001")

    try:
        token_data = await scambia_codice(cfg, code, str(state_data.get("pkce") or ""))
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("spid_scambio_token_fallito", errore=type(exc).__name__,
                       motivo=str(exc)[:120] if isinstance(exc, oidc.ErroreOidc) else None)
        return _ritorno(redirect_uri, spid_errore="SPID-004")

    try:
        claims = await verifica_id_token(
            str(token_data.get("id_token", "")), str(state_data.get("nonce", "")), cfg
        )
    except (IdTokenNonValido, httpx.HTTPError) as exc:
        logger.warning("spid_id_token_rifiutato", motivo=str(exc))
        return _ritorno(redirect_uri, spid_errore="SPID-005")

    # Revisione B3: livello dall'`acr` dell'id_token verificato, minimo L2.
    # acr assente: stessa risposta (SPID-009, politica invariata) ma con un
    # messaggio esplicito per chi esercisce: è un problema di configurazione
    # dell'aggregatore, non dell'utente (revisione R3).
    acr = claims.get("acr")
    if acr is None or (isinstance(acr, (str, list)) and not acr):
        logger.error("spid_acr_assente",
                     messaggio=ACR_ASSENTE,
                     minimo=_livello_minimo(cfg))
        return _ritorno(redirect_uri, spid_errore="SPID-009")
    livello_verificato = livello_da_acr(acr)
    if livello_verificato is None or livello_verificato < _livello_minimo(cfg):
        logger.warning("spid_livello_insufficiente", acr=str(claims.get("acr"))[:80],
                       minimo=_livello_minimo(cfg))
        return _ritorno(redirect_uri, spid_errore="SPID-009")

    if cfg.attributi_da_userinfo:
        # R13b (VD-C-069): attributi dallo UserInfo (firmato, eventualmente cifrato),
        # con lo stesso sub dell'id_token; il livello resta quello dell'id_token
        try:
            attributi = await leggi_userinfo(cfg, str(token_data.get("access_token") or ""),
                                             str(claims.get("sub") or ""))
        except (httpx.HTTPError, oidc.ErroreOidc, IdTokenNonValido) as exc:
            logger.warning("spid_userinfo_rifiutato", motivo=str(exc)[:160])
            return _ritorno(redirect_uri, spid_errore="SPID-005")
        claims = {**attributi, **{k: claims[k] for k in ("iss", "aud", "sub", "acr", "nonce")
                                  if k in claims}}
    try:
        codice_fiscale = codice_fiscale_dai_claim(claims)
    except CodiceFiscaleNonValido as exc:
        logger.warning("spid_codice_fiscale_non_valido", motivo=str(exc))
        return _ritorno(redirect_uri, spid_errore="SPID-007")

    provider = str(state_data.get("provider", "spid"))
    return await _completa_login({**state_data, "livello": livello_verificato},
                                 codice_fiscale, provider)


def _chiave_cliente() -> Any:
    pem = settings.spid_chiave_privata_client.get_secret_value().strip()
    if not pem or not settings.spid_chiave_client_kid.strip():
        raise oidc.ErroreOidc("SPID_CHIAVE_PRIVATA_CLIENT o SPID_CHIAVE_CLIENT_KID assenti")
    return oidc.chiave_privata(pem)


async def scambia_codice(cfg: ConfigSpid, code: str, verifier: str) -> dict[str, Any]:
    """Scambio del codice all'endpoint del token con l'autenticazione del cliente scelta
    in console (client_secret_post, client_secret_basic, private_key_jwt) e il
    code_verifier PKCE. Il segreto va SOLO a un host fissato al deploy."""
    if not oidc.host_ammesso(cfg.url_token, settings.spid_host_ammessi):
        raise oidc.ErroreOidc("endpoint del token fuori da SPID_HOST_AMMESSI: segreto non "
                              "inviato")
    dati = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": f"{settings.base_url}/api/v1/auth/spid/callback",
            "client_id": cfg.client_id}
    if verifier:
        dati["code_verifier"] = verifier
    autenticazione: tuple[str, str] | None = None
    segreto = settings.spid_client_secret.get_secret_value()
    if cfg.metodo_autenticazione == "private_key_jwt":
        dati["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        dati["client_assertion"] = oidc.asserzione_cliente(
            client_id=cfg.client_id, audience=cfg.url_token, chiave=_chiave_cliente(),
            kid=settings.spid_chiave_client_kid)
    elif cfg.metodo_autenticazione == "client_secret_basic":
        # RFC 6749 §2.3.1: client_id e segreto codificati form-urlencoded
        autenticazione = (urllib.parse.quote(cfg.client_id, safe=""),
                          urllib.parse.quote(segreto, safe=""))
    else:
        dati["client_secret"] = segreto
    async with _client_http() as client:
        resp = await client.post(cfg.url_token, data=dati, auth=autenticazione)
        resp.raise_for_status()
        corpo = resp.json()
    if not isinstance(corpo, dict):
        raise oidc.ErroreOidc("risposta del token non è un oggetto")
    return corpo


async def leggi_userinfo(cfg: ConfigSpid, access_token: str, sub: str) -> dict[str, Any]:
    if not access_token:
        raise oidc.ErroreOidc("access_token assente: UserInfo non leggibile")
    if not oidc.host_ammesso(cfg.url_userinfo, settings.spid_host_ammessi):
        raise oidc.ErroreOidc("endpoint UserInfo fuori da SPID_HOST_AMMESSI")
    async with _client_http() as client:
        resp = await client.get(cfg.url_userinfo,
                                headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
    pem = settings.spid_chiave_privata_client.get_secret_value().strip()
    return oidc.decodifica_userinfo(
        resp.content, resp.headers.get("content-type", ""), jwks=await jwks_op(cfg),
        issuer=cfg.issuer, client_id=cfg.client_id, sub_id_token=sub,
        chiave_decifratura=oidc.chiave_privata(pem) if pem else None)


class RichiestaScambio(BaseModel):
    """Scambio del codice monouso ricevuto al ritorno da SPID/CIE."""

    model_config = ConfigDict(extra="forbid")
    codice: str = Field(..., min_length=20, max_length=128,
                        description="Codice monouso dal frammento #spid_codice (120 s)")
    tenant_id: uuid.UUID | None = Field(
        None, description="Ente scelto, quando la persona è utente di più enti (REGOLE §14.12)")


@router.post("/scambio", summary="Scambia il codice monouso SPID/CIE con il token di sessione")
async def spid_scambio(request: Request, body: RichiestaScambio) -> JSONResponse:
    """
    Esiti (sempre 200 con `esito`, o un errore HTTP con codice SPID-0xx):
      - «accesso»: dati dell'utente; il token è nel cookie HttpOnly ``vd_sessione``
        (con ``vd_csrf`` per il doppio invio anti-CSRF), mai nel corpo (VD-C-079);
      - «scelta_ente»: la persona è utente di più enti → elenco e nuovo codice.
    """
    dati = await _consuma_codice(body.codice)
    if dati is None:
        raise HTTPException(400, _errore_spid(
            "SPID-012", "Codice di accesso non valido, già usato o scaduto",
            "Ripeti l'accesso con SPID o CIE."))
    if not _sessione_corrisponde(request, str(dati.get("sessione_hash", ""))):
        logger.warning("spid_scambio_sessione_non_corrispondente")
        raise HTTPException(400, _errore_spid(
            "SPID-006", "Accesso iniziato in un altro browser o scaduto",
            "Ripeti l'accesso con SPID o CIE da questo browser."))

    codice_fiscale = str(dati.get("codice_fiscale", ""))
    utenti = await utenti_ammessi_spid(await utenti_per_codice_fiscale(codice_fiscale))
    if body.tenant_id is not None:
        utenti = [u for u in utenti if u.tenant_id == str(body.tenant_id)]
    if not utenti:
        # Nessun dettaglio sull'ente scelto: non si rivela se esiste (§14.4, §14.14).
        raise HTTPException(403, _errore_spid(
            "SPID-008", ERRORI_RITORNO["SPID-008"],
            "L'identità è stata verificata, ma nessun ente attivo ha un utente attivo "
            "con questo codice fiscale."))
    if len(utenti) > 1:
        nuovo = await _nuovo_codice(dati)
        return JSONResponse({
            "esito": "scelta_ente",
            "codice": nuovo,
            "scadenza_secondi": _CODICE_TTL,
            "enti": [{"tenant_id": u.tenant_id, "ragione_sociale": u.ragione_sociale,
                      "ruolo": u.ruolo} for u in utenti],
        })

    utente = utenti[0]
    risposta = await emetti_accesso(request, utente, metodo=str(dati.get("metodo", "spid")),
                                    extra={"spid_level": int(dati.get("livello", 2))},
                                    registra=_registra_accesso)
    risposta.delete_cookie(COOKIE_SESSIONE, path=_COOKIE_PATH)
    return risposta


@router.get(
    "/mock-callback",
    summary="Mock SPID callback (solo sviluppo)",
    include_in_schema=False,
)
async def spid_mock_callback(
    request: Request,
    state: str = Query(...),
    provider: str = Query("spid"),
    livello: int = Query(2),
    cf: str = Query("FTZPRV00A01Z999V", description="Codice fiscale simulato"),
) -> RedirectResponse:
    """Mock SPID: solo con VERUMDOC_DEV_AUTH=1 e ENV development/test.
    Simula SOLO la verifica dell'identità: l'associazione all'utente, il codice
    monouso e lo scambio sono quelli del login reale."""
    if not _mock_abilitato():
        raise HTTPException(404)

    state_data = await _get_state(state)
    if state_data is None:
        return _ritorno_login("SPID-002")
    redirect_uri = str(state_data.get("redirect_uri", ""))
    if not _sessione_corrisponde(request, str(state_data.get("sessione_hash", ""))):
        return _ritorno(redirect_uri, spid_errore="SPID-006")
    # Il mock simula l'`acr` del gestore con `livello`: stessa soglia del reale (B3)
    if livello < _livello_minimo():
        return _ritorno(redirect_uri, spid_errore="SPID-009")
    try:
        codice_fiscale = valida_codice_fiscale(cf)
    except CodiceFiscaleNonValido:
        return _ritorno(redirect_uri, spid_errore="SPID-007")
    return await _completa_login({**state_data, "livello": livello}, codice_fiscale,
                                 f"{provider}_mock")


@router.get("/providers", summary="Lista provider SPID disponibili")
async def list_providers() -> JSONResponse:
    """Lista degli Identity Provider SPID configurati."""
    providers = [
        {"id": "poste", "nome": "Poste Italiane", "logo": "poste.png"},
        {"id": "infocert", "nome": "InfoCert", "logo": "infocert.png"},
        {"id": "sielte", "nome": "Sielte", "logo": "sielte.png"},
        {"id": "aruba", "nome": "ArubaPEC", "logo": "aruba.png"},
        {"id": "namirialtsp", "nome": "Namirial", "logo": "namirial.png"},
        {"id": "tim", "nome": "TIM id", "logo": "tim.png"},
        {"id": "lepida", "nome": "Lepida ID", "logo": "lepida.png"},
        {
            "id": "cie",
            "nome": "CIE — Carta d'Identità Elettronica",
            "logo": "cie.png",
            "tipo": "cie",
        },
    ]
    return JSONResponse({"providers": providers, "count": len(providers)})
