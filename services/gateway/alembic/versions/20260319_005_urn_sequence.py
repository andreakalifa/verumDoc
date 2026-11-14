# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Aggiunge sequence per progressivo URN conforme AGID.

Revision ID: 20260319_005
Revises: 20260319_004
Create Date: 2026-03-19
"""
from __future__ import annotations
from alembic import op

revision = "20260319_005"
down_revision = "20260319_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sequence globale per il progressivo URN — AGID richiede univocità
    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS public.urn_progressivo
            START WITH 1
            INCREMENT BY 1
            NO MAXVALUE
            CACHE 10;
    """)


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS public.urn_progressivo;")
