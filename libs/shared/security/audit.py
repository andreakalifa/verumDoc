# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
F4.4 — Audit trail centralizzato.
Ogni operazione sensibile viene registrata in audit_global.
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import func

from libs.shared.db import get_async_session
from libs.shared.models import AuditGlobal

logger = structlog.get_logger(__name__)


async def log_audit(
    evento: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    aip_id: str | None = None,
    dettagli: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """
    Registra un evento di audit in audit_global (append-only, RLS).
    Non lancia eccezioni — il fallimento del log non blocca l'operazione.
    """
    import uuid
    try:
        async with get_async_session() as session:
            record = AuditGlobal(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
                user_id=uuid.UUID(user_id) if user_id else None,
                aip_id=uuid.UUID(aip_id) if aip_id else None,
                evento=evento,
                dettagli=dettagli or {},
                ip_address=ip_address,
                user_agent=user_agent,
                # Revisione 2 di R05 (S7): l'istante viene dal DATABASE (now() =
                # inizio della transazione), mai dall'orologio dell'applicazione:
                # l'export dell'audit nel WORM chiude i periodi sull'orizzonte
                # delle transazioni aperte (vd_transazioni_aperte), che vale solo
                # se created_at = xact_start
                created_at=func.now(),
            )
            session.add(record)
            await session.commit()
    except Exception as exc:
        # Log su stdout ma non propagare
        logger.error("audit_log_failed", evento=evento, error=str(exc))


async def registra_audit(
    session: Any,
    *,
    evento: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    aip_id: str | None = None,
    dettagli: dict[str, Any] | None = None,
) -> None:
    """Voce di audit NELLA transazione del chiamante (REGOLE §15.1, §15.3).

    R04 servizi (VD-C-075): a differenza di ``log_audit`` non apre una sessione
    propria e non ingoia gli errori: se la voce non si scrive, l'operazione non
    si conferma. Identificativi non UUID → ``ValueError`` (errore del chiamante).
    """
    import uuid

    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO public.audit_global (id, tenant_id, user_id, aip_id, evento, dettagli) "
            "VALUES (:id, :tenant, :utente, :aip, :evento, CAST(:dettagli AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "tenant": uuid.UUID(str(tenant_id)) if tenant_id else None,
            "utente": uuid.UUID(str(user_id)) if user_id else None,
            "aip": uuid.UUID(str(aip_id)) if aip_id else None,
            "evento": evento[:100],
            "dettagli": _json(dettagli or {}),
        },
    )


def _json(valore: dict[str, Any]) -> str:
    import json

    return json.dumps(valore, default=str, ensure_ascii=False)
