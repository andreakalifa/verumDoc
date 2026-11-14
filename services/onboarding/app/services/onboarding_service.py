# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Logica di business onboarding: creazione tenant, attivazione, schema DB.
Riferimento piano: F1.5.1

Eventi di ciclo di vita (g3-backend, REGOLE §12.3): si scrivono nell'outbox
(libs/shared/outbox.py) NELLA STESSA transazione del cambio di stato; dopo il
commit si tenta subito la pubblicazione. Con Kafka giù la richiesta riesce lo
stesso e l'evento resta in outbox finché il relay (gateway) non lo pubblica:
nessun evento perso, nessun errore verso il client.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.services.state_machine import validate_transition
from libs.shared.db import get_async_session
from libs.shared.models import Tenant, TenantStato
from libs.shared.outbox import accoda_evento, pubblica_dopo_commit

logger = structlog.get_logger(__name__)


def _generate_schema_name(ragione_sociale: str, codice_fiscale: str) -> str:
    """
    Genera un nome schema PostgreSQL valido dal codice fiscale del tenant.
    Formato: t_{cf_normalizzato}
    """
    cf = re.sub(r"[^a-z0-9]", "", codice_fiscale.lower())[:20]
    return f"t_{cf}"


async def crea_tenant(
    ragione_sociale: str,
    codice_fiscale: str,
    email_referente: str,
    piano: str = "starter",
    pec_referente: str | None = None,
    codice_ipa: str | None = None,
    natura: str | None = None,
    dichiarata_da: str | None = None,
    motivo_natura: str | None = None,
) -> Tenant:
    """
    Crea un nuovo tenant in stato TRIAL.
    Verifica unicità codice fiscale e genera nome schema.

    Revisione 2 di R05 (S5): una natura dichiarata alla creazione (PRIVATO
    esenta lo scarto dal nulla osta) ha la sua voce d'audit
    ``tenant.natura_dichiarata`` con chi e perché, NELLA transazione della
    creazione (come ``PUT /tenant/{id}/natura``); PRIVATO senza motivo è un errore.
    """
    if natura == "PRIVATO" and not (motivo_natura or "").strip():
        raise HTTPException(status_code=422, detail={
            "type": "ONB-422", "title": "Motivo obbligatorio",
            "detail": "natura PRIVATO: indicare il motivo (esenzione dal nulla osta)"})
    codice_fiscale = codice_fiscale.upper().strip()
    schema_name = _generate_schema_name(ragione_sociale, codice_fiscale)

    async with get_async_session() as session:
        # Verifica unicità CF
        existing = await session.execute(
            select(Tenant).where(Tenant.codice_fiscale == codice_fiscale)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "ONB-001",
                    "title": "Tenant già registrato",
                    "detail": f"Codice fiscale {codice_fiscale!r} già presente nel sistema.",
                },
            )

        # Verifica unicità schema
        existing_schema = await session.execute(
            select(Tenant).where(Tenant.schema_name == schema_name)
        )
        if existing_schema.scalar_one_or_none():
            schema_name = f"{schema_name}_{uuid.uuid4().hex[:6]}"

        tenant = Tenant(
            id=uuid.uuid4(),
            ragione_sociale=ragione_sociale.strip(),
            codice_fiscale=codice_fiscale,
            schema_name=schema_name,
            stato=TenantStato.TRIAL,
            piano=piano,
            email_referente=email_referente.strip(),
            pec_referente=pec_referente,
            codice_ipa=codice_ipa,
            natura=natura,
            storage_quota_gb=10 if piano == "starter" else 100,
        )
        session.add(tenant)
        await session.flush()   # il tenant esiste prima dell'evento (FK dell'outbox)
        if natura is not None:
            from libs.shared.security.audit import registra_audit

            await registra_audit(session, evento="tenant.natura_dichiarata",
                                 tenant_id=str(tenant.id), user_id=dichiarata_da,
                                 dettagli={"prima": None, "dopo": natura,
                                           "motivo": motivo_natura, "alla_creazione": True})
        await accoda_evento(
            session,
            topic=settings.kafka_topic_lifecycle,
            chiave=str(tenant.id),
            tenant_id=tenant.id,
            payload={
                "evento": "tenant.creato",
                "tenant_id": str(tenant.id),
                "ragione_sociale": ragione_sociale,
                "piano": piano,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()
        await session.refresh(tenant)

    logger.info("tenant_created",
                tenant_id=str(tenant.id),
                cf=codice_fiscale,
                schema=schema_name,
                piano=piano)

    # Evento già durevole in outbox: tentativo immediato, mai un errore al client
    await pubblica_dopo_commit(evento="lifecycle", tenant_id=str(tenant.id))

    return tenant


async def attiva_tenant(tenant_id: str) -> Tenant:
    """
    Transizione TRIAL → ATTIVO.
    Crea lo schema PostgreSQL per-tenant.
    """
    async with get_async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})

        validate_transition(tenant.stato, TenantStato.ATTIVO)

        # NOTA: nessuna creazione schema per-tenant (architettura single-schema)
        # L'isolamento avviene tramite tenant_id su pdv e aip.
        tenant.stato = TenantStato.ATTIVO
        tenant.data_attivazione = datetime.now(UTC)
        await accoda_evento(
            session,
            topic=settings.kafka_topic_lifecycle,
            chiave=tenant_id,
            tenant_id=tenant.id,
            payload={
                "evento": "tenant.attivato",
                "tenant_id": tenant_id,
                "schema_name": tenant.schema_name,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()
        await session.refresh(tenant)

    logger.info("tenant_activated",
                tenant_id=tenant_id,
                schema=tenant.schema_name)

    # Evento già durevole in outbox: tentativo immediato, mai un errore al client
    await pubblica_dopo_commit(evento="lifecycle", tenant_id=str(tenant.id))

    return tenant


async def cambia_stato_tenant(
    tenant_id: str, nuovo_stato: str, motivo: str | None = None) -> Tenant:
    """Cambia lo stato del tenant con validazione della state machine."""
    try:
        stato_enum = TenantStato(nuovo_stato.upper())
    except ValueError:
        raise HTTPException(
            422,
            {"type": "ONB-202", "title": f"Stato {nuovo_stato!r} non valido",
             "detail": f"Valori ammessi: {[s.value for s in TenantStato]}"},
        ) from None

    async with get_async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(404, {"type": "ONB-404", "title": "Tenant non trovato"})

        validate_transition(tenant.stato, stato_enum)
        vecchio_stato = tenant.stato
        tenant.stato = stato_enum
        await accoda_evento(
            session,
            topic=settings.kafka_topic_lifecycle,
            chiave=tenant_id,
            tenant_id=tenant.id,
            payload={
                "evento": "tenant.stato_cambiato",
                "tenant_id": tenant_id,
                "da": vecchio_stato.value,
                "a": stato_enum.value,
                "motivo": motivo,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()
        await session.refresh(tenant)

    logger.info("tenant_state_changed",
                tenant_id=tenant_id,
                da=vecchio_stato.value,
                a=stato_enum.value,
                motivo=motivo)

    # Evento già durevole in outbox: tentativo immediato, mai un errore al client
    await pubblica_dopo_commit(evento="lifecycle", tenant_id=str(tenant.id))

    return tenant
