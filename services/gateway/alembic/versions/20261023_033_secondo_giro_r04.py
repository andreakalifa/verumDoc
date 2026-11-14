# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Secondo giro delle correzioni R04 (verifica del revisore)

Revision ID: 20261023_033
Revises: 20261023_032
Create Date: 2026-10-23

1. Outbox, errori del TOPIC (N1): ``outbox_topic_sospesi`` — un topic con un
   errore di topic o di autorizzazione (``TopicAuthorizationFailed``,
   ``UnknownTopicOrPartition``…) si SOSPENDE con pausa crescente invece di
   fermare le chiavi una per una; ``idx_outbox_chiavi_in_sospeso`` (origine,
   chiave) WHERE pubblicato_il IS NULL per l'ordine per chiave dietro un topic
   sospeso. ``outbox_eventi`` è partizionata: indice ON ONLY sulla madre, poi
   CONCURRENTLY per partizione e aggancio (come la 032); offline un indice
   normale.
2. Testimone, allarmi raggruppati (N3): ``catena_allarmi_testimone`` — i rifiuti
   consecutivi con la stessa causa formano UN allarme aperto con un contatore
   (al più uno aperto per causa); ``catena_checkpoint.allarme_id``. I rifiuti
   aperti esistenti si raggruppano per causa (ricavata dal motivo registrato).
3. Audit e registri contro il PROPRIETARIO (rilievo 5, N4):
   a. ``FORCE ROW LEVEL SECURITY`` su ``audit_global`` e sulle sue partizioni:
      anche il proprietario ha solo INSERT e SELECT;
   b. ``vd_crea_partizione_registro(tabella, inizio)``: unica via per creare le
      partizioni dei registri (``audit_global``, ``verifiche_registro``), con le
      loro difese; SECURITY DEFINER del proprietario dei registri; la
      manutenzione la esegue e non è proprietaria dei registri;
   c. con un superutente (su RDS ``rds_superuser``), altrimenti avviso:
      - ruolo ``verumdoc_audit_proprietario`` (NOLOGIN, nessun membro):
        proprietario dei registri, delle loro partizioni e della funzione (b);
      - ruolo ``verumdoc_manutenzione`` (NOLOGIN): proprietario delle tabelle
        partizionate operative (``outbox_eventi``, ``webhook_consegne``,
        ``eventi_cliente``) e delle loro partizioni, CREATE sullo schema
        ``partizioni``, EXECUTE di (b). Il ruolo di login della manutenzione ne
        è membro (e di ``verumdoc_applicazione``);
      - event trigger ``vd_protezione_registri_oggetti`` (ddl_command_end):
        rifiuta CREATE/ALTER/REPLACE FUNCTION sulle funzioni di protezione,
        CREATE/ALTER RULE, CREATE/ALTER TRIGGER e CREATE/ALTER POLICY sui
        registri e sulle loro partizioni (ammessi solo i trigger e le politiche
        delle difese, con la forma attesa);
      - ``vd_protezione_registri_drop`` (sql_drop) rifiuta anche il DROP di
        quelle funzioni, dei trigger, delle regole e delle politiche dei registri;
      - ``vd_protezione_registri_stato`` (dopo ogni ALTER TABLE) controlla anche
        RLS attiva e FORCE su ``audit_global`` e partizioni, nomi conformi delle
        partizioni agganciate (niente RENAME), nessuna tabella dei registri non
        agganciata e NON vuota, trigger delle difese attivi (non REPLICA).
      Il superutente resta fuori dal perimetro di queste difese (vedi
      docs/architettura/catena.md): le difese contro di lui sono WORM e catena.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import text

revision = "20261023_033"
down_revision = "20261023_032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _upgrade_topic_sospesi()
    _upgrade_allarmi_testimone()
    _upgrade_protezione_proprietario()
    _upgrade_indice_chiavi()


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS public.{INDICE_CHIAVI}")
    _downgrade_protezione_proprietario()
    _downgrade_allarmi_testimone()
    op.drop_table("outbox_topic_sospesi", schema="public")


# ── 1. Outbox: topic sospesi ────────────────────────────────────────────────

