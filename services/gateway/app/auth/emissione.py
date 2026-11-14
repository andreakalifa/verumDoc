# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/emissione.py — emissione della sessione del portale (R10).

Una sola funzione (REGOLE §9.1) per ogni metodo d'accesso (SPID/CIE, password
con codice a tempo, passkey, attivazione da invito): stesso token, stessi
permessi, stesso cookie HttpOnly (``app.auth.sessione.consegna_sessione``, con la
scadenza per inattività del ruolo) e stesso corpo della risposta.

R10 (D30): con un ruolo personalizzato il token porta il ruolo BASE (perimetro),
i permessi del ruolo intersecati con i concedibili ATTUALI del conservatore
(``RUOLI_PERMESSI_CONCEDIBILI``) e gli identificativi per la revoca (``rp``,
``pv``); gli scope del gateway derivano dai permessi. Senza ruolo personalizzato
gli scope sono quelli del ruolo fisso (come prima).

R16 (VD-C-153): ogni sessione porta la versione dei metodi d'accesso ammessi (``vm``) e,
con un ruolo personalizzato, quella dei concedibili (``vc``), lette nella stessa
transazione dei permessi: un cambio successivo revoca la sessione al prossimo uso
(``libs.shared.security.revoca``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text, update

from app.auth.ruoli import scopes_del_ruolo
from app.config import settings
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.models import User
from libs.shared.security.permessi import (
    CLAIM_PERMESSI,
    CLAIM_RUOLO_PERSONALIZZATO,
    CLAIM_VERSIONE_CONCEDIBILI,
    CLAIM_VERSIONE_METODI,
    CLAIM_VERSIONE_PERMESSI,
    permessi_identita,
)
from libs.verumdoc_rules.parametri_servizio import RUOLI_PERMESSI_CONCEDIBILI
from libs.verumdoc_rules.permessi import MAI_DELEGABILI, SEMPRE_INCLUSI, scope_dei_permessi

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class UtenteAccesso:
    """Utente attivo di un ente non bloccato, verificato da un metodo d'accesso."""

    user_id: str
    tenant_id: str
    ruolo: str
    nome: str
    cognome: str
    ragione_sociale: str
    piano: str


@dataclass(frozen=True)
class PermessiUtente:
    scopes: list[str]
    claim: dict[str, Any]


async def permessi_utente(user_id: str, tenant_id: str, ruolo: str) -> PermessiUtente:
    """Scope e claim dei permessi dell'utente al momento dell'accesso."""
    from libs.shared.db import DBNonInizializzato
    from libs.shared.parametri import parametri

    try:
        uuid.UUID(str(user_id)), uuid.UUID(str(tenant_id))
    except ValueError:
        # identificativi non UUID (dati di prova): nessun ruolo personalizzato possibile
        return PermessiUtente(scopes_del_ruolo(ruolo), {})
    try:
        return await _permessi_utente(user_id, tenant_id, ruolo, parametri)
    except DBNonInizializzato:
        # nessun database nel processo (test unitari): permessi del ruolo fisso
        return PermessiUtente(scopes_del_ruolo(ruolo), {})


async def _permessi_utente(user_id: str, tenant_id: str, ruolo: str,
                           parametri: Any) -> PermessiUtente:
    from libs.shared.security.revoca import versioni_accesso

    with ambito_ente(tenant_id):
        async with get_async_session() as session:
            vm, vc = await versioni_accesso(session, tenant_id)
            riga = (await session.execute(text(
                "SELECT u.ruolo_personalizzato_id::text AS rp, u.permessi_versione AS pv, "
                "       r.permessi, r.ruolo_base "
                "FROM public.users u LEFT JOIN public.ruoli_personalizzati r "
                "  ON r.id = u.ruolo_personalizzato_id AND r.tenant_id = u.tenant_id "
                "WHERE u.id = CAST(:u AS uuid) AND u.tenant_id = CAST(:t AS uuid)"),
                {"u": user_id, "t": tenant_id})).mappings().one_or_none()
        if riga is None or not riga["rp"]:
            return PermessiUtente(scopes_del_ruolo(ruolo), {CLAIM_VERSIONE_METODI: vm})
        concedibili = set((await parametri([RUOLI_PERMESSI_CONCEDIBILI], tenant_id))
                          .get(RUOLI_PERMESSI_CONCEDIBILI) or ())
    if riga["ruolo_base"] != ruolo:
        # ruolo base diverso dal ruolo dell'utente (non accade: l'assegnazione li
        # allinea): nessun permesso oltre la consultazione (fail-closed)
        logger.error("ruolo_personalizzato_base_diverso", user_id=user_id)
        permessi = set(SEMPRE_INCLUSI)
    else:
        permessi = ((set(riga["permessi"] or ()) & concedibili) | SEMPRE_INCLUSI) - MAI_DELEGABILI
    elenco = sorted(permessi)
    return PermessiUtente(scope_dei_permessi(elenco), {
        CLAIM_PERMESSI: elenco, CLAIM_RUOLO_PERSONALIZZATO: riga["rp"],
        CLAIM_VERSIONE_PERMESSI: int(riga["pv"]),
        CLAIM_VERSIONE_METODI: vm, CLAIM_VERSIONE_CONCEDIBILI: vc,
    })


async def _registra_accesso(user_id: str) -> None:
    async with get_async_session() as session:
        await session.execute(update(User).where(User.id == uuid.UUID(user_id))
                              .values(ultimo_login=datetime.now(UTC)))


async def emetti_accesso(request: Request, utente: UtenteAccesso, *, metodo: str,
                         extra: dict[str, Any] | None = None,
                         registra: Any = None,
                         corpo_extra: dict[str, Any] | None = None) -> JSONResponse:
    """Token della sessione del portale nel cookie HttpOnly e corpo «accesso»."""
    from app.auth.jwt_handler import create_access_token
    from app.auth.sessione import consegna_sessione
    from libs.shared.security.inattivita import CLAIM_SESSIONE, SESSIONE_PORTALE

    permessi = await permessi_utente(utente.user_id, utente.tenant_id, utente.ruolo)
    token = create_access_token(
        tenant_id=utente.tenant_id, user_id=utente.user_id, ruolo=utente.ruolo,
        scopes=permessi.scopes,
        extra={"piano": utente.piano, "auth_method": metodo, **(extra or {}),
               **permessi.claim, CLAIM_SESSIONE: SESSIONE_PORTALE},
    )
    await (registra or _registra_accesso)(utente.user_id)
    logger.info("accesso", tenant_id=utente.tenant_id, user_id=utente.user_id,
                ruolo=utente.ruolo, metodo=metodo,
                ruolo_personalizzato=permessi.claim.get(CLAIM_RUOLO_PERSONALIZZATO))
    risposta = JSONResponse({
        "esito": "accesso",
        "sessione": "cookie",
        "expires_in": settings.jwt_expiry_seconds,
        "utente": {
            "tenant_id": utente.tenant_id, "user_id": utente.user_id, "ruolo": utente.ruolo,
            # revisione R17: permessi EFFETTIVI anche per i ruoli fissi (pagine della console)
            "scopes": permessi.scopes, "permessi": sorted(permessi_identita(
                {"ruolo": utente.ruolo, **permessi.claim})),
            "nome": utente.nome, "cognome": utente.cognome,
            "ragione_sociale": utente.ragione_sociale, "piano": utente.piano,
        },
        **(corpo_extra or {}),
    })
    await consegna_sessione(risposta, request, token)
    return risposta


__all__ = ["PermessiUtente", "UtenteAccesso", "emetti_accesso", "permessi_utente"]
