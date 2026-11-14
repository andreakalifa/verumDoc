# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revoca effettiva dei token (revisione di sicurezza B5).

Un JWT resta valido fino a ``exp`` (1 h): senza questo controllo un utente
disattivato o un ente SOSPESO/DISATTIVATO manterrebbero l'accesso fino alla
scadenza. Scelta: controllo leggero sul database con cache breve, applicato
da gateway e servizi (dashboard-api, distribution, onboarding, hashchain), al
posto di token da 15 minuti con rinnovo (che avrebbe chiesto un flusso di
rinnovo nel frontend e lasciato comunque fino a 15 minuti di accesso).

Regole:
  - token rifiutato (401 AUTH-003) se ``user_id`` è un utente con
    ``attivo = false`` o se ``tenant_id`` è un ente SOSPESO o DISATTIVATO;
  - (N2) rifiutato anche se il ruolo attuale dell'utente è diverso da quello
    del token (declassamento o promozione: serve un nuovo accesso) o se
    l'utente appartiene ora a un ente diverso da ``tenant_id``. Fanno
    eccezione al confronto del ruolo i token delle chiavi API (ruolo
    API_SYSTEM, user_id = responsabile della chiave);
  - identificativi non UUID (es. client PDND) o righe assenti non revocano:
    il token è già firmato dal gateway, che lo emette solo per utenti ed enti
    esistenti; la revoca riguarda chi È STATO disattivato;
  - cache per terna (utente, ente, ruolo) di ``VD_REVOCA_CACHE_SECONDI`` secondi
    (predefinito 30): la revoca diventa effettiva entro questo intervallo;
  - database non inizializzato nel processo (eccezione dedicata
    ``libs.shared.db.DBNonInizializzato``, es. test unitari senza DB): il
    controllo non si applica; errore del database: 503 (fail-closed), mai
    accesso concesso «alla cieca».

Revisione R05 (R12) — uscita lato server: «Esci» (``DELETE
/api/v1/auth/sessione`` del gateway) registra la sessione in
``sessioni_revocate`` (impronta SHA-256 del token e ``jti``) fino alla scadenza
del token (``revoca_sessione``). Da lì il token è rifiutato (401 AUTH-003):
  - da ``token_revocato`` (ogni servizio che verifica un token chiama
    ``richiedi_non_revocato``), per ``jti``, anche se presentato come Bearer;
  - da ``MiddlewareSessioneCookie`` (ogni servizio che accetta il cookie), per
    impronta del cookie, prima di qualunque rotta.
Effetto: immediato nel processo che ha registrato l'uscita, entro
``VD_REVOCA_CACHE_SECONDI`` (30 s) negli altri processi e servizi.

R16 (VD-C-153): anche un cambio dei METODI d'accesso ammessi
(``ACCESSO_METODI_AMMESSI``) o dei PERMESSI CONCEDIBILI (``RUOLI_PERMESSI_CONCEDIBILI``),
del conservatore o dell'ente, compresi i ``LIMITE.`` e le modifiche programmate applicate
alla decorrenza, revoca le sessioni aperte al prossimo uso del token (entro la cache).
Il trigger della migrazione 057 alza i contatori di ``versioni_accesso``; il token porta
la somma (conservatore + ente) all'emissione (``vm`` per ogni sessione del portale, ``vc``
per i ruoli personalizzati, gli unici i cui permessi dipendono dai concedibili) e la
revoca li confronta nella STESSA query già fatta per utente ed ente: nessun costo in più
per richiesta. Token senza ``vm`` (chiavi API, client PDND, sessioni emesse prima del
rilascio, al più per la loro durata residua): nessun confronto.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from libs.shared.security.jwt_rs256 import ErroreTokenNonValido

logger = structlog.get_logger(__name__)

STATI_ENTE_BLOCCATI = frozenset({"SOSPESO", "DISATTIVATO"})
_MASSIMO_VOCI_CACHE = 10_000

#: Ruolo dei token emessi per le chiavi API (user_id = responsabile della chiave)
RUOLO_CHIAVE_API = "API_SYSTEM"

