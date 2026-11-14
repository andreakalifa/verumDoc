# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/sessione.py — sessione del portale (cookie HttpOnly)

Riferimenti: VD-C-079 (R05, agente r05-sicurezza); REGOLE_SVILUPPO §19.7;
libs/shared/security/sessione_cookie.py (cookie e controlli CSRF);
docs/architettura/sicurezza.md.

Il portale non riceve più il token: lo scambio SPID/CIE (``POST
/api/v1/auth/spid/scambio``) e, solo in sviluppo, ``/api/v1/auth/dev-token``
lo consegnano nel cookie ``vd_sessione`` (HttpOnly, Secure, SameSite=Strict).
Qui:
  - ``GET /api/v1/auth/sessione``: chi è connesso (per ricostruire l'interfaccia
    dopo un ricaricamento della pagina) e la scadenza, MAI il token;
  - ``DELETE /api/v1/auth/sessione``: uscita. Revisione R05 (R12): oltre a
    cancellare i cookie, se la richiesta porta un token valido (dal cookie o
    Bearer) la sessione si registra come chiusa (``sessioni_revocate``) fino
    alla sua scadenza: un token sottratto non vale più, in nessun servizio
    (``libs.shared.security.revoca``, ``MiddlewareSessioneCookie``).
Le richieste del portale autenticate con il cookie passano dalla stessa
verifica dei token Bearer (``get_current_user``).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.config import login_sviluppo_consentito, settings
from libs.shared.db import get_async_session
from libs.shared.models import Tenant, User
from libs.shared.parametri import limite_inattivita_ruolo
from libs.shared.security.inattivita import (
    CLAIM_SESSIONE,
    SESSIONE_PORTALE,
    chiudi_sessione_attiva,
    registra_sessione,
)
from libs.shared.security.permessi import permessi_identita
from libs.shared.security.sessione_cookie import (
    cancella_cookie_sessione,
    imposta_cookie_sessione,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Sessione del portale"])

#: R07 (D14): la console avvisa 2 minuti prima della chiusura per inattività.
AVVISO_INATTIVITA_SECONDI = 120


def avviso_inattivita(limite_secondi: int | None) -> int:
    """Revisione W5: anticipo dell'avviso, al più metà del limite (con un limite
    breve l'avviso non resta sempre aperto; il minimo del motore è 5 minuti)."""
    if not limite_secondi or limite_secondi <= 0:
        return AVVISO_INATTIVITA_SECONDI
    return max(1, min(AVVISO_INATTIVITA_SECONDI, limite_secondi // 2))


def cookie_sicuro(request: Request) -> bool:
    """``Secure`` sempre, tranne in development/test su http (localhost senza TLS):
    la stessa regola del cookie del flusso SPID."""
    return not (login_sviluppo_consentito(settings.env) and request.url.scheme == "http")


async def limite_inattivita(ruolo: str | None, tenant_id: Any) -> int:
    """R06 (VD-C-127), R07 (D14): limite d'inattività (secondi) della sessione del
    ruolo nell'ente: impostazione ``SESSIONE_INATTIVITA_MINUTI_<RUOLO>`` della
    console (il conservatore fissa il massimo, l'ente può solo abbassarlo)."""
    return await limite_inattivita_ruolo(ruolo, tenant_id)


async def consegna_sessione(risposta: Response, request: Request, token: str) -> None:
    """Mette il token appena emesso nei cookie della sessione (mai nel corpo) e,
    R06 (VD-C-127), registra la sessione con il limite d'inattività del ruolo
    (``sessioni_attive``): da lì ogni servizio la rifiuta dopo l'inattività."""
    from app.auth.jwt_handler import verify_access_token
    from libs.shared.db import DBNonInizializzato

    payload = verify_access_token(token)
    try:
        await registra_sessione(token, payload, limite_secondi=await limite_inattivita(
            payload.get("ruolo"), payload.get("tenant_id")))
    except DBNonInizializzato:
        pass  # nessun database nel processo (test unitari): nessuna scadenza da applicare
    imposta_cookie_sessione(risposta, token, durata_secondi=settings.jwt_expiry_seconds,
                            sicuro=cookie_sicuro(request))


class UtenteSessione(BaseModel):
    tenant_id: str
    user_id: str
    ruolo: str
    scopes: list[str] = Field(default_factory=list)
    permessi: list[str] = Field(default_factory=list, description=(
        "Revisione R17: permessi effettivi (ruolo fisso o personalizzato), per pagine e "
        "comandi della console; il servizio ricontrolla sempre"))
    piano: str = ""
    ragione_sociale: str = ""
    nome: str | None = None
    cognome: str | None = None


class RispostaSessione(BaseModel):
    utente: UtenteSessione
    scadenza: int | None = Field(None, description="Scadenza della sessione (epoch, secondi)")
    autenticazione: str = Field(description="cookie (portale) | bearer | chiave_api")
    inattivita_secondi: int | None = Field(
        None, description="R06: la sessione del portale scade dopo questi secondi senza "
                          "richieste (rinnovo a scorrimento); null fuori dal portale")
    avviso_inattivita_secondi: int | None = Field(
        None, description="R07 (D14): anticipo con cui la console avvisa della chiusura per "
                          "inattività («resta collegato»); null fuori dal portale")


@router.get("/sessione", response_model=RispostaSessione,
            summary="Utente della sessione corrente (senza token)")
async def leggi_sessione(request: Request,
                         user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Dati per l'interfaccia: ente, ruolo, permessi, nomi. 401 senza sessione."""
    ragione_sociale, nome, cognome = "", None, None
    async with get_async_session() as session:
        tenant = (await session.execute(select(Tenant.ragione_sociale).where(
            Tenant.id == str(user.get("tenant_id"))))).scalar_one_or_none() \
            if _uuid_valido(user.get("tenant_id")) else None
        if _uuid_valido(user.get("user_id")):
            persona = (await session.execute(select(User.nome, User.cognome).where(
                User.id == str(user.get("user_id"))))).one_or_none()
            if persona is not None:
                nome, cognome = persona.nome, persona.cognome
    ragione_sociale = tenant or ""
    stato = request.scope.get("state") or {}
    metodo = "cookie" if stato.get("autenticazione_cookie") else (
        "chiave_api" if user.get("auth_method") == "api_key" else "bearer")
    portale = user.get(CLAIM_SESSIONE) == SESSIONE_PORTALE
    limite = (await limite_inattivita(user.get("ruolo"), user.get("tenant_id"))
              if portale else None)
    return {
        "utente": {
            "tenant_id": str(user.get("tenant_id") or ""),
            "user_id": str(user.get("user_id") or ""),
            "ruolo": str(user.get("ruolo") or ""),
            "scopes": [str(s) for s in user.get("scopes") or []],
            # revisione R17 (audit R17-01/02): permessi EFFETTIVI (ruolo fisso o
            # personalizzato): la console mostra pagine e comandi secondo questi
            "permessi": sorted(permessi_identita(user)),
            "piano": str(user.get("piano") or ""),
            "ragione_sociale": ragione_sociale,
            "nome": nome,
            "cognome": cognome,
        },
        "scadenza": user.get("exp"),
        "autenticazione": metodo,
        "inattivita_secondi": limite,
        # R07 (D14, W5): anticipo dell'avviso «resta collegato» nella console
        "avviso_inattivita_secondi": avviso_inattivita(limite) if portale else None,
    }


@router.delete("/sessione", status_code=204,
               summary="Uscita: chiude la sessione lato server e cancella i cookie")
async def chiudi_sessione(request: Request) -> Response:
    """Idempotente: risponde 204 anche senza sessione o con una sessione già
    chiusa. Con il cookie la richiesta porta il token anti-CSRF (controllo del
    middleware della sessione). Un token valido si registra come revocato fino
    a ``exp`` (revisione R05, R12)."""
    from fastapi import HTTPException

    from app.auth.jwt_handler import verify_access_token
    from libs.shared.security.revoca import revoca_sessione

    autorizzazione = request.headers.get("authorization") or ""
    if autorizzazione.lower().startswith("bearer "):
        token = autorizzazione.split(" ", 1)[1].strip()
        try:
            payload = verify_access_token(token)
        except HTTPException:
            payload = None  # token scaduto o non valido: non c'è nulla da revocare
        if payload is not None:
            await revoca_sessione(token, payload)
            # R06 (VD-C-127): la sessione non vale più in nessun servizio, subito
            await chiudi_sessione_attiva(token)
    risposta = Response(status_code=204)
    cancella_cookie_sessione(risposta, sicuro=cookie_sicuro(request))
    return risposta


def _uuid_valido(valore: Any) -> bool:
    from uuid import UUID

    try:
        UUID(str(valore))
    except (TypeError, ValueError):
        return False
    return True


__all__ = ["consegna_sessione", "cookie_sicuro", "router"]
