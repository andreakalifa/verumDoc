# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Pulizia dello staging: rinvio per riga e PdV bloccati (R03 f3, REV-N1)

Revision ID: 20261021_024
Revises: 20261021_023
Create Date: 2026-10-21

La pulizia dello staging (``app.services.pulizia_staging``) ripartiva a ogni giro
dal PdV più vecchio: se i PdV non verificabili più vecchi superavano il lavoro di
un giro, i PdV più recenti non venivano MAI raggiunti (REV-N1). Ora ogni PdV ha
il proprio rinvio con attesa crescente e un massimo di tentativi:

  - ``public.pdv.staging_verifica_prossima`` (NOT NULL, predefinito 1/1/1970 =
    «mai guardato»): il PdV non è candidato prima di questo istante. Il job prende
    i candidati in ordine di ``(staging_verifica_prossima, id)`` fra quelli già
    scaduti: un PdV rinviato va «in fondo» e non blocca gli altri;
  - ``public.pdv.staging_verifica_tentativi`` (NOT NULL, predefinito 0): verifiche
    non riuscite consecutive;
  - ``public.pdv.staging_verifica_motivo``: motivo dell'ultima verifica non
    riuscita (consultabile; nessun dato personale, solo chiavi e codici);
  - ``public.pdv.staging_verifica_bloccata_il``: istante in cui il PdV è stato
    escluso dalla pulizia (motivo permanente o tentativi esauriti). Esce
    dall'indice dei candidati; un operatore lo può rimettere in coda
    (``python -m app.services.pulizia_staging --sblocca <pdv_id>``).

Indici (CONCURRENTLY, fuori transazione, per non bloccare le scritture su ``pdv``):
  - ``idx_pdv_staging_da_verificare`` (staging_verifica_prossima, id) sui soli PdV
    nel PdA o conservati, con staging da pulire e non bloccati: la lettura di un
    lotto è una scansione d'intervallo fino ad «adesso» (costo limitato dal lotto,
    i PdV rinviati nel futuro non si leggono);
  - ``idx_pdv_staging_bloccati`` (staging_verifica_bloccata_il) sui soli bloccati
    (metrica ed elenco per la console);
  - ``idx_pdv_staging_da_pulire`` (migrazione 021) si elimina: non serve più.

Colonne aggiunte con predefiniti costanti: in PostgreSQL ≥ 11 nessuna
riscrittura della tabella.
Downgrade: ricrea l'indice della 021, elimina indici e colonne nuovi.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261021_024"
down_revision = "20261021_023"
branch_labels = None
depends_on = None

#: «Mai guardato»: ogni PdV entra fra i candidati appena è nel PdA.
PROSSIMA_INIZIALE = "'1970-01-01 00:00:00+00'"
_PDV_NEL_PDA = "stato IN ('IN_BUILD', 'CONSERVATO')"


def upgrade() -> None:
    op.add_column("pdv", sa.Column("staging_verifica_prossima", sa.DateTime(timezone=True),
                                   nullable=False, server_default=sa.text(PROSSIMA_INIZIALE)),
                  schema="public")
    op.add_column("pdv", sa.Column("staging_verifica_tentativi", sa.SmallInteger(),
                                   nullable=False, server_default=sa.text("0")),
                  schema="public")
    op.add_column("pdv", sa.Column("staging_verifica_motivo", sa.String(500), nullable=True),
                  schema="public")
    op.add_column("pdv", sa.Column("staging_verifica_bloccata_il", sa.DateTime(timezone=True),
                                   nullable=True), schema="public")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_staging_da_verificare "
            "ON public.pdv (staging_verifica_prossima, id) "
            "WHERE staging_eliminato_il IS NULL AND staging_verifica_bloccata_il IS NULL "
            f"AND {_PDV_NEL_PDA}"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_staging_bloccati "
            "ON public.pdv (staging_verifica_bloccata_il) "
            "WHERE staging_eliminato_il IS NULL AND staging_verifica_bloccata_il IS NOT NULL"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_staging_da_pulire")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_staging_da_pulire "
            "ON public.pdv (data_aggiornamento, id) "
            f"WHERE staging_eliminato_il IS NULL AND {_PDV_NEL_PDA}"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_staging_bloccati")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_staging_da_verificare")
    for colonna in ("staging_verifica_bloccata_il", "staging_verifica_motivo",
                    "staging_verifica_tentativi", "staging_verifica_prossima"):
        op.drop_column("pdv", colonna, schema="public")
