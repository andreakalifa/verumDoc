# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Outbox, idempotenza del versamento, catena delle impronte append-only (R08/R09/R15)

Revision ID: 20261020_011
Revises: 20261020_010
Create Date: 2026-10-20

Contenuto (agente be-pipeline):
  - public.outbox_eventi (VD-C-015, REGOLE §12.3): eventi scritti nella stessa
    transazione del cambio di stato, pubblicati da un relay;
  - public.versamento_impronte (VD-C-016, REGOLE §12.1): per ogni PdV
    l'impronta dell'indice normalizzato, l'impronta della richiesta e la
    Idempotency-Key facoltativa; unicità per cliente della chiave e
    dell'impronta dell'indice fra i PdV non rifiutati (``attivo``);
  - public.hashchain_blocks (VD-C-018, VD-C-019): ``versione_formato`` (1 =
    blocchi storici, 2 = Merkle RFC 6962 + hash con istante e AIP),
    ``stato_firma`` (DA_FIRMARE / FIRMATO / NON_FIRMATO) e ``motivo_firma``;
    i blocchi esistenti diventano NON_FIRMATO con motivo esplicito;
  - trigger append-only su hashchain_blocks: DELETE vietata, UPDATE ammesso
    solo sulle colonne di firma e solo da DA_FIRMARE (REGOLE §10.1, §15.4).

Nessun contenuto conservato viene modificato (§10.6): ai blocchi storici si
aggiungono solo colonne di stato.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_011"
down_revision = "20261020_010"
branch_labels = None
depends_on = None

_STATI_FIRMA = ("DA_FIRMARE", "FIRMATO", "NON_FIRMATO")


def upgrade() -> None:
    # ── outbox_eventi ────────────────────────────────────────────────
    op.create_table(
        "outbox_eventi",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # Ordine totale di pubblicazione (più eventi della stessa transazione
        # hanno lo stesso creato_il): sequenza crescente assegnata all'INSERT
        sa.Column("sequenza", sa.BigInteger(), sa.Identity(always=True), nullable=False,
                  unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("chiave", sa.String(200), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("creato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("pubblicato_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ultimo_errore", sa.Text(), nullable=True),
        schema="public",
    )
    op.create_index(
        "idx_outbox_in_sospeso", "outbox_eventi", ["sequenza"],
        schema="public", postgresql_where=sa.text("pubblicato_il IS NULL"),
    )

    # ── versamento_impronte ──────────────────────────────────────────
    op.create_table(
        "versamento_impronte",
        sa.Column("pdv_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pdv.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("impronta_indice", sa.String(64), nullable=False),
        sa.Column("impronta_richiesta", sa.String(64), nullable=False),
        sa.Column("chiave_idempotenza", sa.String(200), nullable=True),
        sa.Column("attivo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        schema="public",
    )
    op.create_index(
        "uq_versamento_idempotenza", "versamento_impronte",
        ["tenant_id", "chiave_idempotenza"], unique=True, schema="public",
        postgresql_where=sa.text("chiave_idempotenza IS NOT NULL"),
    )
    op.create_index(
        "uq_versamento_impronta_attiva", "versamento_impronte",
        ["tenant_id", "impronta_indice"], unique=True, schema="public",
        postgresql_where=sa.text("attivo"),
    )

    # ── hashchain_blocks: formato e firma ────────────────────────────
    op.add_column("hashchain_blocks", sa.Column(
        "versione_formato", sa.SmallInteger(), nullable=False, server_default=sa.text("1"),
    ), schema="public")
    op.add_column("hashchain_blocks", sa.Column("stato_firma", sa.String(20), nullable=True),
                  schema="public")
    op.add_column("hashchain_blocks", sa.Column("motivo_firma", sa.Text(), nullable=True),
                  schema="public")
    op.execute(
        "UPDATE public.hashchain_blocks SET stato_firma = 'NON_FIRMATO', "
        "motivo_firma = 'non firmato: blocco sigillato in formato 1, prima "
        "dell''integrazione con il servizio di firma' "
        "WHERE stato_firma IS NULL"
    )
    op.alter_column("hashchain_blocks", "stato_firma", nullable=False, schema="public")
    op.create_check_constraint(
        "ck_hashchain_stato_firma", "hashchain_blocks",
        "stato_firma IN ('" + "', '".join(_STATI_FIRMA) + "')", schema="public",
    )
    op.create_check_constraint(
        "ck_hashchain_versione_formato", "hashchain_blocks",
        "versione_formato IN (1, 2)", schema="public",
    )

    # ── Append-only ─────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION public.hashchain_blocks_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'hashchain_blocks è append-only: TRUNCATE vietata';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'hashchain_blocks è append-only: DELETE vietata (blocco %)',
                    OLD.numero_blocco;
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.numero_blocco IS DISTINCT FROM OLD.numero_blocco
               OR NEW.hash_blocco IS DISTINCT FROM OLD.hash_blocco
               OR NEW.hash_blocco_precedente IS DISTINCT FROM OLD.hash_blocco_precedente
               OR NEW.merkle_root IS DISTINCT FROM OLD.merkle_root
               OR NEW.aip_ids IS DISTINCT FROM OLD.aip_ids
               OR NEW.num_aip IS DISTINCT FROM OLD.num_aip
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.versione_formato IS DISTINCT FROM OLD.versione_formato THEN
                RAISE EXCEPTION 'hashchain_blocks è append-only: contenuto del blocco % non modificabile',
                    OLD.numero_blocco;
            END IF;
            IF OLD.stato_firma <> 'DA_FIRMARE' THEN
                RAISE EXCEPTION 'hashchain_blocks: firma del blocco % già registrata (%)',
                    OLD.numero_blocco, OLD.stato_firma;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_hashchain_blocks_append_only
        BEFORE UPDATE OR DELETE ON public.hashchain_blocks
        FOR EACH ROW EXECUTE FUNCTION public.hashchain_blocks_append_only();
    """)
    op.execute("""
        CREATE TRIGGER trg_hashchain_blocks_no_truncate
        BEFORE TRUNCATE ON public.hashchain_blocks
        FOR EACH STATEMENT EXECUTE FUNCTION public.hashchain_blocks_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_hashchain_blocks_no_truncate ON public.hashchain_blocks")
    op.execute("DROP TRIGGER IF EXISTS trg_hashchain_blocks_append_only ON public.hashchain_blocks")
    op.execute("DROP FUNCTION IF EXISTS public.hashchain_blocks_append_only()")
    op.drop_constraint("ck_hashchain_versione_formato", "hashchain_blocks", schema="public")
    op.drop_constraint("ck_hashchain_stato_firma", "hashchain_blocks", schema="public")
    op.drop_column("hashchain_blocks", "motivo_firma", schema="public")
    op.drop_column("hashchain_blocks", "stato_firma", schema="public")
    op.drop_column("hashchain_blocks", "versione_formato", schema="public")
    op.drop_index("uq_versamento_impronta_attiva", table_name="versamento_impronte", schema="public")
    op.drop_index("uq_versamento_idempotenza", table_name="versamento_impronte", schema="public")
    op.drop_table("versamento_impronte", schema="public")
    op.drop_index("idx_outbox_in_sospeso", table_name="outbox_eventi", schema="public")
    op.drop_table("outbox_eventi", schema="public")
