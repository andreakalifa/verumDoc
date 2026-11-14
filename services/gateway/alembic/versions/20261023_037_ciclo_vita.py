# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Ciclo di vita: pratiche di rettifica, annullamento, scarto, riversamento e restituzione (R05)

Revision ID: 20261023_037
Revises: 20261023_035
Create Date: 2026-10-23

Agente r05-ciclo-vita (contratto Q2, VD-C-072, VD-P-06). Ramo separato da
``20261023_035``: la migrazione di unione è del coordinatore.

Tabelle nuove (scritte SOLO dal servizio ``ciclo-vita``):

1. ``pratiche`` — una riga per procedimento (tipo RETTIFICA, ANNULLAMENTO,
   SCARTO, RIVERSAMENTO_USCITA, RIVERSAMENTO_INGRESSO, RESTITUZIONE) con stato
   della macchina a stati del tipo, ``versione`` (comandi con lo stato atteso,
   REGOLE §12.5), dati della richiesta, impronta del piano (REGOLE §13), PdV
   coinvolti, chiave d'idempotenza e i campi del LAVORO fuori richiesta con
   lease (REGOLE §12.9): ``lavoro`` (passo richiesto al lavoratore, NULL =
   nessuno), ``prossimo_tentativo``, ``tentativi``, ``lease_detentore``,
   ``lease_scade_il``. Una sola pratica di RETTIFICA aperta per PdV e un solo
   ANNULLAMENTO per PdV (indici unici parziali).
2. ``pratiche_stati`` — cronologia append-only delle transizioni (chi, ruolo,
   motivo, allegati citati): è anche il registro delle autorizzazioni.
3. ``pratiche_allegati`` — allegati obbligatori (autorizzazione del
   produttore, nulla osta, verbale, atti, manifesti, PdD ricevuti) archiviati
   nel WORM con impronta SHA-256; righe immutabili.
4. ``pratiche_oggetti`` — PdA o PdV coinvolti, con esito per elemento
   (selezionato, bloccato con motivo, eliminato, esportato, versato, errore) e
   identificativo della foglia del registro (contratto Q3).
