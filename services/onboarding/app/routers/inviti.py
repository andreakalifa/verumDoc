# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — inviti per e-mail con attivazione guidata (R10, D08).

Riferimenti: decisione D08; migrazione 20261102_049 (``inviti``); attivazione nel
gateway (``app/auth/accesso_locale.py``); notifiche con il topic esistente
``notification.send`` tramite l'outbox (REGOLE §12.3, §21.1: l'invito si crea
anche se la posta è lenta; l'e-mail parte dopo il commit).

  GET    …/tenant/{tenant_id}/inviti               inviti dell'ente
  POST   …/tenant/{tenant_id}/inviti               nuovo utente (non attivo) e invito
  POST   …/tenant/{tenant_id}/inviti/{id}/reinvia  nuovo collegamento (il vecchio decade)
  DELETE …/tenant/{tenant_id}/inviti/{id}          revoca

Chi: un ruolo FISSO con ``utenti:gestisci`` (amministratore dell'ente o il
conservatore), con le stesse regole dei ruoli assegnabili della creazione di un
utente (l'amministratore invita solo OPERATORE e VIEWER o un ruolo
personalizzato che può assegnare).

Il collegamento è ``<console>/attiva#invito=<ente>.<segreto>``: nel FRAMMENTO,
quindi non arriva ai server né ai log; del collegamento resta solo l'impronta
SHA-256. Scadenza ``INVITO_VALIDITA_ORE`` (l'ente può solo ridurla). Il segreto
non torna mai nella risposta all'amministratore.

R12a (VD-C-152): il collegamento NON passa in chiaro dall'outbox, dal topic e
dalla coda della posta: si sigilla con la chiave pubblica del servizio di posta
(``POSTA_CHIAVE_PUBBLICA``, ``libs/shared/security/sigillo.py``) legandolo a
ente, invito e destinatario; il testo porta un segnaposto che la posta
sostituisce al momento dell'invio. Senza chiave l'invito non si crea (503,
controllo PRIMA di ogni scrittura).
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.jwt_handler import verify_access_token
from app.routers.ruoli import richiedi_gestore, ruolo_assegnabile
from app.routers.utenti import (
    _RUOLI_GESTIBILI_DALL_ENTE,
    Chiamante,
    _cf_validato,
    _ruolo_assegnabile,
    _uuid_o_404,
)
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.outbox import accoda_evento
from libs.shared.parametri import parametri
from libs.shared.security.audit import registra_audit
from libs.shared.security.revoca import dipendenza_non_revocato
from libs.shared.security.sigillo import SEGNAPOSTO, ErroreSigillo, contesto_posta, sigilla
from libs.verumdoc_rules.parametri_servizio import INVITO_VALIDITA_ORE
from libs.verumdoc_rules.permessi import UTENTI_GESTISCI

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding/tenant/{tenant_id}/inviti",
                   tags=["Inviti"], responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])

TOPIC_POSTA = "notification.send"
#: Ruoli delle persone che l'amministratore dell'ente gestisce (come in utenti.py, A1)
RUOLI_GESTIBILI = frozenset(r.value for r in _RUOLI_GESTIBILI_DALL_ENTE)


def indirizzo_console() -> str:
    """Indirizzo della console nei collegamenti (configurazione, mai dalla richiesta)."""
    from app.config import settings

    return str(settings.base_url_console).rstrip("/")


def chiave_posta() -> tuple[str, str]:
    """(chiave pubblica, id) della posta; ``ErroreApi`` 503 se manca o non è valida.
    Si chiama PRIMA della transazione: nessun utente o invito senza e-mail sigillabile."""
    from app.config import settings

    pubblica = str(settings.posta_chiave_pubblica or "").strip()
    try:
        if not pubblica:
            raise ErroreSigillo("POSTA_CHIAVE_PUBBLICA non configurata")
        sigilla("prova", pubblica, "prova", id_chiave=settings.posta_chiave_pubblica_id)
    except ErroreSigillo as exc:
        raise errore_registrato("ONB-503", exc, evento="invito_posta_non_configurata") from None
    return pubblica, settings.posta_chiave_pubblica_id


def nuovo_token(tid: uuid.UUID) -> tuple[str, str]:
    """(token, impronta): l'ente nel token sceglie solo l'ambito RLS dell'attivazione."""
    token = f"{tid.hex}.{secrets.token_urlsafe(32)}"
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


