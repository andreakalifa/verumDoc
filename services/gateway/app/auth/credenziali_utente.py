# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/credenziali_utente.py — gestione delle PROPRIE credenziali
dalla console (R12b, VD-C-156).

Riferimenti: REGOLE_SVILUPPO §19.1, §19.3, §19.7, §15.1 (audit nella transazione),
§12.3 (avvisi con l'outbox), §31 R10 (7) (limite come riserva atomica) e R10 (5)
(nessuna enumerazione); NIST SP 800-63B §4.2 e §6.1 (riautenticazione, gestione degli
autenticatori, avviso al titolare). Credenziali: ``app.auth.credenziali`` (R10).

Rotte (sessione personale della console: cookie del portale o lo stesso token Bearer;
mai chiavi API né token di canale → 403 ACC-403-CREDENZIALI):
  GET    /api/v1/auth/credenziali                         stato: passkey, password, codice
                                                          a tempo, codici rimasti, finestra
  POST   /api/v1/auth/credenziali/riautenticazione/passkey/opzioni   sfida per la conferma
  POST   /api/v1/auth/credenziali/riautenticazione        conferma dell'identità (password e
                                                          codice o codice di recupero; o passkey)
  POST   /api/v1/auth/credenziali/passkey/opzioni         opzioni per una nuova passkey
  POST   /api/v1/auth/credenziali/passkey                 registra la nuova passkey
  PATCH  /api/v1/auth/credenziali/passkey/{id}            rinomina
  DELETE /api/v1/auth/credenziali/passkey/{id}            revoca (mai l'ultima utilizzabile)
  PUT    /api/v1/auth/credenziali/password                cambio (verifica della vecchia)
  POST   /api/v1/auth/credenziali/totp                    nuovo segreto del codice a tempo
  POST   /api/v1/auth/credenziali/totp/conferma           attiva il nuovo codice a tempo
  POST   /api/v1/auth/credenziali/codici-recupero         nuovi codici (mostrati una volta)

Regole:
  - metodi dell'ente: le rotte delle passkey solo se l'ente ammette PASSKEY, quelle di
    password, codice a tempo e codici di recupero solo con PASSWORD_TOTP; con solo
    SPID/CIE nessuna rotta locale (403 ACC-403-CREDENZIALI; lo stato lo dice);
  - riautenticazione RECENTE per ogni modifica: la conferma vale per la SOLA sessione
    che l'ha fatta (chiave in Redis = impronta del token di sessione) per
    ``ACCESSO_RIAUTENTICAZIONE_MINUTI`` (motore, predefinito 5, l'ente solo riduce);
    Redis non raggiungibile → 503, mai una modifica senza conferma (fail-closed);
  - limite dei tentativi come RISERVA ATOMICA prima della verifica (R10, U1): per IP
    reale (``ACCESSO_FALLIMENTI_IP_15_MINUTI``, stesso contatore dell'accesso) e per
    PERSONA (``ACCESSO_TENTATIVI_MASSIMI`` in 15 minuti); la riserva resta solo per un
    tentativo sbagliato; una password o un codice sbagliati contano anche per il blocco
    dell'account come all'accesso (``accesso_locale._fallimento``);
  - ultima credenziale: una passkey non si revoca se dopo la revoca non resta alcun modo
    di accedere con i metodi ATTUALI dell'ente (altre passkey, password con codice a
    tempo, SPID/CIE con il codice fiscale); le righe delle credenziali della persona si
    bloccano (FOR UPDATE) prima del conteggio: due revoche in parallelo non lasciano
    la persona senza accesso;
  - ogni modifica nella STESSA transazione: voce d'audit (``credenziali.*``) e avviso
    per e-mail all'indirizzo della persona (outbox, topic ``notification.send``;
    messaggio sempre inviato, non spegnibile dalle preferenze);
  - VD-P-123: cambio della password, nuovo codice a tempo e revoca di una passkey
    chiudono TUTTE le altre sessioni della persona (``sessioni_attive``, effetto
    immediato in ogni servizio); la sessione corrente resta;
  - segreti mostrati una sola volta: il segreto del nuovo codice a tempo (salvato
    cifrato solo dopo la conferma con un codice valido) e i codici di recupero
    (salvati come impronta SHA-256).
"""
from __future__ import annotations

import functools
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import accesso_locale as al
from app.auth import credenziali as cr
from app.auth.dependencies import get_current_user
from app.auth.fornitori import metodi_ammessi_ente
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.outbox import accoda_evento
from libs.shared.security.audit import registra_audit
from libs.shared.security.inattivita import CLAIM_SESSIONE, SESSIONE_PORTALE
from libs.shared.security.revoca import impronta_token
from libs.verumdoc_rules.parametri_servizio import (
    ACCESSO_RIAUTENTICAZIONE_MINUTI,
    ACCESSO_TENTATIVI_MASSIMI,
    METODO_PASSKEY,
    METODO_PASSWORD_TOTP,
    METODO_SPID_CIE,
    PASSWORD_LUNGHEZZA_MINIMA,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth/credenziali", tags=["Le mie credenziali"])
_bearer = HTTPBearer(auto_error=False)

TOPIC_POSTA = "notification.send"
PASSAGGIO_TTL = 300
FINESTRA_TENTATIVI = 900
ERRORI_TOTP_MASSIMI = 3
#: Esiti che contano come TENTATIVO SBAGLIATO (riserva tenuta per IP e persona)
FALLIMENTI = frozenset({"ACC-422-RIAUTENTICAZIONE", "ACC-400-PASSKEY", "ACC-400-PASSAGGIO"})
_CONTROLLO = re.compile(r"[\x00-\x1f\x7f]")


# ── Chi chiama ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Persona:
    """Persona di una sessione personale della console (mai chiave API o canale)."""

    user_id: str
    tenant_id: str
    sessione: str  # impronta SHA-256 del token della sessione


def _uuid(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


async def persona(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    user: Annotated[dict, Depends(get_current_user)],
) -> Persona:
    """Solo il token di una sessione del portale (claim ``ses``), emesso per una
    persona di un ente: le chiavi API e i token di canale non gestiscono credenziali."""
    user_id, tenant_id = _uuid(user.get("user_id")), _uuid(user.get("tenant_id"))
    if (credentials is None or user.get(CLAIM_SESSIONE) != SESSIONE_PORTALE
            or user.get("auth_method") in ("api_key", "token_canale")
            or user_id is None or tenant_id is None):
        raise ErroreApi("ACC-403-CREDENZIALI")
    return Persona(user_id, tenant_id, impronta_token(credentials.credentials))


Chiamante = Annotated[Persona, Depends(persona)]


# ── Redis: riautenticazione, passaggi, riserve ───────────────────────

def _non_disponibile(evento: str, exc: Exception) -> ErroreApi:
    logger.error(evento, errore=type(exc).__name__)
    return ErroreApi("ACC-503", "Servizio della sessione non raggiungibile: riprovare tra poco.")


async def _con_redis(operazione: Any) -> Any:
    try:
        r = await al._redis()
    except Exception as exc:  # noqa: BLE001 — Redis giù: nessuna modifica (fail-closed)
        raise _non_disponibile("credenziali_redis_non_disponibile", exc) from None
    try:
        return await operazione(r)
    except ErroreApi:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _non_disponibile("credenziali_redis_errore", exc) from None
    finally:
        await r.aclose()


def _chiave(tipo: str, p: Persona) -> str:
    return f"{al._PREFISSO}{tipo}:{p.sessione}"


async def _parametri(tenant_id: str) -> dict[str, Any]:
    from libs.shared.parametri import parametri

    return await parametri([ACCESSO_RIAUTENTICAZIONE_MINUTI, ACCESSO_TENTATIVI_MASSIMI,
                            PASSWORD_LUNGHEZZA_MINIMA], tenant_id)


async def _riserva_persona(p: Persona) -> None:
    """R10 (U1) applicata alla persona: INCR atomico PRIMA della verifica; oltre il
    limite la riserva si restituisce e si risponde 429."""
    limite = int((await _parametri(p.tenant_id))[ACCESSO_TENTATIVI_MASSIMI])
    nome = f"{al._PREFISSO}persona:riautenticazione:{p.user_id}"

    async def _incr(r: Any) -> int:
        # INCR e scadenza in un passo atomico (revisione: mai una chiave senza TTL)
        n = await al.incr_con_scadenza(r, nome, FINESTRA_TENTATIVI)
        if n > limite:
            if int(await r.decr(nome)) < 0:
                await r.delete(nome)
        return n

    if await _con_redis(_incr) > limite:
        logger.warning("credenziali_troppi_tentativi_persona", user_id=p.user_id)
        raise ErroreApi("ACC-429", headers={"Retry-After": str(FINESTRA_TENTATIVI)})


async def _restituisci_persona(p: Persona) -> None:
    nome = f"{al._PREFISSO}persona:riautenticazione:{p.user_id}"

    async def _decr(r: Any) -> None:
        if int(await r.decr(nome)) < 0:
            await r.delete(nome)

    await _con_redis(_decr)


def con_riserve(funzione: Any) -> Any:
    """Rotta che verifica un segreto della persona: riserva un tentativo per IP reale
    (stesso contatore dell'accesso) e per persona PRIMA di eseguire; solo un esito in
    ``FALLIMENTI`` tiene le riserve, ogni altro esito le restituisce."""
    @functools.wraps(funzione)
    async def _rotta(*args: Any, **kwargs: Any) -> Any:
        request: Request = kwargs["request"]
        p: Persona = kwargs["chi"]
        ip = await al._riserva_tentativo_ip(request)
        try:
            await _riserva_persona(p)
        except ErroreApi:
            if ip:
                await al._restituisci("fallimenti", request)
            raise
        fallito = False
        try:
            return await funzione(*args, **kwargs)
        except ErroreApi as exc:
            fallito = exc.codice in FALLIMENTI
            raise
        finally:
            if not fallito:
                if ip:
                    await al._restituisci("fallimenti", request)
                await _restituisci_persona(p)
    return _rotta


async def _riautenticazione(p: Persona) -> int | None:
    """Secondi di validità rimasti della conferma dell'identità di questa sessione
    (None se assente o di un'altra persona)."""
    async def _leggi(r: Any) -> int | None:
        nome = _chiave("riaut", p)
        grezzo = await r.get(nome)
        if not grezzo:
            return None
        if json.loads(grezzo).get("user_id") != p.user_id:
            return None
        ttl = int(await r.ttl(nome))
        return ttl if ttl > 0 else None

    return await _con_redis(_leggi)


async def richiedi_riautenticazione(p: Persona) -> None:
    """Ogni modifica: conferma dell'identità recente IN QUESTA SESSIONE (fail-closed)."""
    if await _riautenticazione(p) is None:
        raise ErroreApi("ACC-403-RIAUTENTICAZIONE")


async def _salva_passaggio(tipo: str, p: Persona, dati: dict[str, Any]) -> None:
    async def _scrivi(r: Any) -> None:
        await r.setex(_chiave(tipo, p), PASSAGGIO_TTL, json.dumps({**dati, "user_id": p.user_id}))

    await _con_redis(_scrivi)


async def _leggi_passaggio(tipo: str, p: Persona, *, consuma: bool) -> dict[str, Any] | None:
    async def _leggi(r: Any) -> dict[str, Any] | None:
        nome = _chiave(tipo, p)
        grezzo = await (r.getdel(nome) if consuma else r.get(nome))
        if not grezzo:
            return None
        dati = json.loads(grezzo)
        return dati if dati.get("user_id") == p.user_id else None

    return await _con_redis(_leggi)


# ── Metodi dell'ente ─────────────────────────────────────────────────

async def _metodi(p: Persona) -> tuple[str, ...]:
    return tuple(await metodi_ammessi_ente(p.tenant_id))


async def _richiedi_metodo(p: Persona, metodo: str) -> tuple[str, ...]:
    metodi = await _metodi(p)
    if metodo not in metodi:
        raise ErroreApi("ACC-403-CREDENZIALI", (
            "L'ente ammette solo SPID o CIE: le credenziali si gestiscono presso il gestore "
            "dell'identità digitale." if not ({METODO_PASSKEY, METODO_PASSWORD_TOTP} & set(metodi))
            else "Questo metodo d'accesso non è ammesso dall'ente."))
    return metodi


# ── Stato ────────────────────────────────────────────────────────────

async def _stato_db(p: Persona) -> dict[str, Any]:
    with ambito_ente(p.tenant_id):
        async with get_async_session() as session:
            utente = (await session.execute(text(
                "SELECT email, codice_fiscale FROM public.users WHERE id = CAST(:u AS uuid) "
                "AND tenant_id = CAST(:t AS uuid) AND attivo"),
                {"u": p.user_id, "t": p.tenant_id})).mappings().one_or_none()
            passkey = (await session.execute(text(
                "SELECT id::text AS id, nome, creata_il, ultimo_uso "
                "FROM public.credenziali_passkey WHERE user_id = CAST(:u AS uuid) "
                "ORDER BY creata_il, id"),
                {"u": p.user_id})).mappings().all()
            password = (await session.execute(text(
                "SELECT impostata_il, bloccato_fino_a FROM public.credenziali_password "
                "WHERE user_id = CAST(:u AS uuid)"), {"u": p.user_id})).mappings().one_or_none()
            totp = (await session.execute(text(
                "SELECT confermato_il FROM public.credenziali_totp "
                "WHERE user_id = CAST(:u AS uuid)"), {"u": p.user_id})).mappings().one_or_none()
            codici = (await session.execute(text(
                "SELECT count(*) FILTER (WHERE usato_il IS NULL) AS rimasti, count(*) AS totale "
                "FROM public.codici_recupero WHERE user_id = CAST(:u AS uuid)"),
                {"u": p.user_id})).mappings().one()
    if utente is None:
        raise ErroreApi("ACC-403-CREDENZIALI")
    return {"utente": dict(utente), "passkey": [dict(r) for r in passkey],
            "password": dict(password) if password else None,
            "totp": dict(totp) if totp else None, "codici": dict(codici)}


def _iso(valore: Any) -> str | None:
    return valore.isoformat() if isinstance(valore, datetime) else None


@router.get("", summary="Le mie credenziali (senza segreti)")
async def stato(chi: Chiamante) -> dict[str, Any]:
    try:
        metodi = await _metodi(chi)
        dati = await _stato_db(chi)
        par = await _parametri(chi.tenant_id)
    except SQLAlchemyError as exc:
        raise errore_registrato("ACC-503", exc, evento="credenziali_stato_errore") from None
    locali = [m for m in (METODO_PASSKEY, METODO_PASSWORD_TOTP) if m in metodi]
    password_totp = dati["password"] is not None and dati["totp"] is not None
    conferma: list[str] = []
    if METODO_PASSWORD_TOTP in metodi and password_totp:
        conferma.append(METODO_PASSWORD_TOTP)
    if METODO_PASSKEY in metodi and dati["passkey"]:
        conferma.append(METODO_PASSKEY)
    try:
        rimasti = await _riautenticazione(chi) if locali else None
    except ErroreApi:
        rimasti = None  # Redis giù: lo stato si legge, le modifiche no (fail-closed)
    return {
        "disponibile": bool(locali),
        "motivo": None if locali else (
            "L'ente ammette solo SPID o CIE: le credenziali si gestiscono presso il gestore "
            "dell'identità digitale."),
        "metodi_ente": list(metodi),
        "metodi_locali": locali,
        "passkey": [{"id": k["id"], "nome": k["nome"], "creata_il": _iso(k["creata_il"]),
                     "ultimo_uso": _iso(k["ultimo_uso"])} for k in dati["passkey"]]
        if METODO_PASSKEY in metodi else [],
        "password": {"impostata": dati["password"] is not None,
                     "impostata_il": _iso((dati["password"] or {}).get("impostata_il"))},
        "totp": {"attivo": dati["totp"] is not None,
                 "confermato_il": _iso((dati["totp"] or {}).get("confermato_il"))},
        "codici_recupero": {"rimasti": int(dati["codici"]["rimasti"] or 0),
                            "totale": int(dati["codici"]["totale"] or 0)},
        "spid_cie": METODO_SPID_CIE in metodi and bool(dati["utente"].get("codice_fiscale")),
        "riautenticazione": {
            "metodi": conferma,
            "valida_per_secondi": rimasti,
            "finestra_minuti": int(par[ACCESSO_RIAUTENTICAZIONE_MINUTI]),
        },
        "password_lunghezza_minima": int(par[PASSWORD_LUNGHEZZA_MINIMA]),
    }


# ── Riautenticazione ─────────────────────────────────────────────────

async def _passkey_della_persona(p: Persona) -> list[bytes]:
    with ambito_ente(p.tenant_id):
        async with get_async_session() as session:
            return [bytes(r[0]) for r in (await session.execute(text(
                "SELECT credential_id FROM public.credenziali_passkey "
                "WHERE user_id = CAST(:u AS uuid) ORDER BY creata_il, id"),
                {"u": p.user_id})).all()]


@router.post("/riautenticazione/passkey/opzioni", summary="Sfida per confermare con una passkey")
async def riautenticazione_opzioni(request: Request, chi: Chiamante) -> dict[str, Any]:
    await _richiedi_metodo(chi, METODO_PASSKEY)
    await al._conta_opzioni_passkey(request)
    try:
        ammesse = await _passkey_della_persona(chi)
    except SQLAlchemyError as exc:
        raise errore_registrato("ACC-503", exc, evento="credenziali_opzioni_errore") from None
    if not ammesse:
        raise ErroreApi("ACC-404-CREDENZIALE", "Non hai passkey registrate.")
    sfida = secrets.token_bytes(32)
    await _salva_passaggio("riautpk", chi, {"sfida": cr.b64url(sfida)})
    opzioni = json.loads(cr.opzioni_accesso(sfida=sfida, configurazione=await al._conf_passkey()))
    opzioni["allowCredentials"] = [{"type": "public-key", "id": cr.b64url(c)} for c in ammesse]
    return {"opzioni": opzioni, "scadenza_secondi": PASSAGGIO_TTL}


class RichiestaRiautenticazione(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metodo: Literal["PASSWORD_TOTP", "PASSKEY"]
    password: str | None = Field(None, max_length=200)
    codice: str | None = Field(None, max_length=10, description="Codice a tempo a 6 cifre")
    codice_recupero: str | None = Field(None, max_length=40)
    credenziale: dict[str, Any] | None = None


@router.post("/riautenticazione", summary="Conferma dell'identità per modificare le credenziali")
@con_riserve
async def riautentica(request: Request, body: RichiestaRiautenticazione,
                      chi: Chiamante) -> dict[str, Any]:
    await _richiedi_metodo(chi, body.metodo)
    try:
        if body.metodo == METODO_PASSWORD_TOTP:
            ok = await _verifica_password_totp(chi, body.password or "", body.codice,
                                               body.codice_recupero)
        else:
            ok = await _verifica_passkey_persona(chi, body.credenziale)
    except cr.CredenzialiNonDisponibili as exc:
        raise al._non_disponibile(exc) from None
    except SQLAlchemyError as exc:
        raise errore_registrato("ACC-503", exc, evento="credenziali_riautenticazione_errore"
                                ) from None
    if not ok:
        raise ErroreApi("ACC-422-RIAUTENTICAZIONE")
    minuti = int((await _parametri(chi.tenant_id))[ACCESSO_RIAUTENTICAZIONE_MINUTI])
    # revisione: l'audit si scrive e si conferma PRIMA della conferma in Redis; se non
    # si scrive, nessuna conferma (nessuna modifica possibile senza la sua voce d'audit)
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                await registra_audit(session, evento="credenziali.riautenticazione",
                                     tenant_id=chi.tenant_id, user_id=chi.user_id,
                                     dettagli={"metodo": body.metodo})
    except SQLAlchemyError as exc:
        raise errore_registrato("ACC-503", exc, evento="credenziali_audit_errore") from None

    async def _scrivi(r: Any) -> None:
        await r.setex(_chiave("riaut", chi), minuti * 60, json.dumps({
            "user_id": chi.user_id, "metodo": body.metodo,
            "il": datetime.now(UTC).isoformat()}))

    await _con_redis(_scrivi)
    return {"esito": "confermata", "valida_per_secondi": minuti * 60, "finestra_minuti": minuti}


async def _verifica_password_totp(p: Persona, password: str, codice: str | None,
                                  codice_recupero: str | None) -> bool:
    """Password E (codice a tempo o di recupero) della persona; un errore conta come
    all'accesso (blocco dell'account con audit); account bloccato = errore, stesso
    costo (bcrypt sempre calcolato)."""
    with ambito_ente(p.tenant_id):
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT p.impronta, p.bloccato_fino_a, s.segreto_cifrato, s.ultimo_passo "
                "FROM public.credenziali_password p JOIN public.credenziali_totp s "
                "ON s.user_id = p.user_id WHERE p.user_id = CAST(:u AS uuid) "
                "FOR UPDATE OF p, s"), {"u": p.user_id})).mappings().one_or_none()
            ok = cr.verifica_password(password, riga["impronta"] if riga else None)
            if riga is None:
                return False
            if al._bloccato(dict(riga)):
                return False
            if ok and codice:
                segreto = cr.decifra_segreto(riga["segreto_cifrato"], p.user_id)
                passo = cr.passo_totp_valido(segreto, codice,
                                             ultimo_passo=int(riga["ultimo_passo"]))
                ok = passo is not None
                if ok:
                    await session.execute(text(
                        "UPDATE public.credenziali_totp SET ultimo_passo = :p "
                        "WHERE user_id = CAST(:u AS uuid) AND ultimo_passo < :p"),
                        {"u": p.user_id, "p": passo})
            elif ok and codice_recupero:
                ok = (await session.execute(text(
                    "UPDATE public.codici_recupero SET usato_il = now() "
                    "WHERE user_id = CAST(:u AS uuid) AND impronta = :i AND usato_il IS NULL "
                    "RETURNING id"), {"u": p.user_id,
                                      "i": cr.impronta_codice_recupero(codice_recupero)})
                      ).first() is not None
                if ok:
                    await registra_audit(session, evento="accesso.codice_recupero_usato",
                                         tenant_id=p.tenant_id, user_id=p.user_id,
                                         dettagli={"uso": "riautenticazione"})
            else:
                ok = False
            if ok:
                await al._azzera_tentativi(session, p.user_id)
                return True
    await al._fallimento(p.user_id, p.tenant_id, "riautenticazione")
    return False


async def _verifica_passkey_persona(p: Persona, credenziale: dict[str, Any] | None) -> bool:
    dati = await _leggi_passaggio("riautpk", p, consuma=True)
    if dati is None:
        raise ErroreApi("ACC-400-PASSAGGIO", "Chiedi di nuovo la conferma con la passkey.")
    if not credenziale:
        return False
    try:
        credential_id = cr.da_b64url(str(credenziale.get("rawId") or credenziale.get("id") or ""))
    except (ValueError, TypeError):
        return False
    conf = await al._conf_passkey()
    with ambito_ente(p.tenant_id):
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT id, chiave_pubblica, contatore FROM public.credenziali_passkey "
                "WHERE credential_id = :c AND user_id = CAST(:u AS uuid) FOR UPDATE"),
                {"c": credential_id, "u": p.user_id})).mappings().one_or_none()
            if riga is None:
                return False  # passkey di un'altra persona o inesistente
            try:
                contatore = cr.verifica_accesso(
                    credenziale, sfida=cr.da_b64url(str(dati["sfida"])),
                    chiave_pubblica=bytes(riga["chiave_pubblica"]),
                    contatore=int(riga["contatore"]), configurazione=conf)
            except Exception as exc:  # noqa: BLE001 — qualunque rifiuto della libreria
                logger.warning("credenziali_passkey_rifiutata", errore=type(exc).__name__)
                return False
            await session.execute(text(
                "UPDATE public.credenziali_passkey SET contatore = :n, ultimo_uso = now() "
                "WHERE id = :i"), {"n": contatore, "i": riga["id"]})
            return True


