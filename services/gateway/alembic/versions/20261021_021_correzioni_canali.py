# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Correzioni dei canali dopo la revisione R03 (agente f1)

Revision ID: 20261021_021
Revises: 20261021_020
Create Date: 2026-10-21

  - ``public.pdv.staging_eliminato_il`` (REV-08): istante in cui la pulizia dello
    staging ha eliminato le copie di transito dei file del PdV (oggetti
    ``versamenti/…`` e ``caricamenti/…`` del bucket di staging), dopo aver
    verificato la copia nel PdA. NULL = copie ancora presenti (o PdV non ancora
    nel PdA). Il PdV e i dati conservati non si toccano (§10.6).
  - indice parziale ``idx_pdv_staging_da_pulire`` (data_aggiornamento, id) sui
    soli PdV nel PdA o conservati con staging non ancora pulito: il job lavora a
    lotti senza scandire la tabella (le righe escono dall'indice una volta pulite).
  - ``public.compat_packer_richieste.impronta_richiesta`` (REV-12): impronta della
    richiesta ``inviaRichiesta``; la stessa chiave con una richiesta diversa è un
    conflitto (409) invece di una sovrascrittura. NULL per le righe precedenti.

Colonne nuove e nullable, nessun dato riscritto. Indice creato con
CONCURRENTLY (fuori transazione) per non bloccare le scritture su ``pdv``.
Downgrade: elimina indice e colonne.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261021_021"
down_revision = "20261021_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pdv", sa.Column("staging_eliminato_il", sa.DateTime(timezone=True),
                                   nullable=True), schema="public")
    op.add_column("compat_packer_richieste",
                  sa.Column("impronta_richiesta", sa.String(64), nullable=True), schema="public")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_staging_da_pulire "
            "ON public.pdv (data_aggiornamento, id) "
            "WHERE staging_eliminato_il IS NULL AND stato IN ('IN_BUILD', 'CONSERVATO')"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_staging_da_pulire")
    op.drop_column("compat_packer_richieste", "impronta_richiesta", schema="public")
    op.drop_column("pdv", "staging_eliminato_il", schema="public")
