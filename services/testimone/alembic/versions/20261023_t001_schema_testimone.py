# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Schema del testimone: stato, checkpoint controfirmati, allarmi, interventi.

Revision ID: t001
Revises:
Create Date: 2026-10-23

R04 — contratto K3 (testimone indipendente). Database PROPRIO del testimone,
schema ``testimone``; nessuna tabella del gateway. Le tre tabelle di prova
(checkpoint_controfirmati, allarmi, interventi_operatore) sono append-only:
un trigger rifiuta UPDATE, DELETE e TRUNCATE anche a chi ha i privilegi di
scrittura (la difesa vera resta la separazione degli account e la catena
delle impronte propria, ``impronta_precedente``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t001"
down_revision = None
branch_labels = None
depends_on = None

S = "testimone"
TABELLE_APPEND_ONLY = ("checkpoint_controfirmati", "allarmi", "interventi_operatore")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "stato_registro",
        sa.Column("origine", sa.Text(), primary_key=True),
        sa.Column("epoca", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("dimensione", sa.BigInteger(), nullable=True),
        sa.Column("radice", sa.LargeBinary(), nullable=True),
        sa.Column("impronta_ultima", sa.LargeBinary(), nullable=False),
        sa.Column("stato", sa.Text(), nullable=False, server_default=sa.text("'ATTIVO'")),
        sa.Column("compromesso_dal", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column(
            "aggiornato_il",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("stato IN ('ATTIVO', 'COMPROMESSO')", name="ck_stato_registro_stato"),
        sa.CheckConstraint(
            "stato = 'ATTIVO' OR compromesso_dal IS NOT NULL",
            name="ck_stato_registro_compromesso_dal",
        ),
        sa.CheckConstraint(
            "(dimensione IS NULL) = (radice IS NULL)", name="ck_stato_registro_testa"
        ),
        sa.CheckConstraint(
            "radice IS NULL OR length(radice) = 32", name="ck_stato_registro_radice"
        ),
        schema=S,
    )
    op.create_table(
        "checkpoint_controfirmati",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "origine",
            sa.Text(),
            sa.ForeignKey(f"{S}.stato_registro.origine", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("epoca", sa.Integer(), nullable=False),
        sa.Column("dimensione", sa.BigInteger(), nullable=False),
        sa.Column("radice", sa.LargeBinary(), nullable=False),
        sa.Column("nota_registro", sa.Text(), nullable=False),
        sa.Column("firma_testimone", sa.Text(), nullable=False),
        sa.Column("nota_controfirmata", sa.Text(), nullable=False),
        sa.Column("impronta", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("impronta_precedente", sa.LargeBinary(), nullable=False),
        sa.Column(
            "controfirmato_il",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "origine", "epoca", "dimensione", name="uq_checkpoint_origine_epoca_dimensione"
        ),
        sa.UniqueConstraint("origine", "impronta_precedente", name="uq_checkpoint_catena"),
        sa.CheckConstraint("dimensione >= 0", name="ck_checkpoint_dimensione"),
        sa.CheckConstraint("length(radice) = 32", name="ck_checkpoint_radice"),
        sa.CheckConstraint(
            "length(impronta) = 32 AND length(impronta_precedente) = 32",
            name="ck_checkpoint_impronte",
        ),
        schema=S,
    )
    op.create_table(
        "allarmi",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "origine",
            sa.Text(),
            sa.ForeignKey(f"{S}.stato_registro.origine", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("epoca", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("blocca", sa.Boolean(), nullable=False),
        sa.Column("dimensione_presentata", sa.BigInteger(), nullable=False),
        sa.Column("radice_presentata", sa.LargeBinary(), nullable=False),
        sa.Column("dimensione_conosciuta", sa.BigInteger(), nullable=True),
        sa.Column("radice_conosciuta", sa.LargeBinary(), nullable=True),
        sa.Column("old", sa.BigInteger(), nullable=False),
        sa.Column("nota_presentata", sa.Text(), nullable=False),
        sa.Column("prova", sa.Text(), nullable=False),
        sa.Column(
            "rilevato_il", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("tipo IN ('FORK', 'PROVA_NON_VALIDA')", name="ck_allarmi_tipo"),
        schema=S,
    )
    op.create_table(
        "interventi_operatore",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "origine",
            sa.Text(),
            sa.ForeignKey(f"{S}.stato_registro.origine", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("azione", sa.Text(), nullable=False),
        sa.Column("operatore", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("epoca_precedente", sa.Integer(), nullable=False),
        sa.Column("epoca_nuova", sa.Integer(), nullable=False),
        sa.Column("stato_precedente", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{S}.checkpoint_controfirmati.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "eseguito_il", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("azione IN ('SBLOCCO', 'NUOVA_BASE')", name="ck_interventi_azione"),
        sa.CheckConstraint("length(btrim(operatore)) > 0", name="ck_interventi_operatore"),
        sa.CheckConstraint("length(btrim(motivo)) >= 10", name="ck_interventi_motivo"),
        schema=S,
    )
    # Append-only: un trigger per riga (UPDATE/DELETE) e uno per istruzione
    # (TRUNCATE) rifiutano ogni modifica delle righe di prova.
    op.execute(f"""
        CREATE FUNCTION {S}.vieta_modifiche() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'tabella append-only del testimone: % su %.% non ammesso',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
    """)
    for t in TABELLE_APPEND_ONLY:
        op.execute(f"""
            CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {S}.{t}
            FOR EACH ROW EXECUTE FUNCTION {S}.vieta_modifiche()
        """)
        op.execute(f"""
            CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {S}.{t}
            FOR EACH STATEMENT EXECUTE FUNCTION {S}.vieta_modifiche()
        """)
    # La riga di stato di un'origine non si cancella (si blocca o si sblocca).
    op.execute(f"""
        CREATE TRIGGER stato_registro_no_delete BEFORE DELETE ON {S}.stato_registro
        FOR EACH ROW EXECUTE FUNCTION {S}.vieta_modifiche()
    """)
    op.execute(f"""
        CREATE TRIGGER stato_registro_no_truncate BEFORE TRUNCATE ON {S}.stato_registro
        FOR EACH STATEMENT EXECUTE FUNCTION {S}.vieta_modifiche()
    """)


def downgrade() -> None:
    # Solo per sviluppo e prove: in esercizio i dati del testimone sono prove
    # e non si eliminano (il downgrade richiede di togliere i trigger).
    op.drop_table("interventi_operatore", schema=S)
    op.drop_table("allarmi", schema=S)
    op.drop_table("checkpoint_controfirmati", schema=S)
    op.execute(f"DROP TRIGGER IF EXISTS stato_registro_no_delete ON {S}.stato_registro")
    op.execute(f"DROP TRIGGER IF EXISTS stato_registro_no_truncate ON {S}.stato_registro")
    op.drop_table("stato_registro", schema=S)
    op.execute(f"DROP FUNCTION IF EXISTS {S}.vieta_modifiche()")
