# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Allineamento dello schema ai modelli condivisi (R03, VD-C-007)

Revision ID: 20261020_010
Revises: 20260320_009
Create Date: 2026-10-20

Dopo questa revisione `alembic upgrade head` su database vuoto produce
esattamente lo schema dei modelli in libs/shared/models (verificato dal test
services/gateway/tests/test_schema_alembic.py con `alembic check`).

Contenuto:
  - tabella metadati_allegato5 con il tipo public.modalita_formazione
    (valori a/b/c/d dell'Allegato 5, LG AgID §2.1.1 — VD-C-037)
  - tabella pdnd_fruitori (client_id PDND → cliente, VD-C-021)
  - fascicoli per cliente: tenant_id, tipo, anno, responsabile, codice unico
    per cliente, date come DATE (VD-C-026); fascicoli_aip.ruolo/note
  - notifications.tenant_id NOT NULL con FK; stato di default 'PENDING'
  - rsc_nominations.data_revoca e una sola nomina attiva per cliente (VD-C-025)
  - tenants: tolta storage_percentuale (valore calcolato, mai letto dal DB)
  - FK verso PdV/PdA/documenti in RESTRICT (REGOLE §10.4); FK tenant su pdv e aip
  - NOT NULL, lunghezze, default e indici allineati ai modelli
  - trigger updated_at sulle tabelle che hanno la colonna

Ogni NOT NULL nuovo su dati esistenti: le colonne con un default tecnico
(date di sistema, JSON vuoti) si riempiono; le colonne di contenuto si
controllano prima e la migrazione si ferma con il conteggio (REGOLE §16.5).
Le migrazioni non toccano contenuti conservati (§10.6).

Valori enum scritti qui in chiaro (le migrazioni sono storia congelata): il
test di allineamento confronta pg_enum con gli enum Python dei modelli.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_010"
down_revision = "20260320_009"
branch_labels = None
depends_on = None

# Valori di public.modalita_formazione = libs.shared.models.ModalitaFormazione
_MODALITA_FORMAZIONE = ("a", "b", "c", "d")

# Tabelle con updated_at che non avevano il trigger (tenants e users lo hanno da 001)
_TABELLE_UPDATED_AT = (
    "classi_documentali", "aip", "fascicoli", "notifications", "metadati_allegato5",
)


def _ferma_se_righe(select_sql: str, messaggio: str) -> None:
    """Interrompe la migrazione se la SELECT restituisce righe (§16.5)."""
    # Nomi e SQL sono costanti di questa migrazione, non input esterni
    blocco = (
        "DO $$ DECLARE n bigint; BEGIN "  # noqa: S608 — SQL costante della migrazione
        f"SELECT count(*) INTO n FROM ({select_sql}) AS q; "
        "IF n > 0 THEN RAISE EXCEPTION "
        "'Migrazione 20261020_010 interrotta: % (% righe)', "
        f"'{messaggio}', n; END IF; END $$;"
    )
    op.execute(blocco)


def _riempi_e_not_null(tabella: str, colonna: str, valore_sql: str) -> None:
    op.execute(
        f"UPDATE public.{tabella} SET {colonna} = {valore_sql} "  # noqa: S608 — costanti
        f"WHERE {colonna} IS NULL"
    )
    op.alter_column(tabella, colonna, nullable=False, schema="public")


def _not_null_controllato(tabella: str, colonna: str) -> None:
    _ferma_se_righe(
        f"SELECT 1 FROM public.{tabella} WHERE {colonna} IS NULL",  # noqa: S608 — costanti
        f"{tabella}.{colonna} contiene valori NULL da correggere a mano",
    )
    op.alter_column(tabella, colonna, nullable=False, schema="public")


def _rifai_fk(tabella: str, nome: str, colonna: str, riferita: str, ondelete: str) -> None:
    op.drop_constraint(nome, tabella, type_="foreignkey", schema="public")
    op.create_foreign_key(
        nome, tabella, riferita, [colonna], ["id"],
        source_schema="public", referent_schema="public", ondelete=ondelete,
    )


def upgrade() -> None:
    # ── tenants ──────────────────────────────────────────────────
    _riempi_e_not_null("tenants", "metadata_json", "'{}'::jsonb")
    op.drop_column("tenants", "storage_percentuale", schema="public")

    # ── audit_global ─────────────────────────────────────────────
    _riempi_e_not_null("audit_global", "dettagli", "'{}'::jsonb")

    # ── classi_documentali ───────────────────────────────────────
    _riempi_e_not_null("classi_documentali", "metadati_obbligatori", "'[]'::jsonb")
    _riempi_e_not_null("classi_documentali", "formati_ammessi", "'[]'::jsonb")
    _riempi_e_not_null("classi_documentali", "max_dimensione_mb", "100")
    _riempi_e_not_null("classi_documentali", "created_at", "now()")
    _riempi_e_not_null("classi_documentali", "updated_at", "now()")
    op.create_index("idx_classi_codice", "classi_documentali", ["codice"], schema="public")

    # ── pdv ──────────────────────────────────────────────────────
    for col, val in (
        ("data_ricezione", "now()"), ("data_aggiornamento", "now()"),
        ("metadata_json", "'{}'::jsonb"), ("errori_validazione", "'[]'::jsonb"),
        ("created_at", "now()"),
    ):
        _riempi_e_not_null("pdv", col, val)
    _rifai_fk("pdv", "pdv_classe_doc_id_fkey", "classe_doc_id", "classi_documentali", "RESTRICT")
    _ferma_se_righe(
        "SELECT 1 FROM public.pdv p LEFT JOIN public.tenants t ON t.id = p.tenant_id "
        "WHERE t.id IS NULL",
        "pdv con tenant_id inesistente",
    )
    op.create_foreign_key(
        "fk_pdv_tenant", "pdv", "tenants", ["tenant_id"], ["id"],
        source_schema="public", referent_schema="public", ondelete="RESTRICT",
    )

    # ── aip ──────────────────────────────────────────────────────
    op.alter_column("aip", "urn", type_=sa.String(500), schema="public")
    for col in ("urn", "s3_key", "s3_bucket", "hash_sha256"):
        _not_null_controllato("aip", col)
    for col, val in (
        ("storage_tier", "'HOT'"), ("dimensione_bytes", "0"),
        ("data_versamento", "now()"), ("anni_conservazione", "10"),
        ("created_at", "now()"), ("updated_at", "now()"),
    ):
        _riempi_e_not_null("aip", col, val)
    _rifai_fk("aip", "aip_pdv_id_fkey", "pdv_id", "pdv", "RESTRICT")
    _rifai_fk("aip", "aip_hashchain_block_id_fkey", "hashchain_block_id",
              "hashchain_blocks", "RESTRICT")
    _ferma_se_righe(
        "SELECT 1 FROM public.aip a LEFT JOIN public.tenants t ON t.id = a.tenant_id "
        "WHERE t.id IS NULL",
        "aip con tenant_id inesistente",
    )
    op.create_foreign_key(
        "fk_aip_tenant", "aip", "tenants", ["tenant_id"], ["id"],
        source_schema="public", referent_schema="public", ondelete="RESTRICT",
    )

    # ── documenti ────────────────────────────────────────────────
    for col in ("formato_mime", "hash_sha256", "s3_key", "dimensione_bytes"):
        _not_null_controllato("documenti", col)
    op.alter_column("documenti", "dimensione_bytes", server_default=None, schema="public")
    _riempi_e_not_null("documenti", "ordine", "0")
    _riempi_e_not_null("documenti", "created_at", "now()")
    op.create_index("idx_documenti_aip", "documenti", ["aip_id"], schema="public")
    _rifai_fk("documenti", "documenti_aip_id_fkey", "aip_id", "aip", "RESTRICT")

    # ── metadati_allegato5 (prima volta nelle migrazioni) ────────
    modalita = postgresql.ENUM(*_MODALITA_FORMAZIONE, name="modalita_formazione", schema="public")
    modalita.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "metadati_allegato5",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("documento_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identificativo_documento", sa.String(255), nullable=False),
        sa.Column("modalita_formazione",
                  postgresql.ENUM(*_MODALITA_FORMAZIONE, name="modalita_formazione",
                                  schema="public", create_type=False),
                  nullable=False),
        sa.Column("tipologia_documentale", sa.String(100), nullable=False),
        sa.Column("data_documento", sa.Date(), nullable=False),
        sa.Column("soggetto_produttore_id", sa.String(255), nullable=False),
        sa.Column("soggetto_produttore_nome", sa.String(500), nullable=False),
        sa.Column("soggetto_destinatario", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("oggetto", sa.String(1000), nullable=False),
        sa.Column("lingua", sa.String(10), nullable=False, server_default=sa.text("'it'")),
        sa.Column("classificazione", sa.String(500)),
        sa.Column("fascicolo_id", sa.String(255)),
        sa.Column("stato_conservazione", sa.String(100), nullable=False,
                  server_default=sa.text("'VERSATO'")),
        sa.Column("formato", sa.String(100), nullable=False),
        sa.Column("versione_documento", sa.String(20), server_default=sa.text("'1.0'")),
        sa.Column("numero_allegati", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tipo_firma", sa.String(100)),
        sa.Column("numero_registrazione", sa.String(100)),
        sa.Column("data_registrazione", sa.Date()),
        sa.Column("numero_progressivo", sa.String(50)),
        sa.Column("codice_ipa", sa.String(50)),
        sa.Column("aoo", sa.String(100)),
        sa.Column("codice_ruo", sa.String(50)),
        sa.Column("metadati_extra", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["documento_id"], ["public.documenti.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("documento_id", name="metadati_allegato5_documento_id_key"),
        schema="public",
    )

    # ── lifecycle_events ─────────────────────────────────────────
    _riempi_e_not_null("lifecycle_events", "dettagli", "'{}'::jsonb")
    _riempi_e_not_null("lifecycle_events", "created_at", "now()")
    _ferma_se_righe(
        "SELECT 1 FROM public.lifecycle_events WHERE ip_address IS NOT NULL "
        "AND ip_address !~ '^[0-9a-fA-F:./]+$'",
        "lifecycle_events.ip_address non convertibile in INET",
    )
    op.alter_column(
        "lifecycle_events", "ip_address", type_=postgresql.INET(),
        postgresql_using="ip_address::inet", schema="public",
    )
    op.create_index("idx_lifecycle_aip", "lifecycle_events", ["aip_id"], schema="public")
    _rifai_fk("lifecycle_events", "lifecycle_events_aip_id_fkey", "aip_id", "aip", "RESTRICT")

    # ── fascicoli (per cliente, VD-C-026) ────────────────────────
    op.add_column("fascicoli", sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
                  schema="public")
    _not_null_controllato("fascicoli", "tenant_id")  # nessun fascicolo senza cliente
    op.create_foreign_key(
        "fk_fascicoli_tenant", "fascicoli", "tenants", ["tenant_id"], ["id"],
        source_schema="public", referent_schema="public", ondelete="RESTRICT",
    )
    op.create_index("idx_fascicoli_tenant", "fascicoli", ["tenant_id"], schema="public")
    op.add_column("fascicoli", sa.Column("tipo", sa.String(30), nullable=False,
                                         server_default=sa.text("'GENERICO'")), schema="public")
    op.add_column("fascicoli", sa.Column("anno", sa.Integer()), schema="public")
    op.add_column("fascicoli", sa.Column("responsabile_id", postgresql.UUID(as_uuid=True)),
                  schema="public")
    op.create_foreign_key(
        "fk_fascicoli_responsabile", "fascicoli", "users", ["responsabile_id"], ["id"],
        source_schema="public", referent_schema="public", ondelete="SET NULL",
    )
    op.alter_column("fascicoli", "codice", type_=sa.String(255), schema="public")
    _not_null_controllato("fascicoli", "codice")
    _ferma_se_righe(
        "SELECT tenant_id, codice FROM public.fascicoli GROUP BY tenant_id, codice "
        "HAVING count(*) > 1",
        "fascicoli con codice duplicato nello stesso cliente",
    )
    op.create_unique_constraint(
        "uq_fascicoli_tenant_codice", "fascicoli", ["tenant_id", "codice"], schema="public",
    )
    op.alter_column("fascicoli", "data_apertura", server_default=None, schema="public")
    op.alter_column(
        "fascicoli", "data_apertura", type_=sa.Date(),
        postgresql_using="(data_apertura AT TIME ZONE 'Europe/Rome')::date", schema="public",
    )
    _riempi_e_not_null("fascicoli", "data_apertura", "CURRENT_DATE")
    op.alter_column("fascicoli", "data_apertura", server_default=sa.text("CURRENT_DATE"),
                    schema="public")
    op.alter_column(
        "fascicoli", "data_chiusura", type_=sa.Date(),
        postgresql_using="(data_chiusura AT TIME ZONE 'Europe/Rome')::date", schema="public",
    )
    for col, val in (
        ("stato", "'APERTO'"), ("metadati", "'{}'::jsonb"),
        ("created_at", "now()"), ("updated_at", "now()"),
    ):
        _riempi_e_not_null("fascicoli", col, val)
    _rifai_fk("fascicoli", "fascicoli_classe_doc_id_fkey", "classe_doc_id",
              "classi_documentali", "RESTRICT")

    # ── fascicoli_aip ────────────────────────────────────────────
    _riempi_e_not_null("fascicoli_aip", "data_inserimento", "now()")
    op.add_column("fascicoli_aip", sa.Column("ruolo", sa.String(50), nullable=False,
                                             server_default=sa.text("'DOCUMENTO'")),
                  schema="public")
    op.add_column("fascicoli_aip", sa.Column("note", sa.Text()), schema="public")
    _rifai_fk("fascicoli_aip", "fascicoli_aip_fascicolo_id_fkey", "fascicolo_id",
              "fascicoli", "CASCADE")
    _rifai_fk("fascicoli_aip", "fascicoli_aip_aip_id_fkey", "aip_id", "aip", "RESTRICT")

    # ── notifications ────────────────────────────────────────────
    op.execute(
        "UPDATE public.notifications n SET tenant_id = a.tenant_id "
        "FROM public.aip a WHERE n.aip_id = a.id AND n.tenant_id IS NULL"
    )
    _not_null_controllato("notifications", "tenant_id")
    op.create_foreign_key(
        "fk_notifications_tenant", "notifications", "tenants", ["tenant_id"], ["id"],
        source_schema="public", referent_schema="public", ondelete="RESTRICT",
    )
    op.create_index("idx_notifications_tenant", "notifications", ["tenant_id"], schema="public")
    _riempi_e_not_null("notifications", "payload_json", "'{}'::jsonb")
    op.alter_column("notifications", "stato", server_default=sa.text("'PENDING'"), schema="public")
    op.execute("UPDATE public.notifications SET stato = 'PENDING' WHERE stato = 'DA_INVIARE'")
    _riempi_e_not_null("notifications", "stato", "'PENDING'")
    _riempi_e_not_null("notifications", "tentativi", "0")
    _riempi_e_not_null("notifications", "created_at", "now()")
    _riempi_e_not_null("notifications", "updated_at", "now()")
    _rifai_fk("notifications", "notifications_aip_id_fkey", "aip_id", "aip", "RESTRICT")

    # ── rsc_nominations ──────────────────────────────────────────
    op.add_column("rsc_nominations", sa.Column("data_revoca", sa.Date()), schema="public")
    _ferma_se_righe(
        "SELECT tenant_id FROM public.rsc_nominations WHERE attiva "
        "GROUP BY tenant_id HAVING count(*) > 1",
        "clienti con più di una nomina attiva del responsabile della conservazione",
    )
    op.create_index(
        "uq_rsc_attiva_per_tenant", "rsc_nominations", ["tenant_id"], unique=True,
        postgresql_where=sa.text("attiva"), schema="public",
    )

    # ── pdnd_fruitori (VD-C-021) ─────────────────────────────────
    op.create_table(
        "pdnd_fruitori",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("descrizione", sa.String(500)),
        sa.Column("attivo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT",
                                name="fk_pdnd_fruitori_tenant"),
        sa.UniqueConstraint("client_id", name="pdnd_fruitori_client_id_key"),
        schema="public",
    )
    op.create_index("idx_pdnd_fruitori_tenant", "pdnd_fruitori", ["tenant_id"], schema="public")

    # ── trigger updated_at ───────────────────────────────────────
    for tabella in _TABELLE_UPDATED_AT:
        op.execute(
            f"CREATE TRIGGER {tabella}_updated_at BEFORE UPDATE ON public.{tabella} "
            "FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()"
        )


def downgrade() -> None:
    for tabella in _TABELLE_UPDATED_AT:
        op.execute(f"DROP TRIGGER IF EXISTS {tabella}_updated_at ON public.{tabella}")

    op.drop_index("idx_pdnd_fruitori_tenant", table_name="pdnd_fruitori", schema="public")
    op.drop_table("pdnd_fruitori", schema="public")

    op.drop_index("uq_rsc_attiva_per_tenant", table_name="rsc_nominations", schema="public")
    op.drop_column("rsc_nominations", "data_revoca", schema="public")

    _rifai_fk("notifications", "notifications_aip_id_fkey", "aip_id", "aip", "NO ACTION")
    op.alter_column("notifications", "stato", server_default=sa.text("'DA_INVIARE'"),
                    schema="public")
    op.drop_index("idx_notifications_tenant", table_name="notifications", schema="public")
    op.drop_constraint("fk_notifications_tenant", "notifications", type_="foreignkey",
                       schema="public")
    op.alter_column("notifications", "tenant_id", nullable=True, schema="public")

    _rifai_fk("fascicoli_aip", "fascicoli_aip_aip_id_fkey", "aip_id", "aip", "NO ACTION")
    _rifai_fk("fascicoli_aip", "fascicoli_aip_fascicolo_id_fkey", "fascicolo_id",
              "fascicoli", "NO ACTION")
    op.drop_column("fascicoli_aip", "note", schema="public")
    op.drop_column("fascicoli_aip", "ruolo", schema="public")

    _rifai_fk("fascicoli", "fascicoli_classe_doc_id_fkey", "classe_doc_id",
              "classi_documentali", "NO ACTION")
    op.alter_column("fascicoli", "data_apertura", server_default=None, schema="public")
    op.alter_column("fascicoli", "data_apertura", type_=sa.DateTime(timezone=True),
                    nullable=True, schema="public")
    op.alter_column("fascicoli", "data_apertura", server_default=sa.text("now()"),
                    schema="public")
    op.alter_column("fascicoli", "data_chiusura", type_=sa.DateTime(timezone=True),
                    schema="public")
    op.drop_constraint("uq_fascicoli_tenant_codice", "fascicoli", type_="unique", schema="public")
    op.alter_column("fascicoli", "codice", type_=sa.String(100), nullable=True, schema="public")
    op.drop_constraint("fk_fascicoli_responsabile", "fascicoli", type_="foreignkey",
                       schema="public")
    op.drop_column("fascicoli", "responsabile_id", schema="public")
    op.drop_column("fascicoli", "anno", schema="public")
    op.drop_column("fascicoli", "tipo", schema="public")
    op.drop_index("idx_fascicoli_tenant", table_name="fascicoli", schema="public")
    op.drop_constraint("fk_fascicoli_tenant", "fascicoli", type_="foreignkey", schema="public")
    op.drop_column("fascicoli", "tenant_id", schema="public")

    _rifai_fk("lifecycle_events", "lifecycle_events_aip_id_fkey", "aip_id", "aip", "NO ACTION")
    op.drop_index("idx_lifecycle_aip", table_name="lifecycle_events", schema="public")
    op.alter_column("lifecycle_events", "ip_address", type_=sa.String(45),
                    postgresql_using="host(ip_address)", schema="public")

    op.drop_table("metadati_allegato5", schema="public")
    op.execute("DROP TYPE IF EXISTS public.modalita_formazione")

    _rifai_fk("documenti", "documenti_aip_id_fkey", "aip_id", "aip", "NO ACTION")
    op.drop_index("idx_documenti_aip", table_name="documenti", schema="public")
    op.alter_column("documenti", "dimensione_bytes", server_default=sa.text("0"),
                    schema="public")

    op.drop_constraint("fk_aip_tenant", "aip", type_="foreignkey", schema="public")
    _rifai_fk("aip", "aip_hashchain_block_id_fkey", "hashchain_block_id",
              "hashchain_blocks", "NO ACTION")
    _rifai_fk("aip", "aip_pdv_id_fkey", "pdv_id", "pdv", "NO ACTION")

    op.drop_constraint("fk_pdv_tenant", "pdv", type_="foreignkey", schema="public")
    _rifai_fk("pdv", "pdv_classe_doc_id_fkey", "classe_doc_id", "classi_documentali", "NO ACTION")

    op.drop_index("idx_classi_codice", table_name="classi_documentali", schema="public")

    op.add_column(
        "tenants",
        sa.Column("storage_percentuale", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        schema="public",
    )
    # NOT NULL aggiunti dall'upgrade: lasciati (rimuoverli non ripristina dati e
    # non cambia il comportamento delle revisioni precedenti).