class InvitoCrea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    nome: str = Field(..., min_length=2, max_length=100)
    cognome: str = Field(..., min_length=1, max_length=100)
    ruolo: str = Field("OPERATORE", description="OPERATORE o VIEWER (ADMIN_TENANT solo dal "
                                                "conservatore)")
    ruolo_personalizzato_id: uuid.UUID | None = None
    codice_fiscale: str | None = Field(None, description="Serve per accedere con SPID o CIE")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("nome", "cognome")
    @classmethod
    def _testo(cls, v: str) -> str:
        return v.strip()

    @field_validator("codice_fiscale")
    @classmethod
    def _cf(cls, v: str | None) -> str | None:
        return _cf_validato(v)


def _stato(riga: Any, adesso: datetime) -> str:
    if riga["stato"] == "IN_ATTESA" and riga["scade_il"] <= adesso:
        return "SCADUTO"
    return str(riga["stato"])


def _json(riga: Any, adesso: datetime) -> dict[str, Any]:
    return {"id": str(riga["id"]), "user_id": str(riga["user_id"]), "email": riga["email"],
            "nome": riga["nome"], "cognome": riga["cognome"], "stato": _stato(riga, adesso),
            "scade_il": riga["scade_il"].isoformat(),
            "usato_il": riga["usato_il"].isoformat() if riga["usato_il"] else None,
            "metodo_attivato": riga["metodo_attivato"],
            "creato_il": riga["created_at"].isoformat()}


def _errore(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("ONB-503", exc, evento=evento)


async def _scadenza(tid: uuid.UUID) -> datetime:
    ore = int((await parametri([INVITO_VALIDITA_ORE], tid))[INVITO_VALIDITA_ORE])
    return datetime.now(UTC) + timedelta(hours=ore)


async def _invia(session: Any, *, tid: uuid.UUID, invito_id: uuid.UUID, email: str, nome: str,
                 ragione_sociale: str, token: str, scade_il: datetime,
                 chiave: tuple[str, str]) -> None:
    """E-mail d'invito sull'outbox (topic esistente ``notification.send``) con il
    collegamento SIGILLATO per la posta (VD-C-152)."""
    oggetto_id = f"invito:{invito_id}"
    sigillo = sigilla(f"{indirizzo_console()}/attiva#invito={token}", chiave[0],
                      contesto_posta(str(tid), oggetto_id, email), id_chiave=chiave[1])
    collegamento = SEGNAPOSTO % "collegamento"
    scadenza = scade_il.astimezone(UTC).strftime("%d/%m/%Y %H:%M UTC")
    testo = (f"Gentile {nome},\n\n{ragione_sociale} ti invita a usare VerumDoc, il servizio "
             f"di conservazione dei documenti.\n\nPer attivare il tuo accesso apri questo "
             f"collegamento entro il {scadenza}:\n{collegamento}\n\nTi chiederemo l'indirizzo "
             f"e-mail a cui hai ricevuto questo messaggio e di scegliere come accedere.\n"
             f"Se non aspettavi questo invito, ignora il messaggio.")
    await accoda_evento(session, topic=TOPIC_POSTA, chiave=str(invito_id), tenant_id=tid,
                        payload={"evento": "accesso.invito", "tipo": "email",
                                 "tenant_id": str(tid), "oggetto_id": oggetto_id,
                                 "destinatario": email,
                                 "oggetto": f"Invito a VerumDoc da {ragione_sociale}",
                                 "testo": testo, "segreti": {"collegamento": sigillo}})


_SQL_ELENCO = ("SELECT i.*, u.nome, u.cognome FROM public.inviti i JOIN public.users u "
               "ON u.id = i.user_id WHERE i.tenant_id = :t")


@router.get("", summary="Inviti dell'ente")
async def elenco(tenant_id: str, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    adesso = datetime.now(UTC)
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                _SQL_ELENCO + " ORDER BY i.created_at DESC LIMIT 500"), {"t": tid})
            ).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore("inviti_elenco_errore", exc) from None
    return {"items": [_json(r, adesso) for r in righe]}


