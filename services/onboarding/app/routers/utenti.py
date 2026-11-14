# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — F3-03: gestione utenti + API key per tenant.

VD-C-001: il chiamante si ricava SOLO da un JWT verificato (firma RS256 con la
chiave pubblica da configurazione, iss, aud, exp). Ruolo e tenant vengono dal
payload verificato; un ADMIN_TENANT/RSC_CONSERVATORE agisce solo sul proprio
tenant (un altro tenant risponde 404, REGOLE §14.4); SUPER_ADMIN su tutti.

R02 (g2-auth):
  - utenti con nome, cognome e codice fiscale (validato con
    libs.shared.security.codice_fiscale): il codice fiscale lega l'accesso
    SPID/CIE all'utente (gateway, app/auth/spid.py);
  - email o codice fiscale già presenti nell'ente → 409 (prima 201 senza
    creare nulla, con `ON CONFLICT (email)` che non corrispondeva ad alcun vincolo);
  - chiavi API nella rappresentazione UNICA condivisa con il gateway
    (libs.shared.security.chiavi_api: identificativo pubblico + HMAC con pepper
    API_KEY_PEPPER): una chiave generata qui autentica davvero sul gateway;
  - errori del database: mai il testo SQL al client (log sì), ma 503 con un
    codice di riferimento; mai 200 con un campo di errore.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.jwt_handler import utente_da_authorization, verify_access_token
from app.config import settings
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.models import ApiKey, AuditGlobal, Tenant, User, UserRuolo
from libs.shared.security.audit import registra_audit
from libs.shared.security.chiavi_api import (
    PREFISSO_CHIAVE,
    carica_pepper,
    genera_chiave_api,
)
from libs.shared.security.codice_fiscale import (
    CodiceFiscaleNonValido,
    maschera_codice_fiscale,
    valida_codice_fiscale,
)
from libs.shared.security.inattivita import chiudi_sessioni_utente
from libs.shared.security.permessi import ruolo_personalizzato
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding/tenant", tags=["Utenti Tenant"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])

_RUOLI_GESTORI_DEL_PROPRIO_TENANT = frozenset({"ADMIN_TENANT", "RSC_CONSERVATORE"})
#: Ruoli ATTUALI degli utenti che un gestore d'ente (non SUPER_ADMIN) può
#: modificare o disattivare: mai pari o superiori al proprio (revisione A1).
_RUOLI_GESTIBILI_DALL_ENTE = (UserRuolo.OPERATORE, UserRuolo.VIEWER)
#: Ruoli assegnabili da questa API, per ruolo del chiamante. I ruoli globali
#: (SUPER_ADMIN, RSC_CONSERVATORE) e API_SYSTEM non si assegnano mai da qui.
_RUOLI_ASSEGNABILI: dict[str, frozenset[str]] = {
    "SUPER_ADMIN": frozenset({"ADMIN_TENANT", "OPERATORE", "VIEWER"}),
    "ADMIN_TENANT": frozenset({"OPERATORE", "VIEWER"}),
    "RSC_CONSERVATORE": frozenset({"OPERATORE", "VIEWER"}),
}
_PATTERN_RUOLI = "^(" + "|".join(r.value for r in UserRuolo) + ")$"
#: Livello dei ruoli per la scelta del responsabile di una chiave API
#: (revisione N1): chi genera la chiave può indicare come responsabile solo un
#: utente di livello NON superiore al proprio. RSC_CONSERVATORE (ruolo globale
#: del conservatore) sta sopra ADMIN_TENANT; un ruolo sconosciuto è trattato
#: come il più alto (mai attribuibile).
_LIVELLO_RUOLO: dict[str, int] = {
    "SUPER_ADMIN": 5,
    "RSC_CONSERVATORE": 4,
    "ADMIN_TENANT": 3,
    "OPERATORE": 2,
    "VIEWER": 1,
    "API_SYSTEM": 1,
}
_LIVELLO_MASSIMO = max(_LIVELLO_RUOLO.values()) + 1
#: Permessi delle chiavi API: quelli del ruolo API_SYSTEM (versare e consultare).
PERMESSI_CHIAVE_API = ["versamento:ud", "versamento:doc", "read"]

