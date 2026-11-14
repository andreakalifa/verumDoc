# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Eventi per i clienti e webhook firmati (contratto C3)

Revision ID: 20261021_017
Revises: 20261021_014
Create Date: 2026-10-21

Contenuto (agente R03 b3, VD-C-078 parte webhook):
  - public.eventi_cliente: elenco degli eventi di ogni ente. ``id`` bigint
    crescente assegnato dal database = cursore dell'API ``GET /api/v1/eventi``.
    Un evento per (ente, tipo, risorsa): l'indice unico rende idempotente il
    consumer Kafka (consegna almeno una volta, REGOLE §12.2);
  - public.webhook_sottoscrizioni: sottoscrizioni per ente (URL https, tipi di
    evento, segreto CIFRATO a riposo, attiva). L'eliminazione è logica
    (``eliminata_il``): le consegne restano come storico;
  - public.webhook_consegne: una consegna per (sottoscrizione, evento) con
    stato, tentativi, prossimo tentativo, lease del lavoratore ed esito
    dell'ultimo tentativo. Le consegne FALLITE sono la coda di scarto (DLQ)
    consultabile e ripetibile dall'API (REGOLE §12.4).

Tabelle nuove: nessun dato esistente toccato. Downgrade: elimina le tre
tabelle (eventi, sottoscrizioni e storico delle consegne si perdono).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_017"
down_revision = "20261021_014"
branch_labels = None
depends_on = None

STATI_CONSEGNA = ("IN_CODA", "IN_CONSEGNA", "CONSEGNATA", "FALLITA", "ANNULLATA")


def upgrade() -> None:
    op.create_table(
        "eventi_cliente",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT",
                                name="fk_eventi_cliente_tenant"),
                  nullable=False),
        sa.Column("tipo", sa.String(60), nullable=False),
        sa.Column("risorsa_tipo", sa.String(40), nullable=False),
        sa.Column("risorsa_id", sa.String(100), nullable=False),
        sa.Column("dati", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("origine", sa.String(200), nullable=True),
        sa.Column("creato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "tipo", "risorsa_id",
                            name="uq_eventi_cliente_risorsa"),
        schema="public",
    )
    op.create_index("idx_eventi_cliente_cursore", "eventi_cliente", ["tenant_id", "id"],
                    schema="public")
    op.create_index("idx_eventi_cliente_tipo_cursore", "eventi_cliente",
                    ["tenant_id", "tipo", "id"], schema="public")

    op.create_table(
        "webhook_sottoscrizioni",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT",
                                name="fk_webhook_sottoscrizioni_tenant"),
                  nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("tipi", postgresql.JSONB(), nullable=False),
        sa.Column("descrizione", sa.String(200), nullable=True),
        sa.Column("segreto_cifrato", sa.Text(), nullable=False),
        sa.Column("segreto_suffisso", sa.String(8), nullable=False),
        sa.Column("attiva", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("creata_da", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("creata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("aggiornata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("eliminata_il", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("jsonb_typeof(tipi) = 'array'",
                           name="ck_webhook_sottoscrizioni_tipi"),
        schema="public",
    )
    op.create_index("idx_webhook_sottoscrizioni_tenant", "webhook_sottoscrizioni",
                    ["tenant_id"], schema="public",
                    postgresql_where=sa.text("eliminata_il IS NULL"))

    op.create_table(
        "webhook_consegne",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT",
                                name="fk_webhook_consegne_tenant"),
                  nullable=False),
        sa.Column("sottoscrizione_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.webhook_sottoscrizioni.id", ondelete="RESTRICT",
                                name="fk_webhook_consegne_sottoscrizione"),
                  nullable=False),
        sa.Column("evento_id", sa.BigInteger(),
                  sa.ForeignKey("public.eventi_cliente.id", ondelete="RESTRICT",
                                name="fk_webhook_consegne_evento"),
                  nullable=False),
        sa.Column("stato", sa.String(20), nullable=False),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("riprove", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("prossimo_tentativo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ultimo_stato_http", sa.SmallInteger(), nullable=True),
        sa.Column("ultimo_codice", sa.String(20), nullable=True),
        sa.Column("ultimo_errore", sa.Text(), nullable=True),
        sa.Column("ultimo_tentativo_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consegnata_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("aggiornata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("sottoscrizione_id", "evento_id",
                            name="uq_webhook_consegne_evento"),
        sa.CheckConstraint(
            "stato IN (" + ", ".join(f"'{s}'" for s in STATI_CONSEGNA) + ")",
            name="ck_webhook_consegne_stato",
        ),
        schema="public",
    )
    # Prelievo del lavoratore: solo le righe da fare, in ordine di scadenza
    op.create_index("idx_webhook_consegne_da_fare", "webhook_consegne",
                    ["prossimo_tentativo"], schema="public",
                    postgresql_where=sa.text("stato IN ('IN_CODA', 'IN_CONSEGNA')"))
    op.create_index("idx_webhook_consegne_sottoscrizione", "webhook_consegne",
                    ["sottoscrizione_id", "id"], schema="public")
    op.create_index("idx_webhook_consegne_sottoscrizione_stato", "webhook_consegne",
                    ["sottoscrizione_id", "stato", "id"], schema="public")


def downgrade() -> None:
    op.drop_table("webhook_consegne", schema="public")
    op.drop_table("webhook_sottoscrizioni", schema="public")
    op.drop_table("eventi_cliente", schema="public")