@router.post("", status_code=201, summary="Invita una persona (utente non attivo + e-mail)")
async def crea(tenant_id: str, body: InvitoCrea, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    richiedi_gestore(caller, UTENTI_GESTISCI)
    ruolo = _ruolo_assegnabile(caller, body.ruolo)
    scade_il = await _scadenza(tid)
    chiave = chiave_posta()
    token, impronta = nuovo_token(tid)
    try:
        async with get_async_session() as session:
            ente = (await session.execute(text(
                "SELECT ragione_sociale FROM public.tenants WHERE id = :t"), {"t": tid})
            ).scalar_one_or_none()
            if ente is None:
                raise ErroreApi("ONB-404", "Tenant non trovato")
            if body.ruolo_personalizzato_id is not None:
                ruolo = _ruolo_assegnabile(caller, await ruolo_assegnabile(
                    session, caller, tid, body.ruolo_personalizzato_id))
            uid = uuid.uuid4()
            await session.execute(text(
                "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo, "
                " codice_fiscale, ruolo_personalizzato_id) VALUES (:u, :t, :e, :n, :c, "
                " CAST(:r AS public.user_ruolo), false, :cf, :rp)"),
                {"u": uid, "t": tid, "e": body.email, "n": body.nome, "c": body.cognome,
                 "r": ruolo.value, "cf": body.codice_fiscale,
                 "rp": body.ruolo_personalizzato_id})
            invito_id = (await session.execute(text(
                "INSERT INTO public.inviti (tenant_id, user_id, email, impronta, scade_il, "
                " creato_da) VALUES (:t, :u, :e, :h, :s, :c) RETURNING id"),
                {"t": tid, "u": uid, "e": body.email, "h": impronta, "s": scade_il,
                 "c": _uuid(caller.get("user_id"))})).scalar_one()
            await _invia(session, tid=tid, invito_id=invito_id, email=body.email,
                         nome=body.nome, ragione_sociale=ente, token=token, scade_il=scade_il,
                         chiave=chiave)
            await registra_audit(session, evento="invito.creato", tenant_id=str(tid),
                                 user_id=_testo(caller.get("user_id")),
                                 dettagli={"invito_id": str(invito_id), "utente_id": str(uid),
                                           "ruolo": ruolo.value,
                                           "ruolo_personalizzato_id": (
                                               str(body.ruolo_personalizzato_id)
                                               if body.ruolo_personalizzato_id else None),
                                           "scade_il": scade_il.isoformat()})
    except IntegrityError as exc:
        if "uq_users_tenant_email" in str(exc.orig) or "uq_users_tenant_cf" in str(exc.orig):
            raise ErroreApi("ACC-409-INVITO", "Esiste già un utente con questa e-mail o questo "
                                              "codice fiscale nell'ente") from None
        raise _errore("invito_crea_errore", exc) from None
    except SQLAlchemyError as exc:
        raise _errore("invito_crea_errore", exc) from None
    logger.info("invito_creato", tenant_id=str(tid), invito_id=str(invito_id))
    return {"id": str(invito_id), "user_id": str(uid), "email": body.email,
            "stato": "IN_ATTESA", "scade_il": scade_il.isoformat()}


@router.post("/{invito_id}/reinvia", summary="Nuovo collegamento d'invito (il vecchio decade)")
async def reinvia(tenant_id: str, invito_id: str, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    iid = _uuid_o_404(invito_id, "Invito non trovato")
    richiedi_gestore(caller, UTENTI_GESTISCI)
    scade_il = await _scadenza(tid)
    chiave = chiave_posta()
    token, impronta = nuovo_token(tid)
    try:
        async with get_async_session() as session:
            vecchio = (await session.execute(text(
                _SQL_ELENCO + " AND i.id = :i FOR UPDATE OF i"), {"t": tid, "i": iid})
            ).mappings().one_or_none()
            if vecchio is None:
                raise ErroreApi("ONB-404", "Invito non trovato")
            persona = await _persona_gestibile(session, caller, tid, vecchio["user_id"])
            attivo = persona["attivo"]
            if attivo:
                # un utente DISATTIVATO (es. dispositivo perso) si può invitare di nuovo:
                # l'attivazione sostituisce tutte le sue credenziali locali
                raise ErroreApi("ACC-409-INVITO", "La persona ha l'accesso attivo: per "
                                                  "rifarlo, prima disattiva l'utente")
            await session.execute(text(
                "UPDATE public.inviti SET stato = 'REVOCATO' WHERE user_id = :u "
                "AND stato = 'IN_ATTESA'"), {"u": vecchio["user_id"]})
            nuovo_id = (await session.execute(text(
                "INSERT INTO public.inviti (tenant_id, user_id, email, impronta, scade_il, "
                " creato_da) VALUES (:t, :u, :e, :h, :s, :c) RETURNING id"),
                {"t": tid, "u": vecchio["user_id"], "e": vecchio["email"], "h": impronta,
                 "s": scade_il, "c": _uuid(caller.get("user_id"))})).scalar_one()
            ente = (await session.execute(text(
                "SELECT ragione_sociale FROM public.tenants WHERE id = :t"), {"t": tid})
            ).scalar_one()
            await _invia(session, tid=tid, invito_id=nuovo_id, email=vecchio["email"],
                         nome=vecchio["nome"], ragione_sociale=ente, token=token,
                         scade_il=scade_il, chiave=chiave)
            await registra_audit(session, evento="invito.reinviato", tenant_id=str(tid),
                                 user_id=_testo(caller.get("user_id")),
                                 dettagli={"invito_id": str(nuovo_id),
                                           "precedente": str(iid)})
    except SQLAlchemyError as exc:
        raise _errore("invito_reinvia_errore", exc) from None
    return {"id": str(nuovo_id), "stato": "IN_ATTESA", "scade_il": scade_il.isoformat()}


@router.delete("/{invito_id}", status_code=204, response_class=Response, response_model=None,
               summary="Revoca un invito in attesa")
async def revoca(tenant_id: str, invito_id: str, caller: Chiamante) -> None:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    iid = _uuid_o_404(invito_id, "Invito non trovato")
    richiedi_gestore(caller, UTENTI_GESTISCI)
    try:
        async with get_async_session() as session:
            utente = (await session.execute(text(
                "SELECT user_id FROM public.inviti WHERE id = :i AND tenant_id = :t "
                "AND stato = 'IN_ATTESA' FOR UPDATE"), {"i": iid, "t": tid})).scalar_one_or_none()
            if utente is None:
                raise ErroreApi("ONB-404", "Invito non trovato o non più in attesa")
            await _persona_gestibile(session, caller, tid, utente)
            await session.execute(text(
                "UPDATE public.inviti SET stato = 'REVOCATO' WHERE id = :i"), {"i": iid})
            await registra_audit(session, evento="invito.revocato", tenant_id=str(tid),
                                 user_id=_testo(caller.get("user_id")),
                                 dettagli={"invito_id": str(iid)})
    except SQLAlchemyError as exc:
        raise _errore("invito_revoca_errore", exc) from None


async def _persona_gestibile(session: Any, caller: dict[str, Any], tid: uuid.UUID,
                             user_id: Any) -> dict[str, Any]:
    """Revisione R10 (V2): stesse regole della gestione utenti (``aggiorna_utente``).
    Chi non è SUPER_ADMIN gestisce solo OPERATORE e VIEWER (mai un amministratore:
    404, come un utente inesistente); con un ruolo personalizzato, deve poterlo
    assegnare ancora oggi (composizione con i concedibili attuali)."""
    riga = (await session.execute(text(
        "SELECT attivo, ruolo::text AS ruolo, ruolo_personalizzato_id FROM public.users "
        "WHERE id = :u AND tenant_id = :t FOR UPDATE"), {"u": user_id, "t": tid})
    ).mappings().one_or_none()
    if riga is None:
        raise ErroreApi("ONB-404", "Invito non trovato")
    if caller.get("ruolo") != "SUPER_ADMIN" and riga["ruolo"] not in RUOLI_GESTIBILI:
        logger.warning("invito_persona_non_gestibile", ruolo=riga["ruolo"],
                       ruolo_chiamante=caller.get("ruolo"))
        raise ErroreApi("ONB-404", "Invito non trovato")
    if riga["ruolo_personalizzato_id"] is not None:
        await ruolo_assegnabile(session, caller, tid, riga["ruolo_personalizzato_id"])
    return dict(riga)


def _uuid(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


def _testo(valore: Any) -> str | None:
    u = _uuid(valore)
    return str(u) if u else None


__all__ = ["indirizzo_console", "nuovo_token", "router"]
