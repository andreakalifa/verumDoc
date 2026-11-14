# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Verifica continua dell'integrità: programmazione, registro delle verifiche, richieste (R04)

Revision ID: 20261023_027
Revises: 20261021_024
Create Date: 2026-10-23

Piano R03–R05 §5 punto 4 («ogni oggetto dell'archivio viene riletto da storage e
confrontato con la sua foglia almeno una volta l'anno, più verifiche su richiesta;
gli esiti stanno in un registro delle verifiche consultabile»). Lavoratore:
``services/hashchain/app/verifica_continua`` (``python -m app.verifica_continua``).

Tabelle nuove (nessuna modifica a tabelle esistenti):

1. ``verifiche_richieste`` — verifiche su richiesta (elenco di PdA o intero ente).
   Indice unico parziale: al più una richiesta d'ente attiva per ente.
2. ``verifiche_programmazione`` — UNA riga per PdA: ``prossima_verifica`` ordina
   la rotazione, ``lease_id``/``lease_detentore``/``lease_scade_il`` il lavoro
   preso da una replica (la presa in carico sposta ``prossima_verifica`` alla
   scadenza del lease: una replica ferma restituisce il lavoro da sola),
   ``richiesta_id`` la precedenza delle verifiche su richiesta, ``anomalia_dal``
   le anomalie aperte. Righe ~ numero di PdA (non di file).
3. ``verifiche_registro`` — append-only (trigger: UPDATE, DELETE e TRUNCATE
   vietati), una riga per oggetto riletto. PARTIZIONATA PER MESE su
   ``verificato_il``: a regime (archivio verificato una volta l'anno più la
   crescita) sono milioni di righe al giorno. Convenzioni di
   ``libs/shared/db/partizioni.py``: partizioni nello schema ``partizioni``
   (il confronto modelli/schema riflette solo ``public``), nome
   ``verifiche_registro_p<AAAAMM>``, limiti in UTC, NESSUNA partizione
   predefinita (una riga fuori dalle partizioni fa fallire l'INSERT in modo
   esplicito: la verifica si ripete dopo il lease). La migrazione crea il mese
   corrente e i tre successivi; poi li crea in anticipo il lavoratore
   (``VERIFICA_PARTIZIONI_MESI_AVANTI``) con le funzioni di quel modulo.
   Nessun distacco: il registro è evidenza dei controlli (REGOLE §15.8).
   Chiave primaria ``(verificato_il, id)`` perché la chiave di partizione deve
   farne parte. ``aip_id``/``documento_id`` senza chiave esterna: l'evidenza
   resta anche dopo uno scarto autorizzato del PdA.
4. ``verifiche_copertura`` — per ente e giorno dell'ultima verifica conclusa,
   numero di PdA: la copertura del ciclo si calcola senza ``COUNT(*)``.
5. ``verifiche_stato`` — posizioni del lavoratore (semina, riconciliazione).

Indici: quelli di ``verifiche_programmazione`` (la tabella che cresce con i PdA e
riceve aggiornamenti continui) si creano ``CONCURRENTLY`` fuori transazione, come
nelle migrazioni 020–024. Sulle tabelle PARTIZIONATE PostgreSQL non ammette
``CREATE INDEX CONCURRENTLY``: gli indici di ``verifiche_registro`` si creano sul
padre insieme alla tabella, vuota (nessun blocco su dati esistenti); ogni
partizione nuova li eredita.

