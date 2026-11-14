# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Parametri in console: modifiche programmate, preferenze, avvisi automatici (R07)

Revision ID: 20261030_046
Revises: 20261030_045
Create Date: 2026-10-30

1. D28. ``configurazione_bozze``: una bozza di impostazioni si può APPLICARE
   con decorrenza futura (stato ``PROGRAMMATA``, colonna ``valido_dal``). Il
   lavoro periodico di onboarding (``app.services.programmate``) ricalcola il
   piano e l'impatto il giorno prima (``anteprima_programmata``,
   ``anteprima_il``), avvisa l'autore (``avviso_autore_il``) e alla decorrenza
   applica la bozza con lo stesso codice dell'applicazione immediata. Indice
   parziale sulle sole bozze programmate.
2. D31/D29. ``preferenze_utente``: preferenze personali salvate sul server
   (tema, densità, pagina iniziale, colonne visibili, ente scelto in testata);
   una riga per utente, versione per il salvataggio con la versione letta.
   Senza ``tenant_id``: si legge e scrive solo per l'utente del token.
3. D13/D17. ``avvisi_automatici``: un avviso automatico all'ente si registra una
   volta per (tipo, ente, chiave) — es. quota al 80% del giorno, chiave di
   idempotenza mancante nella settimana — insieme all'evento nell'outbox.
   Row Level Security per ente come le altre tabelle con ``tenant_id``.
