# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Versamento unico: motore delle regole v1, chiave UD univoca, fotografia delle regole

Revision ID: 20261021_014
Revises: 20261020_013
Create Date: 2026-10-21

Contenuto (agente R03 a2):
  - public.pdv: ``regole_applicate`` (jsonb, fotografia delle versioni applicate:
    catalogo regole, profilo di metadati, impostazioni effettive con origine —
    REGOLE §8.3) e ``avvisi`` (jsonb, rilievi AVVISA salvati sul PdV — §8.6);
  - public.versamento_impronte: ``chiave_ud_registro``/``_anno``/``_numero`` e
    indice UNICO parziale ``uq_versamento_chiave_ud_attiva`` per cliente fra i
    PdV non rifiutati (VD-C-095). Le righe esistenti si riempiono dalla chiave
    salvata in ``pdv.metadata_json``; se ci sono doppioni fra i PdV attivi la
    migrazione si ferma con l'elenco (§16.5): nessuna riga cancellata;
  - public.impostazioni_valori: valori delle impostazioni per livello
    (CONSERVATORE, ENTE, CLASSE) del motore delle regole (VD-P-01, §8.2);
  - public.profili_metadati: profili di metadati per classe, versionati con
    validità temporale (§8.4, §8.8).

Nessun contenuto conservato viene toccato (§10.6). Downgrade: elimina tabelle,
indice e colonne nuove (i dati di regole e profili si perdono: sono tabelle
nuove di questa revisione).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_014"
down_revision = "20261020_013"
branch_labels = None
depends_on = None

INDICE_CHIAVE_UD = "uq_versamento_chiave_ud_attiva"


def upgrade() -> None:
    # ── pdv: fotografia delle regole e avvisi ───────────────────────────
    op.add_column(
        "pdv", sa.Column("regole_applicate", postgresql.JSONB(), nullable=True), schema="public"
    )
    op.add_column(
        "pdv",
        sa.Column(
            "avvisi", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        schema="public",
    )

    # ── versamento_impronte: chiave UD ─────────────────────────────────
    op.add_column(
        "versamento_impronte", sa.Column("chiave_ud_registro", sa.String(100)), schema="public"
    )
    op.add_column(
        "versamento_impronte", sa.Column("chiave_ud_anno", sa.String(10)), schema="public"
    )
    op.add_column(
        "versamento_impronte", sa.Column("chiave_ud_numero", sa.String(100)), schema="public"
    )
    # Riempimento dalla chiave salvata nel PdV, con la stessa normalizzazione del
    # gateway (registro: maiuscolo e senza spazi ai lati, '' se assente).
    op.execute(
        "UPDATE public.versamento_impronte v SET "
        " chiave_ud_registro = "
        "   upper(btrim(coalesce(p.metadata_json #>> '{chiave,registro}', ''))), "
        " chiave_ud_anno = btrim(p.metadata_json #>> '{chiave,anno}'), "
        " chiave_ud_numero = btrim(p.metadata_json #>> '{chiave,numero}') "
        "FROM public.pdv p WHERE p.id = v.pdv_id "
        "AND btrim(coalesce(p.metadata_json #>> '{chiave,numero}', '')) <> '' "
        "AND btrim(coalesce(p.metadata_json #>> '{chiave,anno}', '')) <> '' "
        "AND length(btrim(p.metadata_json #>> '{chiave,anno}')) <= 10 "
        "AND length(btrim(p.metadata_json #>> '{chiave,numero}')) <= 100 "
        "AND length(btrim(coalesce(p.metadata_json #>> '{chiave,registro}', ''))) <= 100"
    )
    # Vincolo nuovo su dati esistenti (§16.5): fermata con l'elenco dei doppioni
    op.execute(
        "DO $$ DECLARE elenco text; n integer; BEGIN "
        "SELECT count(*), string_agg(gruppo, '; ') INTO n, elenco FROM ("
        "  SELECT format('tenant=%s registro=%s anno=%s numero=%s pdv=%s', tenant_id, "
        "         chiave_ud_registro, chiave_ud_anno, chiave_ud_numero, "
        "         string_agg(pdv_id::text, ',')) AS gruppo "
        "  FROM public.versamento_impronte WHERE attivo AND chiave_ud_numero IS NOT NULL "
        "  GROUP BY tenant_id, chiave_ud_registro, chiave_ud_anno, chiave_ud_numero "
        "  HAVING count(*) > 1 LIMIT 20) d; "
        "IF n > 0 THEN RAISE EXCEPTION "
        "'Migrazione 20261021_014 interrotta: chiavi UD doppie fra PdV attivi (% gruppi): %', "
        "n, elenco; END IF; END $$;"
    )
    op.create_index(
        INDICE_CHIAVE_UD,
        "versamento_impronte",
        ["tenant_id", "chiave_ud_registro", "chiave_ud_anno", "chiave_ud_numero"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("attivo AND chiave_ud_numero IS NOT NULL"),
    )

    # ── impostazioni_valori ────────────────────────────────────────────
    op.create_table(
        "impostazioni_valori",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("livello", sa.String(20), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "classe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("codice", sa.String(100), nullable=False),
        sa.Column("valore", postgresql.JSONB(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("creato_da", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "livello",
            "tenant_id",
            "classe_id",
            "codice",
            name="uq_impostazioni_valori_livello",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "(livello = 'CONSERVATORE' AND tenant_id IS NULL AND classe_id IS NULL) OR "
            "(livello = 'ENTE' AND tenant_id IS NOT NULL AND classe_id IS NULL) OR "
            "(livello = 'CLASSE' AND classe_id IS NOT NULL)",
            name="ck_impostazioni_valori_livello",
        ),
        schema="public",
    )
    op.create_index(
        "idx_impostazioni_valori_tenant", "impostazioni_valori", ["tenant_id"], schema="public"
    )
    op.create_index(
        "idx_impostazioni_valori_classe", "impostazioni_valori", ["classe_id"], schema="public"
    )

    # ── profili_metadati ───────────────────────────────────────────────
    op.create_table(
        "profili_metadati",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "classe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("versione", sa.Integer(), nullable=False),
        sa.Column("valido_dal", sa.Date(), nullable=False),
        sa.Column("valido_al", sa.Date(), nullable=True),
        sa.Column("campi", postgresql.JSONB(), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
        sa.Column("creato_da", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("classe_id", "versione", name="uq_profili_metadati_versione"),
        sa.CheckConstraint("versione >= 1", name="ck_profili_metadati_versione"),
        sa.CheckConstraint(
            "valido_al IS NULL OR valido_al >= valido_dal", name="ck_profili_metadati_validita"
        ),
        schema="public",
    )
    op.create_index(
        "idx_profili_metadati_classe",
        "profili_metadati",
        ["classe_id", "valido_dal"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("idx_profili_metadati_classe", table_name="profili_metadati", schema="public")
    op.drop_table("profili_metadati", schema="public")
    op.drop_index(
        "idx_impostazioni_valori_classe", table_name="impostazioni_valori", schema="public"
    )
    op.drop_index(
        "idx_impostazioni_valori_tenant", table_name="impostazioni_valori", schema="public"
    )
    op.drop_table("impostazioni_valori", schema="public")
    op.drop_index(INDICE_CHIAVE_UD, table_name="versamento_impronte", schema="public")
    for colonna in ("chiave_ud_numero", "chiave_ud_anno", "chiave_ud_registro"):
        op.drop_column("versamento_impronte", colonna, schema="public")
    op.drop_column("pdv", "avvisi", schema="public")
    op.drop_column("pdv", "regole_applicate", schema="public")
