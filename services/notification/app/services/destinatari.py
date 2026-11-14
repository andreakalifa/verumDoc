# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/services/destinatari.py
Destinatari reali delle notifiche — VD-C-024, REGOLE §21.2.

Una sola funzione, destinatari(tenant, evento), calcola gli indirizzi dai dati
del cliente (public.tenants.email_referente). Indirizzi segnaposto
(tenant-x@verumdoc.local, *.example, *.invalid…) non sono destinatari: il
messaggio resta «non partito» con il motivo, e la dashboard lo mostra.
Solo clienti attivi o in prova ricevono notifiche (REGOLE §21.5).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import structlog

from app.services.smtp import indirizzo_plausibile

logger = structlog.get_logger(__name__)

STATI_CLIENTE_NOTIFICABILI = {"ATTIVO", "TRIAL"}
# Evento → canale e campo del cliente da cui leggere l'indirizzo
EVENTI_EMAIL_REFERENTE = {"pdv.invalid", "aip.conserved",
                          # R07: avvisi automatici (D13, D17) e scadenza dello staging (D15)
                          "quota.soglia_raggiunta", "idempotenza.chiave_omessa",
                          "versamento.staging_scaduto",
                          # R09: avvisi del ciclo di vita (D20, D21, D23)
                          "ciclo_vita.avviso"}


@dataclass(frozen=True)
class Destinatari:
    indirizzi: list[str] = field(default_factory=list)
    motivo: str | None = None  # valorizzato quando non c'è nessun destinatario


LettoreCliente = Callable[[str], Awaitable[dict | None]]


async def leggi_cliente(tenant_id: str) -> dict | None:
    """Dati di contatto del cliente dal DB (None se il cliente non esiste)."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        riga = (
            (
                await session.execute(
                    text(
                        "SELECT email_referente, pec_referente, stato::text AS stato "
                        "FROM public.tenants WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": tenant_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(riga) if riga else None


async def destinatari(
    tenant_id: str, evento: str, *, lettore: LettoreCliente = leggi_cliente
) -> Destinatari:
    """Destinatari dell'evento per il cliente, dai dati reali."""
    try:
        UUID(str(tenant_id))
    except (ValueError, TypeError):
        return Destinatari(motivo="evento senza tenant_id valido")
    if evento not in EVENTI_EMAIL_REFERENTE:
        return Destinatari(motivo=f"nessuna regola di destinatari per l'evento {evento}")
    cliente = await lettore(str(tenant_id))
    if cliente is None:
        return Destinatari(motivo="cliente inesistente")
    if str(cliente.get("stato") or "") not in STATI_CLIENTE_NOTIFICABILI:
        return Destinatari(motivo=f"cliente in stato {cliente.get('stato')}: notifiche sospese")
    email = (cliente.get("email_referente") or "").strip()
    if not indirizzo_plausibile(email):
        return Destinatari(
            motivo="destinatario non configurato: email del referente mancante o segnaposto"
        )
    return Destinatari(indirizzi=[email])


# ── R10 (D32): posta per utente secondo le preferenze ───────────────────────

async def destinatari_utenti(tenant_id: str, topic: str, evento: dict) -> list[str]:
    """Indirizzi delle persone dell'ente a cui l'evento riguarda (permesso della
    categoria) e che hanno scelto l'e-mail per quella categoria, o per cui l'ente
    l'ha resa obbligatoria (``NOTIFICHE_OBBLIGATORIE``). Predefinito: nessuno
    (la posta resta al referente come prima, REGOLE §8.14)."""
    from sqlalchemy import text

    from app.domain.campanella import classifica, scelte_effettive
    from libs.shared.db import get_async_session
    from libs.shared.db.ambito import ambito_ente
    from libs.shared.parametri import parametri
    from libs.shared.security.permessi import ha_permesso
    from libs.verumdoc_rules.parametri_servizio import NOTIFICHE_OBBLIGATORIE

    voce = classifica(topic, evento)
    if voce is None:
        return []
    obbligatorie = tuple((await parametri([NOTIFICHE_OBBLIGATORIE], tenant_id))
                         .get(NOTIFICHE_OBBLIGATORIE) or ())
    with ambito_ente(tenant_id):
        async with get_async_session() as session:
            righe = (await session.execute(text(
                "SELECT u.email, u.ruolo::text AS ruolo, u.ruolo_personalizzato_id::text AS rp, "
                "       r.permessi, p.scelte "
                "FROM public.users u "
                "LEFT JOIN public.ruoli_personalizzati r ON r.id = u.ruolo_personalizzato_id "
                "LEFT JOIN public.notifiche_preferenze p ON p.user_id = u.id "
                "WHERE u.tenant_id = CAST(:t AS uuid) AND u.attivo "
                "  AND u.ruolo::text <> 'API_SYSTEM' ORDER BY u.email LIMIT 500"),
                {"t": tenant_id})).mappings().all()
    indirizzi: list[str] = []
    for r in righe:
        identita = {"ruolo": r["ruolo"], "rp": r["rp"], "permessi": list(r["permessi"] or [])}
        if not ha_permesso(identita, voce.permesso):
            continue
        if not scelte_effettive(dict(r["scelte"] or {}), obbligatorie)[voce.categoria]["EMAIL"]:
            continue
        email = (r["email"] or "").strip().lower()
        if indirizzo_plausibile(email) and email not in indirizzi:
            indirizzi.append(email)
    return indirizzi
