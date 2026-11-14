# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Sessioni di caricamento (contratto C1) e richieste dell'adattatore stile Packer

Revision ID: 20261021_015
Revises: 20261021_014
Create Date: 2026-10-21

Contenuto (agente R03 b1, piano R03 §3.1 e §3.4):
  - public.caricamenti: sessione di caricamento di un file grande (S3 multipart
    nello staging ``caricamenti/{tenant}/{id}/dati``): stato, dimensione e parti
    attese, impronte/testa/formato calcolati dal server alla verifica, lease del
    verificatore, PdV che l'ha usata (una sessione si usa una volta sola);
    Idempotency-Key unica per ente;
  - public.caricamenti_parti: parti ricevute (numero, dimensione, SHA-256,
    ETag dello storage): la ripetizione della stessa parte è idempotente, una
    parte diversa con lo stesso numero è un conflitto;
  - public.compat_packer_richieste: richieste ``inviaRichiesta`` dell'adattatore
    stile Packer (id numerico per compatibilità, documenti → PdV).

Solo tabelle nuove: nessun dato esistente né contenuto conservato toccato
(§10.6). Downgrade: elimina le tre tabelle (gli oggetti nello staging restano:
li rimuove la pulizia dello staging o il ciclo di vita del bucket).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_015"
down_revision = "20261021_014"
branch_labels = None
depends_on = None

STATI = ("APERTO", "IN_VERIFICA", "COMPLETO", "SCADUTO", "ANNULLATO", "ERRORE")


def _adesso(nome: str) -> sa.Column:
    return sa.Column(nome, sa.DateTime(timezone=True), nullable=False,
                     server_default=sa.text("now()"))


def _fk(nome: str, destinazione: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(nome, postgresql.UUID(as_uuid=True),
                     sa.ForeignKey(destinazione, ondelete="RESTRICT"), nullable=nullable)


def upgrade() -> None:
    # ── caricamenti ────────────────────────────────────────────────────
    op.create_table(
        "caricamenti",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        _fk("tenant_id", "public.tenants.id"),
        sa.Column("utente_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canale", sa.String(20), nullable=False),
        sa.Column("chiave_idempotenza", sa.String(200), nullable=True),
        sa.Column("impronta_richiesta", sa.String(64), nullable=False),
        sa.Column("nome_file", sa.String(255), nullable=False),
        sa.Column("dimensione", sa.BigInteger(), nullable=False),
        sa.Column("mime_dichiarato", sa.String(255), nullable=True),
        sa.Column("impronta_dichiarata", postgresql.JSONB(), nullable=True),
        sa.Column("dimensione_parte", sa.BigInteger(), nullable=False),
        sa.Column("numero_parti", sa.Integer(), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False, server_default=sa.text("'APERTO'")),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("chiave_oggetto", sa.String(1024), nullable=False),
        sa.Column("upload_id", sa.String(1024), nullable=True),
        sa.Column("impronte", postgresql.JSONB(), nullable=True),
        sa.Column("testa", sa.LargeBinary(), nullable=True),
        sa.Column("formato_rilevato", sa.String(255), nullable=True),
        sa.Column("errore", postgresql.JSONB(), nullable=True),
        sa.Column("scade_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detentore", sa.String(200), nullable=True),
        sa.Column("lease_scade_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativi_verifica", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _fk("pdv_id", "public.pdv.id", nullable=True),
        sa.Column("consumato_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completato_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_eliminato_il", sa.DateTime(timezone=True), nullable=True),
        _adesso("creato_il"),
        _adesso("aggiornato_il"),
        sa.CheckConstraint("stato IN (" + ", ".join(f"'{s}'" for s in STATI) + ")",
                           name="ck_caricamenti_stato"),
        sa.CheckConstraint("dimensione > 0", name="ck_caricamenti_dimensione"),
        sa.CheckConstraint("dimensione_parte > 0 AND numero_parti >= 1",
                           name="ck_caricamenti_parti"),
        schema="public",
    )
    op.create_index("idx_caricamenti_tenant", "caricamenti", ["tenant_id", "creato_il"],
                    schema="public")
    op.create_index("uq_caricamenti_idempotenza", "caricamenti",
                    ["tenant_id", "chiave_idempotenza"], unique=True, schema="public",
                    postgresql_where=sa.text("chiave_idempotenza IS NOT NULL"))
    op.create_index("idx_caricamenti_da_verificare", "caricamenti", ["lease_scade_il"],
                    schema="public", postgresql_where=sa.text("stato = 'IN_VERIFICA'"))
    op.create_index("idx_caricamenti_da_pulire", "caricamenti", ["scade_il"], schema="public",
                    postgresql_where=sa.text("pdv_id IS NULL AND storage_eliminato_il IS NULL"))
    op.create_index("idx_caricamenti_pdv", "caricamenti", ["pdv_id"], schema="public",
                    postgresql_where=sa.text("pdv_id IS NOT NULL"))

    # ── caricamenti_parti ──────────────────────────────────────────────
    op.create_table(
        "caricamenti_parti",
        sa.Column("caricamento_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.caricamenti.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("numero", sa.Integer(), primary_key=True),
        _fk("tenant_id", "public.tenants.id"),
        sa.Column("dimensione", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("etag", sa.String(200), nullable=True),
        _adesso("ricevuta_il"),
        sa.CheckConstraint("numero >= 1", name="ck_caricamenti_parti_numero"),
        schema="public",
    )

    # ── compat_packer_richieste ────────────────────────────────────────
    op.create_table(
        "compat_packer_richieste",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        _fk("tenant_id", "public.tenants.id"),
        sa.Column("chiave", sa.String(200), nullable=False),
        sa.Column("codice_servizio", sa.String(100), nullable=False),
        sa.Column("tipologia_documento", sa.String(100), nullable=False),
        sa.Column("url_callback", sa.String(2048), nullable=True),
        sa.Column("documenti", postgresql.JSONB(), nullable=False),
        sa.Column("utente_id", postgresql.UUID(as_uuid=True), nullable=False),
        _adesso("creata_il"),
        _adesso("aggiornata_il"),
        sa.UniqueConstraint("tenant_id", "chiave", name="uq_compat_packer_richieste_chiave"),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("compat_packer_richieste", schema="public")
    op.drop_table("caricamenti_parti", schema="public")
    op.drop_index("idx_caricamenti_pdv", table_name="caricamenti", schema="public")
    op.drop_index("idx_caricamenti_da_pulire", table_name="caricamenti", schema="public")
    op.drop_index("idx_caricamenti_da_verificare", table_name="caricamenti", schema="public")
    op.drop_index("uq_caricamenti_idempotenza", table_name="caricamenti", schema="public")
    op.drop_index("idx_caricamenti_tenant", table_name="caricamenti", schema="public")
    op.drop_table("caricamenti", schema="public")
