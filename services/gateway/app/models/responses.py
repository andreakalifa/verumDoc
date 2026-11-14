# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: models/responses
Servizio: gateway
Riferimento piano: F1.1.4 — Endpoint API v1

Modelli Pydantic per le response API v1.
ProblemDetail è il modello Problema del catalogo errori (RFC 9457, VD-C-100).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from libs.shared.errori import Problema

#: Errore HTTP: formato unico application/problem+json (RFC 9457) del catalogo
#: libs/shared/errori (VD-C-100). Alias mantenuto per i router esistenti.
ProblemDetail = Problema


class VersamentoResponse(BaseModel):
    """Response 202 al versamento UD."""
    pdv_id: UUID = Field(..., description="ID del Pacchetto di Versamento creato")
    stato: str = Field("RICEVUTO", description="Stato iniziale del PdV")
    messaggio: str = Field(
        "Versamento ricevuto. La conservazione è in corso.",
        description="Messaggio human-readable",
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StatoPdvResponse(BaseModel):
    """Response GET /api/v1/versamento/{pdv_id}."""
    pdv_id: UUID
    stato: str
    classe_documentale: str | None = None
    data_ricezione: datetime | None = None
    data_aggiornamento: datetime | None = None
    errori: list[Any] = Field(default_factory=list)
    rdv_preliminare: str | dict[str, Any] | None = None
    rdv_definitivo: str | dict[str, Any] | None = None
    aip_id: UUID | None = None
    urn: str | None = None


class HealthResponse(BaseModel):
    """Response endpoint /health/*."""
    status: str
    service: str
    version: str
    checks: dict[str, Any] = Field(default_factory=dict)
