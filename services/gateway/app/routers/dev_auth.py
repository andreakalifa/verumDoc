# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Endpoint di sviluppo: conia un JWT senza autenticazione (profili demo).
F1-05: scopes corretti per ogni ruolo.

VD-C-002 (REGOLE §19.2): la rotta esiste SOLO con il flag esplicito
VERUMDOC_DEV_AUTH=1 E ENV development/test. In ogni altro caso main.py non
registra nemmeno il router (staging, production, prod, valori sconosciuti...).
Il controllo è ripetuto nell'endpoint come difesa in profondità.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.jwt_handler import create_access_token
from app.config import settings
from libs.shared.security.inattivita import CLAIM_SESSIONE, SESSIONE_PORTALE

router = APIRouter(prefix="/api/v1/auth", tags=["Dev Auth"])

# Scopes per ruolo — F1-05 allineamento RBAC
SCOPES_PER_RUOLO: dict[str, list[str]] = {
    "SUPER_ADMIN":      ["*"],
    "ADMIN_TENANT":     ["versamento:ud", "versamento:doc", "read",
                         "distribution", "lifecycle", "admin:tenant"],
    "OPERATORE":        ["versamento:ud", "versamento:doc", "read", "lifecycle"],
    "VIEWER":           ["read"],
    "RSC_CONSERVATORE": ["*", "rsc:approve", "rsc:report"],
    "API_SYSTEM":       ["versamento:ud", "versamento:doc", "read"],
}


class DevTokenRequest(BaseModel):
    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    user_id:   str = "00000000-0000-0000-0000-000000000099"
    ruolo:     str = "ADMIN_TENANT"
    scopes:    list[str] | None = None


@router.post("/dev-token", summary="Genera JWT dev (solo sviluppo)")
async def dev_token(body: DevTokenRequest, request: Request) -> JSONResponse:
    # Difesa in profondità: anche se il router fosse registrato per errore.
    if not settings.login_sviluppo_abilitato():
        raise HTTPException(404)

    scopes = body.scopes or SCOPES_PER_RUOLO.get(body.ruolo, ["read"])

    token = create_access_token(
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        ruolo=body.ruolo,
        scopes=scopes,
        # R06 (VD-C-127): anche il token di sviluppo è di una sessione del portale
        extra={CLAIM_SESSIONE: SESSIONE_PORTALE},
    )
    risposta = JSONResponse({
        "access_token": token,
        "token_type":   "bearer",
        "ruolo":        body.ruolo,
        "tenant_id":    body.tenant_id,
        "scopes":       scopes,
        "nota":         "Token dev — non usare in produzione",
    })
    # R05 (VD-C-079): anche il portale di sviluppo usa la sessione in cookie
    # HttpOnly; il token nel corpo resta per script e prove (solo sviluppo).
    from app.auth.sessione import consegna_sessione

    await consegna_sessione(risposta, request, token)
    return risposta
