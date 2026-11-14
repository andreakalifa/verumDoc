# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Terzo giro delle correzioni R04 (terza verifica del revisore)

Revision ID: 20261023_034
Revises: 20261023_033
Create Date: 2026-10-23

1. ``catena_blocchi_registro`` (T4): blocchi PERSISTENTI del registro delle
   impronte. Finché uno è aperto il registro non sequenzia, non emette
   checkpoint, non esporta nel WORM e non contatta il testimone; gli allarmi
   continuano. Cause: ``REGISTRO_INDIETRO`` (il database è dietro il WORM o il
   testimone, per esempio dopo un ripristino da backup), ``REGISTRO_DIVERGENTE``
   (foglie o radice diverse da quelle già pubblicate nel WORM),
   ``WORM_CONTENUTO_DIVERSO`` (scrittura WORM con lo stesso nome e contenuto
   diverso: niente ritentativi a ogni giro). Al più un blocco aperto per causa.
2. Schema ``protezione`` (T1): impronta della struttura dei registri
   (``audit_global``, ``verifiche_registro``: colonne con tipo e NOT NULL,
   vincoli, indici unici) in ``protezione.impronte_registri``; l'event trigger
   di stato rifiuta ogni ALTER TABLE, CREATE/ALTER INDEX che la cambi, e ogni
   vincolo, indice unico o NOT NULL propri di una partizione dei registri. Le
   migrazioni che cambiano i registri usano
   ``libs.shared.db.migrazioni_registri.registri_modificabili`` (sospende e
   riattiva gli event trigger, aggiorna le impronte).
3. T2: l'event trigger «oggetti» rifiuta ``CREATE TABLE ... PARTITION OF`` un
   registro (le partizioni dei registri si creano solo con
   ``vd_crea_partizione_registro``, che usa LIKE + difese + ATTACH).
