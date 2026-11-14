# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Consolidamento architettura DB — da dual-schema a single-schema public

Revision ID: 20260318_002
Revises: 20260318_001
Create Date: 2026-03-18 00:00:01.000000
Aggiornato: Sprint v4.2 — Consolidamento DB

ARCHITETTURA DB (versione definitiva, Sprint v4.2):
  Tutti i dati operativi risiedono nello schema public.
  L'isolamento multi-tenant è garantito da tenant_id (NOT NULL) su pdv e aip.
  Non viene creato nessuno schema PostgreSQL per-tenant.
  Il campo schema_name in public.tenants è mantenuto come campo identificativo
  ma non viene più usato per creare schemi separati.

  Questa migration è una no-op documentale — l'architettura è stabilita
  da 001 (schema public) e 006 (tenant_id su pdv/aip).
  Per modifiche future alla struttura multi-tenant, creare nuove migration
  che operano SEMPRE su public.* con schema="public".

Non toccare questa migration senza aggiornare Regole_Sviluppo_VerumDoc.docx § DB.
"""
from __future__ import annotations

from alembic import op

revision: str = "20260318_002"
down_revision = "20260318_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    No-op documentale.
    L'architettura single-schema public.* è definita in migration 001.
    L'isolamento multi-tenant via tenant_id è definito in migration 006.
    """
    pass


def downgrade() -> None:
    pass
