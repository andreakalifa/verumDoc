# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Correzioni della revisione indipendente di R04 (rev4)

Revision ID: 20261023_031
Revises: 20261023_030
Create Date: 2026-10-23

1. ``catena_checkpoint`` (rilievo 1): un checkpoint RIFIUTATO dal testimone è un
   allarme persistente finché un operatore non ne registra la gestione
   (``rifiuto_gestito_il``, ``rifiuto_gestito_da``, ``rifiuto_nota``); il
   trigger di protezione impedisce che un RIFIUTATO cambi stato (mai «superato»
   né «controfirmato» dopo) e che la gestione si scriva due volte o su un
   checkpoint non rifiutato.
2. Outbox (rilievo 2): ``outbox_chiavi_ferme`` — chiavi ferme per un errore del
   loro evento (in riprova con pausa crescente) o BLOCCATE dopo
   ``OUTBOX_MAX_TENTATIVI`` (``bloccato_il``): il relay esclude i loro eventi
   dalle letture normali, le altre chiavi proseguono; ``outbox_eventi.scartato_il``
   per un evento bloccato scartato da un operatore (code di scarto). L'indice
   per la sonda dei gruppi è nella 032 (CONCURRENTLY, fuori transazione).
3. «Rielabora» (rilievo 4): ``scarti_ripartenze`` — UNA ripartenza per PdV e
   per episodio d'errore (``errore_il`` = istante in cui il PdV è entrato in
   ERRORE): chiave primaria ``(pdv_id, errore_il)``; l'evento di ripartenza si
   accoda solo dopo l'INSERT riuscito, nella stessa transazione.
4. Manutenzione delle partizioni (rilievo 6): ``manutenzione_lease`` — una
   replica alla volta con un lease in tabella invece del lock consultivo di
   SESSIONE, che attraverso PgBouncer in modalità transaction non vale.
5. Audit e registri append-only (rilievo 5), due difese:
   a. ruoli separati: gruppo ``verumdoc_applicazione`` (NOLOGIN) con i soli
      privilegi DML sulle tabelle di ``public`` (e i privilegi predefiniti per
      le tabelle future); su ``audit_global``, ``verifiche_registro``,
      ``catena_pda``, ``catena_registro``, ``catena_nodi`` niente UPDATE né
      DELETE, su ``catena_checkpoint`` niente DELETE. I ruoli dei servizi ne
      sono membri e NON sono proprietari: non possono staccare, eliminare né
      disattivare trigger. Proprietario = ruolo delle migrazioni e della
      manutenzione (``DATABASE_URL_MANUTENZIONE``). Serve CREATEROLE; senza, la
      migrazione lo segnala e il gruppo va creato dall'amministratore;
   b. event trigger (anche contro il proprietario): ``sql_drop`` rifiuta il DROP
      di ``audit_global``, ``verifiche_registro``, delle loro partizioni e delle
      tabelle ``catena_*``; ``ddl_command_start`` rifiuta un ALTER TABLE che
      nomina quei registri con DETACH, DISABLE (trigger, RLS) o NO FORCE;
      ``ddl_command_end`` controlla dopo ogni ALTER TABLE che le partizioni dei
      registri siano tutte agganciate (nessun distacco, nemmeno in corso) e che
      i trigger append-only siano attivi. Creare un event trigger richiede un
      superutente (su RDS: ``rds_superuser``); senza, la migrazione lo
      segnala (stato onesto) e restano i ruoli separati.
6. Eventi per i clienti (rilievo 10): ``eventi_cliente.tenant_id`` ammette NULL
   = evento GLOBALE letto da tutti gli enti (``catena.checkpoint``: una riga
   per checkpoint invece di una per ente). Solo catalogo, nessuna riscrittura.
   Downgrade: gli eventi globali e le loro consegne si eliminano (prima della
   031 non esistevano), poi di nuovo NOT NULL.
"""
from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql  # noqa: F401 — sa.dialects.postgresql.UUID
from alembic import op

revision = "20261023_031"
down_revision = "20261023_030"
branch_labels = None
depends_on = None

# Funzione di protezione di catena_checkpoint (migrazione 025) con le regole
# nuove del rifiuto; ``{extra}`` = blocco aggiuntivo (vuoto nel downgrade).
_PROTEZIONE_CHECKPOINT = """
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
    {extra}
    RETURN NEW;
