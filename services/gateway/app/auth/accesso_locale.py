# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/accesso_locale.py — accesso locale e attivazione da invito (R10, D08).

Riferimenti: decisione D08 (VD-C-069: accesso delle persone senza SPID);
REGOLE_SVILUPPO §19.1, §19.3, §19.7 (sessioni), §14.14 (nessun dato nei
messaggi), §12 (ogni stato intermedio ha un'uscita).

Rotte (pubbliche: l'identità si verifica qui):
  GET  /api/v1/auth/metodi                    metodi abilitati dal conservatore (pulsanti
                                              della pagina d'accesso; nessun dato di enti)
  POST /api/v1/auth/locale/accesso            e-mail e password → passaggio del secondo fattore
  POST /api/v1/auth/locale/secondo-fattore    codice a tempo (o di recupero) → sessione
  POST /api/v1/auth/passkey/opzioni           sfida WebAuthn (passkey individuabili)
  POST /api/v1/auth/passkey/accesso           firma della passkey → sessione
  POST /api/v1/auth/inviti/verifica           collegamento + e-mail → passaggio d'attivazione
  POST /api/v1/auth/inviti/totp               segreto del codice a tempo da registrare nell'app
  POST /api/v1/auth/inviti/passkey/opzioni    opzioni di registrazione della passkey
  POST /api/v1/auth/inviti/completa           crea le credenziali, attiva l'utente, consuma l'invito

Sicurezza:
  - nessuna enumerazione: e-mail sconosciuta, password errata e account bloccato
    danno lo STESSO errore (ACC-401) con lo stesso costo (bcrypt fittizio); un
    collegamento d'invito non valido, scaduto, usato o con un'e-mail diversa dà
    sempre ACC-404-INVITO;
  - limitazione dei tentativi: per account (``ACCESSO_TENTATIVI_MASSIMI`` sbagliati →
    blocco per ``ACCESSO_BLOCCO_MINUTI``, con voce d'audit) e per indirizzo IP REALE
    del cliente (revisione R10, V1: ``X-Forwarded-For`` letto solo dai proxy fidati
    ``PROXY_FIDATI``; contano solo i FALLIMENTI, ``ACCESSO_FALLIMENTI_IP_15_MINUTI``;
    la sfida della passkey ha un limite anonimo separato più largo); per invito
    (e-mail sbagliate → invito revocato);
  - ogni passaggio intermedio (secondo fattore, sfida della passkey, attivazione)
    sta in Redis con scadenza breve, legato al browser da un cookie HttpOnly
    (``vd_preaccesso``, SameSite=Strict) di cui Redis conserva solo l'impronta;
  - l'invito si consuma nella STESSA transazione che crea le credenziali e attiva
    l'utente (UPDATE condizionato sullo stato IN_ATTESA e sulla scadenza): due
    completamenti concorrenti non attivano due volte.
Uscite pulite: passaggio scaduto → si ricomincia (l'invito resta in attesa fino
alla sua scadenza); invito revocato o utente disattivato prima della fine → il
completamento fallisce senza scrivere nulla; sessione non emessa dopo
l'attivazione (es. Redis giù) → l'utente è attivo e accede normalmente.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth import credenziali as cr
from app.auth.emissione import UtenteAccesso, emetti_accesso
from app.auth.fornitori import leggi_fornitori, metodi_ammessi_ente, metodi_ammessi_servizio
from app.auth.ruoli import STATI_CLIENTE_BLOCCATI
from app.config import login_sviluppo_consentito, settings
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.security.audit import registra_audit
from libs.shared.security.ip_cliente import ip_cliente
from libs.verumdoc_rules.parametri_servizio import (
    ACCESSO_BLOCCO_MINUTI,
    ACCESSO_TENTATIVI_MASSIMI,
    INVITO_VALIDITA_ORE,
    METODO_PASSKEY,
    METODO_PASSWORD_TOTP,
    METODO_SPID_CIE,
    PASSWORD_LUNGHEZZA_MINIMA,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["Accesso locale e inviti"])

COOKIE_PREACCESSO = "vd_preaccesso"
_COOKIE_PATH = "/api/v1/auth"
PASSAGGIO_TTL = 300          # secondo fattore e sfida della passkey: 5 minuti
ATTIVAZIONE_TTL = 900        # attivazione guidata: 15 minuti
FINESTRA_IP = 900
_PREFISSO = "acc:"
_TOKEN_INVITO = re.compile(r"^([0-9a-f]{32})\.([A-Za-z0-9_-]{32,64})$")


# ── Stato temporaneo in Redis (stesso client dello state SPID) ───────

async def _redis() -> Any:
    from app.auth import spid

    return await spid._get_redis()


def _impronta(valore: str) -> str:
    return hashlib.sha256(valore.encode("utf-8")).hexdigest()


async def _salva(tipo: str, dati: dict[str, Any], ttl: int, *, chiave: str | None = None) -> str:
    chiave = chiave or secrets.token_urlsafe(32)
    r = await _redis()
    try:
        await r.setex(f"{_PREFISSO}{tipo}:{_impronta(chiave)}", ttl, json.dumps(dati))
    finally:
        await r.aclose()
    return chiave


async def _leggi(tipo: str, chiave: str, *, consuma: bool) -> dict[str, Any] | None:
    if not chiave or len(chiave) > 128:
        return None
    r = await _redis()
    try:
        nome = f"{_PREFISSO}{tipo}:{_impronta(chiave)}"
        grezzo = await (r.getdel(nome) if consuma else r.get(nome))
    finally:
        await r.aclose()
    return json.loads(grezzo) if grezzo else None


#: Esiti che contano come TENTATIVO SBAGLIATO per l'indirizzo (revisione R10, V1):
#: credenziali o codici errati, passkey rifiutata, invito non valido, passaggio falso
FALLIMENTI = frozenset({"ACC-401", "ACC-400-PASSKEY", "ACC-404-INVITO", "ACC-422-CODICE",
                        "ACC-400-PASSAGGIO"})


def ip_della_richiesta(request: Request) -> str:
    """IP reale del cliente dietro l'ALB (``PROXY_FIDATI``), mai dall'intestazione di chiunque."""
    return ip_cliente(request.client.host if request.client else None, request.headers,
                      settings.proxy_fidati)


def limite_ip_attivo() -> bool:
    """Revisione R10 (V1): senza proxy fidati configurati, fuori da sviluppo e test,
    l'indirizzo della connessione è quello del bilanciatore (uguale per tutti): il
    limite per IP diventerebbe un blocco globale, quindi NON si applica (resta il
    blocco per account); l'avvio lo segnala nel log (``app.main``)."""
    return bool(settings.proxy_fidati) or login_sviluppo_consentito(settings.env)


#: R12b (revisione): INCR e scadenza in UN passo (script Lua, atomico in Redis). Prima
#: erano due comandi: un errore fra INCR ed EXPIRE lasciava la chiave SENZA scadenza
#: (blocco permanente). Una chiave rimasta senza scadenza la riceve al primo INCR.
SCRIPT_INCR_CON_SCADENZA = (
    "-- vd:incr_con_scadenza\n"
    "local n = redis.call('INCR', KEYS[1])\n"
    "if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end\n"
    "return n")


async def incr_con_scadenza(r: Any, nome: str, secondi: int) -> int:
    """INCR atomico con la scadenza garantita (``SCRIPT_INCR_CON_SCADENZA``)."""
    return int(await r.eval(SCRIPT_INCR_CON_SCADENZA, 1, nome, int(secondi)))


async def _contatore(tipo: str, request: Request, *, incrementa: bool) -> int:
    nome = f"{_PREFISSO}ip:{tipo}:{_impronta(ip_della_richiesta(request))}"
    r = await _redis()
    try:
        if not incrementa:
            return int(await r.get(nome) or 0)
        return await incr_con_scadenza(r, nome, FINESTRA_IP)
    finally:
        await r.aclose()


async def _restituisci(tipo: str, request: Request) -> None:
    """Restituisce un tentativo riservato (DECR). Se nel frattempo la chiave è
    scaduta, il DECR la ricrea negativa e senza scadenza: la si toglie."""
    nome = f"{_PREFISSO}ip:{tipo}:{_impronta(ip_della_richiesta(request))}"
    r = await _redis()
    try:
        if int(await r.decr(nome)) < 0:
            await r.delete(nome)
    finally:
        await r.aclose()


async def _riserva_tentativo_ip(request: Request) -> bool:
    """Revisione R10 (U1): riserva ATOMICAMENTE un tentativo (INCR) PRIMA della
    rotta, così richieste in parallelo non leggono tutte lo stesso valore sotto il
    limite. Oltre il limite: restituisce la riserva e 429. True se ha riservato."""
    if not limite_ip_attivo():
        return False
    if await _contatore("fallimenti", request, incrementa=True) \
            > settings.accesso_fallimenti_ip_15_minuti:
        await _restituisci("fallimenti", request)
        logger.warning("accesso_locale_troppi_fallimenti_ip")
        raise ErroreApi("ACC-429", headers={"Retry-After": str(FINESTRA_IP)})
    return True


async def _conta_opzioni_passkey(request: Request) -> None:
    """Limite anonimo separato e più largo per la sola sfida della passkey (nessun
    segreto: limita il carico, non indovina credenziali)."""
    if limite_ip_attivo() and await _contatore("opzioni", request, incrementa=True) \
            > settings.accesso_opzioni_passkey_ip_15_minuti:
        raise ErroreApi("ACC-429", headers={"Retry-After": str(FINESTRA_IP)})


def sorvegliato(funzione: Any) -> Any:
    """Rotta d'accesso: ogni richiesta riserva un tentativo dell'IP prima di
    eseguire (429 oltre il limite); solo un esito in ``FALLIMENTI`` lo tiene, ogni
    altro esito (riuscito, metodo non ammesso, errore) lo restituisce."""
    @functools.wraps(funzione)
    async def _rotta(*args: Any, **kwargs: Any) -> Any:
        request: Request = kwargs["request"]
        riservato = await _riserva_tentativo_ip(request)
        fallito = False
        try:
            return await funzione(*args, **kwargs)
        except ErroreApi as exc:
            fallito = exc.codice in FALLIMENTI
            raise
        finally:
            if riservato and not fallito:
                await _restituisci("fallimenti", request)
    return _rotta


def _sicuro(request: Request) -> bool:
    return not (login_sviluppo_consentito(settings.env) and request.url.scheme == "http")


def _con_cookie(risposta: JSONResponse, request: Request, valore: str, ttl: int) -> JSONResponse:
    risposta.set_cookie(COOKIE_PREACCESSO, valore, max_age=ttl, path=_COOKIE_PATH,
                        httponly=True, secure=_sicuro(request), samesite="strict")
    return risposta


def _browser(request: Request) -> str:
    """Impronta del cookie del browser che ha iniziato il passaggio (vuota se manca)."""
    valore = request.cookies.get(COOKIE_PREACCESSO, "")
    return _impronta(valore) if valore else ""


def _stesso_browser(request: Request, dati: dict[str, Any]) -> bool:
    atteso = str(dati.get("browser") or "")
    return bool(atteso) and hmac.compare_digest(_browser(request), atteso)


async def _parametri(tenant_id: Any) -> dict[str, Any]:
    from libs.shared.parametri import parametri

    return await parametri([ACCESSO_TENTATIVI_MASSIMI, ACCESSO_BLOCCO_MINUTI,
                            PASSWORD_LUNGHEZZA_MINIMA, INVITO_VALIDITA_ORE], tenant_id)


def _errore_db(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("ACC-503", exc, evento=evento)


def _non_disponibile(exc: Exception) -> ErroreApi:
    logger.error("accesso_locale_credenziali_non_disponibili", motivo=str(exc))
    return ErroreApi("ACC-503", "Accesso con codice a tempo non disponibile in questo ambiente.")


# ── Metodi disponibili ───────────────────────────────────────────────

@router.get("/metodi", summary="Metodi d'accesso abilitati dal conservatore")
async def metodi() -> dict[str, Any]:
    """Quali pulsanti mostrare nella pagina d'accesso. Il controllo per ente si
    fa all'accesso (nessuna informazione sugli enti qui)."""
    return {"metodi": list(await metodi_ammessi_servizio())}


# ── Utenti candidati ─────────────────────────────────────────────────

_SQL_UTENTE = (
    "SELECT u.id::text AS user_id, u.tenant_id::text AS tenant_id, u.ruolo::text AS ruolo, "
    "       u.nome, u.cognome, u.email, t.ragione_sociale, t.piano, t.stato::text AS stato_ente "
    "FROM public.users u JOIN public.tenants t ON t.id = u.tenant_id "
    "WHERE u.attivo AND u.ruolo::text <> 'API_SYSTEM' ")


def _utente(riga: Any) -> UtenteAccesso:
    return UtenteAccesso(user_id=riga["user_id"], tenant_id=riga["tenant_id"],
                         ruolo=riga["ruolo"], nome=riga["nome"], cognome=riga["cognome"],
                         ragione_sociale=riga["ragione_sociale"], piano=riga["piano"])


async def _utenti_con_password(email: str) -> list[dict[str, Any]]:
    async with get_async_session() as session:
        righe = (await session.execute(text(
            _SQL_UTENTE.replace("SELECT ", "SELECT p.impronta, p.tentativi_falliti, "
                                "p.bloccato_fino_a, ", 1)
            .replace("FROM public.users u", "FROM public.users u JOIN "
                     "public.credenziali_password p ON p.user_id = u.id", 1)
            + "AND lower(u.email) = :e ORDER BY t.ragione_sociale, t.id LIMIT 10"),
            {"e": email})).mappings().all()
    return [dict(r) for r in righe if r["stato_ente"] not in STATI_CLIENTE_BLOCCATI]


async def _fallimento(user_id: str, tenant_id: str, motivo: str) -> None:
    """Un tentativo sbagliato; al limite, blocco temporaneo con voce d'audit."""
    par = await _parametri(tenant_id)
    massimi, minuti = int(par[ACCESSO_TENTATIVI_MASSIMI]), int(par[ACCESSO_BLOCCO_MINUTI])
    with ambito_ente(tenant_id):
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "UPDATE public.credenziali_password SET tentativi_falliti = tentativi_falliti + 1, "
                " bloccato_fino_a = CASE WHEN tentativi_falliti + 1 >= :m "
                "   THEN now() + make_interval(mins => :b) ELSE bloccato_fino_a END "
                "WHERE user_id = CAST(:u AS uuid) RETURNING tentativi_falliti"),
                {"u": user_id, "m": massimi, "b": minuti})).first()
            if riga is not None and int(riga[0]) == massimi:
                await registra_audit(session, evento="accesso.bloccato", tenant_id=tenant_id,
                                     user_id=user_id, dettagli={"motivo": motivo,
                                                                "minuti": minuti})
    logger.info("accesso_locale_tentativo_errato", motivo=motivo)


async def _azzera_tentativi(session: Any, user_id: str) -> None:
    await session.execute(text(
        "UPDATE public.credenziali_password SET tentativi_falliti = 0, bloccato_fino_a = NULL "
        "WHERE user_id = CAST(:u AS uuid)"), {"u": user_id})


def _bloccato(riga: dict[str, Any]) -> bool:
    fino = riga.get("bloccato_fino_a")
    return fino is not None and fino > datetime.now(UTC)


# ── Password + codice a tempo ────────────────────────────────────────

class RichiestaAccesso(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=200)


@router.post("/locale/accesso", summary="Accesso con e-mail e password (primo passo)")
@sorvegliato
async def accesso_password(request: Request, body: RichiestaAccesso) -> JSONResponse:
    """Esiti: «secondo_fattore» con il passaggio (5 minuti) e, se la persona è
    utente di più enti, l'elenco degli enti; altrimenti ACC-401 (sempre lo stesso)."""
    email = body.email.strip().lower()
    try:
        candidati = await _utenti_con_password(email)
    except SQLAlchemyError as exc:
        raise _errore_db("accesso_password_errore", exc) from None
    if not candidati:
        cr.verifica_password(body.password, None)  # stesso costo: nessuna enumerazione
        raise ErroreApi("ACC-401")
    validi = []
    for riga in candidati:
        if cr.verifica_password(body.password, riga["impronta"]) and not _bloccato(riga):
            validi.append(riga)
        elif not _bloccato(riga):
            await _fallimento(riga["user_id"], riga["tenant_id"], "password")
    ammessi = [r for r in validi
               if METODO_PASSWORD_TOTP in await metodi_ammessi_ente(r["tenant_id"])]
    if not validi:
        raise ErroreApi("ACC-401")
    if not ammessi:
        raise ErroreApi("ACC-403-METODO")
    browser = secrets.token_urlsafe(32)
    passaggio = await _salva("2fa", {"utenti": [r["user_id"] for r in ammessi],
                                     "browser": _impronta(browser)}, PASSAGGIO_TTL)
    corpo: dict[str, Any] = {"esito": "secondo_fattore", "passaggio": passaggio,
                             "scadenza_secondi": PASSAGGIO_TTL}
    if len(ammessi) > 1:
        corpo["enti"] = [{"tenant_id": r["tenant_id"], "ragione_sociale": r["ragione_sociale"]}
                         for r in ammessi]
    return _con_cookie(JSONResponse(corpo), request, browser, PASSAGGIO_TTL)


class RichiestaSecondoFattore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passaggio: str = Field(..., min_length=20, max_length=128)
    codice: str | None = Field(None, max_length=10, description="Codice a tempo a 6 cifre")
    codice_recupero: str | None = Field(None, max_length=40)
    tenant_id: uuid.UUID | None = None


@router.post("/locale/secondo-fattore", summary="Codice a tempo o di recupero → sessione")
@sorvegliato
async def secondo_fattore(request: Request, body: RichiestaSecondoFattore) -> JSONResponse:
    dati = await _leggi("2fa", body.passaggio, consuma=True)
    if dati is None or not _stesso_browser(request, dati):
        raise ErroreApi("ACC-400-PASSAGGIO")
    utenti = [str(u) for u in dati.get("utenti") or []]
    if body.tenant_id is not None or len(utenti) > 1:
        scelto = str(body.tenant_id or "")
        utenti = [u for u in utenti if await _ente_utente(u) == scelto]
    if len(utenti) != 1 or not (body.codice or body.codice_recupero):
        raise ErroreApi("ACC-400-PASSAGGIO", "Indicare l'ente e il codice.")
    user_id = utenti[0]
    try:
        utente = await _verifica_secondo_fattore(user_id, body)
    except cr.CredenzialiNonDisponibili as exc:
        raise _non_disponibile(exc) from None
    except SQLAlchemyError as exc:
        raise _errore_db("secondo_fattore_errore", exc) from None
    if utente is None:
        raise ErroreApi("ACC-401")
    return await emetti_accesso(request, utente, metodo="password_totp")


async def _ente_utente(user_id: str) -> str:
    async with get_async_session() as session:
        return str((await session.execute(text(
            "SELECT tenant_id::text FROM public.users WHERE id = CAST(:u AS uuid)"),
            {"u": user_id})).scalar_one_or_none() or "")


async def _verifica_secondo_fattore(user_id: str, body: RichiestaSecondoFattore
                                    ) -> UtenteAccesso | None:
    async with get_async_session() as session:
        riga = (await session.execute(text(
            _SQL_UTENTE.replace("SELECT ", "SELECT p.bloccato_fino_a, s.segreto_cifrato, "
                                "s.ultimo_passo, ", 1)
            .replace("FROM public.users u", "FROM public.users u JOIN "
                     "public.credenziali_password p ON p.user_id = u.id JOIN "
                     "public.credenziali_totp s ON s.user_id = u.id", 1)
            + "AND u.id = CAST(:u AS uuid) FOR UPDATE OF p, s"), {"u": user_id})
        ).mappings().one_or_none()
        if riga is None or riga["stato_ente"] in STATI_CLIENTE_BLOCCATI or _bloccato(riga):
            return None
        if METODO_PASSWORD_TOTP not in await metodi_ammessi_ente(riga["tenant_id"]):
            return None
        ok = False
        if body.codice:
            segreto = cr.decifra_segreto(riga["segreto_cifrato"], user_id)
            passo = cr.passo_totp_valido(segreto, body.codice,
                                         ultimo_passo=int(riga["ultimo_passo"]))
            if passo is not None:
                await session.execute(text(
                    "UPDATE public.credenziali_totp SET ultimo_passo = :p "
                    "WHERE user_id = CAST(:u AS uuid) AND ultimo_passo < :p"),
                    {"u": user_id, "p": passo})
                ok = True
        elif body.codice_recupero:
            usato = (await session.execute(text(
                "UPDATE public.codici_recupero SET usato_il = now() "
                "WHERE user_id = CAST(:u AS uuid) AND impronta = :i AND usato_il IS NULL "
                "RETURNING id"), {"u": user_id,
                                  "i": cr.impronta_codice_recupero(body.codice_recupero)}
            )).first()
            if usato is not None:
                ok = True
                await registra_audit(session, evento="accesso.codice_recupero_usato",
                                     tenant_id=riga["tenant_id"], user_id=user_id,
                                     dettagli={})
        if ok:
            await _azzera_tentativi(session, user_id)
            return _utente(riga)
    await _fallimento(user_id, riga["tenant_id"], "secondo_fattore")
    return None


# ── Passkey ──────────────────────────────────────────────────────────

async def _conf_passkey() -> dict[str, Any]:
    fornitori = await leggi_fornitori()
    return dict(fornitori.righe.get(METODO_PASSKEY, (False, {}))[1])


@router.post("/passkey/opzioni", summary="Sfida per l'accesso con passkey")
async def passkey_opzioni(request: Request) -> JSONResponse:
    await _conta_opzioni_passkey(request)
    sfida = secrets.token_bytes(32)
    browser = secrets.token_urlsafe(32)
    passaggio = await _salva("pk", {"sfida": cr.b64url(sfida), "browser": _impronta(browser)},
                             PASSAGGIO_TTL)
    opzioni = cr.opzioni_accesso(sfida=sfida, configurazione=await _conf_passkey())
    return _con_cookie(JSONResponse({"passaggio": passaggio, "opzioni": json.loads(opzioni)}),
                       request, browser, PASSAGGIO_TTL)


class RichiestaPasskey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passaggio: str = Field(..., min_length=20, max_length=128)
    credenziale: dict[str, Any]


@router.post("/passkey/accesso", summary="Accesso con passkey")
@sorvegliato
async def passkey_accesso(request: Request, body: RichiestaPasskey) -> JSONResponse:
    dati = await _leggi("pk", body.passaggio, consuma=True)
    if dati is None or not _stesso_browser(request, dati):
        raise ErroreApi("ACC-400-PASSAGGIO")
    try:
        credential_id = cr.da_b64url(str(body.credenziale.get("rawId") or body.credenziale.get(
            "id") or ""))
    except (ValueError, TypeError):
        raise ErroreApi("ACC-400-PASSKEY") from None
    try:
        utente = await _verifica_passkey(credential_id, body.credenziale,
                                         cr.da_b64url(str(dati["sfida"])))
    except SQLAlchemyError as exc:
        raise _errore_db("passkey_accesso_errore", exc) from None
    if utente is None:
        raise ErroreApi("ACC-400-PASSKEY")
    return await emetti_accesso(request, utente, metodo="passkey")


async def _verifica_passkey(credential_id: bytes, credenziale: dict[str, Any],
                            sfida: bytes) -> UtenteAccesso | None:
    conf = await _conf_passkey()
    async with get_async_session() as session:
        riga = (await session.execute(text(
            _SQL_UTENTE.replace("SELECT ", "SELECT k.id AS passkey_id, k.chiave_pubblica, "
                                "k.contatore, ", 1)
            .replace("FROM public.users u", "FROM public.users u JOIN "
                     "public.credenziali_passkey k ON k.user_id = u.id", 1)
            + "AND k.credential_id = :c FOR UPDATE OF k"), {"c": credential_id})
        ).mappings().one_or_none()
        if riga is None or riga["stato_ente"] in STATI_CLIENTE_BLOCCATI:
            return None
        if METODO_PASSKEY not in await metodi_ammessi_ente(riga["tenant_id"]):
            return None
        try:
            contatore = cr.verifica_accesso(
                credenziale, sfida=sfida, chiave_pubblica=bytes(riga["chiave_pubblica"]),
                contatore=int(riga["contatore"]), configurazione=conf)
        except Exception as exc:  # noqa: BLE001 — qualunque rifiuto della libreria
            logger.warning("passkey_rifiutata", errore=type(exc).__name__)
            return None
        await session.execute(text(
            "UPDATE public.credenziali_passkey SET contatore = :n, ultimo_uso = now() "
            "WHERE id = :i"), {"n": contatore, "i": riga["passkey_id"]})
        return _utente(riga)


# ── Attivazione da invito ────────────────────────────────────────────

def ente_del_token(token: str) -> str | None:
    """Ente scritto nel collegamento (``<ente>.<segreto>``): serve solo a scegliere
    l'ambito della transazione (RLS); il controllo è l'impronta dell'intero token."""
    m = _TOKEN_INVITO.match(token or "")
    return str(uuid.UUID(m.group(1))) if m else None


class RichiestaVerificaInvito(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(..., min_length=40, max_length=120)
    email: str = Field(..., min_length=3, max_length=255)


@router.post("/inviti/verifica", summary="Apre l'attivazione guidata di un invito")
@sorvegliato
async def invito_verifica(request: Request, body: RichiestaVerificaInvito) -> JSONResponse:
    ente = ente_del_token(body.token)
    if ente is None:
        raise ErroreApi("ACC-404-INVITO")
    try:
        invito = await _invito_valido(ente, body.token, body.email.strip().lower())
    except SQLAlchemyError as exc:
        raise _errore_db("invito_verifica_errore", exc) from None
    if invito is None:
        raise ErroreApi("ACC-404-INVITO")
    metodi_ente = list(await metodi_ammessi_ente(ente))
    if not invito["codice_fiscale"] and METODO_SPID_CIE in metodi_ente:
        metodi_ente.remove(METODO_SPID_CIE)  # senza codice fiscale SPID/CIE non riconosce l'utente
    browser = secrets.token_urlsafe(32)
    passaggio = await _salva("att", {
        "invito_id": invito["id"], "tenant_id": ente, "user_id": invito["user_id"],
        "email": invito["email"], "browser": _impronta(browser), "errori": 0,
    }, ATTIVAZIONE_TTL)
    par = await _parametri(ente)
    return _con_cookie(JSONResponse({
        "passaggio": passaggio, "scadenza_secondi": ATTIVAZIONE_TTL,
        "ente": invito["ragione_sociale"], "nome": invito["nome"], "metodi": metodi_ente,
        "password_lunghezza_minima": int(par[PASSWORD_LUNGHEZZA_MINIMA]),
    }), request, browser, ATTIVAZIONE_TTL)


async def _invito_valido(ente: str, token: str, email: str) -> dict[str, Any] | None:
    """Invito IN_ATTESA, non scaduto, con questa e-mail; un'e-mail sbagliata conta
    come tentativo (al limite l'invito è revocato). Sempre None negli altri casi."""
    massimi = int((await _parametri(ente))[ACCESSO_TENTATIVI_MASSIMI])
    with ambito_ente(ente):
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT i.id::text AS id, i.user_id::text AS user_id, i.email, "
                "       i.tentativi_errati, u.nome, u.codice_fiscale, t.ragione_sociale "
                "FROM public.inviti i JOIN public.users u ON u.id = i.user_id "
                "JOIN public.tenants t ON t.id = i.tenant_id "
                "WHERE i.impronta = :h AND i.tenant_id = CAST(:t AS uuid) "
                "  AND i.stato = 'IN_ATTESA' AND i.scade_il > now() AND NOT u.attivo "
                "  AND t.stato::text NOT IN ('SOSPESO', 'DISATTIVATO') FOR UPDATE OF i"),
                {"h": _impronta(token), "t": ente})).mappings().one_or_none()
            if riga is None:
                return None
            if not hmac.compare_digest(riga["email"].lower(), email):
                revoca = int(riga["tentativi_errati"]) + 1 >= massimi
                await session.execute(text(
                    "UPDATE public.inviti SET tentativi_errati = tentativi_errati + 1, "
                    " stato = CASE WHEN :r THEN 'REVOCATO' ELSE stato END WHERE id = :i"),
                    {"r": revoca, "i": uuid.UUID(riga["id"])})
                if revoca:
                    await registra_audit(session, evento="invito.revocato_tentativi",
                                         tenant_id=ente, user_id=riga["user_id"],
                                         dettagli={"invito_id": riga["id"]})
                return None
            return dict(riga)


