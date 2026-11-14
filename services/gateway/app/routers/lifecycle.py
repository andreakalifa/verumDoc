# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
F3.3 — Lifecycle endpoint nel gateway.
POST /api/v1/lifecycle/{aip_id}/avvia  — DISMESSO (R05): 410 LC-410 con rinvio
GET  /api/v1/lifecycle/scadenze        — AIP con scadenza imminente

R05 (contratto Q2, VD-C-072, VD-P-06): il ciclo di vita (rettifica,
annullamento, scarto con iter, riversamento, restituzione) è nel servizio
dedicato ``ciclo-vita`` (``/api/v1/ciclo-vita/pratiche``). Scelta: RINVIO
documentato invece della rimozione: la rotta resta montata (``main.py`` non
cambia, i client esistenti ricevono un errore del catalogo che dice dove
andare invece di un 404 muto) e risponde, dopo autenticazione e ruolo come
prima, ``410 LC-410`` con ``dettagli.sostituto`` (metodo e percorso) e
l'intestazione ``Link: <…>; rel="successor-version"``. Nessuno stato cambia.
``/scadenze`` (lettura dei termini) resta qui invariata.

VD-C-003: ogni endpoint ha la dipendenza di autenticazione e ruolo
(`Depends(...)`): senza token 401, con ruolo non ammesso 403, mai 500.

VD-C-036 (parte gateway): la gestione del ciclo di vita in aip-builder è
irraggiungibile e, se raggiunta, metterebbe l'AIP in ELIMINAZIONE prima di
esportarlo (MIGRAZIONE) o senza autorizzazione (ANNULLAMENTO_VERSANTE).
Finché non esistono l'esportazione verso il nuovo conservatore e il workflow di
scarto autorizzato (R19), il gateway RIFIUTA esplicitamente l'avvio degli
scenari: nessuno stato dell'AIP viene modificato.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import require_role
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi
from libs.shared.models import Aip, AipStato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/lifecycle", tags=["Lifecycle"])

# Dipendenze create una volta sola (B008: niente chiamate nei default degli argomenti)
_ruoli_avvio = require_role("RSC_CONSERVATORE", "SUPER_ADMIN")
_ruoli_scadenze = require_role("RSC_CONSERVATORE", "SUPER_ADMIN", "ADMIN_TENANT")
UtenteAvvio = Annotated[dict[str, Any], Depends(_ruoli_avvio)]
UtenteScadenze = Annotated[dict[str, Any], Depends(_ruoli_scadenze)]

# Ruoli del conservatore che vedono le scadenze (solo metadati) di tutti i clienti
_RUOLI_CONSERVATORE = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})


class LifecycleRequest(BaseModel):
    scenario: str = Field(
        ...,
        pattern="^(SCADENZA_ORDINARIA|ANNULLAMENTO_VERSANTE|MIGRAZIONE|RETTIFICA)$",
    )
    motivo: str = Field(..., min_length=10, max_length=1000)
    aip_sostitutivo_id: str | None = None


#: Scenario del vecchio avvio → operazione che lo sostituisce nel servizio ciclo-vita
SOSTITUTI: dict[str, str] = {
    "SCADENZA_ORDINARIA": "/api/v1/ciclo-vita/pratiche/scarto/proposta",
    "ANNULLAMENTO_VERSANTE": "/api/v1/ciclo-vita/pratiche/annullamento",
    "MIGRAZIONE": "/api/v1/ciclo-vita/pratiche/riversamento-uscita",
    "RETTIFICA": "/api/v1/ciclo-vita/pratiche/rettifica",
}


@router.post(
    "/{aip_id}/avvia",
    summary="Avvio di uno scenario del ciclo di vita (dismesso: vedi /api/v1/ciclo-vita)",
    deprecated=True,
    responses={410: {"description": "Operazione spostata nel servizio ciclo-vita (LC-410)"}},
)
async def avvia_lifecycle(
    aip_id: str,
    body: LifecycleRequest,
    user: UtenteAvvio,
) -> dict[str, Any]:
    """
    Richiede ruolo RSC_CONSERVATORE o SUPER_ADMIN (come prima: 401/403 invariati).

    Rinvio documentato (R05): risponde sempre 410 LC-410 con l'operazione che
    sostituisce lo scenario (``dettagli.sostituto``) e l'intestazione ``Link``.
    Nessuno stato dell'AIP viene modificato e il database non si legge.
    """
    sostituto = SOSTITUTI[body.scenario]
    logger.info("lifecycle_avvio_rinviato", aip_id=aip_id, scenario=body.scenario,
                sostituto=sostituto, tenant_id=user.get("tenant_id"))
    raise ErroreApi(
        "LC-410",
        f"Lo scenario {body.scenario} si gestisce come pratica del servizio ciclo-vita: "
        f"POST {sostituto}",
        dettagli={"scenario": body.scenario,
                  "sostituto": {"metodo": "POST", "percorso": sostituto}},
        headers={"Link": f'<{sostituto}>; rel="successor-version"'},
    )


@router.get("/scadenze", summary="AIP con scadenza imminente")
async def scadenze_imminenti(
    user: UtenteScadenze,
    giorni: Annotated[int, Query(ge=0, le=3650)] = 90,
) -> dict[str, Any]:
    """
    Lista AIP con scadenza conservazione entro N giorni.
    ADMIN_TENANT vede solo i propri AIP (tenant del token verificato);
    se il tenant del token non è determinabile la richiesta è negata (§14.3).
    """
    ruolo = user.get("ruolo", "")
    tenant_uuid: UUID | None = None
    if ruolo not in _RUOLI_CONSERVATORE:
        try:
            tenant_uuid = UUID(str(user.get("tenant_id", "")))
        except ValueError as exc:
            logger.warning("lifecycle_tenant_non_determinabile", ruolo=ruolo)
            raise HTTPException(
                403,
                {"type": "AUTH-021", "title": "Cliente non determinabile dal token"},
            ) from exc

    adesso = datetime.now(UTC)
    scadenza_limite = adesso + timedelta(days=giorni)

    async with get_async_session() as session:
        q = select(Aip).where(
            Aip.stato.in_([AipStato.CONSERVATO, AipStato.IN_CATENA]),
            Aip.scadenza_conservazione <= scadenza_limite,
            Aip.scadenza_conservazione.isnot(None),
        )
        if tenant_uuid is not None:
            q = q.where(Aip.tenant_id == tenant_uuid)

        result = await session.execute(q.order_by(Aip.scadenza_conservazione.asc(), Aip.id))
        aips = result.scalars().all()

    return {
        "total": len(aips),
        "giorni_soglia": giorni,
        "items": [
            {
                "aip_id": str(a.id),
                "urn": a.urn,
                "scadenza_conservazione": a.scadenza_conservazione.isoformat(),
                "giorni_rimanenti": (a.scadenza_conservazione - adesso).days,
                "anni_conservazione": a.anni_conservazione,
            }
            for a in aips
        ],
    }