# ── Modifiche: audit, avviso, altre sessioni ─────────────────────────

_DESCRIZIONI = {
    "credenziali.passkey_aggiunta": "è stata aggiunta una passkey",
    "credenziali.passkey_rinominata": "è stato cambiato il nome di una passkey",
    "credenziali.passkey_revocata": "è stata revocata una passkey",
    "credenziali.password_cambiata": "è stata cambiata la password",
    "credenziali.totp_rigenerato": "è stato attivato un nuovo codice a tempo",
    "credenziali.codici_recupero_rigenerati": "sono stati generati nuovi codici di recupero "
                                              "(i precedenti non valgono più)",
}


async def _registra_modifica(session: Any, p: Persona, evento: str,
                             dettagli: dict[str, Any], *, chiudi_altre: bool) -> int:
    """Audit, voce personale della campanella (R13b, VD-C-163), avviso per e-mail
    (outbox) e, se richiesto, chiusura delle altre sessioni: tutto nella transazione
    della modifica. Sessioni chiuse."""
    chiuse = 0
    if chiudi_altre:
        chiuse = len((await session.execute(text(
            "DELETE FROM public.sessioni_attive WHERE user_id = CAST(:u AS uuid) "
            "AND impronta <> :s RETURNING impronta"),
            {"u": p.user_id, "s": p.sessione})).all())
    await registra_audit(session, evento=evento, tenant_id=p.tenant_id, user_id=p.user_id,
                         dettagli={**dettagli, "altre_sessioni_chiuse": chiuse})
    # R13b (VD-C-163): voce PERSONALE della campanella, sempre (anche senza e-mail),
    # separata dagli eventi dell'ente e dai webhook; nella stessa transazione
    await session.execute(text(
        "INSERT INTO public.notifiche_personali (user_id, tenant_id, tipo, titolo, "
        "collegamento) VALUES (CAST(:u AS uuid), CAST(:t AS uuid), :tipo, :titolo, "
        "'/preferenze/credenziali')"),
        {"u": p.user_id, "t": p.tenant_id, "tipo": evento,
         "titolo": ("Le tue credenziali: " + _DESCRIZIONI[evento]
                    + (". Le altre sessioni sono state chiuse" if chiudi_altre else "")
                    + ". Se non sei stato tu, avvisa subito l'amministratore")[:300]})
    utente = (await session.execute(text(
        "SELECT u.email, u.nome, t.ragione_sociale FROM public.users u JOIN public.tenants t "
        "ON t.id = u.tenant_id WHERE u.id = CAST(:u AS uuid)"),
        {"u": p.user_id})).mappings().one_or_none()
    if utente is None or not utente["email"]:
        logger.warning("credenziali_avviso_senza_email", user_id=p.user_id, evento=evento)
        return chiuse
    quando = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    testo = (f"Gentile {utente['nome'] or ''},\n\nil {quando}, nella console di VerumDoc per "
             f"{utente['ragione_sociale']}, {_DESCRIZIONI[evento]}."
             + ("\nLe altre sessioni aperte con il tuo account sono state chiuse."
                if chiudi_altre else "")
             + "\n\nSe sei stato tu non devi fare nulla. Se non riconosci questa modifica, "
               "avvisa subito l'amministratore del tuo ente o il conservatore: il tuo "
               "account potrebbe essere usato da altri.")
    await accoda_evento(session, topic=TOPIC_POSTA, chiave=f"credenziali:{p.user_id}",
                        tenant_id=p.tenant_id, payload={
                            "evento": "accesso.credenziali_modificate", "tipo": "email",
                            "tenant_id": p.tenant_id,
                            "oggetto_id": f"credenziali:{p.user_id}:{uuid.uuid4()}",
                            "destinatario": utente["email"],
                            "oggetto": "VerumDoc: le tue credenziali sono cambiate",
                            "testo": testo})
    return chiuse