#: (user_id, tenant_id, ruolo) → (revocato, istante di scadenza della voce)
_cache: dict[tuple[str, str, str], tuple[bool, float]] = {}  # (utente, ente, ruolo|rp|pv|vm|vc)

#: R16 (VD-C-153): somma dei contatori del conservatore e dell'ente per tipo
SQL_VERSIONI_ACCESSO = (
    "SELECT COALESCE(sum(v.versione) FILTER (WHERE v.tipo = 'METODI'), 0), "
    "       COALESCE(sum(v.versione) FILTER (WHERE v.tipo = 'CONCEDIBILI'), 0) "
    "FROM public.versioni_accesso v WHERE v.ambito IN ('*', CAST(:tid AS text))")


async def versioni_accesso(session: Any, tenant_id: str | None) -> tuple[int, int]:
    """(metodi, concedibili) attuali per l'ente: da mettere nel token all'emissione."""
    from sqlalchemy import text

    riga = (await session.execute(text(SQL_VERSIONI_ACCESSO), {"tid": tenant_id})).one()
    return int(riga[0]), int(riga[1])


class RevocaNonVerificabile(RuntimeError):
    """Il database non ha risposto: l'accesso non si concede (fail-closed)."""


def durata_cache() -> float:
    try:
        return max(0.0, float(os.environ.get("VD_REVOCA_CACHE_SECONDI", "30")))
    except ValueError:
        return 30.0


def svuota_cache_revoche() -> None:
    """Svuota la cache (test; o dopo una disattivazione per effetto immediato
    nel processo che l'ha eseguita)."""
    _cache.clear()
    _cache_sessioni.clear()


# ── Sessioni chiuse con «Esci» (revisione R05, R12) ─────────────────────────

#: "i:<impronta>" | "j:<jti>" → (revocata, istante di scadenza della voce)
_cache_sessioni: dict[str, tuple[bool, float]] = {}


def impronta_token(token: str) -> str:
    """SHA-256 esadecimale del token (mai il token in chiaro nel database)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def sessione_revocata(*, impronta: str | None = None, jti: str | None = None) -> bool:
    """True se la sessione (impronta del token o ``jti``) è stata chiusa con
    «Esci» e il token non è ancora scaduto. Errore del database:
    ``RevocaNonVerificabile`` (fail-closed); database non inizializzato: False."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import DBNonInizializzato, get_async_session

    chiavi = [k for k in (f"i:{impronta}" if impronta else None,
                          f"j:{jti}" if jti else None) if k]
    if not chiavi:
        return False
    adesso = time.monotonic()
    voci = [_cache_sessioni.get(k) for k in chiavi]
    if any(v is not None and v[1] > adesso and v[0] for v in voci):
        return True
    if all(v is not None and v[1] > adesso for v in voci):
        return False
    try:
        async with get_async_session() as session:
            revocata = bool((await session.execute(text(
                "SELECT EXISTS (SELECT 1 FROM public.sessioni_revocate "
                "WHERE (impronta = :i OR jti = :j) AND scade_il > now())"),
                {"i": impronta, "j": jti})).scalar())
    except DBNonInizializzato:
        return False
    except SQLAlchemyError as exc:
        logger.error("revoca_sessione_non_verificabile", errore=type(exc).__name__)
        raise RevocaNonVerificabile("controllo della sessione non riuscito") from exc
    if len(_cache_sessioni) >= _MASSIMO_VOCI_CACHE:
        _cache_sessioni.clear()
    for k in chiavi:
        _cache_sessioni[k] = (revocata, adesso + durata_cache())
    return revocata