async def _attivazione(request: Request, passaggio: str) -> dict[str, Any]:
    dati = await _leggi("att", passaggio, consuma=False)
    if dati is None or not _stesso_browser(request, dati):
        raise ErroreApi("ACC-400-PASSAGGIO")
    return dati


class RichiestaPassaggio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passaggio: str = Field(..., min_length=20, max_length=128)


@router.post("/inviti/totp", summary="Segreto del codice a tempo da registrare nell'app")
async def invito_totp(request: Request, body: RichiestaPassaggio) -> dict[str, Any]:
    dati = await _attivazione(request, body.passaggio)
    segreto = cr.nuovo_segreto_totp()
    try:
        dati["totp_cifrato"] = cr.cifra_segreto(segreto, str(dati["user_id"]))
    except cr.CredenzialiNonDisponibili as exc:
        raise _non_disponibile(exc) from None
    await _salva("att", dati, ATTIVAZIONE_TTL, chiave=body.passaggio)
    return {"segreto": segreto, "uri": cr.uri_totp(segreto, email=str(dati["email"]))}


@router.post("/inviti/passkey/opzioni", summary="Opzioni di registrazione della passkey")
async def invito_passkey_opzioni(request: Request, body: RichiestaPassaggio) -> dict[str, Any]:
    dati = await _attivazione(request, body.passaggio)
    sfida = secrets.token_bytes(32)
    dati["sfida"] = cr.b64url(sfida)
    await _salva("att", dati, ATTIVAZIONE_TTL, chiave=body.passaggio)
    opzioni = cr.opzioni_registrazione(user_id=str(dati["user_id"]), email=str(dati["email"]),
                                       nome=str(dati["email"]), sfida=sfida, escludi=[],
                                       configurazione=await _conf_passkey())
    return {"opzioni": json.loads(opzioni)}


