# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Registro delle impronte: foglie interne (ciclo di vita, audit) ed export dell'audit (R05)

Revision ID: 20261023_039
Revises: 20261023_035
Create Date: 2026-10-23

Agente r05-catena-residui (contratto Q3, VD-C-112). Ramo separato da
``20261023_035``: la migrazione di unione è del coordinatore.

1. ``catena_foglie_interne`` (Q3): richieste di foglie di formato 2 che non
   sono la conservazione di un PdA: ``SCARTO`` (tombstone), ``ANNULLAMENTO``,
   ``RETTIFICA`` (da ``ciclo-vita`` con l'API interna della hashchain) e
   ``AUDIT`` (segmenti dell'audit esportati nel WORM). Il JSON canonico della
   foglia (``dati``) e il suo hash si fissano alla richiesta; ``chiave`` è la
   chiave d'idempotenza ((tipo, pda, pratica) o il segmento dell'audit).
   ``indice`` e ``sequenziata_il`` si scrivono UNA volta dal sequenziatore;
   tutto il resto è immutabile, nessuna riga si cancella (trigger).
2. ``catena_coda``: una riga in coda è un PdA (``aip_id``) OPPURE una foglia
   interna (``foglia_interna_id``): un solo ordine totale (``sequenza``) per
   tutte le foglie del registro (REGOLE §12.8). L'aip-builder continua a
   inserire ``(aip_id)`` come prima.
3. ``catena_registro``: ``aip_id`` e ``tenant_id`` facoltativi SOLO per le
   foglie interne (vincolo ``ck_catena_registro_pda``: una foglia PDA o
   RIVERSAMENTO ha sempre il suo PdA, una foglia interna mai: le query che
   collegano PdA e foglia restano ``r.aip_id = a.id``); tipi nuovi ammessi.
   I vincoli nuovi si aggiungono ``NOT VALID`` in una transazione breve
   (``DROP NOT NULL`` e ``DROP/ADD CONSTRAINT`` prendono ACCESS EXCLUSIVE per un
   istante, con ``lock_timeout``), poi si convalidano IN AUTOCOMMIT, ognuno nella
   sua transazione (revisione R05, R6): la convalida legge la tabella (una riga
   per PdA) con il solo SHARE UPDATE EXCLUSIVE, senza bloccare letture e INSERT.
4. ``audit_esportazioni`` (VD-C-112): un segmento dell'audit per periodo,
   append-only, con impronta del segmento precedente e radice di Merkle delle
   righe; l'impronta entra nel registro come foglia ``AUDIT``.

Privilegi: i servizi (gruppo ``verumdoc_applicazione``, migrazione 031) ricevono
i privilegi predefiniti; qui si tolgono UPDATE e DELETE su ``audit_esportazioni``
e DELETE su ``catena_foglie_interne``.

Downgrade: possibile solo finché il registro non contiene foglie interne
(``catena_registro`` è append-only: altrimenti errore esplicito); le richieste
in coda di foglie interne si tolgono dalla coda.

Revisione 2 di R05 (S1): riprendibile dopo un errore in qualunque punto. Con
``transaction_per_migration`` (env.py) il segmento transazionale che precede un
``autocommit_block`` si salta se è già stato confermato (``_gia_applicato``:
controllo di catalogo, il segmento è atomico); indici CONCURRENTLY rimasti
invalidi si ricreano; ``VALIDATE`` è idempotente.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261023_039"
down_revision = "20261023_035"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
TIPI_INTERNI = ("SCARTO", "ANNULLAMENTO", "RETTIFICA", "AUDIT")
TIPI_PDA = ("PDA", "RIVERSAMENTO")
TIPI_REGISTRO = TIPI_PDA + TIPI_INTERNI


def _in(colonna: str, valori: tuple[str, ...]) -> str:
    return f"{colonna} IN ('" + "', '".join(valori) + "')"


_PROTEZIONE_INTERNE = """
CREATE OR REPLACE FUNCTION public.catena_foglie_interne_protetta()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'catena_foglie_interne è append-only: % vietata', TG_OP;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tipo IS DISTINCT FROM OLD.tipo
       OR NEW.chiave IS DISTINCT FROM OLD.chiave OR NEW.aip_id IS DISTINCT FROM OLD.aip_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.pda IS DISTINCT FROM OLD.pda
       OR NEW.pratica IS DISTINCT FROM OLD.pratica
       OR NEW.riferimento IS DISTINCT FROM OLD.riferimento
       OR NEW.impronta IS DISTINCT FROM OLD.impronta OR NEW.dati IS DISTINCT FROM OLD.dati
       OR NEW.hash IS DISTINCT FROM OLD.hash OR NEW.richiesta_da IS DISTINCT FROM OLD.richiesta_da
       OR NEW.creata_il IS DISTINCT FROM OLD.creata_il THEN
        RAISE EXCEPTION 'catena_foglie_interne: contenuto della foglia % non modificabile', OLD.id;
    END IF;
    IF OLD.indice IS NOT NULL AND (NEW.indice IS DISTINCT FROM OLD.indice
       OR NEW.sequenziata_il IS DISTINCT FROM OLD.sequenziata_il) THEN
        RAISE EXCEPTION 'catena_foglie_interne: la foglia % è già nel registro', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
"""

_REVOCA = """
DO $r$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@RUOLO@') THEN
        REVOKE UPDATE, DELETE ON public.audit_esportazioni FROM @RUOLO@;
        REVOKE DELETE ON public.catena_foglie_interne FROM @RUOLO@;
    END IF;
END
$r$;
""".replace("@RUOLO@", RUOLO_APPLICAZIONE)


def _lock_timeout() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def _limita_attesa_lock() -> None:
    """``lock_timeout`` per la transazione corrente (SET LOCAL)."""
    op.execute(f"SET LOCAL lock_timeout = '{_lock_timeout()}'")


def _convalida_fuori_dal_lock(tabella: str, *vincoli: str) -> None:
    """Chiude la transazione con i lock esclusivi, poi ``VALIDATE CONSTRAINT`` in
    autocommit, uno per transazione (revisione R05, R6)."""
    istruzioni = [f"ALTER TABLE public.{tabella} VALIDATE CONSTRAINT {v}" for v in vincoli]
    if context.is_offline_mode():
        for istruzione in istruzioni:
            op.execute(istruzione)
        return
    with op.get_context().autocommit_block():
        for istruzione in istruzioni:
            op.execute(istruzione)


def _gia_applicato(oggetto: str) -> bool:
    """Revisione R05 (S1): True se il segmento transazionale che crea
    ``oggetto`` è già stato confermato da una corsa precedente interrotta (il
    segmento è UNA transazione: o tutto o niente; ``alembic_version`` si
    aggiorna solo a fine revisione)."""
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def upgrade() -> None:
    _limita_attesa_lock()
    # Segmenti (revisione R05, S1): [1-3 catalogo] → commit → [VALIDATE in
    # autocommit, idempotenti] → [4 + versione, una transazione]. Il primo si
    # salta se una corsa interrotta l'ha già confermato.
    if not _gia_applicato("public.catena_foglie_interne"):
        _catalogo_foglie_coda_registro()
    _convalida_fuori_dal_lock("catena_coda", "ck_catena_coda_voce")
    _convalida_fuori_dal_lock("catena_registro", "ck_catena_registro_tipo",
                              "ck_catena_registro_pda")
    _limita_attesa_lock()
    _audit_esportazioni()


def _catalogo_foglie_coda_registro() -> None:
    # ── 1. Foglie interne ───────────────────────────────────────────────
    op.create_table(
        "catena_foglie_interne",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("chiave", sa.String(800), nullable=False),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT")),
        sa.Column("pda", sa.String(500)),
        sa.Column("pratica", sa.String(500)),
        sa.Column("riferimento", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("impronta", sa.String(64)),
        sa.Column("dati", sa.Text(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.Column("richiesta_da", sa.String(200), nullable=False),
        sa.Column("creata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("indice", sa.BigInteger()),
        sa.Column("sequenziata_il", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("chiave", name="uq_catena_foglie_interne_chiave"),
        sa.UniqueConstraint("indice", name="uq_catena_foglie_interne_indice"),
        sa.CheckConstraint(_in("tipo", TIPI_INTERNI), name="ck_catena_foglie_interne_tipo"),
        sa.CheckConstraint("(tipo = 'AUDIT') = (aip_id IS NULL)",
                           name="ck_catena_foglie_interne_pda"),
        sa.CheckConstraint("octet_length(hash) = 32", name="ck_catena_foglie_interne_hash"),
        sa.CheckConstraint("(indice IS NULL) = (sequenziata_il IS NULL)",
                           name="ck_catena_foglie_interne_sequenza"),
        schema="public",
    )
    op.create_index("idx_catena_foglie_interne_aip", "catena_foglie_interne",
                    ["aip_id", "indice"], schema="public",
                    postgresql_where=sa.text("aip_id IS NOT NULL"))
    op.execute(_PROTEZIONE_INTERNE)
    op.execute("""
        CREATE TRIGGER trg_catena_foglie_interne_protetta
        BEFORE UPDATE OR DELETE ON public.catena_foglie_interne
        FOR EACH ROW EXECUTE FUNCTION public.catena_foglie_interne_protetta();
    """)
    op.execute("""
        CREATE TRIGGER trg_catena_foglie_interne_no_truncate
        BEFORE TRUNCATE ON public.catena_foglie_interne
        FOR EACH STATEMENT EXECUTE FUNCTION public.catena_foglie_interne_protetta();
    """)

    # ── 2. Coda: PdA oppure foglia interna ──────────────────────────────
    op.alter_column("catena_coda", "aip_id", nullable=True, schema="public")
    op.add_column("catena_coda", sa.Column(
        "foglia_interna_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("public.catena_foglie_interne.id", ondelete="RESTRICT")),
        schema="public")
    op.create_unique_constraint("uq_catena_coda_foglia_interna", "catena_coda",
                                ["foglia_interna_id"], schema="public")
    op.execute("ALTER TABLE public.catena_coda ADD CONSTRAINT ck_catena_coda_voce "
               "CHECK ((aip_id IS NULL) <> (foglia_interna_id IS NULL)) NOT VALID")

    # ── 3. Registro: tipi nuovi, PdA solo per le foglie dei PdA ─────────
    # Solo catalogo sotto il lock esclusivo (nessuna scansione), poi commit
    op.alter_column("catena_registro", "aip_id", nullable=True, schema="public")
    op.alter_column("catena_registro", "tenant_id", nullable=True, schema="public")
    op.drop_constraint("ck_catena_registro_tipo", "catena_registro", schema="public")
    op.execute("ALTER TABLE public.catena_registro ADD CONSTRAINT ck_catena_registro_tipo "
               f"CHECK ({_in('tipo', TIPI_REGISTRO)}) NOT VALID")
    op.execute("ALTER TABLE public.catena_registro ADD CONSTRAINT ck_catena_registro_pda "
               f"CHECK (({_in('tipo', TIPI_PDA)}) = (aip_id IS NOT NULL) "
               "AND (tipo = 'AUDIT' OR tenant_id IS NOT NULL)) NOT VALID")


def _audit_esportazioni() -> None:
    # ── 4. Export dell'audit nel WORM ───────────────────────────────────
    op.create_table(
        "audit_esportazioni",
        sa.Column("periodo_da", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("periodo_a", sa.DateTime(timezone=True), nullable=False),
        sa.Column("righe", sa.BigInteger(), nullable=False),
        sa.Column("radice_righe", sa.String(64), nullable=False),
        sa.Column("impronta_segmento", sa.String(64), nullable=False),
        sa.Column("impronta_precedente", sa.String(64)),
        sa.Column("worm_bucket", sa.String(100), nullable=False),
        sa.Column("worm_chiave", sa.String(500), nullable=False),
        sa.Column("foglia_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.catena_foglie_interne.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("esportato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("periodo_a", name="uq_audit_esportazioni_periodo_a"),
        sa.UniqueConstraint("impronta_segmento", name="uq_audit_esportazioni_impronta"),
        sa.UniqueConstraint("worm_chiave", name="uq_audit_esportazioni_chiave"),
        sa.CheckConstraint("periodo_a > periodo_da", name="ck_audit_esportazioni_periodo"),
        sa.CheckConstraint("righe >= 0", name="ck_audit_esportazioni_righe"),
        schema="public",
    )
    op.execute("""
        CREATE TRIGGER trg_audit_esportazioni_append_only
        BEFORE UPDATE OR DELETE ON public.audit_esportazioni
        FOR EACH ROW EXECUTE FUNCTION public.catena_append_only();
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_esportazioni_no_truncate
        BEFORE TRUNCATE ON public.audit_esportazioni
        FOR EACH STATEMENT EXECUTE FUNCTION public.catena_append_only();
    """)
    op.execute(_REVOCA)


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.catena_registro
                       WHERE tipo NOT IN ('PDA', 'RIVERSAMENTO')) THEN
                RAISE EXCEPTION 'downgrade 20261023_039 impossibile: il registro contiene '
                    'foglie interne (append-only)';
            END IF;
        END
        $d$;
    """)
    op.drop_table("audit_esportazioni", schema="public")
    op.execute("ALTER TABLE public.catena_registro DROP CONSTRAINT ck_catena_registro_pda")
    op.execute("ALTER TABLE public.catena_registro DROP CONSTRAINT ck_catena_registro_tipo")
    op.execute("ALTER TABLE public.catena_registro ADD CONSTRAINT ck_catena_registro_tipo "
               f"CHECK ({_in('tipo', TIPI_PDA)})")
    op.alter_column("catena_registro", "tenant_id", nullable=False, schema="public")
    op.alter_column("catena_registro", "aip_id", nullable=False, schema="public")
    op.execute("DELETE FROM public.catena_coda WHERE foglia_interna_id IS NOT NULL")
    op.execute("ALTER TABLE public.catena_coda DROP CONSTRAINT ck_catena_coda_voce")
    op.drop_constraint("uq_catena_coda_foglia_interna", "catena_coda", schema="public")
    op.drop_column("catena_coda", "foglia_interna_id", schema="public")
    op.alter_column("catena_coda", "aip_id", nullable=False, schema="public")
    op.drop_index("idx_catena_foglie_interne_aip", table_name="catena_foglie_interne",
                  schema="public")
    op.execute("DROP TRIGGER IF EXISTS trg_catena_foglie_interne_no_truncate "
               "ON public.catena_foglie_interne")
    op.execute("DROP TRIGGER IF EXISTS trg_catena_foglie_interne_protetta "
               "ON public.catena_foglie_interne")
    op.drop_table("catena_foglie_interne", schema="public")
    op.execute("DROP FUNCTION IF EXISTS public.catena_foglie_interne_protetta()")
