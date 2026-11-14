# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Consolidamento DB v4.2 — colonne mancanti, enum AGID, indici performance

Revision ID: 20260319_007
Revises: 20260319_006
Create Date: 2026-03-20

Questo file fa parte del piano di consolidamento DB Sprint v4.2.
Applica le correzioni identificate nell'audit DB:
  - DB-03: rinomina metadata → payload_json in notifications (se esiste)
  - DB-10: (rimosso in R03, vedi sotto e migrazione 20261020_010)
  - DB-11: aggiunge colonna storage_percentuale a tenants
  - DB-user_ruolo: aggiunge RSC_CONSERVATORE a user_ruolo
  - Aggiunge classi_abilitate (JSONB) a tenants per configurazione per-tenant
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_007"
down_revision = "20260319_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── DB-11: storage_percentuale a tenants ─────────────────────
    # Colonna calcolata (applicazione aggiorna) — non generata per compatibilità
    op.execute("""
        ALTER TABLE public.tenants
        ADD COLUMN IF NOT EXISTS storage_percentuale FLOAT NOT NULL DEFAULT 0.0;
    """)

    # ── classi_abilitate a tenants ────────────────────────────────
    # JSONB array di UUID classi abilitate per questo tenant (null = tutte)
    op.execute("""
        ALTER TABLE public.tenants
        ADD COLUMN IF NOT EXISTS classi_abilitate JSONB;
    """)

    # ── DB-03: metadata → payload_json in notifications ───────────
    # Applica rename solo se la colonna si chiama ancora "metadata"
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'notifications'
                  AND column_name = 'metadata'
            ) THEN
                ALTER TABLE public.notifications
                RENAME COLUMN metadata TO payload_json;
            END IF;
        END $$;
    """)

    # ── notifications: aggiunge tenant_id se mancante ────────────
    op.execute("""
        ALTER TABLE public.notifications
        ADD COLUMN IF NOT EXISTS tenant_id UUID;
    """)

    # ── DB-10: enum modalita_formazione ──────────────────────────
    # RIMOSSO (R03, VD-C-007/VD-C-037): qui c'erano tre
    # «ALTER TYPE modalita_formazione ADD VALUE» su un tipo che nessuna
    # migrazione aveva mai creato: su database vuoto l'upgrade si fermava
    # con UndefinedObjectError e nessun database ha mai superato questa
    # revisione. Il tipo, con i valori a/b/c/d dell'Allegato 5 (LG AgID
    # §2.1.1), nasce ora con la tabella metadati_allegato5 nella
    # migrazione 20261020_010.

    # ── RSC_CONSERVATORE a user_ruolo ────────────────────────────
    op.execute("""
        DO $$ BEGIN
            ALTER TYPE public.user_ruolo ADD VALUE IF NOT EXISTS 'RSC_CONSERVATORE';
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)


def downgrade() -> None:
    # storage_percentuale e classi_abilitate possono essere rimossi
    op.execute("""
        ALTER TABLE public.tenants
        DROP COLUMN IF EXISTS storage_percentuale;
    """)
    op.execute("""
        ALTER TABLE public.tenants
        DROP COLUMN IF EXISTS classi_abilitate;
    """)
    op.execute("""
        ALTER TABLE public.notifications
        DROP COLUMN IF EXISTS tenant_id;
    """)
    # NOTA: ALTER TYPE ADD VALUE è irreversibile — i valori enum non possono
    # essere rimossi in PostgreSQL senza recreare il tipo