class RichiestaCompleta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passaggio: str = Field(..., min_length=20, max_length=128)
    metodo: Literal["PASSWORD_TOTP", "PASSKEY", "SPID_CIE"]
    password: str | None = Field(None, max_length=200)
    codice: str | None = Field(None, max_length=10)
    credenziale: dict[str, Any] | None = None
    nome_passkey: str = Field("Passkey", min_length=1, max_length=100)


@router.post("/inviti/completa", summary="Completa l'attivazione: credenziali e utente attivo")
@sorvegliato
async def invito_completa(request: Request, body: RichiestaCompleta) -> JSONResponse:
    dati = await _attivazione(request, body.passaggio)
    ente, user_id = str(dati["tenant_id"]), str(dati["user_id"])
    metodi_ente = await metodi_ammessi_ente(ente)
    if body.metodo not in metodi_ente:
        raise ErroreApi("ACC-403-METODO")
    scritture = await _prepara_credenziali(body, dati, ente)
    try:
        utente, codici = await _attiva(ente, user_id, str(dati["invito_id"]), body.metodo,
                                       scritture)
    except cr.CredenzialiNonDisponibili as exc:
        raise _non_disponibile(exc) from None
    except SQLAlchemyError as exc:
        raise _errore_db("invito_completa_errore", exc) from None
    await _leggi("att", body.passaggio, consuma=True)
    if utente is None:
        raise ErroreApi("ACC-404-INVITO")
    if body.metodo == METODO_SPID_CIE:
        return JSONResponse({"esito": "attivato", "metodo": METODO_SPID_CIE,
                             "messaggio": "Account attivato: accedi con SPID o CIE."})
    try:
        return await emetti_accesso(request, utente, metodo=body.metodo.lower(), corpo_extra={
            "esito": "attivato_e_accesso", "metodo": body.metodo, "codici_recupero": codici})
    except Exception as exc:  # noqa: BLE001 — l'attivazione è fatta: si accede di nuovo
        logger.warning("invito_sessione_non_emessa", errore=type(exc).__name__)
        return JSONResponse({"esito": "attivato", "metodo": body.metodo,
                             "codici_recupero": codici,
                             "messaggio": "Account attivato: accedi di nuovo."})


