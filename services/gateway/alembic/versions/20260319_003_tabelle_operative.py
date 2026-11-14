# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Tabelle operative: classi_documentali, pdv, aip, documenti, etc.

Revision ID: 20260319_003
Revises: 20260318_002
Create Date: 2026-03-19
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.dialects import postgresql

revision = "20260319_003"
down_revision = "20260318_002"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── Enum types (IF NOT EXISTS tramite DO block) ──────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE pdv_stato AS ENUM (
                'RICEVUTO','IN_VALIDAZIONE','VALIDATO',
                'IN_BUILD','CONSERVATO','ERRORE','ANNULLATO'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE aip_stato AS ENUM (
                'IN_BUILD','FIRMATO','TIMESTAMPATO','SU_STORAGE',
                'IN_CATENA','CONSERVATO','ESIBIZIONE','ELIMINAZIONE'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE storage_tier AS ENUM (
                'HOT','WARM','COLD','ARCHIVAL'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tipo_flusso AS ENUM (
                'ATTIVO','PASSIVO','INTERNO'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Definiamo i tipi Enum con create_type=False
    # (il tipo esiste già — creato sopra con DO block)
    pdv_stato   = postgresql.ENUM("RICEVUTO","IN_VALIDAZIONE","VALIDATO",
                                   "IN_BUILD","CONSERVATO","ERRORE","ANNULLATO",
                                   name="pdv_stato", create_type=False)
    aip_stato   = postgresql.ENUM("IN_BUILD","FIRMATO","TIMESTAMPATO","SU_STORAGE",
                                   "IN_CATENA","CONSERVATO","ESIBIZIONE","ELIMINAZIONE",
                                   name="aip_stato", create_type=False)
    storage_tier = postgresql.ENUM("HOT","WARM","COLD","ARCHIVAL",
                                    name="storage_tier", create_type=False)
    tipo_flusso  = postgresql.ENUM("ATTIVO","PASSIVO","INTERNO",
                                    name="tipo_flusso", create_type=False)

    # ── classi_documentali ───────────────────────────────────────
    op.create_table(
        "classi_documentali",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("codice", sa.String(50), nullable=False, unique=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("descrizione", sa.Text),
        sa.Column("anni_conservazione", sa.Integer, nullable=False, server_default="10"),
        sa.Column("conservazione_permanente", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("metadati_obbligatori", JSONB, server_default="[]"),
        sa.Column("formati_ammessi", JSONB, server_default="[]"),
        sa.Column("max_dimensione_mb", sa.Integer, server_default="100"),
        sa.Column("attiva", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )

    # Classi documentali di default
    op.execute("""
        INSERT INTO public.classi_documentali
            (id, codice, nome, anni_conservazione, formati_ammessi)
        VALUES
            (gen_random_uuid(),'fattura',        'Fattura',         10,
             '["application/pdf","application/pkcs7-mime","text/plain"]'),
            (gen_random_uuid(),'contratto',      'Contratto',       10,
             '["application/pdf","application/pkcs7-mime"]'),
            (gen_random_uuid(),'delibera',       'Delibera',        30,
             '["application/pdf","application/pkcs7-mime"]'),
            (gen_random_uuid(),'determinazione', 'Determinazione',  10,
             '["application/pdf","application/pkcs7-mime"]'),
            (gen_random_uuid(),'verbale',        'Verbale',         10,
             '["application/pdf","application/pkcs7-mime"]'),
            (gen_random_uuid(),'circolare',      'Circolare',       10,
             '["application/pdf"]'),
            (gen_random_uuid(),'decreto',        'Decreto',         30,
             '["application/pdf","application/pkcs7-mime"]'),
            (gen_random_uuid(),'altro',          'Altro documento', 10,
             '["application/pdf","application/xml","text/plain","image/tiff","image/jpeg","image/png"]')
        ON CONFLICT (codice) DO NOTHING;
    """)

    # ── pdv ──────────────────────────────────────────────────────
    op.create_table(
        "pdv",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("numero_protocollo", sa.String(100)),
        sa.Column("oggetto", sa.String(500), nullable=False),
        sa.Column("stato", pdv_stato, nullable=False, server_default="RICEVUTO"),
        sa.Column("classe_doc_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.classi_documentali.id"), nullable=False),
        sa.Column("tipo_flusso", tipo_flusso, nullable=False, server_default="ATTIVO"),
        sa.Column("data_ricezione", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("data_aggiornamento", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("metadata_json", JSONB, server_default="{}"),
        sa.Column("errori_validazione", JSONB, server_default="[]"),
        sa.Column("s3_prefix", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )
    op.create_index("idx_pdv_stato",    "pdv", ["stato"],         schema="public")
    op.create_index("idx_pdv_classe",   "pdv", ["classe_doc_id"], schema="public")
    op.create_index("idx_pdv_data_ric", "pdv", ["data_ricezione"], schema="public")

    # ── aip ──────────────────────────────────────────────────────
    op.create_table(
        "aip",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("pdv_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.pdv.id"), nullable=False),
        sa.Column("urn", sa.String(500), unique=True),
        sa.Column("stato", aip_stato, nullable=False, server_default="IN_BUILD"),
        sa.Column("storage_tier", storage_tier, server_default="HOT"),
        sa.Column("s3_key", sa.String(500)),
        sa.Column("s3_bucket", sa.String(100)),
        sa.Column("s3_version_id", sa.String(200)),
        sa.Column("sincro_xml_key", sa.String(500)),
        sa.Column("indice_xml_key", sa.String(500)),
        sa.Column("dimensione_bytes", sa.BigInteger, server_default="0"),
        sa.Column("hash_sha256", sa.String(64)),
        sa.Column("hash_sha512", sa.String(128)),
        sa.Column("firma_cades_ref", sa.String(500)),
        sa.Column("tsa_token_ref", sa.String(500)),
        sa.Column("tsa_timestamp", sa.DateTime(timezone=True)),
        sa.Column("hashchain_block_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.hashchain_blocks.id")),
        sa.Column("data_versamento", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("data_conservazione", sa.DateTime(timezone=True)),
        sa.Column("scadenza_conservazione", sa.DateTime(timezone=True)),
        sa.Column("anni_conservazione", sa.Integer, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )
    op.create_index("idx_aip_stato",  "aip", ["stato"],           schema="public")
    op.create_index("idx_aip_pdv",    "aip", ["pdv_id"],          schema="public")
    op.create_index("idx_aip_urn",    "aip", ["urn"],             schema="public")
    op.create_index("idx_aip_data_v", "aip", ["data_versamento"], schema="public")

    # ── documenti ────────────────────────────────────────────────
    op.create_table(
        "documenti",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("aip_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id"), nullable=False),
        sa.Column("nome_file", sa.String(500), nullable=False),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("formato_mime", sa.String(200)),
        sa.Column("formato_pronom", sa.String(100)),
        sa.Column("dimensione_bytes", sa.BigInteger, server_default="0"),
        sa.Column("hash_sha256", sa.String(64)),
        sa.Column("hash_sha512", sa.String(128)),
        sa.Column("s3_key", sa.String(500)),
        sa.Column("ordine", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )

    # ── lifecycle_events ─────────────────────────────────────────
    op.create_table(
        "lifecycle_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("aip_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id"), nullable=False),
        sa.Column("evento", sa.String(100), nullable=False),
        sa.Column("da_stato", sa.String(50)),
        sa.Column("a_stato", sa.String(50)),
        sa.Column("dettagli", JSONB, server_default="{}"),
        sa.Column("utente_id", UUID(as_uuid=True)),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )

    # ── fascicoli ────────────────────────────────────────────────
    op.create_table(
        "fascicoli",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("codice", sa.String(100)),
        sa.Column("titolo", sa.String(500), nullable=False),
        sa.Column("descrizione", sa.Text),
        sa.Column("classe_doc_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.classi_documentali.id")),
        sa.Column("data_apertura", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("data_chiusura", sa.DateTime(timezone=True)),
        sa.Column("stato", sa.String(50), server_default="APERTO"),
        sa.Column("metadati", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )

    # ── fascicoli_aip ────────────────────────────────────────────
    op.create_table(
        "fascicoli_aip",
        sa.Column("fascicolo_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.fascicoli.id"), primary_key=True),
        sa.Column("aip_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id"), primary_key=True),
        sa.Column("data_inserimento", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        schema="public",
    )

    # ── notifications ────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("aip_id", UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id")),
        sa.Column("tipo", sa.String(100), nullable=False),
        sa.Column("canale", sa.String(50), nullable=False),
        sa.Column("destinatario", sa.String(500), nullable=False),
        sa.Column("payload_json", JSONB, server_default="{}"),
        sa.Column("stato", sa.String(50), server_default="DA_INVIARE"),
        sa.Column("tentativi", sa.Integer, server_default="0"),
        sa.Column("prossimo_tentativo", sa.DateTime(timezone=True)),
        sa.Column("inviato_at", sa.DateTime(timezone=True)),
        sa.Column("errore", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("notifications",      schema="public")
    op.drop_table("fascicoli_aip",      schema="public")
    op.drop_table("fascicoli",          schema="public")
    op.drop_table("lifecycle_events",   schema="public")
    op.drop_table("documenti",          schema="public")
    op.drop_table("aip",                schema="public")
    op.drop_table("pdv",                schema="public")
    op.drop_table("classi_documentali", schema="public")
    op.execute("DROP TYPE IF EXISTS tipo_flusso;")
    op.execute("DROP TYPE IF EXISTS storage_tier;")
    op.execute("DROP TYPE IF EXISTS aip_stato;")
    op.execute("DROP TYPE IF EXISTS pdv_stato;")
