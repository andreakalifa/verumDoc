# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Aggiunge tipologia_xml a classi_documentali.

Revision ID: 20260319_004
Revises: 20260319_003
Create Date: 2026-03-19
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20260319_004"
down_revision = "20260319_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enum tipo SInCRO
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tipo_sincro_xml AS ENUM ('DI', 'DAI', 'AGG');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Aggiunge colonna tipologia_xml con default DAI
    op.add_column(
        "classi_documentali",
        sa.Column(
            "tipologia_xml",
            sa.Enum("DI", "DAI", "AGG", name="tipo_sincro_xml", create_type=False),
            nullable=False,
            server_default="DAI",
        ),
        schema="public",
    )

    # Aggiorna le classi esistenti con la tipologia corretta
    op.execute("""
        UPDATE public.classi_documentali
        SET tipologia_xml = 'DAI'
        WHERE codice IN ('fattura', 'contratto', 'delibera', 'determinazione',
                        'verbale', 'circolare', 'decreto');
    """)

    op.execute("""
        UPDATE public.classi_documentali
        SET tipologia_xml = 'DI'
        WHERE codice IN ('allegato_tecnico', 'immagine', 'documento_tecnico');
    """)

    op.execute("""
        UPDATE public.classi_documentali
        SET tipologia_xml = 'DI'
        WHERE codice = 'altro';
    """)


def downgrade() -> None:
    op.drop_column("classi_documentali", "tipologia_xml", schema="public")
    op.execute("DROP TYPE IF EXISTS tipo_sincro_xml;")
