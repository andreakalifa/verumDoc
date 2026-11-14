# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Router onboarding tenant: CRUD + state machine + attivazione.
Sprint 0 FIX-003: autenticazione JWT obbligatoria su tutti gli endpoint.
Sprint 0 FIX-006: log_audit attivato su operazioni critiche.

Endpoint:
  POST   /api/v1/onboarding/tenant          — Registra (SUPER_ADMIN)
  GET    /api/v1/onboarding/tenant/{id}     — Dettaglio (autenticato)
  GET    /api/v1/onboarding/tenant          — Lista (SUPER_ADMIN)
  POST   /api/v1/onboarding/tenant/{id}/attiva  — Attiva (SUPER_ADMIN)
  PUT    /api/v1/onboarding/tenant/{id}/stato   — Cambia stato (SUPER_ADMIN)
  PUT    /api/v1/onboarding/tenant/{id}/natura  — Natura PA/PRIVATO (SUPER_ADMIN, R05)
  DELETE /api/v1/onboarding/tenant/{id}    — Disattiva (SUPER_ADMIN)
"""
from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select

from app.auth.jwt_handler import utente_da_authorization, verify_access_token
from app.models.tenant import (
    TenantCreate,
    TenantList,
    TenantNaturaChange,
    TenantResponse,
    TenantStatoChange,
)
from app.services.onboarding_service import (
    attiva_tenant,
    cambia_stato_tenant,
    crea_tenant,
)
from app.services.state_machine import get_transizioni_ammesse
from libs.shared.db import get_async_session
from libs.shared.models import Tenant, TenantStato
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding", tags=["Onboarding"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])


# ── Sprint 0 FIX-003: helpers autenticazione ────────────────────────

def _require_auth(authorization: str | None) -> dict:
    """Verifica JWT Bearer RS256 (firma, iss, aud, exp). Obbligatorio ovunque."""
    return utente_da_authorization(authorization)


def _require_role(authorization: str | None, *allowed_roles: str) -> dict:
    """Verifica JWT + controlla ruolo."""
    user = _require_auth(authorization)
    if allowed_roles and user.get("ruolo") not in allowed_roles:
        raise HTTPException(
            403,
            {"type": "AUTH-010", "title": "Ruolo non autorizzato",
             "detail": f"Richiesto uno tra: {list(allowed_roles)}"},
        )
    return user


def _require_super_admin(authorization: str | None) -> dict:
    return _require_role(authorization, "SUPER_ADMIN")


def _require_admin(authorization: str | None) -> dict:
    return _require_role(
        authorization, "SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE",
    )


def _check_tenant_access(user: dict, tenant_id: str) -> None:
    """ADMIN_TENANT accede solo al proprio tenant; SUPER_ADMIN a tutti."""
    ruolo = user.get("ruolo", "")
    if ruolo in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
        return
    tenant_token = str(user.get("tenant_id") or "").strip().lower()
    if not tenant_token or tenant_token != str(tenant_id).strip().lower():
        # 404 e non 403 fra clienti diversi: l'indirizzo non rivela se esiste (§14.4)
        raise HTTPException(
            404, {"type": "ONB-404", "title": "Tenant non trovato"},
        )


# ── Endpoint ────────────────────────────────────────────────────────

@router.post(
    "/tenant",
    response_model=TenantResponse,
    status_code=201,
    summary="Registra nuovo tenant",
)
async def registra_tenant(
    body: TenantCreate,
    authorization: str | None = Header(default=None),
) -> TenantResponse:
    """
    Registra un nuovo tenant in stato TRIAL.
    Sprint 0 FIX-003: richiede SUPER_ADMIN.
    """
    user = _require_super_admin(authorization)
    tenant = await crea_tenant(
        ragione_sociale=body.ragione_sociale,
        codice_fiscale=body.codice_fiscale,
        email_referente=body.email_referente,
        piano=body.piano,
        pec_referente=body.pec_referente,
        codice_ipa=body.codice_ipa,
        natura=body.natura,
        # S5: la natura dichiarata ha la sua voce d'audit nella transazione
        dichiarata_da=user.get("user_id"),
        motivo_natura=body.motivo_natura,
    )
    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit
        await log_audit(
            evento="tenant.creato",
            tenant_id=str(tenant.id),
            user_id=user.get("user_id"),
            dettagli={
                "ragione_sociale": body.ragione_sociale,
                "codice_fiscale": body.codice_fiscale,
                "piano": body.piano,
                "natura": body.natura,
            },
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)
    return TenantResponse.from_orm(tenant)


@router.get(
    "/tenant/{tenant_id}",
    response_model=TenantResponse,
    summary="Dettaglio tenant",
)
async def get_tenant(
    tenant_id: str,
    authorization: str | None = Header(default=None),
) -> TenantResponse:
    user = _require_auth(authorization)
    _check_tenant_access(user, tenant_id)

    async with get_async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})
    return TenantResponse.from_orm(tenant)


@router.get("/tenant", response_model=TenantList, summary="Lista tenant")
async def lista_tenant(
    stato: str | None = Query(None, description="Filtra per stato"),
    piano: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    authorization: str | None = Header(default=None),
) -> TenantList:
    _require_super_admin(authorization)

    async with get_async_session() as session:
        q = select(Tenant)
        if stato:
            try:
                q = q.where(Tenant.stato == TenantStato(stato.upper()))
            except ValueError:
                raise HTTPException(
                    422, {"type": "ONB-203", "title": f"Stato {stato!r} non valido"},
                ) from None
        if piano:
            q = q.where(Tenant.piano == piano.lower())

        count_result = await session.execute(
            select(func.count()).select_from(q.subquery())
        )
        total = count_result.scalar_one()

        result = await session.execute(q.offset(skip).limit(limit))
        tenants = result.scalars().all()

    return TenantList(
        total=total,
        items=[TenantResponse.from_orm(t) for t in tenants],
    )


@router.post(
    "/tenant/{tenant_id}/attiva",
    response_model=TenantResponse,
    summary="Attiva tenant (TRIAL -> ATTIVO)",
)
async def attiva(
    tenant_id: str,
    authorization: str | None = Header(default=None),
) -> TenantResponse:
    """Attiva il tenant e imposta lo stato su ATTIVO."""
    user = _require_super_admin(authorization)
    tenant = await attiva_tenant(tenant_id)
    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit
        await log_audit(
            evento="tenant.attivato",
            tenant_id=tenant_id,
            user_id=user.get("user_id"),
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)
    return TenantResponse.from_orm(tenant)


@router.put(
    "/tenant/{tenant_id}/stato",
    response_model=TenantResponse,
    summary="Cambia stato tenant",
)
async def cambia_stato(
    tenant_id: str,
    body: TenantStatoChange,
    authorization: str | None = Header(default=None),
) -> TenantResponse:
    """Cambia stato con validazione state machine."""
    user = _require_super_admin(authorization)
    tenant = await cambia_stato_tenant(tenant_id, body.stato, body.motivo)
    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit
        await log_audit(
            evento=f"tenant.stato_cambiato_{body.stato.lower()}",
            tenant_id=tenant_id,
            user_id=user.get("user_id"),
            dettagli={"nuovo_stato": body.stato, "motivo": body.motivo},
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)
    return TenantResponse.from_orm(tenant)


@router.put(
    "/tenant/{tenant_id}/natura",
    response_model=TenantResponse,
    summary="Dichiara la natura dell'ente (PA o PRIVATO)",
)
async def dichiara_natura(
    tenant_id: str,
    body: TenantNaturaChange,
    authorization: str | None = Header(default=None),
) -> TenantResponse:
    """Revisione R05 (R9): la natura decide se lo scarto richiede il nulla osta
    della Soprintendenza. Finché non è dichiarata l'ente è trattato come PA.
    Solo SUPER_ADMIN; voce d'audit nella stessa transazione. R07 (D25): motivo
    obbligatorio solo per PRIVATO; per PA facoltativo, precompilato dal codice IPA."""
    from libs.shared.security.audit import registra_audit

    user = _require_super_admin(authorization)
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"}) from None
    async with get_async_session() as session:
        tenant = (await session.execute(
            select(Tenant).where(Tenant.id == tid).with_for_update())).scalar_one_or_none()
        if tenant is None:
            raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})
        prima = tenant.natura
        tenant.natura = body.natura
        # R07 (D25): per la PA il motivo è facoltativo; se manca si registra il codice
        # IPA dell'ente (nessun client dell'indice IPA: il codice è quello dichiarato)
        motivo = body.motivo
        origine = "dichiarato"
        if motivo is None and body.natura == "PA":
            # W8: il codice non è verificato sull'indice IPA: l'audit lo dice
            motivo = (f"Codice IPA dichiarato {tenant.codice_ipa} (non verificato "
                      f"sull'indice IPA)" if tenant.codice_ipa else None)
            origine = "dichiarato_ipa" if tenant.codice_ipa else "assente"
        await session.flush()
        await registra_audit(session, evento="tenant.natura_dichiarata", tenant_id=str(tid),
                             user_id=user.get("user_id"),
                             dettagli={"prima": prima, "dopo": body.natura,
                                       "motivo": motivo, "origine_motivo": origine})
        await session.refresh(tenant)
    return TenantResponse.from_orm(tenant)


@router.delete(
    "/tenant/{tenant_id}",
    status_code=204,
    response_class=Response,
    response_model=None,
    summary="Disattiva tenant",
)
async def disattiva(
    tenant_id: str,
    authorization: str | None = Header(default=None),
) -> None:
    """Disattiva il tenant (qualsiasi stato -> DISATTIVATO)."""
    user = _require_super_admin(authorization)
    await cambia_stato_tenant(
        tenant_id, "DISATTIVATO", "Eliminazione richiesta via API",
    )
    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit
        await log_audit(
            evento="tenant.disattivato",
            tenant_id=tenant_id,
            user_id=user.get("user_id"),
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)


@router.get(
    "/tenant/{tenant_id}/stato-info",
    summary="Info stato e transizioni ammesse",
)
async def stato_info(
    tenant_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """Ritorna lo stato corrente e le transizioni possibili."""
    user = _require_auth(authorization)
    _check_tenant_access(user, tenant_id)

    async with get_async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})
    trans = get_transizioni_ammesse(tenant.stato)
    return {
        "tenant_id": tenant_id,
        "stato_corrente": tenant.stato.value,
        "transizioni_ammesse": trans,
        "stato_finale": not trans,
    }


# ── Classi documentali abilitate per tenant ──────────────────────────

@router.get(
    "/tenant/{tenant_id}/classi",
    summary="Classi documentali abilitate per il tenant",
)
async def get_classi_tenant(
    tenant_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """F3-01c: lista classi documentali abilitate per un tenant specifico."""
    user = _require_auth(authorization)
    _check_tenant_access(user, tenant_id)

    async with get_async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(
                404, {"type": "ONB-404", "title": "Tenant non trovato"},
            )

        classi_ids = getattr(tenant, "classi_abilitate", None) or []

        from sqlalchemy import text

        if classi_ids:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text, codice, nome, tipologia_xml, "
                        "anni_conservazione, attiva "
                        "FROM public.classi_documentali "
                        "WHERE id = ANY(:ids) AND attiva = true"
                    ),
                    {"ids": classi_ids},
                )
            ).fetchall()
        else:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text, codice, nome, tipologia_xml, "
                        "anni_conservazione, attiva "
                        "FROM public.classi_documentali "
                        "WHERE attiva = true ORDER BY nome"
                    )
                )
            ).fetchall()

    return {
        "tenant_id": tenant_id,
        "classi": [
            {
                "id": r[0], "codice": r[1], "nome": r[2],
                "tipologia_xml": r[3], "anni_conservazione": r[4],
                "attiva": r[5],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.put(
    "/tenant/{tenant_id}/classi",
    summary="Aggiorna classi documentali abilitate per il tenant",
)
async def update_classi_tenant(
    tenant_id: str,
    body: dict,
    authorization: str | None = Header(default=None),
) -> dict:
    """F3-01c: aggiorna la lista di classi abilitate per il tenant."""
    user = _require_admin(authorization)
    _check_tenant_access(user, tenant_id)

    classi_ids: list[str] = body.get("classi_ids", [])

    import re

    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    for cid in classi_ids:
        if not uuid_pattern.match(str(cid).lower()):
            raise HTTPException(
                400, {"type": "ONB-400", "title": f"ID classe non valido: {cid!r}"},
            )

    async with get_async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(
                404, {"type": "ONB-404", "title": "Tenant non trovato"},
            )

        from sqlalchemy import text

        await session.execute(
            text(
                "UPDATE public.tenants "
                "SET classi_abilitate = :classi WHERE id = :tid"
            ),
            {"classi": json.dumps(classi_ids), "tid": tenant_id},
        )
        await session.commit()
        logger.info(
            "classi_tenant_updated", tenant_id=tenant_id, n=len(classi_ids),
        )

    return {
        "tenant_id": tenant_id,
        "classi_ids": classi_ids,
        "aggiornato": True,
    }
