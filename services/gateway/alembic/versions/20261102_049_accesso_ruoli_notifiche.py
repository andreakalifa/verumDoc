# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Accesso, ruoli e notifiche (R10: decisioni D08, D12, D30, D32)

Revision ID: 20261102_049
Revises: 20261101_048
Create Date: 2026-11-02

1. D08 ``fornitori_identita``: fornitori d'identità registrati dal conservatore
   (uno per tipo: SPID_CIE, PASSKEY, PASSWORD_TOTP); configurazione del
   servizio senza ente, fuori dalla Row Level Security; mai segreti.
2. D30 ``ruoli_personalizzati`` (RLS per ente): ruolo base OPERATORE o VIEWER e
   permessi; un vincolo esclude i permessi mai delegabili. ``users`` riceve
   ``ruolo_personalizzato_id`` (chiave esterna COMPOSTA con l'ente: un utente non
   prende il ruolo di un altro ente) e ``permessi_versione`` (la revoca dei
   token confronta la versione).
3. D08 credenziali locali: ``credenziali_password``, ``credenziali_totp`` (segreto
   cifrato), ``codici_recupero``, ``credenziali_passkey``. Tabelle di
   autenticazione (lette prima che l'ente sia noto): fuori dalla RLS, tolte ai
   privilegi di ``verumdoc_applicazione`` e concesse SOLO al gruppo
   ``verumdoc_credenziali_accesso`` (login del gateway): il server SFTP esposto
   e gli altri servizi non le leggono.
4. D08 ``inviti`` (RLS per ente): impronta del collegamento, scadenza, stato; un
   solo invito in attesa per utente (indice unico parziale).
5. D32 ``notifiche_preferenze`` e ``notifiche_letture`` (RLS per ente).
6. D12 ``sftp_account.ip_ammessi``: elenco degli indirizzi o reti ammessi.

Riprendibile: tabelle solo se assenti, ``ADD COLUMN IF NOT EXISTS``, vincoli e
chiavi esterne aggiunti ``NOT VALID`` e convalidati in autocommit, indice su
``users`` CONCURRENTLY fuori dalla transazione (un indice INVALID di un tentativo
interrotto si toglie e si ricrea), ``lock_timeout`` per i DDL esclusivi. In
modalità offline (``--sql``) tutto in transazione e senza CONCURRENTLY.

Downgrade: rifiutato se esistono ruoli personalizzati assegnati, credenziali
locali o inviti usati (dati di sicurezza che non si buttano in silenzio).
"""
# ruff: noqa: S608 — SQL composto solo con nomi costanti di questo modulo
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261102_049"
down_revision = "20261101_048"
branch_labels = None
depends_on = None

POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
TABELLE_RLS = ("ruoli_personalizzati", "inviti", "notifiche_preferenze", "notifiche_letture")
TABELLE_CREDENZIALI = ("credenziali_password", "credenziali_totp", "codici_recupero",
                       "credenziali_passkey")
RUOLO_CREDENZIALI = "verumdoc_credenziali_accesso"
RUOLO_APPLICAZIONE = "verumdoc_applicazione"
MAI_DELEGABILI = ("ARRAY['scarto:esegui', 'scarto:autorizza', 'annullamento:approva', "
                  "'firmatari:gestisci', 'pratica:chiudi_eccezioni', 'utenti:gestisci', "
                  "'ruoli:gestisci', 'accesso:configura']::text[]")
INDICE_UTENTI = "idx_users_ruolo_personalizzato"

_PRIVILEGI_CREDENZIALI = """
DO $p$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@CRED@') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE @CRED@ NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo @CRED@ non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;
    @REVOCA@
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@CRED@') THEN
        GRANT USAGE ON SCHEMA public TO @CRED@;
        @GRANT@
    END IF;
END
$p$;
""".replace("@CRED@", RUOLO_CREDENZIALI).replace("@REVOCA@", " ".join(
    f"REVOKE ALL ON public.{t} FROM PUBLIC; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
    f"'{RUOLO_APPLICAZIONE}') THEN REVOKE ALL ON public.{t} FROM {RUOLO_APPLICAZIONE}; END IF;"
    for t in TABELLE_CREDENZIALI)).replace("@GRANT@", " ".join(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{t} TO {RUOLO_CREDENZIALI};"
        for t in TABELLE_CREDENZIALI))


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _in_autocommit(*istruzioni: str) -> None:
    if context.is_offline_mode():
        # script SQL offline: tutto in transazione, quindi indici senza CONCURRENTLY
        for i in istruzioni:
            op.execute(i.replace(" CONCURRENTLY", ""))
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def _gia_applicato(oggetto: str) -> bool:
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def _vincolo(tabella: str, nome: str, definizione: str) -> None:
    """Vincolo tolto e rimesso ``NOT VALID`` (riprendibile); la convalida è in autocommit."""
    op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT IF EXISTS {nome}")
    op.execute(f"ALTER TABLE public.{tabella} ADD CONSTRAINT {nome} {definizione} NOT VALID")


def _indice_non_valido(nome: str) -> str:
    return (f"DO $i$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c "
            f"ON c.oid = i.indexrelid WHERE c.relname = '{nome}' AND NOT i.indisvalid) THEN "
            f"EXECUTE 'DROP INDEX CONCURRENTLY public.{nome}'; END IF; END $i$")


def _creato(nome: str = "created_at") -> sa.Column:
    return sa.Column(nome, sa.DateTime(timezone=True), nullable=False,
                     server_default=sa.text("now()"))


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                     server_default=sa.text("gen_random_uuid()"))


def _utente(pk: bool = False) -> sa.Column:
    return sa.Column("user_id", postgresql.UUID(as_uuid=True),
                     sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
                     primary_key=pk, nullable=False)


def _tabelle() -> None:
    if not _gia_applicato("public.fornitori_identita"):
        op.create_table(
            "fornitori_identita", _uuid_pk(),
            sa.Column("tipo", sa.String(30), nullable=False),
            sa.Column("nome", sa.String(200), nullable=False),
            sa.Column("attivo", sa.Boolean, nullable=False, server_default=sa.text("false")),
            sa.Column("configurazione", postgresql.JSONB, nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("versione", sa.Integer, nullable=False, server_default=sa.text("1")),
            sa.Column("aggiornato_da", postgresql.UUID(as_uuid=True)),
            _creato(), _creato("updated_at"),
            sa.UniqueConstraint("tipo", name="uq_fornitori_identita_tipo"),
            sa.CheckConstraint("tipo IN ('SPID_CIE', 'PASSKEY', 'PASSWORD_TOTP')",
                               name="ck_fornitori_identita_tipo"),
            sa.CheckConstraint("jsonb_typeof(configurazione) = 'object'",
                               name="ck_fornitori_identita_configurazione"),
            sa.CheckConstraint("versione >= 1", name="ck_fornitori_identita_versione"),
            schema="public")
    if not _gia_applicato("public.ruoli_personalizzati"):
        op.create_table(
            "ruoli_personalizzati", _uuid_pk(),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("nome", sa.String(100), nullable=False),
            sa.Column("descrizione", sa.String(500), nullable=False,
                      server_default=sa.text("''")),
            sa.Column("ruolo_base", sa.String(20), nullable=False),
            sa.Column("permessi", postgresql.ARRAY(sa.Text), nullable=False,
                      server_default=sa.text("'{}'::text[]")),
            sa.Column("attivo", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column("versione", sa.Integer, nullable=False, server_default=sa.text("1")),
            sa.Column("creato_da", postgresql.UUID(as_uuid=True)),
            _creato(), _creato("updated_at"),
            sa.UniqueConstraint("tenant_id", "nome", name="uq_ruoli_personalizzati_nome"),
            sa.UniqueConstraint("tenant_id", "id", name="uq_ruoli_personalizzati_tenant_id"),
            sa.CheckConstraint("ruolo_base IN ('OPERATORE', 'VIEWER')",
                               name="ck_ruoli_personalizzati_base"),
            sa.CheckConstraint(f"NOT (permessi && {MAI_DELEGABILI})",
                               name="ck_ruoli_personalizzati_non_delegabili"),
            sa.CheckConstraint("versione >= 1", name="ck_ruoli_personalizzati_versione"),
            schema="public")
    if not _gia_applicato("public.credenziali_password"):
        op.create_table(
            "credenziali_password", _utente(pk=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("impronta", sa.String(100), nullable=False),
            sa.Column("tentativi_falliti", sa.Integer, nullable=False,
                      server_default=sa.text("0")),
            sa.Column("bloccato_fino_a", sa.DateTime(timezone=True)),
            _creato("impostata_il"),
            sa.CheckConstraint("tentativi_falliti >= 0",
                               name="ck_credenziali_password_tentativi"),
            schema="public")
    if not _gia_applicato("public.credenziali_totp"):
        op.create_table(
            "credenziali_totp", _utente(pk=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("segreto_cifrato", sa.Text, nullable=False),
            sa.Column("ultimo_passo", sa.BigInteger, nullable=False,
                      server_default=sa.text("0")),
            _creato("confermato_il"),
            schema="public")
    if not _gia_applicato("public.codici_recupero"):
        op.create_table(
            "codici_recupero", _uuid_pk(), _utente(),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("impronta", sa.String(64), nullable=False),
            sa.Column("usato_il", sa.DateTime(timezone=True)),
            _creato(),
            sa.UniqueConstraint("user_id", "impronta", name="uq_codici_recupero"),
            sa.CheckConstraint("impronta ~ '^[0-9a-f]{64}$'",
                               name="ck_codici_recupero_impronta"),
            schema="public")
    if not _gia_applicato("public.credenziali_passkey"):
        op.create_table(
            "credenziali_passkey", _uuid_pk(), _utente(),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("credential_id", postgresql.BYTEA, nullable=False),
            sa.Column("chiave_pubblica", postgresql.BYTEA, nullable=False),
            sa.Column("contatore", sa.BigInteger, nullable=False, server_default=sa.text("0")),
            sa.Column("nome", sa.String(100), nullable=False,
                      server_default=sa.text("'Passkey'")),
            _creato("creata_il"),
            sa.Column("ultimo_uso", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("credential_id", name="uq_credenziali_passkey_credential"),
            sa.CheckConstraint("contatore >= 0", name="ck_credenziali_passkey_contatore"),
            schema="public")
        op.create_index("idx_credenziali_passkey_utente", "credenziali_passkey", ["user_id"],
                        schema="public")
    if not _gia_applicato("public.inviti"):
        op.create_table(
            "inviti", _uuid_pk(),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False),
            _utente(),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("impronta", sa.String(64), nullable=False),
            sa.Column("stato", sa.String(20), nullable=False,
                      server_default=sa.text("'IN_ATTESA'")),
            sa.Column("scade_il", sa.DateTime(timezone=True), nullable=False),
            sa.Column("usato_il", sa.DateTime(timezone=True)),
            sa.Column("metodo_attivato", sa.String(30)),
            sa.Column("tentativi_errati", sa.Integer, nullable=False,
                      server_default=sa.text("0")),
            sa.Column("creato_da", postgresql.UUID(as_uuid=True)),
            _creato(),
            sa.UniqueConstraint("impronta", name="uq_inviti_impronta"),
            sa.CheckConstraint("stato IN ('IN_ATTESA', 'USATO', 'REVOCATO')",
                               name="ck_inviti_stato"),
            sa.CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_inviti_impronta"),
            sa.CheckConstraint("tentativi_errati >= 0", name="ck_inviti_tentativi"),
            schema="public")
        op.create_index("uq_inviti_in_attesa", "inviti", ["user_id"], unique=True,
                        postgresql_where=sa.text("stato = 'IN_ATTESA'"), schema="public")
        op.create_index("idx_inviti_ente", "inviti", ["tenant_id", sa.text("created_at DESC")],
                        schema="public")
    if not _gia_applicato("public.notifiche_preferenze"):
        op.create_table(
            "notifiche_preferenze", _utente(pk=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("scelte", postgresql.JSONB, nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("versione", sa.Integer, nullable=False, server_default=sa.text("1")),
            _creato("updated_at"),
            sa.CheckConstraint("jsonb_typeof(scelte) = 'object' AND "
                               "octet_length(scelte::text) <= 4096",
                               name="ck_notifiche_preferenze_scelte"),
            sa.CheckConstraint("versione >= 1", name="ck_notifiche_preferenze_versione"),
            schema="public")
    if not _gia_applicato("public.notifiche_letture"):
        op.create_table(
            "notifiche_letture", _utente(pk=True),
            sa.Column("evento_id", sa.BigInteger, primary_key=True, autoincrement=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            _creato("letta_il"),
            schema="public")
        op.create_index("idx_notifiche_letture_ente", "notifiche_letture",
                        ["tenant_id", "letta_il"], schema="public")


def upgrade() -> None:
    _limita_attesa_lock()
    _tabelle()
    for tabella in TABELLE_RLS:
        op.execute(f"ALTER TABLE public.{tabella} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA} ON public.{tabella} AS PERMISSIVE FOR ALL "
                   f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    op.execute(_PRIVILEGI_CREDENZIALI)
    # 2. utenti: colonne (solo catalogo) e chiave esterna composta NOT VALID
    op.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS ruolo_personalizzato_id uuid")
    op.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS permessi_versione integer "
               "NOT NULL DEFAULT 1")
    _vincolo("users", "fk_users_ruolo_personalizzato",
             "FOREIGN KEY (tenant_id, ruolo_personalizzato_id) REFERENCES "
             "public.ruoli_personalizzati (tenant_id, id) ON DELETE RESTRICT")
    # 6. SFTP: elenco degli indirizzi ammessi
    op.execute("ALTER TABLE public.sftp_account ADD COLUMN IF NOT EXISTS ip_ammessi text[] "
               "NOT NULL DEFAULT '{}'::text[]")
    _vincolo("sftp_account", "ck_sftp_account_ip_ammessi",
             "CHECK (cardinality(ip_ammessi) <= 500)")
    _in_autocommit(
        "ALTER TABLE public.users VALIDATE CONSTRAINT fk_users_ruolo_personalizzato",
        "ALTER TABLE public.sftp_account VALIDATE CONSTRAINT ck_sftp_account_ip_ammessi",
        _indice_non_valido(INDICE_UTENTI),
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDICE_UTENTI} ON public.users "
        "(ruolo_personalizzato_id) WHERE ruolo_personalizzato_id IS NOT NULL")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.users WHERE ruolo_personalizzato_id IS NOT NULL) THEN
                RAISE EXCEPTION 'downgrade 20261102_049 impossibile: utenti con un ruolo '
                    'personalizzato (toglierlo prima)';
            END IF;
            IF EXISTS (SELECT 1 FROM public.credenziali_password)
               OR EXISTS (SELECT 1 FROM public.credenziali_passkey)
               OR EXISTS (SELECT 1 FROM public.inviti WHERE stato = 'USATO') THEN
                RAISE EXCEPTION 'downgrade 20261102_049 impossibile: esistono credenziali '
                    'locali o inviti usati';
            END IF;
        END
        $d$;
    """)
    op.execute("DROP INDEX IF EXISTS public.idx_users_ruolo_personalizzato")
    op.execute("ALTER TABLE public.users DROP CONSTRAINT IF EXISTS fk_users_ruolo_personalizzato")
    op.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS ruolo_personalizzato_id")
    op.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS permessi_versione")
    op.execute("ALTER TABLE public.sftp_account DROP CONSTRAINT IF EXISTS "
               "ck_sftp_account_ip_ammessi")
    op.execute("ALTER TABLE public.sftp_account DROP COLUMN IF EXISTS ip_ammessi")
    for tabella in ("notifiche_letture", "notifiche_preferenze", "inviti",
                    *TABELLE_CREDENZIALI, "ruoli_personalizzati", "fornitori_identita"):
        op.execute(f"DROP TABLE IF EXISTS public.{tabella}")
