# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Indice della sonda dei gruppi dell'outbox (R04 correzioni, rev4 rilievo 2)

Revision ID: 20261023_032
Revises: 20261023_031
Create Date: 2026-10-23

``idx_outbox_gruppi_in_sospeso`` (origine, gruppo, sequenza) WHERE pubblicato_il
IS NULL: il relay sceglie i gruppi con almeno un evento pubblicabile con una
sonda per gruppo (``libs/shared/outbox.py``), senza leggere la «finestra» degli
eventi più vecchi che faceva fermare tutto il relay dietro un solo gruppo.

``outbox_eventi`` è partizionata: CREATE INDEX CONCURRENTLY non è ammesso sulla
madre. Si usa la procedura senza blocchi delle scritture:
  1. indice sulla SOLA madre (``ON ONLY``: nasce non valido, nessuna scansione);
  2. per ogni partizione esistente ``CREATE INDEX CONCURRENTLY`` (fuori
     transazione) e ``ALTER INDEX ... ATTACH PARTITION``;
  3. agganciate tutte le partizioni, l'indice della madre diventa valido; le
     partizioni future lo ricevono alla creazione.
Idempotente (``IF NOT EXISTS``, aggancio solo se manca): una migrazione
interrotta si riprende. Come script offline (``--sql``, finestra di
manutenzione) l'indice si crea in un passo sulla madre.
"""
from __future__ import annotations

from alembic import context, op
from sqlalchemy import text

revision = "20261023_032"
down_revision = "20261023_031"
branch_labels = None
depends_on = None

INDICE = "idx_outbox_gruppi_in_sospeso"
COLONNE = "(origine, gruppo, sequenza) WHERE pubblicato_il IS NULL"


def upgrade() -> None:
    if context.is_offline_mode():
        # Script offline (finestra di manutenzione, scritture ferme): indice
        # normale sulla madre, propagato alle partizioni in un solo passo
        op.execute(f"CREATE INDEX IF NOT EXISTS {INDICE} ON public.outbox_eventi {COLONNE}")
        return
    op.execute(f"CREATE INDEX IF NOT EXISTS {INDICE} ON ONLY public.outbox_eventi {COLONNE}")
    partizioni = [r[0] for r in op.get_bind().execute(text(
        "SELECT c.oid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'public.outbox_eventi'::regclass ORDER BY 1")).all()]
    with op.get_context().autocommit_block():
        for qualificato in partizioni:
            nome = qualificato.split(".")[-1] + "_gruppi_idx"
            schema = qualificato.split(".")[0] if "." in qualificato else "public"
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {nome} ON {qualificato} "
                       f"{COLONNE}")
            agganciato = op.get_bind().execute(text(
                "SELECT 1 FROM pg_inherits WHERE inhparent = CAST(:madre AS regclass) "
                "AND inhrelid = CAST(:figlio AS regclass)"),
                {"madre": f"public.{INDICE}", "figlio": f"{schema}.{nome}"}).first()
            if agganciato is None:
                op.execute(f"ALTER INDEX public.{INDICE} ATTACH PARTITION {schema}.{nome}")


def downgrade() -> None:
    # Eliminare l'indice della madre elimina anche quelli agganciati delle partizioni
    op.execute(f"DROP INDEX IF EXISTS public.{INDICE}")