END;
$$;
"""

_REGOLE_RIFIUTO = """
    IF OLD.stato_testimone = 'RIFIUTATO' AND (
           NEW.stato_testimone IS DISTINCT FROM OLD.stato_testimone
           OR NEW.controfirma IS DISTINCT FROM OLD.controfirma
           OR NEW.esito_testimone IS DISTINCT FROM OLD.esito_testimone) THEN
        RAISE EXCEPTION 'catena_checkpoint: il checkpoint % è stato rifiutato dal testimone '
            'e resta tale', OLD.dimensione;
    END IF;
    IF (NEW.rifiuto_gestito_il IS DISTINCT FROM OLD.rifiuto_gestito_il
        OR NEW.rifiuto_gestito_da IS DISTINCT FROM OLD.rifiuto_gestito_da
        OR NEW.rifiuto_nota IS DISTINCT FROM OLD.rifiuto_nota)
       AND (OLD.rifiuto_gestito_il IS NOT NULL OR NEW.stato_testimone <> 'RIFIUTATO') THEN
        RAISE EXCEPTION 'catena_checkpoint: gestione del rifiuto del checkpoint % non ammessa',
            OLD.dimensione;
    END IF;
"""


def upgrade() -> None:
    _upgrade_catena()
    _upgrade_outbox()
    _upgrade_ripartenze()
    _upgrade_lease()
    _upgrade_eventi_globali()
    _upgrade_protezione_registri()


def downgrade() -> None:
    _downgrade_protezione_registri()
    _downgrade_eventi_globali()
    _downgrade_lease()
    _downgrade_ripartenze()
    _downgrade_outbox()
    _downgrade_catena()


# ── 1. Checkpoint rifiutati: allarme persistente ────────────────────────────

def _upgrade_catena() -> None:
    op.add_column("catena_checkpoint", sa.Column("rifiuto_gestito_il",
                                                 sa.DateTime(timezone=True)), schema="public")
    op.add_column("catena_checkpoint", sa.Column("rifiuto_gestito_da", sa.String(200)),
                  schema="public")
    op.add_column("catena_checkpoint", sa.Column("rifiuto_nota", sa.Text()), schema="public")
    op.execute(_PROTEZIONE_CHECKPOINT.replace("{extra}", _REGOLE_RIFIUTO))


def _downgrade_catena() -> None:
    op.execute(_PROTEZIONE_CHECKPOINT.replace("{extra}", ""))
    for colonna in ("rifiuto_nota", "rifiuto_gestito_da", "rifiuto_gestito_il"):
        op.drop_column("catena_checkpoint", colonna, schema="public")


# ── 2. Outbox: chiavi ferme e bloccate ──────────────────────────────────────

def _upgrade_outbox() -> None:
    # Colonna nullable senza predefinito: solo catalogo, nessuna riscrittura delle partizioni
    op.add_column("outbox_eventi", sa.Column("scartato_il", sa.DateTime(timezone=True)),
                  schema="public")
    op.create_table(
        "outbox_chiavi_ferme",
        sa.Column("origine", sa.String(40), primary_key=True),
        sa.Column("chiave", sa.String(200), primary_key=True),
        sa.Column("gruppo", sa.SmallInteger(), nullable=False),
        sa.Column("evento_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evento_creato_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequenza", sa.BigInteger(), nullable=False),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("tentativi", sa.Integer(), nullable=False),
        sa.Column("ultimo_errore", sa.Text()),
        sa.Column("prossimo_tentativo_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bloccato_il", sa.DateTime(timezone=True)),
        sa.Column("fermata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("gruppo BETWEEN 0 AND 63", name="ck_outbox_chiavi_ferme_gruppo"),
        schema="public",
    )
    op.create_index("uq_outbox_chiavi_ferme_evento", "outbox_chiavi_ferme", ["evento_id"],
                    unique=True, schema="public")
    op.create_index("idx_outbox_chiavi_ferme_riprova", "outbox_chiavi_ferme",
                    ["origine", "prossimo_tentativo_il"], schema="public",
                    postgresql_where=sa.text("bloccato_il IS NULL"))


def _downgrade_outbox() -> None:
    op.drop_table("outbox_chiavi_ferme", schema="public")
    op.drop_column("outbox_eventi", "scartato_il", schema="public")


# ── 3. Una ripartenza per PdV ───────────────────────────────────────────────

def _upgrade_ripartenze() -> None:
    op.create_table(
        "scarti_ripartenze",
        sa.Column("pdv_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pdv.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("errore_il", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("scarto_id", sa.BigInteger(),
                  sa.ForeignKey("public.scarti.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("creato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        schema="public",
    )


def _downgrade_ripartenze() -> None:
    op.drop_table("scarti_ripartenze", schema="public")


# ── 4. Lease della manutenzione ─────────────────────────────────────────────

def _upgrade_lease() -> None:
    op.create_table(
        "manutenzione_lease",
        sa.Column("nome", sa.String(100), primary_key=True),
        sa.Column("detentore", sa.String(200), nullable=False),
        sa.Column("scade_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aggiornato_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        schema="public",
    )


def _downgrade_lease() -> None:
    op.drop_table("manutenzione_lease", schema="public")


# ── 6. Eventi globali per i clienti ─────────────────────────────────────────

def _upgrade_eventi_globali() -> None:
    op.execute("ALTER TABLE public.eventi_cliente ALTER COLUMN tenant_id DROP NOT NULL")


def _downgrade_eventi_globali() -> None:
    op.execute("""DELETE FROM public.webhook_consegne c USING public.eventi_cliente e
                  WHERE e.id = c.evento_id AND e.creato_il = c.evento_creato_il
                    AND e.tenant_id IS NULL""")
    op.execute("DELETE FROM public.eventi_cliente WHERE tenant_id IS NULL")
    op.execute("ALTER TABLE public.eventi_cliente ALTER COLUMN tenant_id SET NOT NULL")


# ── 5. Registri append-only: ruoli separati ed event trigger ────────────────

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
#: Registri append-only: niente UPDATE né DELETE per il ruolo dei servizi
SOLO_AGGIUNTA = ("audit_global", "verifiche_registro", "catena_pda", "catena_registro",
                 "catena_nodi")
#: Tabelle che nessuno elimina (sql_drop): registri e catena
PROTETTE_DROP = ("audit_global", "verifiche_registro", "catena_pda", "catena_registro",
                 "catena_nodi", "catena_checkpoint")
#: Regole dei nomi delle partizioni dei registri (schema ``partizioni``)
REGEX_PARTIZIONI = "^(audit_global|verifiche_registro)_p[0-9]+$"
TRIGGER_APPEND_ONLY = ("trg_audit_global_append_only", "trg_audit_global_no_truncate",
                       "trg_verifiche_registro_append_only",
                       "trg_verifiche_registro_no_truncate")

_FUNZIONE_DROP = (
    """
