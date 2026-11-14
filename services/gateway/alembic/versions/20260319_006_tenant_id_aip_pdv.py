# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Aggiunge tenant_id a pdv e aip per isolamento multi-tenant.

B-01 + B-02 FIX: senza tenant_id diretto su aip, lista_aip restituisce
tutti gli AIP di tutti i tenant — data leakage critico.

Revision ID: 20260319_006
Revises: 20260319_005
Create Date: 2026-03-19
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260319_006"
down_revision = "20260319_005"
branch_labels = None
depends_on = None

# UUID del tenant di default (dev seed)
_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # 1. Aggiunge tenant_id a pdv (nullable prima, poi NOT NULL)
    op.add_column(
        "pdv",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    # Popola le righe esistenti con il tenant di default
    op.execute(
        f"UPDATE public.pdv SET tenant_id = '{_DEFAULT_TENANT}'::uuid "
        "WHERE tenant_id IS NULL"
    )
    # Ora rende NOT NULL
    op.alter_column("pdv", "tenant_id", nullable=False, schema="public")

    # 2. Aggiunge tenant_id a aip (via JOIN su pdv)
    op.add_column(
        "aip",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    op.execute(
        "UPDATE public.aip a "
        "SET tenant_id = p.tenant_id "
        "FROM public.pdv p "
        "WHERE a.pdv_id = p.id"
    )
    # Righe orfane (pdv_id non trovato) — usa default
    op.execute(
        f"UPDATE public.aip SET tenant_id = '{_DEFAULT_TENANT}'::uuid "
        "WHERE tenant_id IS NULL"
    )
    op.alter_column("aip", "tenant_id", nullable=False, schema="public")

    # 3. Indici per performance query per tenant
    op.create_index("idx_pdv_tenant",   "pdv", ["tenant_id"], schema="public")
    op.create_index("idx_aip_tenant",   "aip", ["tenant_id"], schema="public")
    op.create_index("idx_aip_tenant_stato", "aip",
                    ["tenant_id", "stato"], schema="public")


def downgrade() -> None:
    op.drop_index("idx_aip_tenant_stato", table_name="aip", schema="public")
    op.drop_index("idx_aip_tenant",       table_name="aip", schema="public")
    op.drop_index("idx_pdv_tenant",       table_name="pdv", schema="public")
    op.drop_column("aip", "tenant_id", schema="public")
    op.drop_column("pdv", "tenant_id", schema="public")
