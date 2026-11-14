# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Performance indexes — Sprint v4.2

Revision ID: 20260319_008
Revises: 20260319_007
Create Date: 2026-03-20

Indici aggiuntivi per le query più frequenti della dashboard e del lifecycle manager.
Identificati dall'analisi del piano EXPLAIN sui pattern di query comuni.

Correzione R03 (VD-C-007): CREATE/DROP INDEX CONCURRENTLY non può girare
dentro una transazione (ActiveSQLTransactionError): le istruzioni ora sono
in ``autocommit_block()``. Nessun database aveva mai superato questa
revisione (la 007 falliva prima), quindi la correzione è sicura.
L'indice parziale sulle notifiche usa lo stato 'PENDING', che la migrazione
20261020_010 rende il default della colonna (allineato al modello).
"""
from __future__ import annotations

from alembic import op


revision = "20260319_008"
down_revision = "20260319_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _crea_indici()


def _crea_indici() -> None:
    # ── pdv: data_ricezione DESC per lista versamenti recenti ─────
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_data_ric_desc
        ON public.pdv (data_ricezione DESC);
    """)

    # ── aip: scadenza_conservazione per lifecycle manager ─────────
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aip_scadenza
        ON public.aip (scadenza_conservazione)
        WHERE scadenza_conservazione IS NOT NULL;
    """)

    # ── notifications: PARTIAL index su PENDING per retry queue ───
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_pending
        ON public.notifications (created_at)
        WHERE stato = 'PENDING';
    """)

    # ── aip: storage_tier per tiered storage lifecycle ────────────
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aip_tier_versamento
        ON public.aip (storage_tier, data_versamento)
        WHERE storage_tier IN ('HOT', 'WARM', 'COLD');
    """)

    # ── audit_global: tenant + evento per audit queries ───────────
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_audit_tenant_evento
        ON public.audit_global (tenant_id, evento, created_at DESC);
    """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _elimina_indici()


def _elimina_indici() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_data_ric_desc;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_aip_scadenza;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_notifications_pending;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_aip_tier_versamento;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_audit_tenant_evento;")