CREATE OR REPLACE FUNCTION public.vd_protezione_registri_drop()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    o record;
BEGIN
    FOR o IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
        IF (o.schema_name = 'partizioni' AND o.object_name ~ '@REGEX@')
           OR (o.schema_name = 'public'
               AND o.object_name IN (@PROTETTE@)
               AND o.object_type = 'table') THEN
            RAISE EXCEPTION 'registro append-only: eliminazione di %.% vietata',
                o.schema_name, o.object_name USING ERRCODE = 'insufficient_privilege';
        END IF;
    END LOOP;
END;
$$;
""")

_FUNZIONE_ALTER = """
CREATE OR REPLACE FUNCTION public.vd_protezione_registri_alter()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    q text := lower(coalesce(current_query(), ''));
BEGIN
    IF q ~ '(audit_global|verifiche_registro)'
       AND q ~ '(detach|disable|no[[:space:]]+force)' THEN
        RAISE EXCEPTION 'registro append-only: DETACH, DISABLE e NO FORCE vietati '
            'su audit_global e verifiche_registro' USING ERRCODE = 'insufficient_privilege';
    END IF;
END;
$$;
"""

_FUNZIONE_STATO = (
    """
CREATE OR REPLACE FUNCTION public.vd_protezione_registri_stato()
RETURNS event_trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'partizioni' AND c.relkind = 'r'
                 AND c.relname ~ '@REGEX@' AND NOT c.relispartition)
       OR EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhdetachpending
                  AND i.inhparent IN (to_regclass('public.audit_global'),
                                      to_regclass('public.verifiche_registro')))
       OR EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgenabled = 'D'
                  AND t.tgname IN (@TRIGGER@)) THEN
        RAISE EXCEPTION 'registro append-only: una partizione di audit_global o '
            'verifiche_registro risulterebbe staccata o un trigger append-only disattivato'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END;
