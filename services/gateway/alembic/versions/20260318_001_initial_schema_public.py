# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Schema public iniziale — tutte le tabelle di controllo VerumDoc

Revision ID: 20260318_001
Revises:
Create Date: 2026-03-18 00:00:00.000000

Crea lo schema public con:
  - Enum types globali (tenant_stato, user_ruolo)
  - Tabella tenants
  - Tabella users
  - Tabella api_keys
  - Tabella rsc_nominations
  - Tabella contracts
  - Tabella audit_global (append-only — RLS applicato)
  - Tabella hashchain_blocks

NOTA: Tutte le tabelle operativi sono in schema public (v4.2).
L'isolamento multi-tenant avviene tramite tenant_id su pdv e aip.
Non esiste creazione di schemi per-tenant (architettura abbandonata).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "20260318_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Assicura che lo schema public esista ─────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS public")

    # ── Enum Types ────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE public.tenant_stato AS ENUM (
                'TRIAL', 'ATTIVO', 'SOSPESO', 'DISATTIVATO'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE public.user_ruolo AS ENUM (
                'SUPER_ADMIN', 'ADMIN_TENANT', 'OPERATORE', 'VIEWER', 'API_SYSTEM'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ── Tabella TENANTS ───────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id",                 postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("codice_fiscale",     sa.String(16),    nullable=False),
        sa.Column("ragione_sociale",    sa.String(500),   nullable=False),
        sa.Column("schema_name",        sa.String(63),    nullable=False),
        sa.Column("stato",              postgresql.ENUM("TRIAL","ATTIVO","SOSPESO","DISATTIVATO",
                                                        name="tenant_stato", schema="public", create_type=False),
                  nullable=False, server_default="TRIAL"),
        sa.Column("piano",              sa.String(50),    nullable=False, server_default="starter"),
        sa.Column("storage_quota_gb",   sa.Integer(),     nullable=False, server_default="100"),
        sa.Column("storage_usato_gb",   sa.Float(),       nullable=False, server_default="0"),
        sa.Column("email_referente",    sa.String(255),   nullable=False),
        sa.Column("pec_referente",      sa.String(255)),
        sa.Column("codice_ipa",         sa.String(50)),
        sa.Column("webhook_url",        sa.String(500)),
        sa.Column("webhook_secret_hash",sa.String(128)),
        sa.Column("metadata_json",      postgresql.JSONB, server_default="{}"),
        sa.Column("data_attivazione",   sa.DateTime(timezone=True)),
        sa.Column("data_scadenza",      sa.DateTime(timezone=True)),
        sa.Column("created_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("codice_fiscale", name="uq_tenants_cf"),
        sa.UniqueConstraint("schema_name",    name="uq_tenants_schema_name"),
        schema="public",
    )
    op.create_index("idx_tenants_cf",          "tenants", ["codice_fiscale"], schema="public")
    op.create_index("idx_tenants_schema_name", "tenants", ["schema_name"],    schema="public")
    op.create_index("idx_tenants_stato",       "tenants", ["stato"],          schema="public")

    # Trigger: updated_at automatico
    op.execute("""
        CREATE OR REPLACE FUNCTION public.set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tenants_updated_at
        BEFORE UPDATE ON public.tenants
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
    """)

    # ── Tabella USERS ─────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",           postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email",        sa.String(255), nullable=False),
        sa.Column("nome",         sa.String(200), nullable=False),
        sa.Column("cognome",      sa.String(200), nullable=False),
        sa.Column("ruolo",        postgresql.ENUM("SUPER_ADMIN","ADMIN_TENANT","OPERATORE",
                                                  "VIEWER","API_SYSTEM",
                                                  name="user_ruolo", schema="public", create_type=False),
                  nullable=False, server_default="OPERATORE"),
        sa.Column("password_hash",  sa.String(128)),
        sa.Column("mfa_secret",     sa.String(64)),
        sa.Column("attivo",         sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ultimo_login",   sa.DateTime(timezone=True)),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        schema="public",
    )
    op.create_index("idx_users_tenant", "users", ["tenant_id"], schema="public")
    op.create_index("idx_users_email",  "users", ["email"],     schema="public")
    op.execute("""
        CREATE TRIGGER users_updated_at
        BEFORE UPDATE ON public.users
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
    """)

    # ── Tabella API_KEYS ──────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",   postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id",     postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome",        sa.String(200), nullable=False),
        sa.Column("key_hash",    sa.String(128), unique=True,   nullable=False),
        sa.Column("key_prefix",  sa.String(10),  nullable=False),
        sa.Column("permessi",    postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("attiva",      sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("scadenza",    sa.DateTime(timezone=True)),
        sa.Column("ultimo_uso",  sa.DateTime(timezone=True)),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"],   ["public.users.id"],   ondelete="CASCADE"),
        schema="public",
    )
    op.create_index("idx_apikeys_tenant", "api_keys", ["tenant_id"], schema="public")
    op.create_index("idx_apikeys_hash",   "api_keys", ["key_hash"],  schema="public")

    # ── Tabella RSC_NOMINATIONS ───────────────────────────────────
    op.create_table(
        "rsc_nominations",
        sa.Column("id",                  postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",           postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome_rsc",            sa.String(300), nullable=False),
        sa.Column("codice_fiscale_rsc",  sa.String(16),  nullable=False),
        sa.Column("email_rsc",           sa.String(255), nullable=False),
        sa.Column("pec_rsc",             sa.String(255)),
        sa.Column("data_nomina",         sa.Date(), nullable=False),
        sa.Column("data_scadenza",       sa.Date()),
        sa.Column("documento_s3_key",    sa.String(500)),
        sa.Column("attiva",              sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"),
        schema="public",
    )
    op.create_index("idx_rsc_tenant", "rsc_nominations", ["tenant_id"], schema="public")

    # ── Tabella CONTRACTS ─────────────────────────────────────────
    op.create_table(
        "contracts",
        sa.Column("id",                 postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",          postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("numero_contratto",   sa.String(100), unique=True, nullable=False),
        sa.Column("data_stipula",       sa.Date(), nullable=False),
        sa.Column("data_scadenza",      sa.Date()),
        sa.Column("piano",              sa.String(50), nullable=False),
        sa.Column("valore_annuo_eur",   sa.Float()),
        sa.Column("template_s3_key",    sa.String(500)),
        sa.Column("firmato_s3_key",     sa.String(500)),
        sa.Column("attivo",             sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"),
        schema="public",
    )
    op.create_index("idx_contracts_tenant", "contracts", ["tenant_id"], schema="public")

    # ── Tabella AUDIT_GLOBAL (append-only — RLS) ──────────────────
    op.create_table(
        "audit_global",
        sa.Column("id",         postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",  postgresql.UUID(as_uuid=True)),
        sa.Column("user_id",    postgresql.UUID(as_uuid=True)),
        sa.Column("aip_id",     postgresql.UUID(as_uuid=True)),
        sa.Column("evento",     sa.String(100), nullable=False),
        sa.Column("dettagli",   postgresql.JSONB, server_default="{}"),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        # NO updated_at — audit è append-only
        schema="public",
    )
    op.create_index("idx_audit_tenant",  "audit_global", ["tenant_id"],  schema="public")
    op.create_index("idx_audit_created", "audit_global", ["created_at"], schema="public")
    op.create_index("idx_audit_evento",  "audit_global", ["evento"],     schema="public")

    # RLS: rende audit_global append-only — nessuno può UPDATE o DELETE
    op.execute("ALTER TABLE public.audit_global ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY audit_global_insert_only
        ON public.audit_global
        FOR INSERT
        WITH CHECK (true);
    """)
    op.execute("""
        CREATE POLICY audit_global_select_all
        ON public.audit_global
        FOR SELECT
        USING (true);
    """)
    # Non creare policy per UPDATE e DELETE = nessuno può farlo

    # ── Tabella HASHCHAIN_BLOCKS ───────────────────────────────────
    op.create_table(
        "hashchain_blocks",
        sa.Column("id",                      postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("numero_blocco",           sa.BigInteger(), unique=True, nullable=False),
        sa.Column("hash_blocco",             sa.String(128),  unique=True, nullable=False),
        sa.Column("hash_blocco_precedente",  sa.String(128),  nullable=False),
        sa.Column("merkle_root",             sa.String(128),  nullable=False),
        sa.Column("aip_ids",                 postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("num_aip",                 sa.Integer(), nullable=False, server_default="0"),
        sa.Column("firma_cades",             sa.Text()),
        sa.Column("tsa_token",               sa.Text()),
        sa.Column("tsa_timestamp",           sa.DateTime(timezone=True)),
        sa.Column("created_at",              sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="public",
    )
    op.create_index("idx_hchain_numero",  "hashchain_blocks", ["numero_blocco"], schema="public")
    op.create_index("idx_hchain_created", "hashchain_blocks", ["created_at"],    schema="public")

    # Blocco genesis (blocco 0) — ancora della catena
    op.execute("""
        INSERT INTO public.hashchain_blocks
            (numero_blocco, hash_blocco, hash_blocco_precedente, merkle_root, aip_ids, num_aip)
        VALUES
            (0,
             'GENESIS_0000000000000000000000000000000000000000000000000000000000000000',
             '0000000000000000000000000000000000000000000000000000000000000000',
             '0000000000000000000000000000000000000000000000000000000000000000',
             '[]',
             0
            )
        ON CONFLICT (numero_blocco) DO NOTHING;
    """)


def downgrade() -> None:
    # Rimuovi in ordine inverso (FK dependencies)
    op.drop_table("hashchain_blocks",  schema="public")
    op.drop_table("audit_global",      schema="public")
    op.drop_table("contracts",         schema="public")
    op.drop_table("rsc_nominations",   schema="public")
    op.drop_table("api_keys",          schema="public")
    op.drop_table("users",             schema="public")
    op.drop_table("tenants",           schema="public")

    op.execute("DROP FUNCTION IF EXISTS public.set_updated_at() CASCADE;")
    op.execute("DROP TYPE IF EXISTS public.user_ruolo;")
    op.execute("DROP TYPE IF EXISTS public.tenant_stato;")
