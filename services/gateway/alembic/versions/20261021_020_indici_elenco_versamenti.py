# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Indici per l'elenco dei versamenti a cursore (dashboard-api, R03 i1)

Revision ID: 20261021_020
Revises: 20261021_019
Create Date: 2026-10-21

``GET /api/dashboard/versamenti`` passa da offset + ``COUNT(*)`` a una
paginazione per chiave ``(data_ricezione, id)`` decrescente, per ente e
facoltativamente per stato. Gli indici esistenti (``idx_pdv_tenant``,
``idx_pdv_data_ric_desc``) non servono quella chiave: con milioni di PdV
l'ordinamento richiederebbe di leggere tutte le righe dell'ente.

  - ``idx_pdv_tenant_ricezione`` (tenant_id, data_ricezione DESC, id DESC);
  - ``idx_pdv_tenant_stato_ricezione`` (tenant_id, stato, data_ricezione DESC,
    id DESC), per il filtro per stato (es. i PdV in ERRORE).

Creati con CONCURRENTLY (fuori transazione: ``autocommit_block``) per non
bloccare le scritture su ``pdv`` in esercizio. Nessun dato toccato. Downgrade:
elimina i due indici.
"""
from __future__ import annotations

from alembic import op

revision = "20261021_020"
down_revision = "20261021_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_tenant_ricezione "
                   "ON public.pdv (tenant_id, data_ricezione DESC, id DESC)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_tenant_stato_ricezione "
                   "ON public.pdv (tenant_id, stato, data_ricezione DESC, id DESC)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_tenant_stato_ricezione")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_tenant_ricezione")