async def revoca_sessione(token: str, payload: dict[str, Any]) -> None:
    """Registra l'uscita: impronta del token e ``jti`` rifiutati fino a ``exp``.
    ``payload`` è quello del token GIÀ verificato. Idempotente."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session

    try:
        scade = datetime.fromtimestamp(int(payload["exp"]), UTC)
    except (KeyError, TypeError, ValueError):
        return  # token senza scadenza: non è un token di sessione del portale
    impronta = impronta_token(token)
    jti = str(payload["jti"])[:200] if payload.get("jti") else None
    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.sessioni_revocate (impronta, jti, user_id, tenant_id, scade_il) "
            "VALUES (:i, :j, CAST(:u AS uuid), CAST(:t AS uuid), :s) "
            "ON CONFLICT (impronta) DO NOTHING"),
            {"i": impronta, "j": jti, "u": _uuid_testo(payload.get("user_id")),
             "t": _uuid_testo(payload.get("tenant_id")), "s": scade})
        # pulizia delle sessioni scadute (la tabella resta piccola)
        await session.execute(text(
            "DELETE FROM public.sessioni_revocate WHERE scade_il < now() - interval '1 day'"))
    adesso = time.monotonic()
    _cache_sessioni[f"i:{impronta}"] = (True, adesso + max(durata_cache(), 1.0))
    if jti:
        _cache_sessioni[f"j:{jti}"] = (True, adesso + max(durata_cache(), 1.0))
    logger.info("sessione_revocata", user_id=payload.get("user_id"),
                tenant_id=payload.get("tenant_id"))


def _uuid_testo(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


async def _leggi_revoca(user_id: str | None, tenant_id: str | None,
                        ruolo: str | None, rp: str | None = None,
                        pv: int | None = None, vm: int | None = None,
                        vc: int | None = None) -> bool:
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import DBNonInizializzato, get_async_session

    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT u.attivo, u.tenant_id::text, u.ruolo::text, "
                "       u.ruolo_personalizzato_id::text, u.permessi_versione, "
                "       (SELECT t.stato::text FROM public.tenants t "
                "        WHERE t.id = CAST(:tid AS uuid)), va.metodi, va.concedibili "
                "FROM (SELECT 1) AS uno "
                "LEFT JOIN public.users u ON u.id = CAST(:uid AS uuid) "
                # R16 (VD-C-153): versioni attuali nella stessa query (nessun costo in più)
                "CROSS JOIN (SELECT COALESCE(sum(v.versione) FILTER (WHERE v.tipo = 'METODI'), "
                "  0) AS metodi, COALESCE(sum(v.versione) FILTER (WHERE v.tipo = "
                "  'CONCEDIBILI'), 0) AS concedibili FROM public.versioni_accesso v "
                "  WHERE v.ambito IN ('*', CAST(:tid AS text))) AS va"
            ), {"uid": user_id, "tid": tenant_id})).one()
    except DBNonInizializzato:
        # Nessun database in questo processo (test unitari senza DB): il
        # controllo non si applica. Ogni altro errore è fail-closed.
        return False
    except SQLAlchemyError as exc:
        logger.error("revoca_non_verificabile", errore=type(exc).__name__)
        raise RevocaNonVerificabile("controllo di revoca non riuscito") from exc
    attivo, ente_utente, ruolo_utente, rp_utente, pv_utente, stato_ente, metodi, \
        concedibili = riga
    if stato_ente in STATI_ENTE_BLOCCATI:
        return True
    # R16 (VD-C-153): metodi ammessi o concedibili cambiati dopo l'emissione del token
    if vm is not None and int(metodi) != vm:
        return True
    if rp and vc is not None and int(concedibili) != vc:
        return True
    if user_id is None or attivo is None:
        # Utente assente (o identificativo non UUID): nessuna revoca.
        return False
    if not attivo:
        return True
    # N2: utente spostato in un altro ente dopo l'emissione del token.
    if tenant_id is not None and ente_utente != tenant_id:
        return True
    # N2: ruolo cambiato (es. declassamento) dopo l'emissione del token.
    # I token delle chiavi API portano ruolo API_SYSTEM e come user_id il
    # responsabile della chiave: il ruolo del token non è quello dell'utente.
    if ruolo and ruolo != RUOLO_CHIAVE_API and ruolo_utente != ruolo:
        return True
    # R10 (D30): ruolo personalizzato assegnato, tolto o modificato dopo
    # l'emissione (versione dei permessi dell'utente): serve un nuovo accesso.
    # Non vale per le chiavi API (user_id = responsabile della chiave).
    if ruolo != RUOLO_CHIAVE_API and (rp or None) != (rp_utente or None):
        return True
    if rp and pv != pv_utente:
        return True
    return False


async def token_revocato(payload: dict[str, Any]) -> bool:
    """True se l'utente del token è disattivato, ha cambiato ruolo o ente,
    o se il suo ente è bloccato."""
    jti = payload.get("jti")
    if jti and await sessione_revocata(jti=str(jti)[:200]):
        return True  # R12: sessione chiusa con «Esci»
    user_id = _uuid_testo(payload.get("user_id"))
    tenant_id = _uuid_testo(payload.get("tenant_id"))
    if user_id is None and tenant_id is None:
        return False
    ruolo_grezzo = payload.get("ruolo")
    ruolo = str(ruolo_grezzo) if ruolo_grezzo else None
    rp = _uuid_testo(payload.get("rp")) if payload.get("rp") else None
    if payload.get("rp") and rp is None:
        return True  # ruolo personalizzato non riconoscibile: fail-closed
    try:
        pv = int(payload["pv"]) if payload.get("pv") is not None else None
        vm = int(payload["vm"]) if payload.get("vm") is not None else None
        vc = int(payload["vc"]) if payload.get("vc") is not None else None
    except (TypeError, ValueError):
        return True
    if rp and vm is not None and vc is None:
        return True  # R16: sessione con ruolo personalizzato senza versione dei concedibili
    chiave = (user_id or "", tenant_id or "",
              f"{ruolo or ''}|{rp or ''}|{pv or ''}|{'' if vm is None else vm}|"
              f"{'' if vc is None else vc}")
    adesso = time.monotonic()
    voce = _cache.get(chiave)
    if voce is not None and voce[1] > adesso:
        return voce[0]
    revocato = await _leggi_revoca(user_id, tenant_id, ruolo, rp, pv, vm, vc)
    if len(_cache) >= _MASSIMO_VOCI_CACHE:
        _cache.clear()
    _cache[chiave] = (revocato, adesso + durata_cache())
    return revocato


async def verifica_non_revocato(payload: dict[str, Any]) -> None:
    """Solleva ErroreTokenNonValido AUTH-003 se il token è revocato;
    RevocaNonVerificabile se il database non risponde."""
    if await token_revocato(payload):
        logger.warning("token_revocato_rifiutato", user_id=payload.get("user_id"),
                       tenant_id=payload.get("tenant_id"))
        raise ErroreTokenNonValido(
            "AUTH-003", "Accesso revocato",
            "La sessione è stata chiusa, oppure l'utenza o l'ente non sono più attivi o "
            "il ruolo è cambiato: accedere di nuovo o rivolgersi all'amministratore.",
            motivo="sessione chiusa, utente disattivato, ente bloccato, ruolo o ente "
                   "dell'utente cambiati, metodi d'accesso o permessi concedibili cambiati",
        )


def dipendenza_non_revocato(
    verifica: Callable[[str], dict[str, Any]],
) -> Callable[..., Any]:
    """Dipendenza FastAPI da aggiungere a un router: se la richiesta porta un
    Bearer VALIDO (verificato con ``verifica``) controlla la revoca. Senza
    token o con token non valido non fa nulla: risponde l'autenticazione
    dell'endpoint (401/403 come prima)."""
    from fastapi import Header, HTTPException

    async def _non_revocato(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            return
        try:
            payload = verifica(authorization.split(" ", 1)[1].strip())
        except HTTPException:
            return
        await richiedi_non_revocato(payload)

    return _non_revocato


async def richiedi_non_revocato(payload: dict[str, Any]) -> None:
    """Come verifica_non_revocato, ma con le risposte HTTP: 401 AUTH-003 se
    revocato, 503 AUTH-503 se il controllo non si può fare."""
    from fastapi import HTTPException

    try:
        await verifica_non_revocato(payload)
    except ErroreTokenNonValido as exc:
        raise HTTPException(401, {"type": exc.codice, "title": exc.titolo,
                                  "detail": exc.dettaglio},
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except RevocaNonVerificabile:
        raise HTTPException(503, {"type": "AUTH-503",
                                  "title": "Verifica dell'accesso non disponibile",
                                  "detail": "Riprovare tra poco."}) from None