def _errore_db(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("ACC-503", exc, evento=evento)


# ── Passkey ──────────────────────────────────────────────────────────

@router.post("/passkey/opzioni", summary="Opzioni per registrare una nuova passkey")
async def passkey_opzioni(chi: Chiamante) -> dict[str, Any]:
    await _richiedi_metodo(chi, METODO_PASSKEY)
    await richiedi_riautenticazione(chi)
    try:
        esistenti = await _passkey_della_persona(chi)
        dati = await _stato_db(chi)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_passkey_opzioni_errore", exc) from None
    sfida = secrets.token_bytes(32)
    await _salva_passaggio("nuovapk", chi, {"sfida": cr.b64url(sfida)})
    email = str(dati["utente"].get("email") or chi.user_id)
    opzioni = cr.opzioni_registrazione(user_id=chi.user_id, email=email, nome=email,
                                       sfida=sfida, escludi=esistenti,
                                       configurazione=await al._conf_passkey())
    return {"opzioni": json.loads(opzioni), "scadenza_secondi": PASSAGGIO_TTL}


def _nome_pulito(valore: str) -> str:
    nome = valore.strip()
    if not nome or _CONTROLLO.search(nome):
        raise ValueError("nome non valido")
    return nome


class RichiestaNuovaPasskey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credenziale: dict[str, Any]
    nome: str = Field("Passkey", min_length=1, max_length=100)

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, v: str) -> str:
        return _nome_pulito(v)