async def _prepara_credenziali(body: RichiestaCompleta, dati: dict[str, Any],
                               ente: str) -> dict[str, Any]:
    """Controlli PRIMA della transazione (nessuna scrittura per dati non validi).
    Un codice a tempo sbagliato conta come errore del passaggio (al terzo, si
    ricomincia dal collegamento)."""
    if body.metodo == METODO_PASSWORD_TOTP:
        minimo = int((await _parametri(ente))[PASSWORD_LUNGHEZZA_MINIMA])
        motivo = cr.motivo_password_non_ammessa(body.password or "", minimo=minimo,
                                                email=str(dati["email"]))
        if motivo:
            raise ErroreApi("ACC-422-PASSWORD", motivo)
        if not dati.get("totp_cifrato"):
            raise ErroreApi("ACC-400-PASSAGGIO", "Prima genera il codice a tempo.")
        try:
            segreto = cr.decifra_segreto(str(dati["totp_cifrato"]), str(dati["user_id"]))
        except cr.CredenzialiNonDisponibili as exc:
            raise _non_disponibile(exc) from None
        passo = cr.passo_totp_valido(segreto, body.codice or "", ultimo_passo=0)
        if passo is None:
            dati["errori"] = int(dati.get("errori", 0)) + 1
            if dati["errori"] >= 3:
                await _leggi("att", body.passaggio, consuma=True)
            else:
                await _salva("att", dati, ATTIVAZIONE_TTL, chiave=body.passaggio)
            raise ErroreApi("ACC-422-CODICE")
        return {"impronta": cr.impronta_password(body.password or ""),
                "totp": str(dati["totp_cifrato"]), "passo": passo}
    if body.metodo == METODO_PASSKEY:
        if not dati.get("sfida") or not body.credenziale:
            raise ErroreApi("ACC-400-PASSAGGIO", "Prima crea la passkey sul dispositivo.")
        try:
            registrata = cr.verifica_registrazione(body.credenziale,
                                                   sfida=cr.da_b64url(str(dati["sfida"])),
                                                   configurazione=await _conf_passkey())
        except Exception as exc:  # noqa: BLE001 — qualunque rifiuto della libreria
            logger.warning("passkey_registrazione_rifiutata", errore=type(exc).__name__)
            raise ErroreApi("ACC-400-PASSKEY") from None
        return {"passkey": registrata, "nome": body.nome_passkey.strip()}
    return {}