Downgrade: elimina tutto ciò che crea (le tabelle sono nuove).
"""
from __future__ import annotations

from alembic import op

revision = "20261023_027"
down_revision = "20261021_024"
branch_labels = None
depends_on = None

_ESITI = "'OK', 'DIVERSO', 'MANCANTE', 'RETENTION_ASSENTE', 'ERRORE_LETTURA'"
_COMPONENTI = "'PDA', 'IPDA', 'FILE', 'RDV'"


def upgrade() -> None:
    # ── 1. richieste ──────────────────────────────────────────────────────
    op.execute(
        "CREATE TABLE public.verifiche_richieste ("
        " id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " tenant_id uuid REFERENCES public.tenants(id) ON DELETE RESTRICT,"
        " ambito varchar(10) NOT NULL,"
        " stato varchar(20) NOT NULL DEFAULT 'IN_PREPARAZIONE',"
        " pda_richiesti jsonb,"
        " richiesta_da varchar(100),"
        " ruolo varchar(50) NOT NULL,"
        " oggetti_totali integer NOT NULL DEFAULT 0,"
        " oggetti_verificati integer NOT NULL DEFAULT 0,"
        " gia_in_verifica integer NOT NULL DEFAULT 0,"
        " anomalie integer NOT NULL DEFAULT 0,"
        " preparazione_conclusa boolean NOT NULL DEFAULT false,"
        " cursore_preparazione uuid,"
        " creata_il timestamp with time zone NOT NULL DEFAULT now(),"
        " conclusa_il timestamp with time zone,"
        " CONSTRAINT ck_verifiche_richieste_ambito CHECK (ambito IN ('PDA', 'ENTE')),"
        " CONSTRAINT ck_verifiche_richieste_stato"
        "   CHECK (stato IN ('IN_PREPARAZIONE', 'IN_CORSO', 'COMPLETATA')),"
        " CONSTRAINT ck_verifiche_richieste_ente CHECK (ambito = 'PDA' OR tenant_id IS NOT NULL))"
    )
    op.execute("CREATE INDEX idx_verifiche_richieste_tenant "
               "ON public.verifiche_richieste (tenant_id, creata_il DESC)")
    op.execute("CREATE INDEX idx_verifiche_richieste_da_preparare "
               "ON public.verifiche_richieste (creata_il) WHERE stato = 'IN_PREPARAZIONE'")
    op.execute("CREATE UNIQUE INDEX uq_verifiche_richieste_ente_attiva "
               "ON public.verifiche_richieste (tenant_id) "
               "WHERE ambito = 'ENTE' AND stato <> 'COMPLETATA'")

    # ── 2. programmazione ─────────────────────────────────────────────────
    op.execute(
        "CREATE TABLE public.verifiche_programmazione ("
        " aip_id uuid PRIMARY KEY REFERENCES public.aip(id) ON DELETE RESTRICT,"
        " tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE RESTRICT,"
        " prossima_verifica timestamp with time zone NOT NULL,"
        " ultima_verifica timestamp with time zone,"
        " ultimo_esito varchar(20),"
        " anomalia_dal timestamp with time zone,"
        " errori_lettura_consecutivi smallint NOT NULL DEFAULT 0,"
        " da_ritentare jsonb,"
        " richiesta_id uuid REFERENCES public.verifiche_richieste(id) ON DELETE RESTRICT,"
        " lease_id uuid,"
        " lease_detentore varchar(200),"
        " lease_scade_il timestamp with time zone,"
        " verifiche_eseguite integer NOT NULL DEFAULT 0,"
        " creata_il timestamp with time zone NOT NULL DEFAULT now(),"
        f" CONSTRAINT ck_verifiche_programmazione_esito"
        f"   CHECK (ultimo_esito IS NULL OR ultimo_esito IN ({_ESITI})))"
    )

    # ── 3. registro (partizionato per mese, append-only) ──────────────────
    op.execute(
        "CREATE TABLE public.verifiche_registro ("
        " verificato_il timestamp with time zone NOT NULL,"
        " id bigint GENERATED ALWAYS AS IDENTITY,"
        " tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE RESTRICT,"
        " aip_id uuid NOT NULL,"
        " documento_id uuid,"
        " componente varchar(10) NOT NULL,"
        " chiave varchar(500),"
        " esito varchar(20) NOT NULL,"
        " algoritmo varchar(20) NOT NULL DEFAULT 'SHA-256',"
        " impronta_attesa varchar(128),"
        " impronta_trovata varchar(128),"
        " dimensione_attesa bigint,"
        " dimensione_trovata bigint,"
        " retention_modalita varchar(20),"
        " retention_fino_a timestamp with time zone,"
        " foglia_attesa varchar(64),"
        " foglia_trovata varchar(64),"
        " tentativo smallint NOT NULL DEFAULT 1,"
        " motivo varchar(500),"
        " richiesta_id uuid,"
        " esecutore varchar(200) NOT NULL,"
        " PRIMARY KEY (verificato_il, id),"
        f" CONSTRAINT ck_verifiche_registro_esito CHECK (esito IN ({_ESITI})),"
        f" CONSTRAINT ck_verifiche_registro_componente CHECK (componente IN ({_COMPONENTI}))"
        ") PARTITION BY RANGE (verificato_il)"
    )
    op.execute("CREATE INDEX idx_verifiche_registro_tenant "
               "ON public.verifiche_registro (tenant_id, verificato_il DESC, id DESC)")
    op.execute("CREATE INDEX idx_verifiche_registro_aip "
               "ON public.verifiche_registro (aip_id, verificato_il DESC)")
    op.execute("CREATE INDEX idx_verifiche_registro_anomalie "
               "ON public.verifiche_registro (verificato_il DESC) WHERE esito <> 'OK'")
    # Partizioni nello schema «partizioni» (convenzione di libs/shared/db/partizioni.py):
    # il confronto modelli/schema (alembic check) riflette solo «public».
    op.execute("CREATE SCHEMA IF NOT EXISTS partizioni")
    # Mese corrente e tre successivi (poi li crea il lavoratore in anticipo)
    op.execute("""
        DO $$
        DECLARE
            inizio date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
            mese date;
        BEGIN
            FOR i IN 0..3 LOOP
                mese := (inizio + make_interval(months => i))::date;
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS partizioni.%I PARTITION OF public.verifiche_registro '
                    'FOR VALUES FROM (%L) TO (%L)',
                    'verifiche_registro_p' || to_char(mese, 'YYYYMM'),
                    (mese::timestamp AT TIME ZONE 'UTC'),
                    ((mese + interval '1 month')::timestamp AT TIME ZONE 'UTC'));
            END LOOP;
        END $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.verifiche_registro_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'verifiche_registro è append-only: % vietata', TG_OP;
        END $$;
    """)
    op.execute("CREATE TRIGGER trg_verifiche_registro_append_only "
               "BEFORE UPDATE OR DELETE ON public.verifiche_registro "
               "FOR EACH ROW EXECUTE FUNCTION public.verifiche_registro_append_only()")
    op.execute("CREATE TRIGGER trg_verifiche_registro_no_truncate "
               "BEFORE TRUNCATE ON public.verifiche_registro "
               "FOR EACH STATEMENT EXECUTE FUNCTION public.verifiche_registro_append_only()")

    # ── 4. copertura e 5. stato del lavoratore ────────────────────────────
    op.execute(
        "CREATE TABLE public.verifiche_copertura ("
        " tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE RESTRICT,"
        " giorno date NOT NULL,"
        " oggetti bigint NOT NULL DEFAULT 0,"
        " PRIMARY KEY (tenant_id, giorno),"
        " CONSTRAINT ck_verifiche_copertura_oggetti CHECK (oggetti >= 0))"
    )
    op.execute(
        "CREATE TABLE public.verifiche_stato ("
        " nome varchar(50) PRIMARY KEY,"
        " valore jsonb NOT NULL,"
        " aggiornato_il timestamp with time zone NOT NULL DEFAULT now())"
    )

    # Indici della programmazione: CONCURRENTLY, fuori transazione
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                   "idx_verifiche_programmazione_prossima "
                   "ON public.verifiche_programmazione (prossima_verifica)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                   "idx_verifiche_programmazione_richieste "
                   "ON public.verifiche_programmazione (prossima_verifica) "
                   "WHERE richiesta_id IS NOT NULL")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                   "idx_verifiche_programmazione_tenant "
                   "ON public.verifiche_programmazione (tenant_id, aip_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                   "idx_verifiche_programmazione_anomalie "
                   "ON public.verifiche_programmazione (tenant_id, anomalia_dal) "
                   "WHERE anomalia_dal IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.verifiche_stato")
    op.execute("DROP TABLE IF EXISTS public.verifiche_copertura")
    op.execute("DROP TABLE IF EXISTS public.verifiche_registro CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.verifiche_registro_append_only()")
    # Lo schema delle partizioni si elimina solo se vuoto (può servire ad altre tabelle)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                           WHERE n.nspname = 'partizioni') THEN
                DROP SCHEMA IF EXISTS partizioni;
            END IF;
        END $$;
    """)
    op.execute("DROP TABLE IF EXISTS public.verifiche_programmazione")
    op.execute("DROP TABLE IF EXISTS public.verifiche_richieste")