@router.post("/passkey", status_code=201, summary="Registra una nuova passkey")
async def passkey_aggiungi(body: RichiestaNuovaPasskey, chi: Chiamante) -> dict[str, Any]:
    await _richiedi_metodo(chi, METODO_PASSKEY)
    await richiedi_riautenticazione(chi)
    dati = await _leggi_passaggio("nuovapk", chi, consuma=True)
    if dati is None:
        raise ErroreApi("ACC-400-PASSAGGIO", "Chiedi di nuovo di creare la passkey.")
    try:
        registrata = cr.verifica_registrazione(body.credenziale,
                                               sfida=cr.da_b64url(str(dati["sfida"])),
                                               configurazione=await al._conf_passkey())
    except Exception as exc:  # noqa: BLE001 — qualunque rifiuto della libreria
        logger.warning("credenziali_passkey_registrazione_rifiutata", errore=type(exc).__name__)
        raise ErroreApi("ACC-400-PASSKEY") from None
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                nuova = (await session.execute(text(
                    "INSERT INTO public.credenziali_passkey (user_id, tenant_id, credential_id, "
                    " chiave_pubblica, contatore, nome) VALUES (CAST(:u AS uuid), "
                    " CAST(:t AS uuid), :c, :k, :n, :nome) RETURNING id::text, creata_il"),
                    {"u": chi.user_id, "t": chi.tenant_id, "c": registrata.credential_id,
                     "k": registrata.chiave_pubblica, "n": registrata.contatore,
                     "nome": body.nome})).one()
                await _registra_modifica(session, chi, "credenziali.passkey_aggiunta",
                                         {"passkey_id": nuova[0], "nome": body.nome},
                                         chiudi_altre=False)
    except IntegrityError:
        raise ErroreApi("ACC-400-PASSKEY", "Questa passkey è già registrata.") from None
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_passkey_errore", exc) from None
    return {"id": nuova[0], "nome": body.nome, "creata_il": _iso(nuova[1])}