# Caricato all'import: fuori da development/test senza pepper il servizio non parte.
_PEPPER: bytes | None = carica_pepper(
    env=settings.env,
    valore=settings.api_key_pepper.get_secret_value(),
    servizio=settings.service_name,
)


async def _chiamante(
    tenant_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Chiamante dal token VERIFICATO (mai decodifica senza firma), autorizzato
    sul tenant dell'URL. Come dipendenza è risolta prima della validazione del
    corpo: senza token 401, ruolo non ammesso 403, altro tenant 404 (mai 422)."""
    caller = utente_da_authorization(authorization)
    _check_can_manage_tenant(caller, tenant_id)
    if ruolo_personalizzato(caller):
        # R10 (D30): la gestione dell'ente non si delega con un ruolo personalizzato
        raise HTTPException(403, {"type": "AUTH-010", "title": "Ruolo non autorizzato"})
    return caller


Chiamante = Annotated[dict[str, Any], Depends(_chiamante)]


def _check_can_manage_tenant(caller: dict[str, Any], tenant_id: str) -> None:
    """Verifica che il chiamante (token verificato) possa gestire il tenant_id."""
    ruolo = caller.get("ruolo", "")
    if ruolo == "SUPER_ADMIN":
        return
    if ruolo in _RUOLI_GESTORI_DEL_PROPRIO_TENANT:
        tenant_token = str(caller.get("tenant_id") or "").strip().lower()
        if not tenant_token or tenant_token != str(tenant_id).strip().lower():
            logger.warning("utenti_accesso_altro_tenant_negato", ruolo=ruolo)
            # 404 e non 403: l'indirizzo non rivela se il tenant esiste (§14.4)
            raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})
        return
    raise HTTPException(403, {"type": "AUTH-010", "title": f"Ruolo {ruolo!r} non autorizzato"})


def _uuid_o_404(valore: str, titolo: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(valore))
    except ValueError as exc:
        raise HTTPException(404, {"type": "ONB-404", "title": titolo}) from exc


def _e_super_admin(caller: dict[str, Any]) -> bool:
    return caller.get("ruolo") == "SUPER_ADMIN"


def _ruolo_assegnabile(caller: dict[str, Any], ruolo: str) -> UserRuolo:
    """Il ruolo richiesto se il chiamante può assegnarlo, altrimenti 403."""
    if ruolo not in _RUOLI_ASSEGNABILI.get(str(caller.get("ruolo") or ""), frozenset()):
        logger.warning("utente_ruolo_non_assegnabile", ruolo_chiamante=caller.get("ruolo"),
                       ruolo_richiesto=ruolo)
        raise HTTPException(403, {"type": "AUTH-010",
                                  "title": f"Il ruolo {ruolo!r} non può essere assegnato "
                                           f"da questo profilo"})
    return UserRuolo(ruolo)


def _uuid_o_none(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


def _stato_per_audit(u: User) -> dict[str, Any]:
    """Stato dell'utente per l'audit: il codice fiscale solo mascherato."""
    return {
        "nome": u.nome, "cognome": u.cognome, "email": u.email,
        "ruolo": getattr(u.ruolo, "value", u.ruolo), "attivo": u.attivo,
        "codice_fiscale": maschera_codice_fiscale(u.codice_fiscale) if u.codice_fiscale else None,
    }


def _audit_utente(session: Any, evento: str, tid: uuid.UUID, uid: uuid.UUID,
                  caller: dict[str, Any], **dettagli: Any) -> None:
    """Riga di audit_global nella STESSA transazione della modifica: se l'audit
    non si scrive, la modifica non avviene."""
    session.add(AuditGlobal(
        id=uuid.uuid4(), tenant_id=tid, user_id=_uuid_o_none(caller.get("user_id")),
        evento=evento,
        dettagli={"utente_id": str(uid), "ruolo_chiamante": caller.get("ruolo"),
                  "tenant_chiamante": str(caller.get("tenant_id") or ""), **dettagli},
    ))


def _errore_servizio(evento: str, exc: Exception, **contesto: Any) -> ErroreApi:
    """Errore del database: dettagli nel log, al client il codice e l'id della
    richiesta da comunicare (VD-C-100)."""
    return errore_registrato(
        "ONB-503", exc, evento=evento,
        dettaglio="Riprovare tra poco; se il problema continua comunicare l'id della richiesta.",
        **contesto)


def _conflitto(exc: IntegrityError) -> HTTPException | None:
    """409 per i vincoli di unicità noti (nessun dato di altri clienti nel messaggio, §14.14)."""
    testo = str(getattr(exc, "orig", exc))
    if "uq_users_tenant_email" in testo:
        return HTTPException(409, {"type": "ONB-409",
                                   "title": "Esiste già un utente con questa email in questo ente"})
    if "uq_users_tenant_cf" in testo:
        return HTTPException(409, {"type": "ONB-409",
                                   "title": "Esiste già un utente con questo codice fiscale "
                                            "in questo ente"})
    return None


def _cf_validato(valore: str | None) -> str | None:
    if valore is None or not str(valore).strip():
        return None
    try:
        return valida_codice_fiscale(valore)
    except CodiceFiscaleNonValido as exc:
        raise ValueError(str(exc)) from exc


class UtenteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    nome: str = Field(..., min_length=2, max_length=100)
    cognome: str = Field(..., min_length=1, max_length=100)
    ruolo: str = Field(..., pattern=_PATTERN_RUOLI,
                       description="OPERATORE o VIEWER; ADMIN_TENANT solo se assegnato dal "
                                   "SUPER_ADMIN. I ruoli globali non si assegnano da qui.")
    codice_fiscale: str | None = Field(
        None, description="Codice fiscale della persona (16 caratteri, D.M. 23/12/1976): "
                          "senza, l'utente non può accedere con SPID o CIE.")
    ruolo_personalizzato_id: uuid.UUID | None = Field(
        None, description="R10 (D30): ruolo personalizzato dell'ente; il ruolo diventa il "
                          "suo ruolo base (OPERATORE o VIEWER)")

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


class UtenteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str | None = Field(default=None, min_length=2, max_length=100)
    cognome: str | None = Field(default=None, min_length=1, max_length=100)
    ruolo: str | None = Field(default=None, pattern=_PATTERN_RUOLI)
    attivo: bool | None = None
    codice_fiscale: str | None = Field(
        default=None, description="Nuovo codice fiscale; stringa vuota o null lo toglie.")
    ruolo_personalizzato_id: uuid.UUID | None = Field(
        default=None, description="R10 (D30): ruolo personalizzato (null lo toglie e lascia "
                                  "il ruolo fisso)")

    @field_validator("codice_fiscale")
    @classmethod
    def _cf(cls, v: str | None) -> str | None:
        return _cf_validato(v)


def _testo(valore: Any) -> str | None:
    u = _uuid_o_none(valore)
    return str(u) if u else None


def _iso(valore: datetime | None) -> str | None:
    return valore.isoformat() if valore else None


def _utente_json(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id), "email": u.email, "nome": u.nome, "cognome": u.cognome,
        "codice_fiscale": u.codice_fiscale,
        "ruolo": getattr(u.ruolo, "value", u.ruolo), "attivo": u.attivo,
        "ruolo_personalizzato_id": (str(u.ruolo_personalizzato_id)
                                    if u.ruolo_personalizzato_id else None),
        "ultimo_login": _iso(u.ultimo_login), "created_at": _iso(u.created_at),
    }


# ── Endpoint utenti ──────────────────────────────────────────────────

@router.get("/{tenant_id}/utenti", summary="Lista utenti del tenant")
async def lista_utenti(tenant_id: str, caller: Chiamante) -> dict:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    try:
        async with get_async_session() as session:
            utenti = (await session.execute(
                select(User).where(User.tenant_id == tid).order_by(User.cognome, User.nome)
            )).scalars().all()
            return {"total": len(utenti), "items": [_utente_json(u) for u in utenti]}
    except SQLAlchemyError as exc:
        raise _errore_servizio("lista_utenti_errore", exc, tenant_id=tenant_id) from exc


@router.post("/{tenant_id}/utenti", status_code=201, summary="Crea utente nel tenant")
async def crea_utente(tenant_id: str, body: UtenteCreate, caller: Chiamante) -> dict:
    """201 con l'utente creato; 409 se email o codice fiscale sono già usati nell'ente.

    Il gestore d'ente (ADMIN_TENANT/RSC_CONSERVATORE) può indicare il codice
    fiscale SOLO creando un OPERATORE o un VIEWER (ruoli inferiori al suo): non
    consegna a nessuno un'utenza esistente né privilegi che non ha già.
    """
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    ruolo = _ruolo_assegnabile(caller, body.ruolo)
    try:
        async with get_async_session() as session:
            if await session.get(Tenant, tid) is None:
                raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})
            if body.ruolo_personalizzato_id is not None:
                from app.routers.ruoli import ruolo_assegnabile

                ruolo = _ruolo_assegnabile(caller, await ruolo_assegnabile(
                    session, caller, tid, body.ruolo_personalizzato_id))
            utente = User(
                id=uuid.uuid4(), tenant_id=tid, email=body.email, nome=body.nome,
                cognome=body.cognome, ruolo=ruolo, attivo=True,
                codice_fiscale=body.codice_fiscale,
                ruolo_personalizzato_id=body.ruolo_personalizzato_id,
            )
            session.add(utente)
            await session.flush()
            await session.refresh(utente)
            risposta = _utente_json(utente)
            _audit_utente(session, "utente.creato", tid, utente.id, caller,
                          nuovo=_stato_per_audit(utente))
    except IntegrityError as exc:
        conflitto = _conflitto(exc)
        if conflitto is not None:
            logger.info("utente_duplicato", tenant_id=tenant_id,
                        codice_fiscale=maschera_codice_fiscale(body.codice_fiscale))
            raise conflitto from exc
        raise _errore_servizio("crea_utente_errore", exc, tenant_id=tenant_id) from exc
    except SQLAlchemyError as exc:
        raise _errore_servizio("crea_utente_errore", exc, tenant_id=tenant_id) from exc
    logger.info("utente_creato", tenant_id=tenant_id, user_id=risposta["id"],
                creato_da=caller.get("user_id"))
    return risposta


@router.put("/{tenant_id}/utenti/{user_id}", summary="Modifica utente")
async def aggiorna_utente(
    tenant_id: str, user_id: str, body: UtenteUpdate, caller: Chiamante,
) -> dict:
    """Modifica un utente dell'ente (revisione di sicurezza A1).

    - SUPER_ADMIN: qualsiasi utente; può cambiare il codice fiscale e assegnare
      ADMIN_TENANT, OPERATORE o VIEWER (mai i ruoli globali da qui).
    - ADMIN_TENANT / RSC_CONSERVATORE (solo il proprio ente): solo utenti il cui
      ruolo ATTUALE è OPERATORE o VIEWER (altrimenti 404, come un utente
      inesistente); possono assegnare solo OPERATORE o VIEWER; il codice fiscale
      di un utente esistente NON lo cambiano (403): è il legame con l'identità
      SPID/CIE e cambiarlo equivale a consegnare l'utenza a un'altra persona.
    Ogni modifica è registrata in audit_global nella stessa transazione.
    """
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    uid = _uuid_o_404(user_id, "Utente non trovato")
    campi = body.model_dump(exclude_unset=True)
    for obbligatorio in ("nome", "cognome", "ruolo", "attivo"):
        if obbligatorio in campi and campi[obbligatorio] is None:
            raise HTTPException(422, {"type": "ONB-422",
                                      "title": f"Il campo {obbligatorio!r} non può essere vuoto"})
    if not campi:
        raise HTTPException(400, {"type": "ONB-400", "title": "Nessun campo da aggiornare"})
    super_admin = _e_super_admin(caller)
    if "codice_fiscale" in campi and not super_admin:
        logger.warning("utente_cambio_cf_negato", tenant_id=tenant_id, user_id=user_id,
                       ruolo=caller.get("ruolo"))
        raise HTTPException(403, {
            "type": "AUTH-010",
            "title": "Il codice fiscale di un utente esistente può cambiarlo solo il "
                     "conservatore (SUPER_ADMIN)",
        })
    if "ruolo" in campi:
        campi["ruolo"] = _ruolo_assegnabile(caller, campi["ruolo"])
    condizioni = [User.id == uid, User.tenant_id == tid]
    if not super_admin:
        condizioni.append(User.ruolo.in_(_RUOLI_GESTIBILI_DALL_ENTE))
    try:
        async with get_async_session() as session:
            prima = (await session.execute(
                select(User).where(*condizioni).with_for_update()
            )).scalar_one_or_none()
            if prima is None:
                raise HTTPException(404, {"type": "ONB-404", "title": "Utente non trovato"})
            precedente = _stato_per_audit(prima)
            ruolo_prima = str(getattr(prima.ruolo, "value", prima.ruolo))
            rp_prima = prima.ruolo_personalizzato_id
            if "ruolo" in campi and "ruolo_personalizzato_id" not in campi and rp_prima:
                campi["ruolo_personalizzato_id"] = None  # un ruolo fisso toglie il personalizzato
            if campi.get("ruolo_personalizzato_id") is not None:
                # R10 (D30): assegnare è come comporre (nessuna escalation); il
                # ruolo dell'utente diventa il ruolo base del ruolo personalizzato
                from app.routers.ruoli import ruolo_assegnabile

                campi["ruolo"] = _ruolo_assegnabile(caller, await ruolo_assegnabile(
                    session, caller, tid, campi["ruolo_personalizzato_id"]))
            rp_cambiato = ("ruolo_personalizzato_id" in campi
                           and campi["ruolo_personalizzato_id"] != rp_prima)
            if rp_cambiato:
                campi["permessi_versione"] = User.permessi_versione + 1
            await session.execute(update(User).where(*condizioni).values(**campi))
            if rp_cambiato:
                await registra_audit(session, evento="utente.ruolo_assegnato",
                                     tenant_id=str(tid), user_id=_testo(caller.get("user_id")),
                                     dettagli={"utente_id": str(uid),
                                               "prima": str(rp_prima) if rp_prima else None,
                                               "dopo": (str(campi["ruolo_personalizzato_id"])
                                                        if campi["ruolo_personalizzato_id"]
                                                        else None)})
            if campi.get("attivo") is False or rp_cambiato or (
                    "ruolo" in campi
                    and str(getattr(campi["ruolo"], "value", campi["ruolo"])) != ruolo_prima):
                # R06 (VD-C-127): sessioni del portale chiuse subito in ogni servizio
                await chiudi_sessioni_utente(session, str(uid))
            await session.flush()
            await session.refresh(prima)
            _audit_utente(session, "utente.modificato", tid, uid, caller,
                          precedente=precedente, nuovo=_stato_per_audit(prima),
                          campi=sorted(campi))
    except IntegrityError as exc:
        conflitto = _conflitto(exc)
        if conflitto is not None:
            raise conflitto from exc
        raise _errore_servizio("aggiorna_utente_errore", exc, tenant_id=tenant_id) from exc
    except SQLAlchemyError as exc:
        raise _errore_servizio("aggiorna_utente_errore", exc, tenant_id=tenant_id) from exc
    logger.info("utente_modificato", tenant_id=tenant_id, user_id=user_id,
                campi=sorted(campi), modificato_da=caller.get("user_id"))
    return {"id": user_id, "aggiornato": True}


@router.delete("/{tenant_id}/utenti/{user_id}", status_code=204,
               response_class=Response, response_model=None,
               summary="Disattiva utente (soft delete)")
async def disattiva_utente(tenant_id: str, user_id: str, caller: Chiamante) -> None:
    """Disattiva un utente; chi non è SUPER_ADMIN solo OPERATORE/VIEWER del
    proprio ente (altrimenti 404). Registrato in audit_global."""
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    uid = _uuid_o_404(user_id, "Utente non trovato")
    condizioni = [User.id == uid, User.tenant_id == tid]
    if not _e_super_admin(caller):
        condizioni.append(User.ruolo.in_(_RUOLI_GESTIBILI_DALL_ENTE))
    try:
        async with get_async_session() as session:
            utente = (await session.execute(
                select(User).where(*condizioni).with_for_update()
            )).scalar_one_or_none()
            if utente is None:
                raise HTTPException(404, {"type": "ONB-404", "title": "Utente non trovato"})
            precedente = _stato_per_audit(utente)
            await session.execute(update(User).where(*condizioni).values(attivo=False))
            # R06 (VD-C-127): sessioni del portale chiuse subito in ogni servizio
            await chiudi_sessioni_utente(session, str(uid))
            # R10 (D08): un invito in attesa non vale più per un utente disattivato
            await session.execute(text(
                "UPDATE public.inviti SET stato = 'REVOCATO' WHERE user_id = :u "
                "AND stato = 'IN_ATTESA'"), {"u": uid})
            _audit_utente(session, "utente.disattivato", tid, uid, caller,
                          precedente=precedente, nuovo={**precedente, "attivo": False},
                          campi=["attivo"])
    except SQLAlchemyError as exc:
        raise _errore_servizio("disattiva_utente_errore", exc, tenant_id=tenant_id) from exc
    logger.info("utente_disattivato", tenant_id=tenant_id, user_id=user_id,
                disattivato_da=caller.get("user_id"))


# ── Endpoint API keys ────────────────────────────────────────────────

class ChiaveApiCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descrizione: str = Field("", max_length=200,
                             description="Sistema che userà la chiave (es. protocollo informatico)")
    user_id: uuid.UUID | None = Field(
        None, description="Utente dell'ente che risponde della chiave; se assente, chi la genera")


@router.get("/{tenant_id}/api-keys", summary="Lista API keys del tenant")
async def lista_api_keys(tenant_id: str, caller: Chiamante) -> dict:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    try:
        async with get_async_session() as session:
            chiavi = (await session.execute(
                select(ApiKey).where(ApiKey.tenant_id == tid).order_by(ApiKey.created_at.desc())
            )).scalars().all()
    except SQLAlchemyError as exc:
        raise _errore_servizio("lista_api_keys_errore", exc, tenant_id=tenant_id) from exc
    return {
        "total": len(chiavi),
        "items": [{
            "id": str(k.id),
            "key_prefix": k.key_prefix,
            "api_key_id": f"{PREFISSO_CHIAVE}{k.key_prefix}",
            "descrizione": k.nome,
            "attiva": k.attiva,
            "scadenza": _iso(k.scadenza),
            "created_at": _iso(k.created_at),
            "last_used_at": _iso(k.ultimo_uso),
        } for k in chiavi],
    }


async def _responsabile_chiave(
    session: Any, tid: uuid.UUID, body: ChiaveApiCreate, caller: dict[str, Any]
) -> uuid.UUID:
    """Utente che risponde della chiave (api_keys.user_id NOT NULL)."""
    if body.user_id is not None:
        utente = await session.get(User, body.user_id)
        if utente is None or utente.tenant_id != tid:
            # 404: l'indirizzo non rivela utenti di altri enti (§14.4)
            raise HTTPException(404, {"type": "ONB-404",
                                      "title": "Utente responsabile non trovato in questo ente"})
        if not utente.attivo:
            logger.warning("api_key_responsabile_rifiutato", motivo="utente non attivo",
                           responsabile=str(body.user_id), creata_da=caller.get("user_id"))
            raise HTTPException(403, {"type": "AUTH-010",
                                      "title": "Il responsabile della chiave deve essere un "
                                               "utente attivo dell'ente"})
        ruolo_resp = getattr(utente.ruolo, "value", utente.ruolo)
        ruolo_chiamante = str(caller.get("ruolo") or "")
        # N1: mai un responsabile di ruolo superiore a chi genera (es. un
        # ADMIN_TENANT/RSC non attribuisce versamenti e audit a un SUPER_ADMIN).
        if (_LIVELLO_RUOLO.get(str(ruolo_resp), _LIVELLO_MASSIMO)
                > _LIVELLO_RUOLO.get(ruolo_chiamante, 0)):
            logger.warning("api_key_responsabile_rifiutato", motivo="ruolo superiore",
                           ruolo_responsabile=str(ruolo_resp), ruolo_chiamante=ruolo_chiamante,
                           creata_da=caller.get("user_id"))
            raise HTTPException(403, {"type": "AUTH-010",
                                      "title": "Il responsabile della chiave non può avere un "
                                               "ruolo superiore a quello di chi la genera"})
        return body.user_id
    try:
        chiamante = uuid.UUID(str(caller.get("user_id") or ""))
    except ValueError:
        chiamante = None
    if chiamante is not None:
        utente = await session.get(User, chiamante)
        # Il chiamante risponde della chiave solo se è un utente attivo DI
        # QUESTO ente (revisione B6: un SUPER_ADMIN di un altro ente no).
        if utente is not None and utente.attivo and utente.tenant_id == tid:
            return chiamante
    raise HTTPException(422, {
        "type": "ONB-422",
        "title": "Indicare l'utente dell'ente che risponde della chiave",
        "detail": "Chi genera la chiave non è un utente registrato: passare user_id di un "
                  "utente attivo dell'ente.",
    })


@router.post("/{tenant_id}/api-keys", status_code=201,
             summary="Genera nuova API key per il tenant")
async def genera_api_key(tenant_id: str, body: ChiaveApiCreate, caller: Chiamante) -> dict:
    """
    Genera una nuova chiave «vd_<identificativo>_<segreto>». Il servizio salva
    solo l'identificativo e l'impronta HMAC: la chiave è visibile UNA SOLA VOLTA.
    """
    if _PEPPER is None:
        raise HTTPException(503, {
            "type": "ONB-530", "title": "Generazione delle chiavi API non disponibile",
            "detail": "API_KEY_PEPPER non configurato in questo ambiente.",
        })
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    generata = genera_chiave_api(_PEPPER)
    descrizione = body.descrizione.strip()
    try:
        async with get_async_session() as session:
            if await session.get(Tenant, tid) is None:
                raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})
            responsabile = await _responsabile_chiave(session, tid, body, caller)
            chiave = ApiKey(
                id=uuid.uuid4(), tenant_id=tid, user_id=responsabile,
                nome=descrizione or "Chiave API", key_prefix=generata.identificativo,
                key_hash=generata.impronta, permessi=list(PERMESSI_CHIAVE_API), attiva=True,
            )
            session.add(chiave)
            await session.flush()
            key_id = str(chiave.id)
    except SQLAlchemyError as exc:
        raise _errore_servizio("genera_api_key_errore", exc, tenant_id=tenant_id) from exc
    logger.info("api_key_generata", tenant_id=tenant_id, api_key_id=key_id,
                identificativo=generata.identificativo, creata_da=caller.get("user_id"))
    return {
        "id": key_id,
        "key_prefix": generata.identificativo,
        "api_key_id": f"{PREFISSO_CHIAVE}{generata.identificativo}",
        "api_key": generata.chiave,   # SEGRETO: visibile solo qui
        "descrizione": descrizione,
        "permessi": list(PERMESSI_CHIAVE_API),
        "attiva": True,
        "avviso": "Salva questa chiave: non sarà più mostrata",
    }


@router.delete("/{tenant_id}/api-keys/{key_id}", status_code=204,
               response_class=Response, response_model=None,
               summary="Revoca API key")
async def revoca_api_key(tenant_id: str, key_id: str, caller: Chiamante) -> None:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    kid = _uuid_o_404(key_id, "API key non trovata")
    try:
        async with get_async_session() as session:
            esito = await session.execute(
                update(ApiKey).where(ApiKey.id == kid, ApiKey.tenant_id == tid)
                .values(attiva=False)
            )
            if esito.rowcount == 0:
                raise HTTPException(404, {"type": "ONB-404", "title": "API key non trovata"})
    except SQLAlchemyError as exc:
        raise _errore_servizio("revoca_api_key_errore", exc, tenant_id=tenant_id) from exc
    logger.info("api_key_revocata", tenant_id=tenant_id, api_key_id=key_id,
                revocata_da=caller.get("user_id"))
