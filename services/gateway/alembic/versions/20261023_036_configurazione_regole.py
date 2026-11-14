# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Configurazione con bozze, accordi di versamento, profili di tipologia (R05 Q1)

Revision ID: 20261023_036
Revises: 20261023_035
Create Date: 2026-10-23

Contratto R05 Q1 (agente r05-regole); REGOLE_SVILUPPO §8.2, §8.4, §8.13, §13.

Tabelle nuove:
  - public.configurazione_bozze: bozze versionate di modifica (§13.3);
  - public.accordi_versamento: versioni degli accordi di versamento (livello
    ACCORDO della catena), APPEND-ONLY;
  - public.profili_tipologia: versioni dei profili di metadati per tipologia
    dell'Allegato 5 create dal conservatore, APPEND-ONLY;
  - public.configurazione_proposte: proposte del conservatore agli enti (§8.13);
  - public.impostazioni_storico: storia dei valori di impostazioni_valori,
    APPEND-ONLY.
Le tabelle append-only hanno un trigger che rifiuta UPDATE e DELETE (una versione
in uso non si modifica, §8.4); TRUNCATE resta al proprietario (come i test).

Indice nuovo su ``pdv`` (classe_doc_id, data_ricezione DESC, id DESC), creato
CONCURRENTLY fuori transazione: campione dei PdV recenti di una classe per
l'anteprima dell'impatto (§13.4), anche per le classi di base usate da tutti
gli enti.

Nessun contenuto conservato viene toccato (§10.6). Downgrade: elimina indice,
trigger, funzione e tabelle (i dati di configurazione creati dopo la 036 si
perdono: le fotografie dei PdV restano).

