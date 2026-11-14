# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Canale SFTP: account per ente, chiavi pubbliche, lavorazioni con lease

Revision ID: 20261021_016
Revises: 20261021_014
Create Date: 2026-10-21

Contenuto (agente R03 b2, piano R03 §3.2):
  - public.sftp_account: account SFTP per ente (nome di accesso unico fra tutti
    gli enti, utente responsabile per conto del quale il canale versa, stato
    ATTIVO/SOSPESO; mai eliminati);
  - public.sftp_chiavi: chiavi pubbliche SSH degli account (impronta SHA256,
    revoca con data; impronta unica fra le chiavi attive dell'account);
  - public.sftp_lavorazioni: presa in carico delle cartelle con lease (REGOLE
    §12.9): al massimo una lavorazione IN_CORSO per account e cartella (indice
    unico parziale), ripresa dopo un crash quando il lease scade.

Solo tabelle nuove: nessun dato esistente né contenuto conservato toccato
(§10.6). Downgrade: elimina le tre tabelle (i dati degli account SFTP si
perdono: sono tabelle nuove di questa revisione).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_016"
down_revision = "20261021_014"
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                     server_default=sa.text("gen_random_uuid()"))


def _adesso(nome: str) -> sa.Column:
    return sa.Column(nome, sa.DateTime(timezone=True), nullable=False,
                     server_default=sa.text("now()"))


def _fk(nome: str, destinazione: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(nome, postgresql.UUID(as_uuid=True),
                     sa.ForeignKey(destinazione, ondelete="RESTRICT"), nullable=nullable)


def upgrade() -> None:
    # ── sftp_account ───────────────────────────────────────────────────
    op.create_table(
        "sftp_account",
        _uuid_pk(),
        _fk("tenant_id", "public.tenants.id"),
        sa.Column("nome_utente", sa.String(64), nullable=False),
        sa.Column("descrizione", sa.String(200), nullable=False, server_default=sa.text("''")),
        _fk("responsabile_id", "public.users.id"),
        sa.Column("stato", sa.String(20), nullable=False, server_default=sa.text("'ATTIVO'")),
        sa.Column("sospeso_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creato_da", postgresql.UUID(as_uuid=True), nullable=True),
        _adesso("created_at"),
        _adesso("updated_at"),
        sa.UniqueConstraint("nome_utente", name="uq_sftp_account_nome_utente"),
        sa.CheckConstraint("stato IN ('ATTIVO', 'SOSPESO')", name="ck_sftp_account_stato"),
        sa.CheckConstraint("nome_utente ~ '^[a-z0-9][a-z0-9._-]{2,63}$'",
                           name="ck_sftp_account_nome_utente"),
        schema="public",
    )
    op.create_index("idx_sftp_account_tenant", "sftp_account", ["tenant_id"], schema="public")

    # ── sftp_chiavi ────────────────────────────────────────────────────
    op.create_table(
        "sftp_chiavi",
        _uuid_pk(),
        _fk("account_id", "public.sftp_account.id"),
        _fk("tenant_id", "public.tenants.id"),
        sa.Column("algoritmo", sa.String(50), nullable=False),
        sa.Column("bit", sa.Integer(), nullable=True),
        sa.Column("chiave_pubblica", sa.Text(), nullable=False),
        sa.Column("impronta", sa.String(100), nullable=False),
        sa.Column("commento", sa.String(200), nullable=True),
        sa.Column("creata_da", postgresql.UUID(as_uuid=True), nullable=True),
        _adesso("created_at"),
        sa.Column("revocata_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocata_da", postgresql.UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    op.create_index("idx_sftp_chiavi_account", "sftp_chiavi", ["account_id"], schema="public")
    op.create_index("uq_sftp_chiavi_attiva", "sftp_chiavi", ["account_id", "impronta"],
                    unique=True, schema="public",
                    postgresql_where=sa.text("revocata_il IS NULL"))

    # ── sftp_lavorazioni ───────────────────────────────────────────────
    op.create_table(
        "sftp_lavorazioni",
        _uuid_pk(),
        _fk("tenant_id", "public.tenants.id"),
        _fk("account_id", "public.sftp_account.id"),
        sa.Column("cartella", sa.String(128), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False, server_default=sa.text("'IN_CORSO'")),
        sa.Column("detentore", sa.String(200), nullable=True),
        sa.Column("lease_scade_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("impronta_indice", sa.String(64), nullable=True),
        sa.Column("chiave_idempotenza", sa.String(200), nullable=True),
        sa.Column("caricamenti", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("pdv_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("esito", postgresql.JSONB(), nullable=True),
        sa.Column("ultimo_errore", postgresql.JSONB(), nullable=True),
        _adesso("created_at"),
        _adesso("updated_at"),
        sa.Column("concluso_il", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("stato IN ('IN_CORSO', 'VERSATA', 'SCARTATA')",
                           name="ck_sftp_lavorazioni_stato"),
        schema="public",
    )
    op.create_index("uq_sftp_lavorazioni_attiva", "sftp_lavorazioni", ["account_id", "cartella"],
                    unique=True, schema="public",
                    postgresql_where=sa.text("stato = 'IN_CORSO'"))
    op.create_index("idx_sftp_lavorazioni_cartella", "sftp_lavorazioni",
                    ["account_id", "cartella", "created_at"], schema="public")
    op.create_index("idx_sftp_lavorazioni_lease", "sftp_lavorazioni", ["lease_scade_il"],
                    schema="public", postgresql_where=sa.text("stato = 'IN_CORSO'"))


def downgrade() -> None:
    op.drop_index("idx_sftp_lavorazioni_lease", table_name="sftp_lavorazioni", schema="public")
    op.drop_index("idx_sftp_lavorazioni_cartella", table_name="sftp_lavorazioni", schema="public")
    op.drop_index("uq_sftp_lavorazioni_attiva", table_name="sftp_lavorazioni", schema="public")
    op.drop_table("sftp_lavorazioni", schema="public")
    op.drop_index("uq_sftp_chiavi_attiva", table_name="sftp_chiavi", schema="public")
    op.drop_index("idx_sftp_chiavi_account", table_name="sftp_chiavi", schema="public")
    op.drop_table("sftp_chiavi", schema="public")
    op.drop_index("idx_sftp_account_tenant", table_name="sftp_account", schema="public")
    op.drop_table("sftp_account", schema="public")