INDICE_CHIAVI = "idx_outbox_chiavi_in_sospeso"
COLONNE_CHIAVI = "(origine, chiave) WHERE pubblicato_il IS NULL"


def _upgrade_topic_sospesi() -> None:
    op.create_table(
        "outbox_topic_sospesi",
        sa.Column("origine", sa.String(40), primary_key=True),
        sa.Column("topic", sa.String(200), primary_key=True),
        sa.Column("sospeso_fino_a", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pausa_secondi", sa.Float(), nullable=False),
        sa.Column("sospensioni", sa.Integer(), nullable=False),
        sa.Column("ultimo_errore", sa.Text()),
        sa.Column("dal", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema="public",
    )


def _upgrade_indice_chiavi() -> None:
    """Ultimo passo (fuori transazione per CONCURRENTLY): vedi la 032."""
    if context.is_offline_mode():
        op.execute(f"CREATE INDEX IF NOT EXISTS {INDICE_CHIAVI} ON public.outbox_eventi "
                   f"{COLONNE_CHIAVI}")
        return
    op.execute(f"CREATE INDEX IF NOT EXISTS {INDICE_CHIAVI} ON ONLY public.outbox_eventi "
               f"{COLONNE_CHIAVI}")
    partizioni = [r[0] for r in op.get_bind().execute(text(
        "SELECT c.oid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'public.outbox_eventi'::regclass ORDER BY 1")).all()]
    with op.get_context().autocommit_block():
        for qualificato in partizioni:
            nome = qualificato.split(".")[-1] + "_chiavi_idx"
            schema = qualificato.split(".")[0] if "." in qualificato else "public"
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {nome} ON {qualificato} "
                       f"{COLONNE_CHIAVI}")
            agganciato = op.get_bind().execute(text(
                "SELECT 1 FROM pg_inherits WHERE inhparent = CAST(:madre AS regclass) "
                "AND inhrelid = CAST(:figlio AS regclass)"),
                {"madre": f"public.{INDICE_CHIAVI}", "figlio": f"{schema}.{nome}"}).first()
            if agganciato is None:
                op.execute(f"ALTER INDEX public.{INDICE_CHIAVI} ATTACH PARTITION {schema}.{nome}")


# ── 2. Testimone: allarmi raggruppati per causa ─────────────────────────────

#: Causa dei rifiuti già registrati, ricavata dal motivo (stessi testi del codice)
_CAUSA_DA_MOTIVO = """
CASE WHEN c.esito_testimone ~ '^(403|422|423):' THEN 'HTTP_' || substr(c.esito_testimone, 1, 3)
     WHEN c.esito_testimone LIKE 'controfirma%' THEN 'CONTROFIRMA_NON_VERIFICABILE'
     WHEN c.esito_testimone LIKE 'il testimone conosce la dimensione%'
          THEN 'DIMENSIONE_TESTIMONE_MAGGIORE'
     ELSE 'ALTRO' END
"""


def _upgrade_allarmi_testimone() -> None:
    op.create_table(
        "catena_allarmi_testimone",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("causa", sa.String(60), nullable=False),
        sa.Column("aperto_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rifiuti", sa.Integer(), nullable=False),
        sa.Column("prima_dimensione", sa.BigInteger(), nullable=False),
        sa.Column("ultima_dimensione", sa.BigInteger(), nullable=False),
        sa.Column("ultimo_motivo", sa.Text()),
        sa.Column("gestito_il", sa.DateTime(timezone=True)),
        sa.Column("gestito_da", sa.String(200)),
        sa.Column("nota", sa.Text()),
        sa.CheckConstraint("rifiuti >= 1", name="ck_catena_allarmi_testimone_rifiuti"),
        schema="public",
    )
    op.create_index("uq_catena_allarmi_testimone_aperto", "catena_allarmi_testimone",
                    ["causa"], unique=True, schema="public",
                    postgresql_where=sa.text("gestito_il IS NULL"))
    op.add_column("catena_checkpoint", sa.Column("allarme_id", sa.BigInteger()),
                  schema="public")
    op.create_foreign_key("catena_checkpoint_allarme_id_fkey", "catena_checkpoint",
                          "catena_allarmi_testimone", ["allarme_id"], ["id"],
                          source_schema="public", referent_schema="public",
                          ondelete="RESTRICT")
    # Rifiuti aperti già presenti: un allarme per causa
    op.execute("""
        INSERT INTO public.catena_allarmi_testimone (causa, aperto_il, ultimo_il, rifiuti,
            prima_dimensione, ultima_dimensione, ultimo_motivo)
        SELECT causa, min(emesso_il), max(emesso_il), count(*), min(dimensione),
               max(dimensione), (array_agg(esito_testimone ORDER BY dimensione DESC))[1]
        FROM (SELECT c.*, @CAUSA@ AS causa FROM public.catena_checkpoint c
              WHERE c.stato_testimone = 'RIFIUTATO' AND c.rifiuto_gestito_il IS NULL) r
        GROUP BY causa""".replace("@CAUSA@", _CAUSA_DA_MOTIVO))
    op.execute("""
        UPDATE public.catena_checkpoint c SET allarme_id = a.id
        FROM public.catena_allarmi_testimone a
        WHERE c.stato_testimone = 'RIFIUTATO' AND c.rifiuto_gestito_il IS NULL
          AND a.gestito_il IS NULL AND a.causa = @CAUSA@""".replace("@CAUSA@", _CAUSA_DA_MOTIVO))


def _downgrade_allarmi_testimone() -> None:
    op.drop_constraint("catena_checkpoint_allarme_id_fkey", "catena_checkpoint",
                       schema="public", type_="foreignkey")
    op.drop_column("catena_checkpoint", "allarme_id", schema="public")
    op.drop_table("catena_allarmi_testimone", schema="public")


# ── 3. Registri append-only contro il proprietario ──────────────────────────

PROPRIETARIO_REGISTRI = "verumdoc_audit_proprietario"
RUOLO_MANUTENZIONE = "verumdoc_manutenzione"
#: Registri con proprietario dedicato (e loro partizioni)
REGISTRI = ("audit_global", "verifiche_registro")
#: Tabelle partizionate operative: le mantiene il ruolo della manutenzione
OPERATIVE_PARTIZIONATE = ("outbox_eventi", "webhook_consegne", "eventi_cliente")
#: Tabelle protette (come la 031)
PROTETTE = ("audit_global", "verifiche_registro", "catena_pda", "catena_registro",
            "catena_nodi", "catena_checkpoint")
REGEX_PARTIZIONI = "^(audit_global|verifiche_registro)_p[0-9]+$"
#: Trigger ammessi sulle tabelle protette: nome → funzione (tutte rifiutano e basta,
#: tranne catena_checkpoint_protetto che controlla gli UPDATE ammessi)
TRIGGER_AMMESSI = (
    ("trg_audit_global_append_only", "vd_audit_append_only"),
    ("trg_audit_global_no_truncate", "vd_audit_append_only"),
    ("trg_verifiche_registro_append_only", "verifiche_registro_append_only"),
    ("trg_verifiche_registro_no_truncate", "verifiche_registro_append_only"),
    ("trg_catena_pda_append_only", "catena_append_only"),
    ("trg_catena_pda_no_truncate", "catena_append_only"),
    ("trg_catena_registro_append_only", "catena_append_only"),
    ("trg_catena_registro_no_truncate", "catena_append_only"),
    ("trg_catena_nodi_append_only", "catena_append_only"),
    ("trg_catena_nodi_no_truncate", "catena_append_only"),
    ("trg_catena_checkpoint_protetto", "catena_checkpoint_protetto"),
    ("trg_catena_checkpoint_no_truncate", "catena_checkpoint_protetto"),
)
#: Funzioni che nessuno sostituisce, modifica o elimina
FUNZIONI_PROTETTE = ("vd_audit_append_only", "verifiche_registro_append_only",
                     "catena_append_only", "catena_checkpoint_protetto",
                     "vd_crea_partizione_registro", "vd_registro_protetto",
                     "vd_protezione_registri_drop", "vd_protezione_registri_alter",
                     "vd_protezione_registri_stato", "vd_protezione_registri_oggetti")

# Tabella protetta: registro o catena per nome, partizione dei registri per nome
# o perché agganciata a un registro.
_FUNZIONE_PROTETTO = """
CREATE OR REPLACE FUNCTION public.vd_registro_protetto(tabella oid)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.oid = tabella AND (
            (n.nspname = 'public' AND c.relname IN (@PROTETTE@))
            OR (n.nspname = 'partizioni' AND c.relname ~ '@REGEX@')
            OR EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid
                       AND i.inhparent IN (to_regclass('public.audit_global'),
                                           to_regclass('public.verifiche_registro')))))
$$;
"""

# Creazione di una partizione di un registro: nome, limiti e difese decisi QUI.
# Ordine pensato per gli event trigger: tabella vuota non agganciata, RLS e FORCE
# (audit), politiche e trigger delle difese, aggancio.
_FUNZIONE_CREA = """
CREATE OR REPLACE FUNCTION public.vd_crea_partizione_registro(tabella text, inizio timestamptz)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    mese timestamptz := date_trunc('month', inizio, 'UTC');
    fine timestamptz;
    nome text;
    funzione text;
BEGIN
    IF tabella NOT IN ('audit_global', 'verifiche_registro') THEN
        RAISE EXCEPTION 'vd_crea_partizione_registro: % non è un registro', tabella;
    END IF;
    IF inizio IS DISTINCT FROM mese THEN
        RAISE EXCEPTION 'vd_crea_partizione_registro: % non è l''inizio di un mese UTC', inizio;
    END IF;
    fine := (mese AT TIME ZONE 'UTC' + interval '1 month') AT TIME ZONE 'UTC';
    nome := tabella || '_p' || to_char(mese AT TIME ZONE 'UTC', 'YYYYMM');
    IF EXISTS (SELECT 1 FROM pg_inherits i
               WHERE i.inhparent = format('public.%I', tabella)::regclass
               AND i.inhrelid = to_regclass(format('partizioni.%I', nome))) THEN
        RETURN false;
    END IF;
    PERFORM set_config('lock_timeout', '5s', true);
    EXECUTE format('CREATE TABLE IF NOT EXISTS partizioni.%I (LIKE public.%I INCLUDING DEFAULTS '
                   'INCLUDING CONSTRAINTS INCLUDING STORAGE INCLUDING COMMENTS)', nome, tabella);
    IF tabella = 'audit_global' THEN
        funzione := 'vd_audit_append_only';
        EXECUTE format('ALTER TABLE partizioni.%I ENABLE ROW LEVEL SECURITY, '
                       'FORCE ROW LEVEL SECURITY', nome);
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polname = 'audit_global_insert_only'
                       AND polrelid = format('partizioni.%I', nome)::regclass) THEN
            EXECUTE format('CREATE POLICY audit_global_insert_only ON partizioni.%I '
                           'FOR INSERT WITH CHECK (true)', nome);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polname = 'audit_global_select_all'
                       AND polrelid = format('partizioni.%I', nome)::regclass) THEN
            EXECUTE format('CREATE POLICY audit_global_select_all ON partizioni.%I '
                           'FOR SELECT USING (true)', nome);
        END IF;
    ELSE
        funzione := 'verifiche_registro_append_only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = format('trg_%s_no_truncate', tabella)
                   AND tgrelid = format('partizioni.%I', nome)::regclass) THEN
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON partizioni.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION public.%I()',
                       format('trg_%s_no_truncate', tabella), nome, funzione);
    END IF;
    EXECUTE format('ALTER TABLE public.%I ATTACH PARTITION partizioni.%I '
                   'FOR VALUES FROM (%L) TO (%L)', tabella, nome, mese, fine);
    RETURN true;
END;
$$;
"""

_FUNZIONE_OGGETTI = """
CREATE OR REPLACE FUNCTION public.vd_protezione_registri_oggetti()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    c record;
    tabella oid;
    nome text;
    funzione text;
    comando "char";
    permissiva boolean;
    usa text;
    verifica text;
BEGIN
    FOR c IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
        IF c.object_type IN ('function', 'procedure') THEN
            IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                       WHERE p.oid = c.objid AND n.nspname = 'public'
                         AND p.proname IN (@FUNZIONI@)) THEN
                RAISE EXCEPTION 'registro append-only: la funzione di protezione % non si '
                    'modifica', c.object_identity USING ERRCODE = 'insufficient_privilege';
            END IF;
        ELSIF c.object_type = 'rule' THEN
            SELECT ev_class INTO tabella FROM pg_rewrite WHERE oid = c.objid;
            IF public.vd_registro_protetto(tabella) THEN
                RAISE EXCEPTION 'registro append-only: regole vietate su %', tabella::regclass
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        ELSIF c.object_type = 'trigger' THEN
            SELECT t.tgrelid, t.tgname, p.proname INTO tabella, nome, funzione
            FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid WHERE t.oid = c.objid;
            IF public.vd_registro_protetto(tabella)
               AND (nome, funzione) NOT IN (@TRIGGER_AMMESSI@) THEN
                RAISE EXCEPTION 'registro append-only: trigger % non ammesso su %', nome,
                    tabella::regclass USING ERRCODE = 'insufficient_privilege';
            END IF;
        ELSIF c.object_type = 'policy' THEN
            SELECT polrelid, polname, polcmd, polpermissive, pg_get_expr(polqual, polrelid),
                   pg_get_expr(polwithcheck, polrelid)
            INTO tabella, nome, comando, permissiva, usa, verifica
            FROM pg_policy WHERE oid = c.objid;
            IF public.vd_registro_protetto(tabella) AND NOT (
                   permissiva AND (
                   (nome = 'audit_global_insert_only' AND comando = 'a' AND usa IS NULL
                    AND verifica = 'true')
                   OR (nome = 'audit_global_select_all' AND comando = 'r' AND usa = 'true'
                       AND verifica IS NULL))) THEN
                RAISE EXCEPTION 'registro append-only: politica % non ammessa su %', nome,
                    tabella::regclass USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END LOOP;
END;
$$;
"""

_FUNZIONE_DROP = """
CREATE OR REPLACE FUNCTION public.vd_protezione_registri_drop()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    o record;
BEGIN
    FOR o IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
        IF (o.object_type = 'table' AND (
                (o.schema_name = 'partizioni' AND o.object_name ~ '@REGEX@')
                OR (o.schema_name = 'public' AND o.object_name IN (@PROTETTE@))))
           OR (o.object_type IN ('function', 'procedure')
               AND o.object_identity ~ '^public\\.(@FUNZIONI_RE@)\\(')
           OR (o.object_type IN ('trigger', 'policy', 'rule')
               AND o.object_identity ~ (' on (public\\.(@PROTETTE_RE@)|partizioni\\.'
                                        || '(audit_global|verifiche_registro)_p[0-9]+)$'))
           OR (o.object_type IN ('trigger', 'policy', 'rule')
               AND public.vd_registro_protetto(to_regclass(
                   substring(o.object_identity from ' on (.*)$')))) THEN
            RAISE EXCEPTION 'registro append-only: eliminazione di % vietata',
                o.object_identity USING ERRCODE = 'insufficient_privilege';
        END IF;
    END LOOP;
END;
$$;
"""

_FUNZIONE_STATO = """
CREATE OR REPLACE FUNCTION public.vd_protezione_registri_stato()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    r record;
    righe boolean;
BEGIN
    -- registri e catena presenti con il loro nome
    IF EXISTS (SELECT 1 FROM unnest(ARRAY[@PROTETTE@]) AS t(nome)
               WHERE to_regclass('public.' || t.nome) IS NULL) THEN
        RAISE EXCEPTION 'registro append-only: un registro protetto non si rinomina'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- partizioni agganciate ai registri: nome conforme (niente RENAME), nessun
    -- distacco in corso
    IF EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               JOIN pg_class m ON m.oid = i.inhparent
               WHERE i.inhparent IN (to_regclass('public.audit_global'),
                                     to_regclass('public.verifiche_registro'))
                 AND (i.inhdetachpending OR n.nspname <> 'partizioni'
                      OR c.relname !~ ('^' || m.relname || '_p[0-9]{6}$'))) THEN
        RAISE EXCEPTION 'registro append-only: partizione dei registri rinominata o in '
            'distacco' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- tabelle dei registri NON agganciate: ammesse solo vuote (in creazione)
    FOR r IN SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'partizioni' AND c.relkind = 'r'
               AND c.relname ~ '@REGEX@' AND NOT c.relispartition LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s)', r.oid::regclass) INTO righe;
        IF righe THEN
            RAISE EXCEPTION 'registro append-only: la partizione % risulterebbe staccata',
                r.oid::regclass USING ERRCODE = 'insufficient_privilege';
        END IF;
    END LOOP;
    -- audit: RLS attiva e FORCE sulla madre e su ogni partizione
    IF EXISTS (SELECT 1 FROM pg_class c
               WHERE (c.oid = to_regclass('public.audit_global')
                      OR c.oid IN (SELECT inhrelid FROM pg_inherits
                                   WHERE inhparent = to_regclass('public.audit_global')))
                 AND NOT (c.relrowsecurity AND c.relforcerowsecurity)) THEN
        RAISE EXCEPTION 'registro append-only: RLS e FORCE obbligatorie su audit_global e '
            'sulle sue partizioni' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- trigger delle difese sempre attivi (né disattivati né solo in replica)
    IF EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgenabled NOT IN ('O', 'A')
               AND t.tgname IN (@NOMI_TRIGGER@)) THEN
        RAISE EXCEPTION 'registro append-only: trigger delle difese disattivato'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END;
$$;
"""


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def _sql(modello: str) -> str:
    """Segnaposti sostituiti con le costanti del modulo (nessun valore esterno)."""
    return (modello.replace("@REGEX@", REGEX_PARTIZIONI)
            .replace("@PROTETTE@", _elenco(PROTETTE))
            .replace("@PROTETTE_RE@", "|".join(PROTETTE))
            .replace("@FUNZIONI@", _elenco(FUNZIONI_PROTETTE))
            .replace("@FUNZIONI_RE@", "|".join(FUNZIONI_PROTETTE))
            .replace("@NOMI_TRIGGER@", _elenco(tuple(n for n, _ in TRIGGER_AMMESSI)))
            .replace("@TRIGGER_AMMESSI@", ", ".join(
                f"('{n}', '{f}')" for n, f in TRIGGER_AMMESSI)))


# Ruoli, proprietà ed event trigger: solo con un superutente (su RDS
# rds_superuser), altrimenti avviso e restano le difese senza privilegi (FORCE
# RLS, funzione di creazione). NB: il testo di questo blocco non deve contenere
# le parole controllate dall'event trigger «alter» della 031.
_PRIVILEGIATO = """
DO $priv$
DECLARE
    su boolean := (SELECT rolsuper FROM pg_roles WHERE rolname = current_user);
    t record;
BEGIN
    IF NOT (su OR EXISTS (SELECT 1 FROM pg_roles x WHERE x.rolname = 'rds_superuser'
                          AND pg_has_role(current_user, x.oid, 'member'))) THEN
        RAISE WARNING 'ruoli %, % e event trigger dei registri non creati: serve un '
            'superutente (docs/architettura/infrastruttura.md)', '@PROPRIETARIO@', '@MANUTENZIONE@';
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@PROPRIETARIO@') THEN
        CREATE ROLE @PROPRIETARIO@ NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@MANUTENZIONE@') THEN
        CREATE ROLE @MANUTENZIONE@ NOLOGIN;
    END IF;
    IF NOT su THEN
        -- RDS: per cedere la proprietà serve essere membri del nuovo proprietario
        EXECUTE format('GRANT @PROPRIETARIO@, @MANUTENZIONE@ TO %I', current_user);
    END IF;
    GRANT USAGE ON SCHEMA public, partizioni TO @PROPRIETARIO@, @MANUTENZIONE@;
    GRANT CREATE ON SCHEMA partizioni TO @PROPRIETARIO@, @MANUTENZIONE@;
    -- registri, loro partizioni e funzione di creazione: proprietario dedicato
    FOR t IN SELECT c.oid::regclass AS nome FROM pg_class c
             WHERE c.oid IN (@REGISTRI_OID@)
                OR c.oid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent IN (@REGISTRI_OID@))
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO @PROPRIETARIO@', t.nome);
    END LOOP;
    ALTER FUNCTION public.vd_crea_partizione_registro(text, timestamptz) OWNER TO @PROPRIETARIO@;
    -- tabelle partizionate operative: ruolo della manutenzione
    FOR t IN SELECT c.oid::regclass AS nome FROM pg_class c
             WHERE c.oid IN (@OPERATIVE_OID@)
                OR c.oid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent IN (@OPERATIVE_OID@))
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO @MANUTENZIONE@', t.nome);
    END LOOP;
    GRANT EXECUTE ON FUNCTION public.vd_crea_partizione_registro(text, timestamptz)
        TO @MANUTENZIONE@;
    IF NOT su THEN
        EXECUTE format('REVOKE @PROPRIETARIO@, @MANUTENZIONE@ FROM %I', current_user);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'vd_protezione_registri_oggetti') THEN
        RETURN;
    END IF;
    CREATE EVENT TRIGGER vd_protezione_registri_oggetti ON ddl_command_end
        WHEN TAG IN ('CREATE FUNCTION', 'ALTER FUNCTION', 'CREATE PROCEDURE',
                     'ALTER PROCEDURE', 'CREATE RULE', 'ALTER RULE', 'CREATE TRIGGER',
                     'ALTER TRIGGER', 'CREATE POLICY', 'ALTER POLICY')
        EXECUTE FUNCTION public.vd_protezione_registri_oggetti();
    ALTER EVENT TRIGGER vd_protezione_registri_oggetti ENABLE ALWAYS;
END
$priv$;
"""

_PRIVILEGIATO_GIU = """
DO $priv$
DECLARE
    su boolean := (SELECT rolsuper FROM pg_roles WHERE rolname = current_user);
    t record;
BEGIN
    DROP EVENT TRIGGER IF EXISTS vd_protezione_registri_oggetti;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@PROPRIETARIO@') THEN
        RETURN;
    END IF;
    IF NOT su THEN
        EXECUTE format('GRANT @PROPRIETARIO@, @MANUTENZIONE@ TO %I', current_user);
    END IF;
    IF to_regprocedure('public.vd_crea_partizione_registro(text, timestamptz)') IS NOT NULL THEN
        EXECUTE format('ALTER FUNCTION public.vd_crea_partizione_registro(text, timestamptz) '
                       'OWNER TO %I', current_user);
    END IF;
    FOR t IN SELECT c.oid::regclass AS nome FROM pg_class c
             WHERE c.oid IN (@REGISTRI_OID@, @OPERATIVE_OID@)
                OR c.oid IN (SELECT inhrelid FROM pg_inherits
                             WHERE inhparent IN (@REGISTRI_OID@, @OPERATIVE_OID@))
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO %I', t.nome, current_user);
    END LOOP;
    IF NOT su THEN
        EXECUTE format('REVOKE @PROPRIETARIO@, @MANUTENZIONE@ FROM %I', current_user);
    END IF;
    -- privilegi in QUESTO database (i ruoli sono del cluster: non si eliminano)
    DROP OWNED BY @PROPRIETARIO@, @MANUTENZIONE@;
END
$priv$;
"""


def _sql_ruoli(modello: str) -> str:
    def oid(tabelle: tuple[str, ...]) -> str:
        return ", ".join(f"to_regclass('public.{t}')" for t in tabelle)

    return (modello.replace("@PROPRIETARIO@", PROPRIETARIO_REGISTRI)
            .replace("@MANUTENZIONE@", RUOLO_MANUTENZIONE)
            .replace("@REGISTRI_OID@", oid(REGISTRI))
            .replace("@OPERATIVE_OID@", oid(OPERATIVE_PARTIZIONATE)))


# FORCE RLS: tutte le tabelle dell'albero dell'audit (madre e partizioni)
_FORZA_RLS = """
DO $rls$
DECLARE
    t record;
BEGIN
    FOR t IN SELECT c.oid::regclass AS nome FROM pg_class c
             WHERE c.oid = to_regclass('public.audit_global')
                OR c.oid IN (SELECT inhrelid FROM pg_inherits
                             WHERE inhparent = to_regclass('public.audit_global'))
    LOOP
        EXECUTE format('ALTER TABLE %s @AZIONE@ ROW LEVEL SECURITY', t.nome);
    END LOOP;
END
$rls$;
"""

# Trigger contro TRUNCATE sulle partizioni di verifiche_registro già esistenti
# (le nuove li ricevono da vd_crea_partizione_registro)
_TRUNCATE_VERIFICHE = """
DO $tr$
DECLARE
    t record;
BEGIN
    FOR t IN SELECT inhrelid::regclass AS nome FROM pg_inherits
             WHERE inhparent = to_regclass('public.verifiche_registro')
               AND NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = inhrelid
                               AND tgname = 'trg_verifiche_registro_no_truncate')
    LOOP
        EXECUTE format('CREATE TRIGGER trg_verifiche_registro_no_truncate BEFORE TRUNCATE '
                       'ON %s FOR EACH STATEMENT EXECUTE FUNCTION '
                       'public.verifiche_registro_append_only()', t.nome);
    END LOOP;
END
$tr$;
"""

# Event trigger della 031 da sospendere durante il downgrade (rimettere NO FORCE
# su audit_global è proprio ciò che rifiutano)
_SOSPENDI_031 = """
DO $s$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'vd_protezione_registri_alter') THEN
        ALTER EVENT TRIGGER vd_protezione_registri_alter @STATO@;
        ALTER EVENT TRIGGER vd_protezione_registri_stato @STATO@;
    END IF;
END
$s$;
"""


def _upgrade_protezione_proprietario() -> None:
    # a. difese senza privilegi speciali
    op.execute(_sql(_FUNZIONE_PROTETTO))
    op.execute(_FORZA_RLS.replace("@AZIONE@", "FORCE"))
    op.execute(_TRUNCATE_VERIFICHE)
    op.execute(_FUNZIONE_CREA)
    op.execute("REVOKE ALL ON FUNCTION public.vd_crea_partizione_registro(text, timestamptz) "
               "FROM PUBLIC")
    # b. funzioni degli event trigger (le sostituisce chi crea gli event trigger)
    op.execute(_sql(_FUNZIONE_OGGETTI))
    op.execute(_sql(_FUNZIONE_DROP))
    op.execute(_sql(_FUNZIONE_STATO))
    # c. ruoli, proprietà, event trigger nuovo (superutente)
    op.execute(_sql_ruoli(_PRIVILEGIATO))


def _migrazione_031():  # noqa: ANN202 — modulo della 031 (testi delle funzioni originali)
    percorso = Path(__file__).with_name("20261023_031_correzioni_revisione_r04.py")
    spec = importlib.util.spec_from_file_location("migrazione_20261023_031", percorso)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def _downgrade_protezione_proprietario() -> None:
    op.execute(_sql_ruoli(_PRIVILEGIATO_GIU))
    m031 = _migrazione_031()
    op.execute(m031._sql_registri(m031._FUNZIONE_DROP))
    op.execute(m031._sql_registri(m031._FUNZIONE_STATO))
    op.execute("DROP FUNCTION IF EXISTS public.vd_protezione_registri_oggetti()")
    op.execute("DROP FUNCTION IF EXISTS public.vd_crea_partizione_registro(text, timestamptz)")
    op.execute(_SOSPENDI_031.replace("@STATO@", "DISABLE"))
    op.execute(_FORZA_RLS.replace("@AZIONE@", "NO FORCE"))
    op.execute(_SOSPENDI_031.replace("@STATO@", "ENABLE ALWAYS"))
    op.execute("DROP FUNCTION IF EXISTS public.vd_registro_protetto(oid)")