class RichiestaNomePasskey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str = Field(..., min_length=1, max_length=100)

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, v: str) -> str:
        return _nome_pulito(v)


def _id_passkey(valore: str) -> str:
    pid = _uuid(valore)
    if pid is None:
        raise ErroreApi("ACC-404-CREDENZIALE")
    return pid


@router.patch("/passkey/{passkey_id}", summary="Rinomina una passkey")
async def passkey_rinomina(passkey_id: str, body: RichiestaNomePasskey,
                           chi: Chiamante) -> dict[str, Any]:
    pid = _id_passkey(passkey_id)
    await _richiedi_metodo(chi, METODO_PASSKEY)
    await richiedi_riautenticazione(chi)
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                riga = (await session.execute(text(
                    "UPDATE public.credenziali_passkey SET nome = :n WHERE id = CAST(:i AS uuid) "
                    "AND user_id = CAST(:u AS uuid) RETURNING id"),
                    {"n": body.nome, "i": pid, "u": chi.user_id})).first()
                if riga is None:
                    raise ErroreApi("ACC-404-CREDENZIALE")
                await _registra_modifica(session, chi, "credenziali.passkey_rinominata",
                                         {"passkey_id": pid, "nome": body.nome},
                                         chiudi_altre=False)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_passkey_errore", exc) from None
    return {"id": pid, "nome": body.nome}


