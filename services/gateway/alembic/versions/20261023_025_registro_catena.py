# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Registro delle impronte a due livelli: albero del PdA, registro globale, checkpoint (R04 catena)

Revision ID: 20261023_025
Revises: 20261021_024
Create Date: 2026-10-23

Contratti K1/K2 di R04 (catena delle impronte rafforzata, piano §5);
REGOLE_SVILUPPO §10.1 (append-only), §12.9 (lease), §16.5.

Tabelle nuove:
  - ``catena_pda``: albero di livello 1 di ogni PdA (dati delle foglie in JSON
    canonico e radice), scritto dall'aip-builder nella transazione del PdA;
  - ``catena_coda``: PdA da aggiungere al registro, in ordine di ``sequenza``
    (scritta dall'aip-builder, svuotata dal sequenziatore della hashchain);
  - ``catena_registro``: foglie del registro globale (indice = posizione 0, 1, 2…);
  - ``catena_nodi``: radici dei sottoalberi perfetti del registro (livello 0 =
    hash di foglia): radice e prove in O(log n) letture;
  - ``catena_checkpoint``: checkpoint firmati (nota C2SP), sigillo qualificato e
    controfirma del testimone;
  - ``catena_stato``: riga unica con dimensione del registro, lease del
    sequenziatore, foglie esportate nel WORM, ultima dimensione controfirmata.

Append-only (trigger, come ``hashchain_blocks``): ``catena_pda``,
``catena_registro`` e ``catena_nodi`` non ammettono UPDATE né DELETE né
TRUNCATE; in ``catena_checkpoint`` il contenuto è immutabile, il sigillo si
registra una sola volta (da DA_FIRMARE) e la controfirma, una volta presente,
non cambia più.

Migrazione dei blocchi esistenti (decisione R04, docs/architettura/catena.md §7):
i blocchi di formato 1 e 2 restano come sono (immutabili e verificabili); i PdA
già nella vecchia catena entrano nel registro con una foglia di «riversamento»
che cita il blocco d'origine, i PdA già su storage ma non ancora in catena con
una foglia di riversamento senza blocco. Qui si mettono soltanto in coda, in
ordine (blocco, posizione nel blocco; poi data di versamento, id): le foglie le
scrive il sequenziatore. Nessun contenuto conservato viene toccato (§10.6).

Downgrade: elimina tabelle, trigger e funzioni (solo sviluppo: un registro già
popolato andrebbe perso; il WORM conserva checkpoint e foglie esportate).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_025"
down_revision = "20261021_024"
branch_labels = None
depends_on = None

STATI_SIGILLO = ("DA_FIRMARE", "FIRMATO", "NON_FIRMATO")
STATI_TESTIMONE = ("NON_CONFIGURATO", "IN_ATTESA", "CONTROFIRMATO", "RIFIUTATO", "SUPERATO")
TIPI_FOGLIA = ("PDA", "RIVERSAMENTO")
_APPEND_ONLY = ("catena_pda", "catena_registro", "catena_nodi")


def _in(colonna: str, valori: tuple[str, ...]) -> str:
    return f"{colonna} IN ('" + "', '".join(valori) + "')"


def upgrade() -> None:
    op.create_table(
        "catena_pda",
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("urn", sa.String(500), nullable=False),
        sa.Column("radice", sa.String(64), nullable=False),
        sa.Column("num_foglie", sa.Integer(), nullable=False),
        sa.Column("foglie", postgresql.JSONB(), nullable=False),
        sa.Column("versione_formato", sa.SmallInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("creato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("num_foglie >= 1", name="ck_catena_pda_foglie"),
        sa.CheckConstraint("versione_formato IN (1)", name="ck_catena_pda_versione"),
        schema="public",
    )
    op.create_table(
        "catena_coda",
        sa.Column("sequenza", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("inserita_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("aip_id", name="uq_catena_coda_aip"),
        schema="public",
    )
    op.create_table(
        "catena_registro",
        sa.Column("indice", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("dati", sa.Text(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.Column("conservato_il", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("aip_id", name="uq_catena_registro_aip"),
        sa.CheckConstraint("indice >= 0", name="ck_catena_registro_indice"),
        sa.CheckConstraint(_in("tipo", TIPI_FOGLIA), name="ck_catena_registro_tipo"),
        sa.CheckConstraint("octet_length(hash) = 32", name="ck_catena_registro_hash"),
        schema="public",
    )
    op.create_table(
        "catena_nodi",
        sa.Column("livello", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("indice", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("livello >= 0 AND livello < 64 AND indice >= 0",
                           name="ck_catena_nodi_coordinate"),
        sa.CheckConstraint("octet_length(hash) = 32", name="ck_catena_nodi_hash"),
        schema="public",
    )
    op.create_table(
        "catena_checkpoint",
        sa.Column("dimensione", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("radice", sa.String(64), nullable=False),
        sa.Column("origine", sa.String(200), nullable=False),
        sa.Column("testo", sa.Text(), nullable=False),
        sa.Column("chiave_verificatore", sa.String(300), nullable=False),
        sa.Column("emesso_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worm_bucket", sa.String(100), nullable=False),
        sa.Column("worm_chiave", sa.String(500), nullable=False),
        sa.Column("worm_version_id", sa.String(200)),
        sa.Column("stato_sigillo", sa.String(20), nullable=False),
        sa.Column("motivo_sigillo", sa.Text()),
        sa.Column("sigillo_cades", sa.Text()),
        sa.Column("data_marca", sa.DateTime(timezone=True)),
        sa.Column("sigillo_worm_chiave", sa.String(500)),
        sa.Column("stato_testimone", sa.String(20), nullable=False),
        sa.Column("controfirma", sa.Text()),
        sa.Column("controfirmato_il", sa.DateTime(timezone=True)),
        sa.Column("esito_testimone", sa.Text()),
        sa.Column("tentativi_testimone", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.CheckConstraint("dimensione >= 1", name="ck_catena_checkpoint_dimensione"),
        sa.CheckConstraint(_in("stato_sigillo", STATI_SIGILLO),
                           name="ck_catena_checkpoint_sigillo"),
        sa.CheckConstraint(_in("stato_testimone", STATI_TESTIMONE),
                           name="ck_catena_checkpoint_testimone"),
        schema="public",
    )
    op.create_index("idx_catena_checkpoint_testimone", "catena_checkpoint",
                    ["stato_testimone", "dimensione"], schema="public",
                    postgresql_where=sa.text("stato_testimone IN ('IN_ATTESA', 'RIFIUTATO')"))
    op.create_index("idx_catena_checkpoint_sigillo", "catena_checkpoint", ["dimensione"],
                    schema="public", postgresql_where=sa.text("stato_sigillo = 'DA_FIRMARE'"))
    op.create_table(
        "catena_stato",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("dimensione", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("detentore", sa.String(200)),
        sa.Column("lease_scade_il", sa.DateTime(timezone=True)),
        sa.Column("worm_foglie_esportate", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("testimone_dimensione", sa.BigInteger()),
        sa.Column("aggiornato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("id = 1", name="ck_catena_stato_unica"),
        sa.CheckConstraint("dimensione >= 0 AND worm_foglie_esportate <= dimensione",
                           name="ck_catena_stato_dimensioni"),
        schema="public",
    )
    op.execute("INSERT INTO public.catena_stato (id) VALUES (1)")

    # ── Append-only ─────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION public.catena_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% è append-only: % vietata', TG_TABLE_NAME, TG_OP;
        END;
        $$;
    """)
    for tabella in _APPEND_ONLY:
        op.execute(f"""
            CREATE TRIGGER trg_{tabella}_append_only
            BEFORE UPDATE OR DELETE ON public.{tabella}
            FOR EACH ROW EXECUTE FUNCTION public.catena_append_only();
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{tabella}_no_truncate
            BEFORE TRUNCATE ON public.{tabella}
            FOR EACH STATEMENT EXECUTE FUNCTION public.catena_append_only();
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.catena_checkpoint_protetto()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'catena_checkpoint è append-only: % vietata', TG_OP;
            END IF;
            IF NEW.dimensione IS DISTINCT FROM OLD.dimensione
               OR NEW.radice IS DISTINCT FROM OLD.radice
               OR NEW.origine IS DISTINCT FROM OLD.origine
               OR NEW.testo IS DISTINCT FROM OLD.testo
               OR NEW.chiave_verificatore IS DISTINCT FROM OLD.chiave_verificatore
               OR NEW.emesso_il IS DISTINCT FROM OLD.emesso_il
               OR NEW.worm_bucket IS DISTINCT FROM OLD.worm_bucket
               OR NEW.worm_chiave IS DISTINCT FROM OLD.worm_chiave
               OR NEW.worm_version_id IS DISTINCT FROM OLD.worm_version_id THEN
                RAISE EXCEPTION 'catena_checkpoint: contenuto del checkpoint % non modificabile',
                    OLD.dimensione;
            END IF;
            IF OLD.stato_sigillo <> 'DA_FIRMARE' AND (
                   NEW.stato_sigillo IS DISTINCT FROM OLD.stato_sigillo
                   OR NEW.motivo_sigillo IS DISTINCT FROM OLD.motivo_sigillo
                   OR NEW.sigillo_cades IS DISTINCT FROM OLD.sigillo_cades
                   OR NEW.data_marca IS DISTINCT FROM OLD.data_marca
                   OR NEW.sigillo_worm_chiave IS DISTINCT FROM OLD.sigillo_worm_chiave) THEN
                RAISE EXCEPTION 'catena_checkpoint: sigillo del checkpoint % già registrato',
                    OLD.dimensione;
            END IF;
            IF OLD.stato_testimone = 'CONTROFIRMATO' AND (
                   NEW.stato_testimone IS DISTINCT FROM OLD.stato_testimone
                   OR NEW.controfirma IS DISTINCT FROM OLD.controfirma
                   OR NEW.controfirmato_il IS DISTINCT FROM OLD.controfirmato_il) THEN
                RAISE EXCEPTION 'catena_checkpoint: controfirma del checkpoint % già registrata',
                    OLD.dimensione;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_catena_checkpoint_protetto
        BEFORE UPDATE OR DELETE ON public.catena_checkpoint
        FOR EACH ROW EXECUTE FUNCTION public.catena_checkpoint_protetto();
    """)
    op.execute("""
        CREATE TRIGGER trg_catena_checkpoint_no_truncate
        BEFORE TRUNCATE ON public.catena_checkpoint
        FOR EACH STATEMENT EXECUTE FUNCTION public.catena_checkpoint_protetto();
    """)

    # ── Coda iniziale: PdA della vecchia catena e PdA su storage ─────
    # join aip → blocco e posizione dell'AIP nell'elenco del blocco (ordine totale)
    op.execute("""
        INSERT INTO public.catena_coda (aip_id)
        SELECT a.id FROM public.hashchain_blocks b
        CROSS JOIN LATERAL jsonb_array_elements_text(b.aip_ids) WITH ORDINALITY AS e(aip, pos)
        JOIN public.aip a ON a.id = CAST(e.aip AS uuid) AND a.hashchain_block_id = b.id
        ORDER BY b.numero_blocco, e.pos
    """)
    op.execute("""
        INSERT INTO public.catena_coda (aip_id)
        SELECT a.id FROM public.aip a
        WHERE a.stato = 'SU_STORAGE' AND a.hashchain_block_id IS NULL
        ORDER BY a.data_versamento, a.id
        ON CONFLICT (aip_id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_catena_checkpoint_no_truncate "
               "ON public.catena_checkpoint")
    op.execute("DROP TRIGGER IF EXISTS trg_catena_checkpoint_protetto ON public.catena_checkpoint")
    for tabella in _APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tabella}_no_truncate ON public.{tabella}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tabella}_append_only ON public.{tabella}")
    op.execute("DROP FUNCTION IF EXISTS public.catena_checkpoint_protetto()")
    op.execute("DROP FUNCTION IF EXISTS public.catena_append_only()")
    op.drop_table("catena_stato", schema="public")
    op.drop_index("idx_catena_checkpoint_sigillo", table_name="catena_checkpoint",
                  schema="public")
    op.drop_index("idx_catena_checkpoint_testimone", table_name="catena_checkpoint",
                  schema="public")
    op.drop_table("catena_checkpoint", schema="public")
    op.drop_table("catena_nodi", schema="public")
    op.drop_table("catena_registro", schema="public")
    op.drop_table("catena_coda", schema="public")
    op.drop_table("catena_pda", schema="public")