5. ``pratiche_esecuzioni`` — esecuzioni del lavoratore, append-only
   (REGOLE §15.8: evidenza per l'auditor).

Vista ``pratiche_canale_versamento`` (tenant_id, responsabile_id, stato):
«account» del canale di versamento ``CICLO_VITA`` per il controllo del token di
canale nel gateway (contratto C2): l'utente per conto del quale ciclo-vita
versa deve avere una pratica di rettifica o di riversamento in ingresso aperta.

Le tabelle append-only hanno il trigger ``vd_pratiche_append_only`` (niente
UPDATE né DELETE, anche per il proprietario) e, se il gruppo dei servizi
``verumdoc_applicazione`` esiste (migrazione 031), REVOKE di UPDATE e DELETE.
I privilegi DML dei servizi arrivano dai privilegi predefiniti della 031.

Indici su tabelle esistenti con CONCURRENTLY (fuori transazione):
``idx_aip_tenant_scadenza`` (selezione dello scarto per ente e scadenza),
``idx_fascicoli_aip_aip`` (fascicoli di un PdA: blocchi dello scarto),
``idx_fascicoli_tenant_cursore`` (elenco dei fascicoli a cursore, VD-C-087).

Downgrade: elimina indici, trigger, funzione e tabelle nuove (i dati delle
pratiche si perdono: le evidenze restano nel WORM e in ``audit_global``).

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

revision = "20261023_037"
down_revision = "20261023_035"
branch_labels = None
depends_on = None

TIPI = ("RETTIFICA", "ANNULLAMENTO", "SCARTO", "RIVERSAMENTO_USCITA",
        "RIVERSAMENTO_INGRESSO", "RESTITUZIONE")
TIPI_ALLEGATO = ("AUTORIZZAZIONE_PRODUTTORE", "NULLA_OSTA", "VERBALE_SCARTO", "ATTO",
                 "MANIFESTO", "PDD_RICEVUTO", "EXPORT_REGISTRI", "ALTRO")
ESITI_OGGETTO = ("SELEZIONATO", "BLOCCATO", "IN_CORSO", "ELIMINATO", "ESPORTATO", "VERSATO",
                 "ERRORE")
APPEND_ONLY = ("pratiche_stati", "pratiche_allegati", "pratiche_esecuzioni")


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


_FUNZIONE = """
CREATE OR REPLACE FUNCTION public.vd_pratiche_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% è append-only: % vietata', TG_TABLE_NAME, TG_OP;
END
$$;
"""

_REVOCA = """
DO $r$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_applicazione') THEN
        EXECUTE 'REVOKE UPDATE, DELETE ON public.@TABELLA@ FROM verumdoc_applicazione';
    END IF;
END
$r$;
"""



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
    # Segmento 1 (una transazione): tabelle, vista, trigger. Saltato se una
    # corsa interrotta dopo il suo commit l'ha già applicato (S1).
    if not _gia_applicato("public.pratiche"):
        _crea_catalogo()
    # Segmento 2 (autocommit, un indice per volta): riprendibile
    _indice_concorrente(
        "idx_aip_tenant_scadenza",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aip_tenant_scadenza "
        "ON public.aip (tenant_id, scadenza_conservazione, id) "
        "WHERE scadenza_conservazione IS NOT NULL")
    _indice_concorrente(
        "idx_fascicoli_aip_aip",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_fascicoli_aip_aip "
        "ON public.fascicoli_aip (aip_id)")
    _indice_concorrente(
        "idx_fascicoli_tenant_cursore",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_fascicoli_tenant_cursore "
        "ON public.fascicoli (tenant_id, created_at DESC, id DESC)")


def _crea_catalogo() -> None:
    op.create_table(
        "pratiche",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT",
                                name="fk_pratiche_tenant"), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("stato", sa.String(40), nullable=False),
        sa.Column("versione", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("dati", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("impronta_piano", sa.String(64)),
        sa.Column("richiede_nulla_osta", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("pdv_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pdv.id", ondelete="RESTRICT",
                                name="fk_pratiche_pdv")),
        sa.Column("pdv_risultato_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pdv.id", ondelete="RESTRICT",
                                name="fk_pratiche_pdv_risultato")),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("creata_da", postgresql.UUID(as_uuid=True)),
        sa.Column("creata_ruolo", sa.String(30)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("conclusa_il", sa.DateTime(timezone=True)),
        sa.Column("lavoro", sa.String(30)),
        sa.Column("prossimo_tentativo", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_detentore", sa.String(200)),
        sa.Column("lease_scade_il", sa.DateTime(timezone=True)),
        sa.Column("ultimo_errore", postgresql.JSONB()),
        sa.Column("conteggi", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(f"tipo IN ({_elenco(TIPI)})", name="ck_pratiche_tipo"),
        sa.CheckConstraint("char_length(motivo) >= 10", name="ck_pratiche_motivo"),
        sa.CheckConstraint("versione >= 1", name="ck_pratiche_versione"),
        schema="public",
    )
    op.create_index("idx_pratiche_tenant_cursore", "pratiche",
                    ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
                    schema="public")
    op.create_index("idx_pratiche_cursore", "pratiche",
                    [sa.text("created_at DESC"), sa.text("id DESC")], schema="public")
    op.create_index("idx_pratiche_lavoro", "pratiche", ["prossimo_tentativo", "id"],
                    schema="public", postgresql_where=sa.text("lavoro IS NOT NULL"))
    op.create_index("uq_pratiche_idempotenza", "pratiche",
                    ["tenant_id", "tipo", "idempotency_key"], unique=True, schema="public",
                    postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    # Una rettifica aperta per PdV; un solo annullamento per PdV (stato finale)
    op.create_index("uq_pratiche_rettifica_aperta", "pratiche", ["pdv_id"], unique=True,
                    schema="public",
                    postgresql_where=sa.text("tipo = 'RETTIFICA' AND "
                                             "stato NOT IN ('COMPLETATA', 'RESPINTA')"))
    op.create_index("uq_pratiche_annullamento", "pratiche", ["pdv_id"], unique=True,
                    schema="public", postgresql_where=sa.text("tipo = 'ANNULLAMENTO'"))
    op.create_index("idx_pratiche_pdv", "pratiche", ["pdv_id"], schema="public",
                    postgresql_where=sa.text("pdv_id IS NOT NULL"))
    op.create_index("idx_pratiche_pdv_risultato", "pratiche", ["pdv_risultato_id"],
                    schema="public", postgresql_where=sa.text("pdv_risultato_id IS NOT NULL"))

    op.create_table(
        "pratiche_stati",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("pratica_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                name="fk_pratiche_stati_pratica"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("da_stato", sa.String(40)),
        sa.Column("a_stato", sa.String(40), nullable=False),
        sa.Column("utente_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ruolo", sa.String(30)),
        sa.Column("metodo", sa.String(20)),
        sa.Column("motivo", sa.Text()),
        sa.Column("allegati", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        schema="public",
    )
    op.create_index("idx_pratiche_stati_pratica", "pratiche_stati", ["pratica_id", "id"],
                    schema="public")

    op.create_table(
        "pratiche_allegati",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("pratica_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                name="fk_pratiche_allegati_pratica"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("nome_file", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=False),
        sa.Column("dimensione", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("chiave", sa.String(500), nullable=False),
        sa.Column("version_id", sa.String(200)),
        sa.Column("conserva_fino_a", sa.DateTime(timezone=True), nullable=False),
        sa.Column("firma", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("caricato_da", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(f"tipo IN ({_elenco(TIPI_ALLEGATO)})",
                           name="ck_pratiche_allegati_tipo"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_pratiche_allegati_sha256"),
        sa.UniqueConstraint("pratica_id", "tipo", "sha256",
                            name="uq_pratiche_allegati_impronta"),
        sa.UniqueConstraint("bucket", "chiave", name="uq_pratiche_allegati_chiave"),
        schema="public",
    )
    op.create_index("idx_pratiche_allegati_pratica", "pratiche_allegati",
                    ["pratica_id", "created_at"], schema="public")

    op.create_table(
        "pratiche_oggetti",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("pratica_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                name="fk_pratiche_oggetti_pratica"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT",
                                name="fk_pratiche_oggetti_aip")),
        sa.Column("pdv_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pdv.id", ondelete="RESTRICT",
                                name="fk_pratiche_oggetti_pdv")),
        sa.Column("urn", sa.String(500)),
        sa.Column("esito", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(60)),
        sa.Column("dettagli", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("foglia_id", sa.String(100)),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("aggiornato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(f"esito IN ({_elenco(ESITI_OGGETTO)})",
                           name="ck_pratiche_oggetti_esito"),
        sa.CheckConstraint("aip_id IS NOT NULL OR pdv_id IS NOT NULL OR urn IS NOT NULL",
                           name="ck_pratiche_oggetti_riferimento"),
        schema="public",
    )
    op.create_index("idx_pratiche_oggetti_pratica", "pratiche_oggetti",
                    ["pratica_id", "esito", "id"], schema="public")
    op.create_index("uq_pratiche_oggetti_aip", "pratiche_oggetti", ["pratica_id", "aip_id"],
                    unique=True, schema="public",
                    postgresql_where=sa.text("aip_id IS NOT NULL"))
    op.create_index("idx_pratiche_oggetti_aip", "pratiche_oggetti", ["aip_id"],
                    schema="public", postgresql_where=sa.text("aip_id IS NOT NULL"))

    op.create_table(
        "pratiche_esecuzioni",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("pratica_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                name="fk_pratiche_esecuzioni_pratica"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lavoro", sa.String(30), nullable=False),
        sa.Column("detentore", sa.String(200), nullable=False),
        sa.Column("tentativo", sa.Integer(), nullable=False),
        sa.Column("avviata_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminata_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("esito", sa.String(20), nullable=False),
        sa.Column("conteggi", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("errore", postgresql.JSONB()),
        sa.CheckConstraint("esito IN ('COMPLETATO', 'PARZIALE', 'ERRORE', 'RINVIATO')",
                           name="ck_pratiche_esecuzioni_esito"),
        schema="public",
    )
    op.create_index("idx_pratiche_esecuzioni_pratica", "pratiche_esecuzioni",
                    ["pratica_id", "id"], schema="public")

    # «Account» del canale CICLO_VITA per il gateway (contratto C2,
    # libs.shared.security.token_canale.TABELLE_ACCOUNT_CANALE): un token di
    # canale del servizio ciclo-vita vale solo per un utente che ha una pratica
    # APERTA che versa (rettifica, riversamento in ingresso) nel proprio ente.
    op.execute(
        "CREATE VIEW public.pratiche_canale_versamento AS "
        "SELECT DISTINCT tenant_id, creata_da AS responsabile_id, 'ATTIVO'::text AS stato "
        "FROM public.pratiche WHERE creata_da IS NOT NULL "
        "AND tipo IN ('RETTIFICA', 'RIVERSAMENTO_INGRESSO') "
        "AND stato NOT IN ('COMPLETATA', 'RESPINTA', 'ESEGUITA')")

    op.execute(_FUNZIONE)
    for tabella in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER trg_{tabella}_append_only BEFORE UPDATE OR DELETE "
            f"ON public.{tabella} FOR EACH ROW EXECUTE FUNCTION public.vd_pratiche_append_only()")
        op.execute(
            f"CREATE TRIGGER trg_{tabella}_no_truncate BEFORE TRUNCATE "
            f"ON public.{tabella} FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.vd_pratiche_append_only()")
        op.execute(_REVOCA.replace("@TABELLA@", tabella))


def downgrade() -> None:
    _limita_attesa_lock()
    with op.get_context().autocommit_block():
        for indice in ("idx_fascicoli_tenant_cursore", "idx_fascicoli_aip_aip",
                       "idx_aip_tenant_scadenza"):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{indice}")
    for tabella in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tabella}_no_truncate ON public.{tabella}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tabella}_append_only ON public.{tabella}")
    op.execute("DROP VIEW IF EXISTS public.pratiche_canale_versamento")
    for tabella in ("pratiche_esecuzioni", "pratiche_oggetti", "pratiche_allegati",
                    "pratiche_stati", "pratiche"):
        op.drop_table(tabella, schema="public")
    op.execute("DROP FUNCTION IF EXISTS public.vd_pratiche_append_only()")