async def _attiva(ente: str, user_id: str, invito_id: str, metodo: str,
                  scritture: dict[str, Any]) -> tuple[UtenteAccesso | None, list[str]]:
    """Una transazione: invito USATO (solo se ancora IN_ATTESA e valido), credenziali,
    utente attivo, audit. None se l'invito non è più valido (niente scritto)."""
    codici: list[str] = []
    with ambito_ente(ente):
        async with get_async_session() as session:
            consumato = (await session.execute(text(
                "UPDATE public.inviti SET stato = 'USATO', usato_il = now(), "
                " metodo_attivato = :m WHERE id = CAST(:i AS uuid) AND user_id = CAST(:u AS uuid) "
                " AND stato = 'IN_ATTESA' AND scade_il > now() RETURNING id"),
                {"m": metodo, "i": invito_id, "u": user_id})).first()
            riga = (await session.execute(text(
                _SQL_UTENTE.replace("WHERE u.attivo", "WHERE NOT u.attivo", 1)
                + "AND u.id = CAST(:u AS uuid) AND u.tenant_id = CAST(:t AS uuid) "
                  "FOR UPDATE OF u"), {"u": user_id, "t": ente})).mappings().one_or_none()
            if consumato is None or riga is None or riga["stato_ente"] in STATI_CLIENTE_BLOCCATI:
                await session.rollback()
                return None, []
            # un nuovo invito sostituisce TUTTE le credenziali locali precedenti
            # (dispositivo perso, persona disattivata e invitata di nuovo)
            for tabella in ("credenziali_passkey", "codici_recupero", "credenziali_totp",
                            "credenziali_password"):
                # nome di tabella costante della tupla qui sopra, mai un dato
                sql_cancella = f"DELETE FROM public.{tabella} WHERE user_id = CAST(:u AS uuid)"  # noqa: S608
                await session.execute(text(sql_cancella), {"u": user_id})
            if metodo == METODO_PASSWORD_TOTP:
                await session.execute(text(
                    "INSERT INTO public.credenziali_password (user_id, tenant_id, impronta) "
                    "VALUES (CAST(:u AS uuid), CAST(:t AS uuid), :h) ON CONFLICT (user_id) DO "
                    "UPDATE SET impronta = EXCLUDED.impronta, tentativi_falliti = 0, "
                    "bloccato_fino_a = NULL, impostata_il = now()"),
                    {"u": user_id, "t": ente, "h": scritture["impronta"]})
                await session.execute(text(
                    "INSERT INTO public.credenziali_totp (user_id, tenant_id, segreto_cifrato, "
                    " ultimo_passo) VALUES (CAST(:u AS uuid), CAST(:t AS uuid), :s, :p) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    " segreto_cifrato = EXCLUDED.segreto_cifrato, "
                    " ultimo_passo = EXCLUDED.ultimo_passo, confermato_il = now()"),
                    {"u": user_id, "t": ente, "s": scritture["totp"], "p": scritture["passo"]})
                codici = cr.nuovi_codici_recupero()
                for c in codici:
                    await session.execute(text(
                        "INSERT INTO public.codici_recupero (user_id, tenant_id, impronta) "
                        "VALUES (CAST(:u AS uuid), CAST(:t AS uuid), :i)"),
                        {"u": user_id, "t": ente, "i": cr.impronta_codice_recupero(c)})
            elif metodo == METODO_PASSKEY:
                pk = scritture["passkey"]
                await session.execute(text(
                    "INSERT INTO public.credenziali_passkey (user_id, tenant_id, credential_id, "
                    " chiave_pubblica, contatore, nome) VALUES (CAST(:u AS uuid), "
                    " CAST(:t AS uuid), :c, :k, :n, :nome)"),
                    {"u": user_id, "t": ente, "c": pk.credential_id, "k": pk.chiave_pubblica,
                     "n": pk.contatore, "nome": scritture["nome"]})
            await session.execute(text(
                "UPDATE public.users SET attivo = true WHERE id = CAST(:u AS uuid)"),
                {"u": user_id})
            await registra_audit(session, evento="invito.attivato", tenant_id=ente,
                                 user_id=user_id, dettagli={"invito_id": invito_id,
                                                            "metodo": metodo})
            return _utente(riga), codici


def scadenza_invito(ore: int, adesso: datetime | None = None) -> datetime:
    return (adesso or datetime.now(UTC)) + timedelta(hours=int(ore))


__all__ = ["COOKIE_PREACCESSO", "ente_del_token", "router", "scadenza_invito"]