Revisione 2 di R05 (S1): riprendibile dopo un errore in qualunque punto. Con
``transaction_per_migration`` (env.py) il segmento transazionale che precede un
``autocommit_block`` si salta se è già stato confermato (``_gia_applicato``:
controllo di catalogo, il segmento è atomico); indici CONCURRENTLY rimasti
invalidi si ricreano; ``VALIDATE`` è idempotente.
"""
# ruff: noqa: S608 — SQL composto solo con i nomi costanti degli indici di questo modulo
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261023_036"
down_revision = "20261023_035"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_APPEND_ONLY = ("accordi_versamento", "profili_tipologia", "impostazioni_storico")


def _id() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _creato() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                     server_default=sa.func.now())


def _fk_tenant(nullable: bool) -> sa.Column:
    return sa.Column("tenant_id", _UUID, sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"),
                     nullable=nullable)


def _fk_classe() -> sa.Column:
    return sa.Column("classe_id", _UUID,
                     sa.ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"))


def _fk_bozza(nullable: bool = True) -> sa.Column:
    return sa.Column("bozza_id", _UUID,
                     sa.ForeignKey("public.configurazione_bozze.id", ondelete="RESTRICT"),
                     nullable=nullable)



def _limita_attesa_lock() -> None:
    """Revisione R05 (R6): ``lock_timeout`` per i DDL di questa revisione (SET
    LOCAL): un lock non ottenuto in tempo fa fallire la migrazione, che si
    riprende, invece di mettere in coda il servizio."""
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _gia_applicato(oggetto: str) -> bool:
    """Revisione R05 (S1): True se il segmento transazionale che crea
    ``oggetto`` è già stato confermato da una corsa precedente interrotta.

    Il segmento che precede un ``autocommit_block`` è UNA transazione (env.py:
    ``transaction_per_migration``): o c'è tutto o non c'è niente, quindi la
    presenza di un suo oggetto basta. ``alembic_version`` invece si aggiorna
    solo alla fine della revisione: senza questo controllo la ripresa fallirebbe
    con «already exists»."""
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def _indice_concorrente(nome: str, ddl: str) -> None:
    """``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` riprendibile: un indice
    lasciato INVALIDO da una corsa interrotta si elimina e si ricrea (IF NOT
    EXISTS da solo lo terrebbe, invalido, per sempre)."""
    with op.get_context().autocommit_block():
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_index i
                         WHERE i.indexrelid = to_regclass('public.{nome}')
                           AND NOT i.indisvalid) THEN
                EXECUTE 'DROP INDEX public.{nome}';
              END IF;
            END $$""")
        op.execute(ddl)


def upgrade() -> None:
    _limita_attesa_lock()
    # Segmento 1 (una transazione): tabelle, indici, trigger. Saltato se una
    # corsa interrotta dopo il suo commit l'ha già applicato (S1).
    if not _gia_applicato("public.configurazione_bozze"):
        _crea_catalogo()
    # Segmento 2 (autocommit): indice su ``pdv`` senza bloccare le scritture
    _indice_concorrente(
        "idx_pdv_classe_ricezione",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_classe_ricezione "
        "ON public.pdv (classe_doc_id, data_ricezione DESC, id DESC)")


def _crea_catalogo() -> None:
    op.create_table(
        "configurazione_bozze",
        _id(),
        sa.Column("risorsa", sa.String(20), nullable=False),
        sa.Column("livello", sa.String(20), nullable=False),
        _fk_tenant(True),
        _fk_classe(),
        sa.Column("accordo_codice", sa.String(50)),
        sa.Column("modifiche", postgresql.JSONB(), nullable=False),
        sa.Column("proposta", postgresql.JSONB()),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("versione", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("stato", sa.String(20), nullable=False, server_default=sa.text("'APERTA'")),
        sa.Column("creato_da", _UUID),
        sa.Column("aggiornato_da", _UUID),
        _creato(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("applicata_il", sa.DateTime(timezone=True)),
        sa.Column("applicata_da", _UUID),
        sa.Column("impronta_piano", sa.String(64)),
        sa.Column("esito", postgresql.JSONB()),
        sa.CheckConstraint("risorsa IN ('impostazioni', 'regole', 'profilo', 'classe', "
                           "'accordo', 'quote')", name="ck_configurazione_bozze_risorsa"),
        sa.CheckConstraint("livello IN ('CONSERVATORE', 'ENTE', 'CLASSE', 'ACCORDO', 'PDV')",
                           name="ck_configurazione_bozze_livello"),
        sa.CheckConstraint("stato IN ('APERTA', 'APPLICATA', 'ANNULLATA')",
                           name="ck_configurazione_bozze_stato"),
        sa.CheckConstraint("versione >= 1", name="ck_configurazione_bozze_versione"),
        schema="public",
    )
    op.create_index("idx_configurazione_bozze_ente", "configurazione_bozze",
                    ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
                    schema="public")
    op.create_index("idx_configurazione_bozze_stato", "configurazione_bozze",
                    ["stato", sa.text("created_at DESC"), sa.text("id DESC")], schema="public")

    op.create_table(
        "accordi_versamento",
        _id(),
        _fk_tenant(False),
        sa.Column("codice", sa.String(50), nullable=False),
        sa.Column("versione", sa.Integer(), nullable=False),
        _fk_classe(),
        sa.Column("descrizione", sa.Text()),
        sa.Column("valido_dal", sa.Date(), nullable=False),
        sa.Column("cessato", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("valori", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("motivo", sa.Text(), nullable=False),
        _fk_bozza(),
        sa.Column("creato_da", _UUID),
        _creato(),
        sa.UniqueConstraint("tenant_id", "codice", "versione",
                            name="uq_accordi_versamento_versione"),
        sa.CheckConstraint("versione >= 1", name="ck_accordi_versamento_versione"),
        sa.CheckConstraint("codice ~ '^[A-Za-z0-9._-]{1,50}$'",
                           name="ck_accordi_versamento_codice"),
        schema="public",
    )
    op.create_index("idx_accordi_versamento_ente", "accordi_versamento",
                    ["tenant_id", "valido_dal"], schema="public")

    op.create_table(
        "profili_tipologia",
        _id(),
        sa.Column("codice", sa.String(50), nullable=False),
        sa.Column("versione", sa.Integer(), nullable=False),
        sa.Column("valido_dal", sa.Date(), nullable=False),
        sa.Column("valido_al", sa.Date()),
        sa.Column("campi", postgresql.JSONB(), nullable=False),
        sa.Column("descrizione", sa.Text()),
        sa.Column("norm_ref", sa.Text()),
        sa.Column("motivo", sa.Text(), nullable=False),
        _fk_bozza(),
        sa.Column("creato_da", _UUID),
        _creato(),
        sa.UniqueConstraint("codice", "versione", name="uq_profili_tipologia_versione"),
        sa.CheckConstraint("versione >= 1", name="ck_profili_tipologia_versione"),
        sa.CheckConstraint("valido_al IS NULL OR valido_al >= valido_dal",
                           name="ck_profili_tipologia_validita"),
        schema="public",
    )

    op.create_table(
        "configurazione_proposte",
        _id(),
        _fk_bozza(nullable=False),
        _fk_tenant(False),
        sa.Column("codice", sa.String(100), nullable=False),
        sa.Column("valore", postgresql.JSONB(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False, server_default=sa.text("'PROPOSTA'")),
        sa.Column("motivo_risposta", sa.Text()),
        sa.Column("risposto_da", _UUID),
        sa.Column("risposto_il", sa.DateTime(timezone=True)),
        _creato(),
        sa.UniqueConstraint("bozza_id", "tenant_id", "codice", name="uq_configurazione_proposte"),
        sa.CheckConstraint("stato IN ('PROPOSTA', 'ACCETTATA', 'RIFIUTATA')",
                           name="ck_configurazione_proposte_stato"),
        schema="public",
    )
    op.create_index("idx_configurazione_proposte_ente", "configurazione_proposte",
                    ["tenant_id", "stato", sa.text("created_at DESC"), sa.text("id DESC")],
                    schema="public")

    op.create_table(
        "impostazioni_storico",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("livello", sa.String(20), nullable=False),
        _fk_tenant(True),
        _fk_classe(),
        sa.Column("codice", sa.String(100), nullable=False),
        sa.Column("valore_prima", postgresql.JSONB()),
        sa.Column("valore_dopo", postgresql.JSONB()),
        sa.Column("motivo", sa.Text(), nullable=False),
        _fk_bozza(),
        sa.Column("proposta_id", _UUID,
                  sa.ForeignKey("public.configurazione_proposte.id", ondelete="RESTRICT")),
        sa.Column("creato_da", _UUID),
        _creato(),
        schema="public",
    )
    op.create_index("idx_impostazioni_storico_ente", "impostazioni_storico",
                    ["tenant_id", sa.text("created_at DESC")], schema="public")

    # Append-only: una versione in uso non si modifica (REGOLE §8.4)
    op.execute("""
        CREATE FUNCTION public.vd_configurazione_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'tabella % in sola aggiunta: % non ammesso (REGOLE 8.4)',
                TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END $$;
    """)
    for tabella in _APPEND_ONLY:
        op.execute(f"CREATE TRIGGER trg_{tabella}_append_only BEFORE UPDATE OR DELETE "
                   f"ON public.{tabella} FOR EACH ROW "
                   "EXECUTE FUNCTION public.vd_configurazione_append_only()")


def downgrade() -> None:
    _limita_attesa_lock()
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_pdv_classe_ricezione")
    for tabella in _APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tabella}_append_only ON public.{tabella}")
    op.execute("DROP FUNCTION IF EXISTS public.vd_configurazione_append_only()")
    op.drop_index("idx_impostazioni_storico_ente", "impostazioni_storico", schema="public")
    op.drop_table("impostazioni_storico", schema="public")
    op.drop_index("idx_configurazione_proposte_ente", "configurazione_proposte", schema="public")
    op.drop_table("configurazione_proposte", schema="public")
    op.drop_table("profili_tipologia", schema="public")
    op.drop_index("idx_accordi_versamento_ente", "accordi_versamento", schema="public")
    op.drop_table("accordi_versamento", schema="public")
    op.drop_index("idx_configurazione_bozze_stato", "configurazione_bozze", schema="public")
    op.drop_index("idx_configurazione_bozze_ente", "configurazione_bozze", schema="public")
    op.drop_table("configurazione_bozze", schema="public")
