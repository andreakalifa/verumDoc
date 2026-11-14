# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Code di scarto consultabili, idempotenza generica, scadenza dello staging, cursori (R04 servizi)

Revision ID: 20261023_029
Revises: 20261021_024
Create Date: 2026-10-23

Agente r04-servizi (ramo separato da 20261021_024: la migrazione di unione è
del coordinatore).

1. **VD-C-075 — code di scarto.** ``public.scarti``: una riga per messaggio
   finito in una coda di scarto Kafka (``pdv.received.dlq``,
   ``pdv.validated.dlq``, ``notification.dlq``) o per riga in errore definitivo
   di una coda in tabella (``indicizzazione_aip`` in ERRORE). La scrive il
   consumer delle code di scarto di dashboard-api
   (``python -m app.services.consumer_scarti``) in modo idempotente: chiave
   unica ``chiave_origine`` (``kafka:<topic>:<partizione>:<offset>`` o
   ``tabella:indicizzazione_aip:<aip_id>:<n>``), ``ON CONFLICT DO NOTHING``,
   offset confermato dopo il commit. Stato DA_GESTIRE → RIELABORATO |
   ARCHIVIATO con chi, quando e motivo (REGOLE §12.4, §15.7). Indice parziale
   ``idx_indicizzazione_aip_errore`` (prossimo_tentativo, aip_id) sulle sole
   righe in ERRORE di ``indicizzazione_aip``: il consumer le legge senza
   scorrere la tabella (una riga per PdA). ``tenant_id``,
   ``pdv_id`` e ``aip_id`` SENZA chiave esterna: un messaggio scartato può
   portare identificativi inesistenti (è proprio il motivo dello scarto).

2. **VD-C-106 — Idempotency-Key fuori dal versamento.**
   ``public.richieste_idempotenti``: (ente, ambito, chiave) → impronta della
   richiesta, stato (IN_CORSO | COMPLETATA), risorsa creata, scadenza. Usata
   da ``libs.shared.security.idempotenza`` per ``POST /api/v2/distribution/richiedi``
   e ``POST /api/v1/webhook``: stessa chiave + stessa richiesta → stessa
   risorsa (200), richiesta diversa → 409.

3. **VD-C-104 — scadenza dello staging dei PdV in ERRORE** (VD-P-36, 90 giorni):
   ``public.pdv.staging_avvisato_il`` (istante dell'avviso al cliente, evento
   ``versamento.staging_scaduto`` 7 giorni prima) e l'indice parziale
   ``idx_pdv_staging_scartati`` (data_aggiornamento, id) sui soli PdV in ERRORE
   con le copie di transito presenti: la pulizia legge un intervallo, mai la
   tabella intera.

4. **VD-C-102 — elenchi a cursore** senza OFFSET né COUNT:
   ``idx_audit_global_cursore`` (created_at DESC, id DESC),
   ``idx_audit_global_tenant_cursore`` (tenant_id, created_at DESC, id DESC),
   ``idx_notifications_tenant_cursore`` (tenant_id, created_at DESC, id DESC) e
   ``idx_notifications_pdd_cursore`` (stesso ordine, solo le righe PDD_* dello
   storico delle distribuzioni).