def credenziali_utilizzabili(metodi: tuple[str, ...] | list[str], *, passkey: int,
                             password_totp: bool, spid_cie: bool) -> int:
    """Modi di accedere che restano con i metodi ATTUALI dell'ente (funzione pura)."""
    return ((passkey if METODO_PASSKEY in metodi else 0)
            + (1 if password_totp and METODO_PASSWORD_TOTP in metodi else 0)
            + (1 if spid_cie and METODO_SPID_CIE in metodi else 0))


@router.delete("/passkey/{passkey_id}", summary="Revoca una passkey (mai l'ultima utilizzabile)")
async def passkey_revoca(passkey_id: str, chi: Chiamante) -> dict[str, Any]:
    pid = _id_passkey(passkey_id)
    metodi = await _richiedi_metodo(chi, METODO_PASSKEY)
    await richiedi_riautenticazione(chi)
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                # tutte le righe delle credenziali della persona bloccate PRIMA del
                # conteggio (sempre nello stesso ordine: passkey, poi password)
                righe = (await session.execute(text(
                    "SELECT id::text FROM public.credenziali_passkey "
                    "WHERE user_id = CAST(:u AS uuid) ORDER BY id FOR UPDATE"),
                    {"u": chi.user_id})).scalars().all()
                if pid not in righe:
                    raise ErroreApi("ACC-404-CREDENZIALE")
                password_totp = (await session.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM public.credenziali_password p JOIN "
                    "public.credenziali_totp s ON s.user_id = p.user_id "
                    "WHERE p.user_id = CAST(:u AS uuid))"), {"u": chi.user_id})).scalar_one()
                spid = (await session.execute(text(
                    "SELECT codice_fiscale IS NOT NULL AND codice_fiscale <> '' "
                    "FROM public.users WHERE id = CAST(:u AS uuid)"),
                    {"u": chi.user_id})).scalar_one_or_none()
                if credenziali_utilizzabili(metodi, passkey=len(righe) - 1,
                                            password_totp=bool(password_totp),
                                            spid_cie=bool(spid)) < 1:
                    raise ErroreApi("ACC-409-ULTIMA")
                await session.execute(text(
                    "DELETE FROM public.credenziali_passkey WHERE id = CAST(:i AS uuid) "
                    "AND user_id = CAST(:u AS uuid)"), {"i": pid, "u": chi.user_id})
                chiuse = await _registra_modifica(session, chi, "credenziali.passkey_revocata",
                                                  {"passkey_id": pid}, chiudi_altre=True)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_passkey_errore", exc) from None
    return {"esito": "revocata", "id": pid, "altre_sessioni_chiuse": chiuse}