$$;
""")


def _sql_registri(modello: str) -> str:
    """Testo SQL delle funzioni degli event trigger: segnaposti sostituiti con le
    costanti del modulo (nessun valore esterno)."""
    def elenco(valori: tuple[str, ...]) -> str:
        return ", ".join(f"'{v}'" for v in valori)

    return (modello.replace("@REGEX@", REGEX_PARTIZIONI)
            .replace("@PROTETTE@", elenco(PROTETTE_DROP))
            .replace("@TRIGGER@", elenco(TRIGGER_APPEND_ONLY)))


# Le scelte che dipendono dai privilegi del ruolo che migra si fanno DENTRO il
# database (blocchi DO): la migrazione funziona anche come script offline
# (``alembic upgrade --sql``) eseguito da un altro ruolo.
_RUOLI = """
DO $ruoli$
DECLARE
    r text := '@RUOLO@';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        ELSE
            RAISE WARNING 'ruolo % non creato: serve CREATEROLE (crearlo come amministratore '
                'e rieseguire i GRANT di questa migrazione)', r;
            RETURN;
        END IF;
    END IF;
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', r);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', r);
    EXECUTE format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', r);
    EXECUTE format('REVOKE UPDATE, DELETE ON @SOLO_AGGIUNTA@ FROM %I', r);
    EXECUTE format('REVOKE DELETE ON public.catena_checkpoint FROM %I', r);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, '
                   'DELETE ON TABLES TO %I', r);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE '
                   'ON SEQUENCES TO %I', r);
END
$ruoli$;
"""

_EVENT_TRIGGER = """
DO $et$
BEGIN
    IF NOT (SELECT rolsuper OR EXISTS (SELECT 1 FROM pg_roles x WHERE x.rolname = 'rds_superuser'
                                       AND pg_has_role(current_user, x.oid, 'member'))
            FROM pg_roles WHERE rolname = current_user) THEN
        RAISE WARNING 'event trigger dei registri non creati: serve un superutente '
            '(vedi docs/architettura/infrastruttura.md)';
        RETURN;
    END IF;
    CREATE EVENT TRIGGER vd_protezione_registri_drop ON sql_drop
        EXECUTE FUNCTION public.vd_protezione_registri_drop();
    CREATE EVENT TRIGGER vd_protezione_registri_alter ON ddl_command_start
        WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION public.vd_protezione_registri_alter();
    CREATE EVENT TRIGGER vd_protezione_registri_stato ON ddl_command_end
        WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION public.vd_protezione_registri_stato();
    -- anche con session_replication_role = replica
    ALTER EVENT TRIGGER vd_protezione_registri_drop ENABLE ALWAYS;
    ALTER EVENT TRIGGER vd_protezione_registri_alter ENABLE ALWAYS;
    ALTER EVENT TRIGGER vd_protezione_registri_stato ENABLE ALWAYS;
END
$et$;
"""

_REVOCA_RUOLI = """
DO $revoca$
BEGIN
    -- Revoca privilegi e privilegi predefiniti in QUESTO database (il ruolo è del
    -- cluster: altri database possono usarlo, non si elimina)
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@RUOLO@') THEN
        DROP OWNED BY @RUOLO@;
    END IF;
END
$revoca$;
"""


def _upgrade_protezione_registri() -> None:
    # a. ruoli separati
    op.execute(_RUOLI.replace("@RUOLO@", RUOLO_APPLICAZIONE).replace(
        "@SOLO_AGGIUNTA@", ", ".join("public." + t for t in SOLO_AGGIUNTA)))
    # b. funzioni (nessun privilegio speciale) ed event trigger (solo un
    #    superutente; su RDS rds_superuser)
    op.execute(_sql_registri(_FUNZIONE_DROP))
    op.execute(_FUNZIONE_ALTER)
    op.execute(_sql_registri(_FUNZIONE_STATO))
    op.execute(_EVENT_TRIGGER)


def _downgrade_protezione_registri() -> None:
    for nome in ("stato", "alter", "drop"):
        op.execute(f"DROP EVENT TRIGGER IF EXISTS vd_protezione_registri_{nome}")
        op.execute(f"DROP FUNCTION IF EXISTS public.vd_protezione_registri_{nome}()")
    op.execute(_REVOCA_RUOLI.replace("@RUOLO@", RUOLO_APPLICAZIONE))