4. D17. Indice parziale su ``versamento_impronte(created_at)`` per i versamenti
   SENZA Idempotency-Key (il controllo settimanale legge solo quelli; finita la
   transizione l'indice resta vuoto). Creato ``CONCURRENTLY``.

Riprendibile: tabelle create solo se assenti (``to_regclass``), colonne e indici
con ``IF NOT EXISTS``, vincoli tolti e rimessi ``NOT VALID`` e convalidati in
autocommit, ``lock_timeout`` per i DDL esclusivi (come la 045).

Downgrade: rifiutato se ci sono bozze PROGRAMMATE (andrebbero perse in silenzio).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261030_046"
down_revision = "20261030_045"
branch_labels = None
depends_on = None

STATI_045 = ("APERTA", "APPLICATA", "ANNULLATA")
STATI = ("APERTA", "PROGRAMMATA", "APPLICATA", "ANNULLATA")
POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
#: Dimensione massima delle preferenze di un utente (testo JSON).
PREFERENZE_MAX_BYTE = 16384


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _gia_applicato(oggetto: str) -> bool:
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def _vincolo(tabella: str, nome: str, condizione: str | None) -> None:
    op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT IF EXISTS {nome}")
    if condizione is not None:
        op.execute(f"ALTER TABLE public.{tabella} ADD CONSTRAINT {nome} "
                   f"CHECK ({condizione}) NOT VALID")


def _in_autocommit(*istruzioni: str) -> None:
    if context.is_offline_mode():
        # script SQL offline: tutto in transazione, quindi indice senza CONCURRENTLY
        for i in istruzioni:
            op.execute(i.replace("CREATE INDEX CONCURRENTLY", "CREATE INDEX"))
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def upgrade() -> None:
    _limita_attesa_lock()
    # 1. D28: bozze programmate
    for colonna, tipo in (("valido_dal", "date"), ("programmata_il", "timestamptz"),
                          ("programmata_da", "uuid"), ("anteprima_programmata", "jsonb"),
                          ("anteprima_il", "timestamptz"), ("avviso_autore_il", "timestamptz")):
        op.execute(f"ALTER TABLE public.configurazione_bozze ADD COLUMN IF NOT EXISTS "
                   f"{colonna} {tipo}")
    _vincolo("configurazione_bozze", "ck_configurazione_bozze_stato",
             f"stato IN ({_elenco(STATI)})")
    _vincolo("configurazione_bozze", "ck_configurazione_bozze_programmata",
             "stato <> 'PROGRAMMATA' OR valido_dal IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_configurazione_bozze_programmate "
               "ON public.configurazione_bozze (valido_dal, id) "
               "WHERE stato = 'PROGRAMMATA'")
    # 2. D31/D29: preferenze personali
    if not _gia_applicato("public.preferenze_utente"):
        op.create_table(
            "preferenze_utente",
            sa.Column("user_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("public.users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("preferenze", postgresql.JSONB, nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("versione", sa.Integer, nullable=False, server_default=sa.text("1")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.CheckConstraint("jsonb_typeof(preferenze) = 'object' AND "
                               f"octet_length(preferenze::text) <= {PREFERENZE_MAX_BYTE}",
                               name="ck_preferenze_utente_oggetto"),
            sa.CheckConstraint("versione >= 1", name="ck_preferenze_utente_versione"),
            schema="public",
        )
    # 3. D13/D17: avvisi automatici agli enti
    if not _gia_applicato("public.avvisi_automatici"):
        op.create_table(
            "avvisi_automatici",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("tipo", sa.String(60), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("chiave", sa.String(100), nullable=False),
            sa.Column("dati", postgresql.JSONB, nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.UniqueConstraint("tipo", "tenant_id", "chiave", name="uq_avvisi_automatici"),
            schema="public",
        )
        op.create_index("idx_avvisi_automatici_ente", "avvisi_automatici",
                        ["tenant_id", sa.text("created_at DESC")], schema="public")
    op.execute("ALTER TABLE public.avvisi_automatici ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.avvisi_automatici")
    op.execute(f"CREATE POLICY {POLITICA} ON public.avvisi_automatici AS PERMISSIVE FOR ALL "
               f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    # convalide e indice concorrente fuori dalla transazione
    _in_autocommit(
        "ALTER TABLE public.configurazione_bozze VALIDATE CONSTRAINT "
        "ck_configurazione_bozze_stato",
        "ALTER TABLE public.configurazione_bozze VALIDATE CONSTRAINT "
        "ck_configurazione_bozze_programmata",
        # un indice INVALID lasciato da una corsa interrotta si ricrea
        "DO $i$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = "
        "i.indexrelid WHERE c.relname = 'idx_versamento_impronte_senza_chiave' AND NOT "
        "i.indisvalid) THEN DROP INDEX public.idx_versamento_impronte_senza_chiave; "
        "END IF; END $i$",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_versamento_impronte_senza_chiave "
        "ON public.versamento_impronte (created_at) WHERE chiave_idempotenza IS NULL",
    )


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.configurazione_bozze WHERE stato = 'PROGRAMMATA') THEN
                RAISE EXCEPTION 'downgrade 20261030_046 impossibile: ci sono modifiche di '
                    'configurazione programmate (annullarle o attenderne l''applicazione)';
            END IF;
        END
        $d$;
    """)
    op.execute("DROP INDEX IF EXISTS public.idx_versamento_impronte_senza_chiave")
    op.execute("DROP TABLE IF EXISTS public.avvisi_automatici")
    op.execute("DROP TABLE IF EXISTS public.preferenze_utente")
    op.execute("DROP INDEX IF EXISTS public.idx_configurazione_bozze_programmate")
    _vincolo("configurazione_bozze", "ck_configurazione_bozze_programmata", None)
    _vincolo("configurazione_bozze", "ck_configurazione_bozze_stato",
             f"stato IN ({_elenco(STATI_045)})")
    for colonna in ("avviso_autore_il", "anteprima_il", "anteprima_programmata",
                    "programmata_da", "programmata_il", "valido_dal"):
        op.execute(f"ALTER TABLE public.configurazione_bozze DROP COLUMN IF EXISTS {colonna}")
    _in_autocommit("ALTER TABLE public.configurazione_bozze VALIDATE CONSTRAINT "
                   "ck_configurazione_bozze_stato")