Funzioni ed event trigger si sostituiscono dentro ``registri_modificabili``
(questa migrazione è il primo esempio d'uso). Ricreare gli event trigger con i
tag nuovi richiede un superutente (su RDS ``rds_superuser``): senza, WARNING.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_034"
down_revision = "20261023_033"
branch_labels = None
depends_on = None


def registri_modificabili(*argomenti, **opzioni):  # noqa: ANN002, ANN003, ANN201
    """``libs.shared.db.migrazioni_registri.registri_modificabili``, importata solo
    all'esecuzione (VD-C-117, R05): ``alembic heads/history`` caricano il file senza
    ``PYTHONPATH``; quando la migrazione gira, ``alembic/env.py`` ha già messo la
    radice del monorepo nel percorso."""
    from libs.shared.db.migrazioni_registri import registri_modificabili as originale

    return originale(*argomenti, **opzioni)


def _migrazione(file: str):  # noqa: ANN202 — modulo di una migrazione precedente
    percorso = Path(__file__).with_name(file)
    spec = importlib.util.spec_from_file_location(f"migrazione_{percorso.stem}", percorso)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def _m033():  # noqa: ANN202
    return _migrazione("20261023_033_secondo_giro_r04.py")


# ── 2. Impronta della struttura dei registri ────────────────────────────────

# Lettura per chiunque esegua un ALTER TABLE (l'event trigger gira con i suoi
# diritti); scrittura solo dell'amministratore che migra.
_PROTEZIONE = (
    "CREATE SCHEMA IF NOT EXISTS protezione",
    "REVOKE ALL ON SCHEMA protezione FROM PUBLIC",
    "GRANT USAGE ON SCHEMA protezione TO PUBLIC",
    "CREATE TABLE IF NOT EXISTS protezione.impronte_registri (tabella text PRIMARY KEY, "
    "impronta text NOT NULL, aggiornata_il timestamptz NOT NULL DEFAULT now())",
    "REVOKE ALL ON protezione.impronte_registri FROM PUBLIC",
    "GRANT SELECT ON protezione.impronte_registri TO PUBLIC",
)

_FUNZIONE_IMPRONTA = """
CREATE OR REPLACE FUNCTION protezione.impronta_registro(tabella regclass)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT md5(
        coalesce((SELECT string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod)
                                    || ':' || a.attnotnull, ',' ORDER BY a.attnum)
                  FROM pg_attribute a
                  WHERE a.attrelid = tabella AND a.attnum > 0 AND NOT a.attisdropped), '')
        || '|' || coalesce((SELECT string_agg(c.conname || ':' || pg_get_constraintdef(c.oid),
                                              ',' ORDER BY c.conname)
                            FROM pg_constraint c WHERE c.conrelid = tabella), '')
        || '|' || coalesce((SELECT string_agg(pg_get_indexdef(i.indexrelid), ',' ORDER BY 1)
                            FROM pg_index i WHERE i.indrelid = tabella AND i.indisunique), ''))
$$;
"""

_FUNZIONE_AGGIORNA = """
CREATE OR REPLACE FUNCTION protezione.aggiorna_impronte()
RETURNS void LANGUAGE sql AS $$
    INSERT INTO protezione.impronte_registri (tabella, impronta, aggiornata_il)
    SELECT t, protezione.impronta_registro(to_regclass('public.' || t)), now()
    FROM unnest(ARRAY['audit_global', 'verifiche_registro']) AS t
    WHERE to_regclass('public.' || t) IS NOT NULL
    ON CONFLICT (tabella) DO UPDATE SET impronta = EXCLUDED.impronta,
        aggiornata_il = EXCLUDED.aggiornata_il
$$;
"""

# Controlli aggiunti allo stato (033) a fine ALTER TABLE / CREATE / ALTER INDEX
_STATO_STRUTTURA = """
    -- T1 (034): struttura dei registri invariata fuori dalle migrazioni
    IF to_regclass('protezione.impronte_registri') IS NOT NULL AND EXISTS (
           SELECT 1 FROM protezione.impronte_registri i
           WHERE protezione.impronta_registro(to_regclass('public.' || i.tabella))
                 IS DISTINCT FROM i.impronta) THEN
        RAISE EXCEPTION 'registro append-only: struttura di un registro (vincoli, colonne, '
            'indici unici) modificata fuori da una migrazione (registri_modificabili)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- partizioni dei registri: vincoli e indici unici solo ereditati, NOT NULL come la madre
    IF EXISTS (SELECT 1 FROM pg_inherits h JOIN pg_constraint c ON c.conrelid = h.inhrelid
               WHERE h.inhparent IN (to_regclass('public.audit_global'),
                                     to_regclass('public.verifiche_registro'))
                 AND c.coninhcount = 0)
       OR EXISTS (SELECT 1 FROM pg_inherits h JOIN pg_index x ON x.indrelid = h.inhrelid
                  WHERE h.inhparent IN (to_regclass('public.audit_global'),
                                        to_regclass('public.verifiche_registro'))
                    AND x.indisunique
                    AND NOT EXISTS (SELECT 1 FROM pg_inherits hi WHERE hi.inhrelid = x.indexrelid))
       OR EXISTS (SELECT 1 FROM pg_inherits h
                  JOIN pg_attribute a ON a.attrelid = h.inhrelid AND a.attnum > 0
                       AND NOT a.attisdropped
                  JOIN pg_attribute m ON m.attrelid = h.inhparent AND m.attname = a.attname
                  WHERE h.inhparent IN (to_regclass('public.audit_global'),
                                        to_regclass('public.verifiche_registro'))
                    AND a.attnotnull <> m.attnotnull) THEN
        RAISE EXCEPTION 'registro append-only: vincolo, indice unico o NOT NULL proprio di '
            'una partizione di un registro' USING ERRCODE = 'insufficient_privilege';
    END IF;
"""

# Rifiuto aggiunto a «oggetti» (033): partizioni dei registri solo dalla funzione
_OGGETTI_PARTIZIONE = """
        ELSIF c.object_type = 'table' AND c.command_tag = 'CREATE TABLE' THEN
            IF EXISTS (SELECT 1 FROM pg_inherits h WHERE h.inhrelid = c.objid
                       AND h.inhparent IN (to_regclass('public.audit_global'),
                                           to_regclass('public.verifiche_registro'))) THEN
                RAISE EXCEPTION 'registro append-only: le partizioni dei registri si creano solo '
                    'con vd_crea_partizione_registro' USING ERRCODE = 'insufficient_privilege';
            END IF;
"""

_FINE_FUNZIONE = """    END LOOP;
END;
$$;
"""
_FINE_STATO = """END;
$$;
"""


def _stato_034() -> str:
    m033 = _m033()
    testo = m033._sql(m033._FUNZIONE_STATO)
    assert testo.rstrip().endswith("END;\n$$;"), "stato della 033 inatteso"
    i = testo.rstrip().rfind("END;\n$$;")
    return testo[:i] + _STATO_STRUTTURA.lstrip("\n") + _FINE_STATO


def _oggetti_034() -> str:
    m033 = _m033()
    testo = m033._sql(m033._FUNZIONE_OGGETTI)
    punto = "        END IF;\n    END LOOP;\nEND;\n$$;"
    assert testo.count(punto) == 1, "oggetti della 033 inattesi"
    return testo.replace(punto, _OGGETTI_PARTIZIONE.lstrip("\n") + punto)


_TAG_OGGETTI_033 = ("'CREATE FUNCTION', 'ALTER FUNCTION', 'CREATE PROCEDURE', 'ALTER PROCEDURE', "
                    "'CREATE RULE', 'ALTER RULE', 'CREATE TRIGGER', 'ALTER TRIGGER', "
                    "'CREATE POLICY', 'ALTER POLICY'")
_TAG_STATO_031 = "'ALTER TABLE'"

# Event trigger ricreati con i tag nuovi (solo superutente; altrimenti WARNING)
_RICREA = """
DO $et$
BEGIN
    IF NOT (SELECT rolsuper OR EXISTS (SELECT 1 FROM pg_roles x WHERE x.rolname = 'rds_superuser'
                                       AND pg_has_role(current_user, x.oid, 'member'))
            FROM pg_roles WHERE rolname = current_user) THEN
        RAISE WARNING 'event trigger dei registri non ricreati: serve un superutente';
        RETURN;
    END IF;
    DROP EVENT TRIGGER IF EXISTS vd_protezione_registri_stato;
    CREATE EVENT TRIGGER vd_protezione_registri_stato ON ddl_command_end
        WHEN TAG IN (@TAG_STATO@) EXECUTE FUNCTION public.vd_protezione_registri_stato();
    DROP EVENT TRIGGER IF EXISTS vd_protezione_registri_oggetti;
    CREATE EVENT TRIGGER vd_protezione_registri_oggetti ON ddl_command_end
        WHEN TAG IN (@TAG_OGGETTI@) EXECUTE FUNCTION public.vd_protezione_registri_oggetti();
END
$et$;
"""


def _ricrea(tag_stato: str, tag_oggetti: str) -> str:
    return _RICREA.replace("@TAG_STATO@", tag_stato).replace("@TAG_OGGETTI@", tag_oggetti)


def upgrade() -> None:
    op.create_table(
        "catena_blocchi_registro",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("causa", sa.String(40), nullable=False),
        sa.Column("aperto_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occorrenze", sa.Integer(), nullable=False),
        sa.Column("dettagli", postgresql.JSONB(), nullable=False),
        sa.Column("chiuso_il", sa.DateTime(timezone=True)),
        sa.Column("chiuso_da", sa.String(200)),
        sa.Column("nota", sa.Text()),
        sa.CheckConstraint("occorrenze >= 1", name="ck_catena_blocchi_registro_occorrenze"),
        sa.CheckConstraint(
            "causa IN ('REGISTRO_INDIETRO', 'REGISTRO_DIVERGENTE', 'WORM_CONTENUTO_DIVERSO')",
            name="ck_catena_blocchi_registro_causa"),
        schema="public",
    )
    op.create_index("uq_catena_blocchi_registro_aperto", "catena_blocchi_registro", ["causa"],
                    unique=True, schema="public", postgresql_where=sa.text("chiuso_il IS NULL"))
    with registri_modificabili(op, motivo="migrazione 20261023_034: struttura dei registri, "
                                          "partizioni solo dalla funzione"):
        for istruzione in _PROTEZIONE:
            op.execute(istruzione)
        op.execute(_FUNZIONE_IMPRONTA)
        op.execute(_FUNZIONE_AGGIORNA)
        op.execute("REVOKE ALL ON FUNCTION protezione.aggiorna_impronte() FROM PUBLIC")
        # (impronte iniziali: le scrive registri_modificabili all'uscita)
        op.execute(_stato_034())
        op.execute(_oggetti_034())
        op.execute(_ricrea(f"{_TAG_STATO_031}, 'CREATE INDEX', 'ALTER INDEX'",
                           f"{_TAG_OGGETTI_033}, 'CREATE TABLE'"))


def downgrade() -> None:
    m033 = _m033()
    with registri_modificabili(op, motivo="downgrade di 20261023_034"):
        op.execute(m033._sql(m033._FUNZIONE_STATO))
        op.execute(m033._sql(m033._FUNZIONE_OGGETTI))
        op.execute(_ricrea(_TAG_STATO_031, _TAG_OGGETTI_033))
        op.execute("DROP SCHEMA IF EXISTS protezione CASCADE")
    op.drop_table("catena_blocchi_registro", schema="public")
