# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
20260320_009_classi_tenant_id.py
Sprint 3A FIX-018: aggiunge tenant_id a classi_documentali per isolamento multi-tenant.

PRIMA: classi_documentali globali — un tenant vedeva/usava classi create da altri.
DOPO:  tenant_id nullable (backward compat), indice composito (tenant_id, codice).

Steps:
  1. ADD COLUMN tenant_id UUID (nullable: NULL = classe di base del servizio)
  2. ADD INDEX idx_classi_tenant_id (tenant_id)
  3. DROP vecchio UNIQUE globale su codice (classi_documentali_codice_key)
  4. ADD UNIQUE NULLS NOT DISTINCT uq_classi_tenant_codice (tenant_id, codice):
     un codice è unico fra le classi di base (tenant_id NULL) e unico dentro
     ciascun cliente; un cliente può avere una sua classe con lo stesso codice
     di una classe di base (la sua prevale).
  5. FK verso tenants con ON DELETE CASCADE

Correzione R03 (VD-C-007, VD-C-023): la versione precedente dichiarava il
passo 3 ma non lo eseguiva, e creava un indice unico parziale che lasciava
le classi di base senza vincolo per tenant. Nessun database aveva mai
raggiunto questa revisione (la 007 falliva prima), quindi la correzione è
sicura. Richiede PostgreSQL >= 15 (NULLS NOT DISTINCT); docker-compose usa 16.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


# revision identifiers
revision = "20260320_009"
down_revision = "20260319_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Aggiunge colonna tenant_id (nullable per classi globali preesistenti)
    op.add_column(
        "classi_documentali",
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=True),
        schema="public",
    )

    # 2. Indice su tenant_id
    op.create_index(
        "idx_classi_tenant_id",
        "classi_documentali",
        ["tenant_id"],
        schema="public",
    )

    # 3. Il vecchio UNIQUE globale su codice impediva le classi per cliente
    op.drop_constraint(
        "classi_documentali_codice_key", "classi_documentali",
        type_="unique", schema="public",
    )

    # 4. Unicità (tenant_id, codice) con i NULL considerati uguali:
    #    copre sia le classi di base sia quelle di ciascun cliente
    op.execute(
        "ALTER TABLE public.classi_documentali "
        "ADD CONSTRAINT uq_classi_tenant_codice "
        "UNIQUE NULLS NOT DISTINCT (tenant_id, codice)"
    )

    # 5. FK verso tenants (opzionale — tenant_id può essere NULL per classi globali)
    op.create_foreign_key(
        "fk_classi_tenant",
        "classi_documentali",
        "tenants",
        ["tenant_id"],
        ["id"],
        source_schema="public",
        referent_schema="public",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_classi_tenant", "classi_documentali", schema="public",
    )
    op.drop_constraint(
        "uq_classi_tenant_codice", "classi_documentali",
        type_="unique", schema="public",
    )
    op.create_unique_constraint(
        "classi_documentali_codice_key", "classi_documentali", ["codice"],
        schema="public",
    )
    op.drop_index(
        "idx_classi_tenant_id",
        table_name="classi_documentali",
        schema="public",
    )
    op.drop_column(
        "classi_documentali", "tenant_id", schema="public",
    )