# ── Password ─────────────────────────────────────────────────────────

class RichiestaPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password_attuale: str = Field(..., min_length=1, max_length=200)
    password_nuova: str = Field(..., min_length=1, max_length=200)


@router.put("/password", summary="Cambia la password (verifica della vecchia)")
@con_riserve
async def password_cambia(request: Request, body: RichiestaPassword,
                          chi: Chiamante) -> dict[str, Any]:
    await _richiedi_metodo(chi, METODO_PASSWORD_TOTP)
    await richiedi_riautenticazione(chi)
    par = await _parametri(chi.tenant_id)
    try:
        email = str((await _stato_db(chi))["utente"].get("email") or "")
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_password_errore", exc) from None
    motivo = cr.motivo_password_non_ammessa(body.password_nuova,
                                            minimo=int(par[PASSWORD_LUNGHEZZA_MINIMA]),
                                            email=email)
    if motivo:
        raise ErroreApi("ACC-422-PASSWORD", motivo)
    if body.password_nuova == body.password_attuale:
        raise ErroreApi("ACC-422-PASSWORD", "La nuova password deve essere diversa dalla attuale")
    nuova = cr.impronta_password(body.password_nuova)  # costo fuori dalla transazione
    sbagliata = False
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                riga = (await session.execute(text(
                    "SELECT impronta, bloccato_fino_a FROM public.credenziali_password "
                    "WHERE user_id = CAST(:u AS uuid) FOR UPDATE"),
                    {"u": chi.user_id})).mappings().one_or_none()
                valida = cr.verifica_password(body.password_attuale,
                                              riga["impronta"] if riga else None)
                if riga is None:
                    raise ErroreApi("ACC-404-CREDENZIALE", "Non hai una password da cambiare.")
                if not valida or al._bloccato(dict(riga)):
                    sbagliata = True
                else:
                    await session.execute(text(
                        "UPDATE public.credenziali_password SET impronta = :h, "
                        "tentativi_falliti = 0, bloccato_fino_a = NULL, impostata_il = now() "
                        "WHERE user_id = CAST(:u AS uuid)"), {"h": nuova, "u": chi.user_id})
                    chiuse = await _registra_modifica(session, chi,
                                                      "credenziali.password_cambiata", {},
                                                      chiudi_altre=True)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_password_errore", exc) from None
    if sbagliata:
        await al._fallimento(chi.user_id, chi.tenant_id, "cambio_password")
        raise ErroreApi("ACC-422-RIAUTENTICAZIONE", "La password attuale non è corretta.")
    return {"esito": "cambiata", "altre_sessioni_chiuse": chiuse}


# ── Codice a tempo e codici di recupero ──────────────────────────────

async def _ha_password(p: Persona) -> bool:
    with ambito_ente(p.tenant_id):
        async with get_async_session() as session:
            return bool((await session.execute(text(
                "SELECT EXISTS (SELECT 1 FROM public.credenziali_password "
                "WHERE user_id = CAST(:u AS uuid))"), {"u": p.user_id})).scalar_one())


