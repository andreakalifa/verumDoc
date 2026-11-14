# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Rimozione degli adattatori di compatibilità (R18.2, VD-C-187, VD-P-191)

Revision ID: 20261114_060
Revises: 20261112_059
Create Date: 2026-11-14

Gli adattatori di compatibilità del gateway sono tolti dal codice (R18.2: la provenienza
delle specifiche usate non è confermabile). Restava nello schema la sola tabella delle loro
richieste (``public.compat_packer_richieste``, migrazioni 015, 021 e 038), senza più modello
né codice che la legga o la scriva: ``alembic check`` (schema identico ai modelli) la
segnalerebbe e i nomi delle sue colonne riprendono l'interfaccia tolta. Questa migrazione la
elimina (con la sua politica di Row Level Security).

Nessun altro oggetto cambia: i valori di canale dei PdV già versati da quei canali
(``metadata_json``) restano come dati storici, nessun vincolo li elenca.

Protezione dei dati: se la tabella contiene righe l'upgrade si FERMA, a meno che
``VD_MIGRAZIONE_060_ELIMINA_RICHIESTE=1`` confermi l'eliminazione (i PdV, i documenti e i
rapporti di versamento collegati restano: la tabella teneva solo la corrispondenza fra
le richieste dell'adattatore e i PdV). Riprendibile (``IF EXISTS``), ``lock_timeout``.
Downgrade: ricrea la tabella VUOTA con colonne, vincoli e politica per ente di prima (le
righe eliminate non tornano).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261114_060"
down_revision = "20261112_059"
branch_labels = None
depends_on = None

TABELLA = "compat_packer_richieste"
POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
CONFERMA = "VD_MIGRAZIONE_060_ELIMINA_RICHIESTE"


def _attesa_lock() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def upgrade() -> None:
    # Un solo blocco SQL: vale anche in modalità offline (``alembic upgrade head --sql``),
    # dove la conferma è quella dell'ambiente in cui si genera lo script.
    confermato = "true" if os.environ.get(CONFERMA, "").strip() == "1" else "false"
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")
    op.execute(f"""
DO $d$
DECLARE
    ha_righe boolean := false;
BEGIN
    IF to_regclass('public.{TABELLA}') IS NOT NULL AND NOT {confermato} THEN
        -- dinamica: un riferimento statico alla tabella fallirebbe se non c'è
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM public.{TABELLA})' INTO ha_righe;
    END IF;
    IF ha_righe THEN
        RAISE EXCEPTION 'public.{TABELLA} contiene righe (richieste dei vecchi adattatori di '
            'compatibilità): esportarle se servono e rilanciare con {CONFERMA}=1 per '
            'eliminarle (PdV, documenti e rapporti di versamento restano)';
    END IF;
END
$d$
""")
    op.execute(f"DROP TABLE IF EXISTS public.{TABELLA}")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")
    op.create_table(
        TABELLA,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chiave", sa.String(200), nullable=False),
        sa.Column("codice_servizio", sa.String(100), nullable=False),
        sa.Column("tipologia_documento", sa.String(100), nullable=False),
        sa.Column("url_callback", sa.String(2048), nullable=True),
        sa.Column("documenti", postgresql.JSONB(), nullable=False),
        sa.Column("utente_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("aggiornata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("impronta_richiesta", sa.String(64), nullable=True),
        sa.UniqueConstraint("tenant_id", "chiave", name=f"uq_{TABELLA}_chiave"),
        schema="public",
    )
    # come la migrazione 038 (RLS per ente)
    op.execute(f"ALTER TABLE public.{TABELLA} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {POLITICA} ON public.{TABELLA} AS PERMISSIVE FOR ALL "
               f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
