# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Letture a cursore dei PdA, posizione di pubblicazione degli eventi, segnalazioni SFTP (R03 f2)

Revision ID: 20261021_022
Revises: 20261021_020
Create Date: 2026-10-21

Correzioni della revisione indipendente R03 (agente f2).

1. **REV-06 — elenco e ricerca dei PdA a cursore** (dashboard-api): la
   paginazione per chiave ``(data_versamento, id)`` decrescente per ente, e
   facoltativamente per stato, ha bisogno di indici che la servano (oggi c'è
   solo ``(tenant_id, stato)``: l'ordinamento leggerebbe tutti i PdA dell'ente):
     - ``idx_aip_tenant_versamento`` (tenant_id, data_versamento DESC, id DESC);
     - ``idx_aip_tenant_stato_versamento`` (tenant_id, stato, data_versamento DESC, id DESC).
   La ricerca testuale va su OpenSearch (distribution): nessun indice trigram.

2. **REV-13 — elenco eventi senza salti**: ``eventi_cliente.posizione``
   (bigint, NULL finché l'evento non è pubblicato) assegnata DOPO il commit
   dell'evento da un passo di pubblicazione seriale: la riga contatore
   ``eventi_cliente_pubblicazione`` (una sola, id = 1) è bloccata dalla
   transazione che assegna, quindi le posizioni diventano visibili in ordine
   crescente e un cursore sulla posizione non salta mai un evento.
     - eventi già presenti: ``posizione = id`` (stesso ordine: i cursori già
       emessi restano validi); contatore = massimo id;
     - ``uq_eventi_cliente_posizione`` (posizione) unico, parziale;
     - ``idx_eventi_cliente_tenant_posizione`` (tenant_id, posizione) e
       ``idx_eventi_cliente_tenant_tipo_posizione`` (tenant_id, tipo, posizione)
       per l'elenco per ente (e per tipo);
     - ``idx_eventi_cliente_da_pubblicare`` (id) WHERE posizione IS NULL, per
       il passo di pubblicazione.
   Gli indici ``idx_eventi_cliente_cursore`` e ``…_tipo_cursore`` (sull'id)
   non servono più all'elenco e si eliminano.

3. **REV-15 — lavoratore SFTP proporzionale alle cartelle pronte**:
   ``sftp_segnalazioni``, una riga per account con attività da guardare (scritta
   dal server SFTP quando un cliente scrive o rinomina in ``in/``, e dal
   controllo periodico di sicurezza), presa in lease da UNA replica del
   lavoratore (``FOR UPDATE SKIP LOCKED``), eliminata quando l'account non ha
   più cartelle da lavorare. ``versione`` cresce a ogni nuova segnalazione:
   chi ha guardato l'account la elimina solo se nessuno l'ha segnalato di nuovo
   nel frattempo.

Indici su tabelle esistenti con CONCURRENTLY (fuori transazione:
``autocommit_block``) per non bloccare le scritture in esercizio, come nella
20261021_020. Downgrade: elimina ciò che crea e ripristina gli indici sull'id.
"""
from __future__ import annotations

from alembic import op

revision = "20261021_022"
down_revision = "20261021_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 2. eventi: posizione di pubblicazione ─────────────────────────────
    op.execute("ALTER TABLE public.eventi_cliente ADD COLUMN IF NOT EXISTS posizione bigint")
    op.execute(
        "CREATE TABLE IF NOT EXISTS public.eventi_cliente_pubblicazione ("
        " id smallint PRIMARY KEY CONSTRAINT ck_eventi_cliente_pubblicazione_unica CHECK (id = 1),"
        " ultima_posizione bigint NOT NULL,"
        " aggiornato_il timestamp with time zone NOT NULL DEFAULT now())"
    )
    # Eventi già registrati: posizione = id (tutti già committati: nessun salto).
    op.execute("UPDATE public.eventi_cliente SET posizione = id WHERE posizione IS NULL")
    op.execute(
        "INSERT INTO public.eventi_cliente_pubblicazione (id, ultima_posizione) "
        "SELECT 1, COALESCE(max(id), 0) FROM public.eventi_cliente "
        "ON CONFLICT (id) DO NOTHING"
    )

    # ── 3. SFTP: account con attività da guardare ─────────────────────────
    op.execute(
        "CREATE TABLE IF NOT EXISTS public.sftp_segnalazioni ("
        " account_id uuid PRIMARY KEY"
        "   CONSTRAINT fk_sftp_segnalazioni_account REFERENCES public.sftp_account(id)"
        "   ON DELETE CASCADE,"
        " tenant_id uuid NOT NULL"
        "   CONSTRAINT fk_sftp_segnalazioni_tenant REFERENCES public.tenants(id)"
        "   ON DELETE RESTRICT,"
        " versione bigint NOT NULL DEFAULT 1,"
        " da_guardare_dal timestamp with time zone NOT NULL DEFAULT now(),"
        " controllo_completo boolean NOT NULL DEFAULT false,"
        " detentore varchar(200),"
        " lease_scade_il timestamp with time zone,"
        " segnalata_il timestamp with time zone NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_sftp_segnalazioni_da_guardare "
               "ON public.sftp_segnalazioni (da_guardare_dal)")

    with op.get_context().autocommit_block():
        # ── 1. PdA a cursore ──────────────────────────────────────────────
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aip_tenant_versamento "
                   "ON public.aip (tenant_id, data_versamento DESC, id DESC)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aip_tenant_stato_versamento "
                   "ON public.aip (tenant_id, stato, data_versamento DESC, id DESC)")
        # ── 2. eventi ─────────────────────────────────────────────────────
        op.execute("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_eventi_cliente_posizione "
                   "ON public.eventi_cliente (posizione) WHERE posizione IS NOT NULL")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eventi_cliente_tenant_posizione "
                   "ON public.eventi_cliente (tenant_id, posizione) "
                   "WHERE posizione IS NOT NULL")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                   "idx_eventi_cliente_tenant_tipo_posizione "
                   "ON public.eventi_cliente (tenant_id, tipo, posizione) "
                   "WHERE posizione IS NOT NULL")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eventi_cliente_da_pubblicare "
                   "ON public.eventi_cliente (id) WHERE posizione IS NULL")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_eventi_cliente_tipo_cursore")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_eventi_cliente_cursore")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eventi_cliente_cursore "
                   "ON public.eventi_cliente (tenant_id, id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eventi_cliente_tipo_cursore "
                   "ON public.eventi_cliente (tenant_id, tipo, id)")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_eventi_cliente_da_pubblicare")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS "
                   "public.idx_eventi_cliente_tenant_tipo_posizione")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_eventi_cliente_tenant_posizione")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_eventi_cliente_posizione")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_aip_tenant_stato_versamento")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_aip_tenant_versamento")
    op.execute("DROP TABLE IF EXISTS public.sftp_segnalazioni")
    op.execute("DROP TABLE IF EXISTS public.eventi_cliente_pubblicazione")
    op.execute("ALTER TABLE public.eventi_cliente DROP COLUMN IF EXISTS posizione")