@router.post("/totp", summary="Nuovo segreto del codice a tempo (mostrato una sola volta)")
async def totp_nuovo(chi: Chiamante) -> JSONResponse:
    await _richiedi_metodo(chi, METODO_PASSWORD_TOTP)
    await richiedi_riautenticazione(chi)
    try:
        if not await _ha_password(chi):
            raise ErroreApi("ACC-404-CREDENZIALE", "Il codice a tempo accompagna la password: "
                                                   "non hai una password.")
        email = str((await _stato_db(chi))["utente"].get("email") or chi.user_id)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_totp_errore", exc) from None
    segreto = cr.nuovo_segreto_totp()
    try:
        cifrato = cr.cifra_segreto(segreto, chi.user_id)
    except cr.CredenzialiNonDisponibili as exc:
        raise al._non_disponibile(exc) from None
    await _salva_passaggio("nuovototp", chi, {"totp_cifrato": cifrato, "errori": 0})
    # mostrato UNA volta: mai in cache del browser né dei proxy (come i codici di recupero)
    return JSONResponse({"segreto": segreto, "uri": cr.uri_totp(segreto, email=email),
                         "scadenza_secondi": PASSAGGIO_TTL},
                        headers={"Cache-Control": "no-store"})


class RichiestaConfermaTotp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codice: str = Field(..., min_length=6, max_length=10)


@router.post("/totp/conferma", summary="Attiva il nuovo codice a tempo")
async def totp_conferma(body: RichiestaConfermaTotp, chi: Chiamante) -> dict[str, Any]:
    await _richiedi_metodo(chi, METODO_PASSWORD_TOTP)
    await richiedi_riautenticazione(chi)
    dati = await _leggi_passaggio("nuovototp", chi, consuma=False)
    if dati is None:
        raise ErroreApi("ACC-400-PASSAGGIO", "Genera di nuovo il codice a tempo.")
    try:
        segreto = cr.decifra_segreto(str(dati["totp_cifrato"]), chi.user_id)
    except cr.CredenzialiNonDisponibili as exc:
        raise al._non_disponibile(exc) from None
    passo = cr.passo_totp_valido(segreto, body.codice, ultimo_passo=0)
    if passo is None:
        errori = int(dati.get("errori", 0)) + 1
        if errori >= ERRORI_TOTP_MASSIMI:
            await _leggi_passaggio("nuovototp", chi, consuma=True)
        else:
            await _salva_passaggio("nuovototp", chi, {**dati, "errori": errori})
        raise ErroreApi("ACC-422-CODICE")
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                if not (await session.execute(text(
                        "SELECT EXISTS (SELECT 1 FROM public.credenziali_password "
                        "WHERE user_id = CAST(:u AS uuid))"), {"u": chi.user_id})).scalar_one():
                    raise ErroreApi("ACC-404-CREDENZIALE")
                await session.execute(text(
                    "INSERT INTO public.credenziali_totp (user_id, tenant_id, segreto_cifrato, "
                    " ultimo_passo) VALUES (CAST(:u AS uuid), CAST(:t AS uuid), :s, :p) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    " segreto_cifrato = EXCLUDED.segreto_cifrato, "
                    " ultimo_passo = EXCLUDED.ultimo_passo, confermato_il = now()"),
                    {"u": chi.user_id, "t": chi.tenant_id, "s": str(dati["totp_cifrato"]),
                     "p": passo})
                chiuse = await _registra_modifica(session, chi, "credenziali.totp_rigenerato",
                                                  {}, chiudi_altre=True)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_totp_errore", exc) from None
    await _leggi_passaggio("nuovototp", chi, consuma=True)
    return {"esito": "attivato", "altre_sessioni_chiuse": chiuse}


@router.post("/codici-recupero", summary="Nuovi codici di recupero (mostrati una sola volta)")
async def codici_rigenera(chi: Chiamante) -> JSONResponse:
    await _richiedi_metodo(chi, METODO_PASSWORD_TOTP)
    await richiedi_riautenticazione(chi)
    codici = cr.nuovi_codici_recupero()
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                if not (await session.execute(text(
                        "SELECT EXISTS (SELECT 1 FROM public.credenziali_totp "
                        "WHERE user_id = CAST(:u AS uuid))"), {"u": chi.user_id})).scalar_one():
                    raise ErroreApi("ACC-404-CREDENZIALE", "I codici di recupero accompagnano "
                                                           "il codice a tempo: attivalo prima.")
                await session.execute(text(
                    "DELETE FROM public.codici_recupero WHERE user_id = CAST(:u AS uuid)"),
                    {"u": chi.user_id})
                for c in codici:
                    await session.execute(text(
                        "INSERT INTO public.codici_recupero (user_id, tenant_id, impronta) "
                        "VALUES (CAST(:u AS uuid), CAST(:t AS uuid), :i)"),
                        {"u": chi.user_id, "t": chi.tenant_id,
                         "i": cr.impronta_codice_recupero(c)})
                await _registra_modifica(session, chi, "credenziali.codici_recupero_rigenerati",
                                         {"quanti": len(codici)}, chiudi_altre=False)
    except SQLAlchemyError as exc:
        raise _errore_db("credenziali_codici_errore", exc) from None
    # mostrati UNA volta: mai in cache del browser né dei proxy
    return JSONResponse({"esito": "rigenerati", "codici_recupero": codici},
                        headers={"Cache-Control": "no-store"})


__all__ = ["FALLIMENTI", "Persona", "con_riserve", "credenziali_utilizzabili", "persona",
           "richiedi_riautenticazione", "router"]
