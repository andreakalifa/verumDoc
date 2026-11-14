# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Accesso, ricerca e pacchetti di distribuzione: indice chiavi API, coda d'indicizzazione, PdD

Revision ID: 20261021_018
Revises: 20261021_014
Create Date: 2026-10-21

Contenuto (agente R03 b4):
  - public.api_keys: indice parziale ``idx_apikeys_prefix`` (key_prefix WHERE
    attiva): la chiave API ora si verifica in ogni servizio pubblico e a ogni
    richiesta (VD-C-099);
  - public.indicizzazione_aip: coda transazionale dell'indicizzazione di
    ricerca (VD-C-093), scritta da aip-builder insieme al PdA e consumata a
    lotti dal lavoratore di distribution. Riempimento iniziale: ogni PdA già
    costruito (SU_STORAGE, IN_CATENA, CONSERVATO, ESIBIZIONE) entra in coda,
    così l'archivio esistente diventa ricercabile;
  - public.pacchetti_distribuzione: PdD con file inclusi, impronte e consegna
    (LINK o ZIP generato fuori dalla richiesta, VD-C-077).

Nessun contenuto conservato viene toccato (§10.6): si legge ``aip`` per il
riempimento della coda. Downgrade: elimina tabelle e indice (le richieste di
PdD registrate qui si perdono; il registro in ``notifications`` resta).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_018"
down_revision = "20261021_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_apikeys_prefix", "api_keys", ["key_prefix"], schema="public",
        postgresql_where=sa.text("attiva"),
    )

    # ── Coda d'indicizzazione ──────────────────────────────────────────
    op.create_table(
        "indicizzazione_aip",
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False, server_default=sa.text("'IN_CODA'")),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("prossimo_tentativo", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("ultimo_errore", sa.Text()),
        sa.Column("richiesta_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("indicizzato_il", sa.DateTime(timezone=True)),
        sa.CheckConstraint("stato IN ('IN_CODA', 'INDICIZZATO', 'ERRORE')",
                           name="ck_indicizzazione_aip_stato"),
        schema="public",
    )
    op.create_index("idx_indicizzazione_aip_coda", "indicizzazione_aip", ["prossimo_tentativo"],
                    schema="public", postgresql_where=sa.text("stato = 'IN_CODA'"))
    op.create_index("idx_indicizzazione_aip_tenant", "indicizzazione_aip", ["tenant_id"],
                    schema="public")
    # L'archivio esistente entra in coda (idempotente: chiave primaria = aip_id)
    op.execute(
        "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id) "
        "SELECT id, tenant_id FROM public.aip "
        "WHERE stato IN ('SU_STORAGE', 'IN_CATENA', 'CONSERVATO', 'ESIBIZIONE') "
        "ON CONFLICT (aip_id) DO NOTHING"
    )

    # ── Pacchetti di distribuzione ─────────────────────────────────────
    op.create_table(
        "pacchetti_distribuzione",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("consegna", sa.String(10), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False,
                  server_default=sa.text("'IN_PREPARAZIONE'")),
        sa.Column("richiedente_id", postgresql.UUID(as_uuid=True)),
        sa.Column("canale", sa.String(20)),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("file_richiesti", postgresql.JSONB()),
        sa.Column("opzioni", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("documenti", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("hash_pdd", sa.String(64)),
        sa.Column("zip_bucket", sa.String(100)),
        sa.Column("zip_key", sa.String(500)),
        sa.Column("zip_dimensione", sa.BigInteger()),
        sa.Column("zip_sha256", sa.String(64)),
        sa.Column("durata_link_secondi", sa.Integer(), nullable=False),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("prossimo_tentativo", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("lease_fino_al", sa.DateTime(timezone=True)),
        sa.Column("ultimo_errore", sa.Text()),
        sa.Column("errore", postgresql.JSONB()),
        sa.Column("creato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("pronto_il", sa.DateTime(timezone=True)),
        sa.Column("scade_il", sa.DateTime(timezone=True)),
        sa.CheckConstraint("tipo IN ('COMPLETO', 'ESTRATTO', 'ESIBIZIONE', 'MIGRAZIONE')",
                           name="ck_pacchetti_distribuzione_tipo"),
        sa.CheckConstraint("consegna IN ('LINK', 'ZIP')",
                           name="ck_pacchetti_distribuzione_consegna"),
        sa.CheckConstraint("stato IN ('IN_PREPARAZIONE', 'PRONTO', 'ERRORE', 'SCADUTO')",
                           name="ck_pacchetti_distribuzione_stato"),
        schema="public",
    )
    op.create_index("idx_pacchetti_distribuzione_coda", "pacchetti_distribuzione",
                    ["prossimo_tentativo"], schema="public",
                    postgresql_where=sa.text("stato = 'IN_PREPARAZIONE'"))
    op.create_index("idx_pacchetti_distribuzione_scadenza", "pacchetti_distribuzione",
                    ["scade_il"], schema="public", postgresql_where=sa.text("stato = 'PRONTO'"))
    op.create_index("idx_pacchetti_distribuzione_tenant", "pacchetti_distribuzione",
                    ["tenant_id", "creato_il"], schema="public")


def downgrade() -> None:
    for indice in ("idx_pacchetti_distribuzione_tenant", "idx_pacchetti_distribuzione_scadenza",
                   "idx_pacchetti_distribuzione_coda"):
        op.drop_index(indice, table_name="pacchetti_distribuzione", schema="public")
    op.drop_table("pacchetti_distribuzione", schema="public")
    op.drop_index("idx_indicizzazione_aip_tenant", table_name="indicizzazione_aip", schema="public")
    op.drop_index("idx_indicizzazione_aip_coda", table_name="indicizzazione_aip", schema="public")
    op.drop_table("indicizzazione_aip", schema="public")
    op.drop_index("idx_apikeys_prefix", table_name="api_keys", schema="public")