Indici su tabelle esistenti con CONCURRENTLY (``autocommit_block``), come le
migrazioni 020–024. ``audit_global`` può essere già PARTIZIONATA dalla 028
(r04-dati, ramo parallelo; l'ordine dei due rami dipende dall'unione): su una
tabella partizionata CONCURRENTLY non è ammesso («cannot create index on
partitioned table concurrently») e la 028 crea essa stessa i due indici del
cursore: la forma della tabella si legge PRIMA dell'``autocommit_block`` e, se
è partizionata, upgrade e downgrade non toccano quei due indici (appartengono
alla 028; correzione del coordinatore R04). Colonna con predefinito NULL: nessuna riscrittura.
Downgrade: elimina indici, colonna e tabelle nuove.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_029"
down_revision = "20261021_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. scarti ─────────────────────────────────────────────────────────
    op.create_table(
        "scarti",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("chiave_origine", sa.String(300), nullable=False),
        sa.Column("fonte", sa.String(20), nullable=False),
        sa.Column("coda", sa.String(100), nullable=False),
        sa.Column("topic_origine", sa.String(100)),
        sa.Column("partizione", sa.Integer()),
        sa.Column("posizione", sa.BigInteger()),
        sa.Column("servizio", sa.String(50), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("pdv_id", postgresql.UUID(as_uuid=True)),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True)),
        sa.Column("codice", sa.String(60)),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("tentativi", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("scartato_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messaggio", postgresql.JSONB(), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False,
                  server_default=sa.text("'DA_GESTIRE'")),
        sa.Column("gestito_il", sa.DateTime(timezone=True)),
        sa.Column("gestito_da", postgresql.UUID(as_uuid=True)),
        sa.Column("motivo_gestione", sa.Text()),
        sa.Column("rielaborazioni", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("registrato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("chiave_origine", name="uq_scarti_chiave_origine"),
        sa.CheckConstraint("fonte IN ('KAFKA', 'TABELLA')", name="ck_scarti_fonte"),
        sa.CheckConstraint("stato IN ('DA_GESTIRE', 'RIELABORATO', 'ARCHIVIATO')",
                           name="ck_scarti_stato"),
        schema="public",
    )
    op.create_index("idx_scarti_stato_cursore", "scarti", ["stato", sa.text("id DESC")],
                    schema="public")
    op.create_index("idx_scarti_coda_stato_cursore", "scarti",
                    ["coda", "stato", sa.text("id DESC")], schema="public")
    op.create_index("idx_scarti_tenant_stato_cursore", "scarti",
                    ["tenant_id", "stato", sa.text("id DESC")], schema="public")
    op.create_index("idx_scarti_pdv", "scarti", ["pdv_id"], schema="public",
                    postgresql_where=sa.text("pdv_id IS NOT NULL"))

    # ── 2. richieste_idempotenti ──────────────────────────────────────────
    op.create_table(
        "richieste_idempotenti",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.tenants.id", ondelete="RESTRICT",
                                name="fk_richieste_idempotenti_tenant"),
                  primary_key=True),
        sa.Column("ambito", sa.String(60), primary_key=True),
        sa.Column("chiave", sa.String(200), primary_key=True),
        sa.Column("impronta", sa.String(64), nullable=False),
        sa.Column("stato", sa.String(20), nullable=False),
        sa.Column("risorsa_id", sa.String(100)),
        sa.Column("creata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("aggiornata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("scade_il", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("stato IN ('IN_CORSO', 'COMPLETATA')",
                           name="ck_richieste_idempotenti_stato"),
        schema="public",
    )
    op.create_index("idx_richieste_idempotenti_scadenza", "richieste_idempotenti",
                    ["scade_il"], schema="public")

    # ── 3. pdv: avviso di scadenza dello staging dei PdV in ERRORE ─────────
    op.add_column("pdv", sa.Column("staging_avvisato_il", sa.DateTime(timezone=True),
                                   nullable=True), schema="public")

    # ── 3-4. indici su tabelle esistenti (fuori transazione) ──────────────
    # La forma di audit_global si legge PRIMA dell'autocommit_block (guardia
    # concordata con r04-dati): su tabella partizionata CONCURRENTLY non è
    # ammesso e i due indici del cursore li crea e possiede la 028.
    partizionata = _audit_partizionata()
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_indicizzazione_aip_errore "
            "ON public.indicizzazione_aip (prossimo_tentativo, aip_id) WHERE stato = 'ERRORE'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pdv_staging_scartati "
            "ON public.pdv (data_aggiornamento, id) "
            "WHERE stato = 'ERRORE' AND staging_eliminato_il IS NULL"
        )
        if not partizionata:
            modo = "" if partizionata is None else "CONCURRENTLY "
            op.execute(
                f"CREATE INDEX {modo}IF NOT EXISTS idx_audit_global_cursore "
                "ON public.audit_global (created_at DESC, id DESC)"
            )
            op.execute(
                f"CREATE INDEX {modo}IF NOT EXISTS idx_audit_global_tenant_cursore "
                "ON public.audit_global (tenant_id, created_at DESC, id DESC)"
            )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_tenant_cursore "
            "ON public.notifications (tenant_id, created_at DESC, id DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_pdd_cursore "
            "ON public.notifications (tenant_id, created_at DESC, id DESC) "
            "WHERE tipo LIKE 'PDD\\_%'"
        )


def _audit_partizionata() -> bool | None:
    """True se ``audit_global`` è già una tabella partizionata (migrazione 028).

    ``None`` in modalità offline (``alembic upgrade --sql``): senza database la
    forma non si conosce; l'upgrade allora scrive ``CREATE INDEX IF NOT EXISTS``
    senza CONCURRENTLY (valido su entrambe le forme: lo script offline è per la
    finestra di manutenzione) e il downgrade non tocca i due indici."""
    if op.get_context().as_sql:
        return None
    tipo = op.get_bind().execute(sa.text(
        "SELECT c.relkind::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = 'audit_global'")).scalar()
    return tipo == "p"


def downgrade() -> None:
    partizionata = _audit_partizionata()  # letta prima dell'autocommit_block
    with op.get_context().autocommit_block():
        indici = ["idx_notifications_pdd_cursore", "idx_notifications_tenant_cursore",
                  "idx_pdv_staging_scartati", "idx_indicizzazione_aip_errore"]
        if partizionata is False:
            indici += ["idx_audit_global_tenant_cursore", "idx_audit_global_cursore"]
        for indice in indici:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{indice}")
    op.drop_column("pdv", "staging_avvisato_il", schema="public")
    op.drop_index("idx_richieste_idempotenti_scadenza", table_name="richieste_idempotenti",
                  schema="public")
    op.drop_table("richieste_idempotenti", schema="public")
    for indice in ("idx_scarti_pdv", "idx_scarti_tenant_stato_cursore",
                   "idx_scarti_coda_stato_cursore", "idx_scarti_stato_cursore"):
        op.drop_index(indice, table_name="scarti", schema="public")
    op.drop_table("scarti", schema="public")
