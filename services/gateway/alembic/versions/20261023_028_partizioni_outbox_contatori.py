# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Partizioni (outbox, eventi per i clienti, consegne, audit) e contatori per ente (R04 dati)

Revision ID: 20261023_028
Revises: 20261021_024
Create Date: 2026-10-23

Obiettivo: reggere 1 milione di documenti al giorno (piano R03–R05 §4.2;
VD-C-076, VD-C-103). Tabelle trasformate in tabelle PARTIZIONATE per intervallo
di tempo; le partizioni stanno nello schema ``partizioni`` (fuori da ``public``),
con nome ``<tabella>_p<AAAAMMGG>`` o ``_p<AAAAMM>`` e limiti UTC. Le partizioni
future le crea in anticipo ``libs.shared.db.partizioni`` (job idempotente,
nessuna estensione); qui si creano quelle che servono ai dati esistenti più
l'anticipo iniziale. PostgreSQL 16 non ammette colonne IDENTITY sulle tabelle
partizionate: gli id progressivi passano a una SEQUENCE con DEFAULT nextval.

1. ``outbox_eventi`` — per GIORNO su ``creato_il`` (VD-C-076):
   - ``origine`` (servizio che ha scritto l'evento: ogni servizio ha il suo relay
     e pubblica solo i propri eventi) e ``gruppo`` (``hashtext(chiave) & 63``:
     ordine per chiave fra più repliche del relay, vedi ``libs/shared/outbox.py``);
   - PK ``(id, creato_il)``; ``sequenza`` da ``outbox_eventi_seq`` (non più
     UNIQUE: un vincolo unico su una tabella partizionata deve contenere la
     chiave di partizione; l'unicità la dà la sequenza);
   - ``idx_outbox_in_sospeso`` (origine, sequenza) WHERE pubblicato_il IS NULL;
   - righe copiate: quelle in attesa e quelle pubblicate negli ultimi 3 giorni
     (retention predefinita); le pubblicate più vecchie non servono (erano già
     consegnate). Origine delle righe esistenti: ``gateway`` (fino a R03 il relay
     del gateway le pubblicava tutte: il comportamento per quelle righe non cambia).
2. ``eventi_cliente`` e ``webhook_consegne`` — per MESE (VD-C-103):
   - eventi: PK ``(id, creato_il)``; l'indice unico ``uq_eventi_cliente_risorsa``
     diventa ``idx_eventi_cliente_risorsa`` (non unico): la deduplica delle
     riconsegne Kafka la fa il consumer sotto lock consultivo per
     (ente, tipo, risorsa) sull'intera retention (``repo_eventi.registra_evento``);
     ``uq_eventi_cliente_posizione`` diventa ``idx_eventi_cliente_posizione`` (non
     unico: l'unicità la garantisce il passo di pubblicazione seriale);
   - consegne: nuova colonna ``evento_creato_il`` (= ``creato_il`` dell'evento,
     scritta nella stessa istruzione che crea l'evento) che è la chiave di
     partizione: consegne ed eventi dello stesso mese stanno in partizioni con
     gli stessi limiti e si staccano insieme a fine retention (prima le
     consegne). La chiave esterna consegna → evento NON si ricrea: su tabelle
     partizionate il distacco della partizione referenziata verifica l'intera
     tabella referente sotto lock (minuti a pieno carico). Integrità per
     costruzione (stessa istruzione) e provata dai test;
   - PK ``(id, evento_creato_il)``; unico ``(sottoscrizione_id, evento_id,
     evento_creato_il)``.
3. ``audit_global`` — per MESE su ``created_at``, append-only:
   - PK ``(id, created_at)``; RLS con le stesse politiche (solo INSERT e SELECT)
     sulla madre e su OGNI partizione; in più trigger ``vd_audit_append_only``
     che vieta UPDATE e DELETE (di riga, clonato su ogni partizione) e TRUNCATE
     (di istruzione, su madre e partizioni): prima la sola RLS non valeva per il
     proprietario delle tabelle;
   - indici: quelli di R03 senza i due ridondanti (``idx_audit_tenant`` e
     ``idx_audit_created`` sono prefissi di altri) più i due indici a cursore di
     r04-servizi (migrazione 029: ``idx_audit_global_cursore``,
     ``idx_audit_global_tenant_cursore``), creati qui perché CREATE INDEX
     CONCURRENTLY non è ammesso su una tabella partizionata;
   - nessuna retention: le partizioni dell'audit non si staccano mai.
4. ``contatori_ente`` e ``contatori_ente_delta`` (piano §4.2, «mai COUNT(*)»):
   trigger su ``pdv``, ``aip`` (di riga) e ``documenti`` (di istruzione, con
   tabelle di transizione) scrivono le variazioni nella stessa transazione;
   la manutenzione le consolida (``libs/shared/db/contatori.py``). Valori
   iniziali calcolati qui dai dati esistenti, prima di creare i trigger (stessa
   transazione: nessuna variazione persa né contata due volte).
5. Indici BRIN (CONCURRENTLY, fuori transazione) su ``aip.data_conservazione``
   (report SLA mensile) e ``documenti.created_at`` (intervalli di tempo, verifica
   a rotazione): pochi kB per milioni di righe, inserimenti invariati.

``pdv``, ``aip``, ``documenti``, ``metadati_allegato5``, ``versamento_impronte`` e
``notifications`` NON si partizionano (motivi nel rapporto r04-dati): chiavi
esterne in ingresso, vincoli unici globali (chiave UD, idempotenza, deduplica)
e retention di anni senza distacco: il partizionamento renderebbe più lente le
letture per id senza togliere lavoro.

Downgrade: ricrea le tabelle non partizionate di R03 copiando le righe (le
pubblicate dell'outbox scartate nell'upgrade non tornano), elimina contatori,
trigger, funzioni, BRIN e lo schema ``partizioni``.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alembic import op
from sqlalchemy import text

revision = "20261023_028"
down_revision = "20261021_024"
branch_labels = None
depends_on = None

SCHEMA = "partizioni"
ANTICIPO_GIORNI = 7
ANTICIPO_MESI = 3
RETENZIONE_OUTBOX_GIORNI = 3


# ── Partizioni ──────────────────────────────────────────────────────────────

def _inizio_mese(d: datetime) -> datetime:
    return datetime(d.year, d.month, 1, tzinfo=UTC)


def _mese_dopo(d: datetime) -> datetime:
    return d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)


def _lett(d: datetime) -> str:
    return "'" + d.strftime("%Y-%m-%d %H:%M:%S+00") + "'"


class _MinimoOffline(str):
    """Query del minimo, in modalità offline (``alembic upgrade --sql``): senza
    database il valore si calcola quando lo script gira (correzione R04 int)."""


def _crea_partizioni_offline(tabella: str, periodo: str, sql_minimo: str) -> None:
    """Stesse partizioni di ``_crea_partizioni`` con un blocco ``DO`` che legge il
    minimo al momento dell'esecuzione dello script offline. Per ``audit_global``
    applica la protezione (RLS e trigger) anche alle partizioni create qui."""
    passo, fmt, anticipo = (("1 day", "YYYYMMDD", f"interval '{ANTICIPO_GIORNI} days'")
                            if periodo == "giorno" else
                            ("1 month", "YYYYMM", f"interval '{ANTICIPO_MESI} months'"))
    unita = "day" if periodo == "giorno" else "month"
    protezione = ""
    if tabella == "audit_global":
        protezione = (
            f"EXECUTE format('ALTER TABLE {SCHEMA}.%I ENABLE ROW LEVEL SECURITY', nome); "
            f"EXECUTE format('CREATE POLICY audit_global_insert_only ON {SCHEMA}.%I "
            f"FOR INSERT WITH CHECK (true)', nome); "
            f"EXECUTE format('CREATE POLICY audit_global_select_all ON {SCHEMA}.%I "
            f"FOR SELECT USING (true)', nome); "
            f"EXECUTE format('CREATE TRIGGER trg_audit_global_no_truncate BEFORE TRUNCATE "
            f"ON {SCHEMA}.%I FOR EACH STATEMENT EXECUTE FUNCTION "
            f"public.vd_audit_append_only()', nome); ")
    op.execute(f"""
        DO $$ DECLARE inizio timestamptz; ultimo timestamptz; nome text; BEGIN
          inizio := date_trunc('{unita}', COALESCE(({sql_minimo}), now()) AT TIME ZONE 'UTC')
                    AT TIME ZONE 'UTC';
          ultimo := date_trunc('{unita}', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                    + {anticipo};
          WHILE inizio <= ultimo LOOP
            nome := '{tabella}_p' || to_char(inizio AT TIME ZONE 'UTC', '{fmt}');
            EXECUTE format('CREATE TABLE {SCHEMA}.%I PARTITION OF public.{tabella} '
                           'FOR VALUES FROM (%L) TO (%L)', nome, inizio,
                           inizio + interval '{passo}');
            {protezione}
            inizio := inizio + interval '{passo}';
          END LOOP;
        END $$""")


def _crea_partizioni(tabella: str, periodo: str, dal: datetime | None) -> list[str]:
    """Partizioni da ``dal`` (o da adesso) fino all'anticipo; nomi creati
    (nessun nome in modalità offline: li crea il blocco ``DO``)."""
    if isinstance(dal, _MinimoOffline):
        _crea_partizioni_offline(tabella, periodo, str(dal))
        return []
    adesso = datetime.now(UTC)
    creati: list[str] = []
    if periodo == "giorno":
        inizio = datetime((dal or adesso).year, (dal or adesso).month, (dal or adesso).day,
                          tzinfo=UTC)
        ultimo = datetime(adesso.year, adesso.month, adesso.day, tzinfo=UTC) + timedelta(
            days=ANTICIPO_GIORNI)
        while inizio <= ultimo:
            fine = inizio + timedelta(days=1)
            nome = f"{tabella}_p{inizio.strftime('%Y%m%d')}"
            op.execute(f"CREATE TABLE {SCHEMA}.{nome} PARTITION OF public.{tabella} "
                       f"FOR VALUES FROM ({_lett(inizio)}) TO ({_lett(fine)})")
            creati.append(nome)
            inizio = fine
    else:
        inizio = _inizio_mese(dal or adesso)
        ultimo = _inizio_mese(adesso)
        for _ in range(ANTICIPO_MESI):
            ultimo = _mese_dopo(ultimo)
        while inizio <= ultimo:
            fine = _mese_dopo(inizio)
            nome = f"{tabella}_p{inizio.strftime('%Y%m')}"
            op.execute(f"CREATE TABLE {SCHEMA}.{nome} PARTITION OF public.{tabella} "
                       f"FOR VALUES FROM ({_lett(inizio)}) TO ({_lett(fine)})")
            creati.append(nome)
            inizio = fine
    return creati


def _minimo(sql: str) -> datetime | None:
    if op.get_context().as_sql:
        return _MinimoOffline(sql)  # type: ignore[return-value]
    valore = op.get_bind().execute(text(sql)).scalar()
    if valore is None:
        return None
    return valore.astimezone(UTC)


def _metti_da_parte(tabella: str) -> None:
    """Rinomina la tabella R03 e i suoi indici e vincoli (suffisso ``_r03``),
    così i nomi tornano liberi per la tabella partizionata."""
    vecchia = f"{tabella}_r03"
    op.execute(f"ALTER TABLE public.{tabella} RENAME TO {vecchia}")
    op.execute(f"""
        DO $$ DECLARE r record; BEGIN
          FOR r IN SELECT conname FROM pg_constraint
                   WHERE conrelid = 'public.{vecchia}'::regclass AND contype IN ('f', 'c') LOOP
            EXECUTE format('ALTER TABLE public.{vecchia} RENAME CONSTRAINT %I TO %I',
                           r.conname, left(r.conname, 50) || '_r03');
          END LOOP;
          FOR r IN SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                   WHERE i.indrelid = 'public.{vecchia}'::regclass LOOP
            EXECUTE format('ALTER INDEX public.%I RENAME TO %I',
                           r.relname, left(r.relname, 50) || '_r03');
          END LOOP;
        END $$""")


# ── Upgrade ─────────────────────────────────────────────────────────────────

def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"COMMENT ON SCHEMA {SCHEMA} IS 'Partizioni delle tabelle di public "
               "(gestite da libs/shared/db/partizioni.py, migrazione 20261023_028)'")
    _upgrade_outbox()
    _upgrade_eventi_consegne()
    _upgrade_audit()
    _upgrade_contatori()
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aip_conservazione_brin "
                   "ON public.aip USING brin (data_conservazione)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documenti_creazione_brin "
                   "ON public.documenti USING brin (created_at)")


def _upgrade_outbox() -> None:
    _metti_da_parte("outbox_eventi")
    op.execute("CREATE SEQUENCE public.outbox_eventi_seq AS bigint")
    op.execute("""
        CREATE TABLE public.outbox_eventi (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            sequenza bigint NOT NULL DEFAULT nextval('public.outbox_eventi_seq'::regclass),
            origine varchar(40) NOT NULL,
            gruppo smallint NOT NULL,
            tenant_id uuid,
            topic varchar(200) NOT NULL,
            chiave varchar(200),
            payload jsonb NOT NULL,
            creato_il timestamptz NOT NULL DEFAULT now(),
            pubblicato_il timestamptz,
            tentativi integer NOT NULL DEFAULT 0,
            ultimo_errore text,
            CONSTRAINT outbox_eventi_pkey PRIMARY KEY (id, creato_il),
            CONSTRAINT outbox_eventi_tenant_id_fkey FOREIGN KEY (tenant_id)
                REFERENCES public.tenants (id) ON DELETE RESTRICT,
            CONSTRAINT ck_outbox_eventi_gruppo CHECK (gruppo BETWEEN 0 AND 63)
        ) PARTITION BY RANGE (creato_il)""")
    op.execute("ALTER SEQUENCE public.outbox_eventi_seq OWNED BY public.outbox_eventi.sequenza")
    op.execute("CREATE INDEX idx_outbox_in_sospeso ON public.outbox_eventi (origine, sequenza) "
               "WHERE pubblicato_il IS NULL")
    condizione = (f"pubblicato_il IS NULL OR creato_il >= now() - interval "
                  f"'{RETENZIONE_OUTBOX_GIORNI} days'")
    dal = _minimo(f"SELECT min(creato_il) FROM public.outbox_eventi_r03 WHERE {condizione}")
    _crea_partizioni("outbox_eventi", "giorno", dal)
    op.execute(f"""
        INSERT INTO public.outbox_eventi (id, sequenza, origine, gruppo, tenant_id, topic, chiave,
                                          payload, creato_il, pubblicato_il, tentativi,
                                          ultimo_errore)
        SELECT id, sequenza, 'gateway', (hashtext(COALESCE(chiave, id::text)) & 63)::smallint,
               tenant_id, topic, chiave, payload, creato_il, pubblicato_il, tentativi,
               ultimo_errore
        FROM public.outbox_eventi_r03 WHERE {condizione}""")
    op.execute("SELECT setval('public.outbox_eventi_seq', "
               "COALESCE((SELECT max(sequenza) FROM public.outbox_eventi_r03), 0) + 1, false)")
    op.execute("DROP TABLE public.outbox_eventi_r03")


def _upgrade_eventi_consegne() -> None:
    _metti_da_parte("webhook_consegne")
    _metti_da_parte("eventi_cliente")
    op.execute("CREATE SEQUENCE public.eventi_cliente_seq AS bigint")
    op.execute("""
        CREATE TABLE public.eventi_cliente (
            id bigint NOT NULL DEFAULT nextval('public.eventi_cliente_seq'::regclass),
            tenant_id uuid NOT NULL,
            tipo varchar(60) NOT NULL,
            risorsa_tipo varchar(40) NOT NULL,
            risorsa_id varchar(100) NOT NULL,
            dati jsonb NOT NULL DEFAULT '{}'::jsonb,
            origine varchar(200),
            creato_il timestamptz NOT NULL DEFAULT now(),
            posizione bigint,
            CONSTRAINT eventi_cliente_pkey PRIMARY KEY (id, creato_il),
            CONSTRAINT fk_eventi_cliente_tenant FOREIGN KEY (tenant_id)
                REFERENCES public.tenants (id) ON DELETE RESTRICT
        ) PARTITION BY RANGE (creato_il)""")
    op.execute("ALTER SEQUENCE public.eventi_cliente_seq OWNED BY public.eventi_cliente.id")
    for istruzione in (
        "CREATE INDEX idx_eventi_cliente_risorsa ON public.eventi_cliente "
        "(tenant_id, tipo, risorsa_id)",
        "CREATE INDEX idx_eventi_cliente_posizione ON public.eventi_cliente (posizione) "
        "WHERE posizione IS NOT NULL",
        "CREATE INDEX idx_eventi_cliente_tenant_posizione ON public.eventi_cliente "
        "(tenant_id, posizione) WHERE posizione IS NOT NULL",
        "CREATE INDEX idx_eventi_cliente_tenant_tipo_posizione ON public.eventi_cliente "
        "(tenant_id, tipo, posizione) WHERE posizione IS NOT NULL",
        "CREATE INDEX idx_eventi_cliente_da_pubblicare ON public.eventi_cliente (id) "
        "WHERE posizione IS NULL",
    ):
        op.execute(istruzione)

    op.execute("CREATE SEQUENCE public.webhook_consegne_seq AS bigint")
    op.execute("""
        CREATE TABLE public.webhook_consegne (
            id bigint NOT NULL DEFAULT nextval('public.webhook_consegne_seq'::regclass),
            tenant_id uuid NOT NULL,
            sottoscrizione_id uuid NOT NULL,
            evento_id bigint NOT NULL,
            evento_creato_il timestamptz NOT NULL,
            stato varchar(20) NOT NULL,
            tentativi integer NOT NULL DEFAULT 0,
            riprove integer NOT NULL DEFAULT 0,
            prossimo_tentativo timestamptz,
            lease_id uuid,
            ultimo_stato_http smallint,
            ultimo_codice varchar(20),
            ultimo_errore text,
            ultimo_tentativo_il timestamptz,
            consegnata_il timestamptz,
            creata_il timestamptz NOT NULL DEFAULT now(),
            aggiornata_il timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT webhook_consegne_pkey PRIMARY KEY (id, evento_creato_il),
            CONSTRAINT uq_webhook_consegne_evento
                UNIQUE (sottoscrizione_id, evento_id, evento_creato_il),
            CONSTRAINT ck_webhook_consegne_stato CHECK (stato IN
                ('IN_CODA', 'IN_CONSEGNA', 'CONSEGNATA', 'FALLITA', 'ANNULLATA')),
            CONSTRAINT fk_webhook_consegne_tenant FOREIGN KEY (tenant_id)
                REFERENCES public.tenants (id) ON DELETE RESTRICT,
            CONSTRAINT fk_webhook_consegne_sottoscrizione FOREIGN KEY (sottoscrizione_id)
                REFERENCES public.webhook_sottoscrizioni (id) ON DELETE RESTRICT
        ) PARTITION BY RANGE (evento_creato_il)""")
    op.execute("ALTER SEQUENCE public.webhook_consegne_seq OWNED BY public.webhook_consegne.id")
    for istruzione in (
        "CREATE INDEX idx_webhook_consegne_da_fare ON public.webhook_consegne "
        "(prossimo_tentativo) WHERE stato IN ('IN_CODA', 'IN_CONSEGNA')",
        "CREATE INDEX idx_webhook_consegne_sottoscrizione ON public.webhook_consegne "
        "(sottoscrizione_id, id)",
        "CREATE INDEX idx_webhook_consegne_sottoscrizione_stato ON public.webhook_consegne "
        "(sottoscrizione_id, stato, id)",
    ):
        op.execute(istruzione)

    dal = _minimo("SELECT min(creato_il) FROM public.eventi_cliente_r03")
    _crea_partizioni("eventi_cliente", "mese", dal)
    _crea_partizioni("webhook_consegne", "mese", dal)
    op.execute("""
        INSERT INTO public.eventi_cliente (id, tenant_id, tipo, risorsa_tipo, risorsa_id, dati,
                                           origine, creato_il, posizione)
        SELECT id, tenant_id, tipo, risorsa_tipo, risorsa_id, dati, origine, creato_il, posizione
        FROM public.eventi_cliente_r03""")
    op.execute("""
        INSERT INTO public.webhook_consegne (id, tenant_id, sottoscrizione_id, evento_id,
            evento_creato_il, stato, tentativi, riprove, prossimo_tentativo, lease_id,
            ultimo_stato_http, ultimo_codice, ultimo_errore, ultimo_tentativo_il, consegnata_il,
            creata_il, aggiornata_il)
        SELECT c.id, c.tenant_id, c.sottoscrizione_id, c.evento_id, e.creato_il, c.stato,
               c.tentativi, c.riprove, c.prossimo_tentativo, c.lease_id, c.ultimo_stato_http,
               c.ultimo_codice, c.ultimo_errore, c.ultimo_tentativo_il, c.consegnata_il,
               c.creata_il, c.aggiornata_il
        FROM public.webhook_consegne_r03 c
        JOIN public.eventi_cliente_r03 e ON e.id = c.evento_id""")
    op.execute("SELECT setval('public.eventi_cliente_seq', "
               "COALESCE((SELECT max(id) FROM public.eventi_cliente_r03), 0) + 1, false)")
    op.execute("SELECT setval('public.webhook_consegne_seq', "
               "COALESCE((SELECT max(id) FROM public.webhook_consegne_r03), 0) + 1, false)")
    op.execute("DROP TABLE public.webhook_consegne_r03")
    op.execute("DROP TABLE public.eventi_cliente_r03")


_AUDIT_INDICI = (
    "CREATE INDEX idx_audit_evento ON public.audit_global (evento)",
    "CREATE INDEX idx_audit_tenant_evento ON public.audit_global "
    "(tenant_id, evento, created_at DESC)",
    "CREATE INDEX idx_audit_global_cursore ON public.audit_global (created_at DESC, id DESC)",
    "CREATE INDEX idx_audit_global_tenant_cursore ON public.audit_global "
    "(tenant_id, created_at DESC, id DESC)",
)


def _protezione_audit(tabella: str) -> None:
    """RLS (solo INSERT e SELECT) e trigger contro TRUNCATE su madre o partizione."""
    op.execute(f"ALTER TABLE {tabella} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY audit_global_insert_only ON {tabella} FOR INSERT WITH CHECK (true)")
    op.execute(f"CREATE POLICY audit_global_select_all ON {tabella} FOR SELECT USING (true)")
    op.execute(f"CREATE TRIGGER trg_audit_global_no_truncate BEFORE TRUNCATE ON {tabella} "
               "FOR EACH STATEMENT EXECUTE FUNCTION public.vd_audit_append_only()")


def _upgrade_audit() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION public.vd_audit_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_global è append-only: % vietato', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$""")
    _metti_da_parte("audit_global")
    op.execute("""
        CREATE TABLE public.audit_global (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id uuid,
            user_id uuid,
            aip_id uuid,
            evento varchar(100) NOT NULL,
            dettagli jsonb NOT NULL DEFAULT '{}'::jsonb,
            ip_address inet,
            user_agent varchar(500),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT audit_global_pkey PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)""")
    for istruzione in _AUDIT_INDICI:
        op.execute(istruzione)
    op.execute("CREATE TRIGGER trg_audit_global_append_only BEFORE UPDATE OR DELETE "
               "ON public.audit_global FOR EACH ROW EXECUTE FUNCTION public.vd_audit_append_only()")
    _protezione_audit("public.audit_global")
    dal = _minimo("SELECT min(created_at) FROM public.audit_global_r03")
    for nome in _crea_partizioni("audit_global", "mese", dal):
        _protezione_audit(f"{SCHEMA}.{nome}")
    op.execute("""
        INSERT INTO public.audit_global (id, tenant_id, user_id, aip_id, evento, dettagli,
                                         ip_address, user_agent, created_at)
        SELECT id, tenant_id, user_id, aip_id, evento, dettagli, ip_address, user_agent,
               created_at
        FROM public.audit_global_r03""")
    op.execute("DROP TABLE public.audit_global_r03")


# Funzioni dei contatori. Giorno = giorno UTC. Per UPDATE si scrivono solo le
# variazioni delle metriche toccate (cambio di stato: -1 vecchio stato, +1 nuovo).
_FUNZIONE_PDV = """
CREATE OR REPLACE FUNCTION public.vd_contatori_pdv() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    oggi date := (now() AT TIME ZONE 'UTC')::date;
    cambio_ente boolean := TG_OP = 'UPDATE' AND OLD.tenant_id IS DISTINCT FROM NEW.tenant_id;
BEGIN
    IF TG_OP = 'DELETE' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.stato IS DISTINCT FROM NEW.stato) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (OLD.tenant_id, 'pdv.stato.' || OLD.stato::text, oggi, -1);
    END IF;
    IF TG_OP = 'DELETE' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.data_ricezione IS DISTINCT FROM NEW.data_ricezione) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (OLD.tenant_id, 'pdv.ricevuti', (OLD.data_ricezione AT TIME ZONE 'UTC')::date, -1);
    END IF;
    IF TG_OP = 'INSERT' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.stato IS DISTINCT FROM NEW.stato) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (NEW.tenant_id, 'pdv.stato.' || NEW.stato::text, oggi, 1);
    END IF;
    IF TG_OP = 'INSERT' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.data_ricezione IS DISTINCT FROM NEW.data_ricezione) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (NEW.tenant_id, 'pdv.ricevuti', (NEW.data_ricezione AT TIME ZONE 'UTC')::date, 1);
    END IF;
    RETURN NULL;
END $$"""

_FUNZIONE_AIP = """
CREATE OR REPLACE FUNCTION public.vd_contatori_aip() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    oggi date := (now() AT TIME ZONE 'UTC')::date;
    cambio_ente boolean := TG_OP = 'UPDATE' AND OLD.tenant_id IS DISTINCT FROM NEW.tenant_id;
    vecchio_conservato boolean := false;
    nuovo_conservato boolean := false;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        vecchio_conservato := OLD.stato::text IN ('CONSERVATO', 'IN_CATENA')
                              AND OLD.data_conservazione IS NOT NULL;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        nuovo_conservato := NEW.stato::text IN ('CONSERVATO', 'IN_CATENA')
                            AND NEW.data_conservazione IS NOT NULL;
    END IF;
    IF TG_OP = 'DELETE' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.stato IS DISTINCT FROM NEW.stato) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (OLD.tenant_id, 'aip.stato.' || OLD.stato::text, oggi, -1);
    END IF;
    IF TG_OP = 'DELETE' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.data_versamento IS DISTINCT FROM NEW.data_versamento) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (OLD.tenant_id, 'aip.versati', (OLD.data_versamento AT TIME ZONE 'UTC')::date, -1);
    END IF;
    IF vecchio_conservato AND (TG_OP = 'DELETE' OR cambio_ente OR NOT nuovo_conservato
       OR OLD.data_conservazione IS DISTINCT FROM NEW.data_conservazione) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (OLD.tenant_id, 'aip.conservati',
                (OLD.data_conservazione AT TIME ZONE 'UTC')::date, -1);
    END IF;
    IF TG_OP = 'INSERT' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.stato IS DISTINCT FROM NEW.stato) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (NEW.tenant_id, 'aip.stato.' || NEW.stato::text, oggi, 1);
    END IF;
    IF TG_OP = 'INSERT' OR cambio_ente
       OR (TG_OP = 'UPDATE' AND OLD.data_versamento IS DISTINCT FROM NEW.data_versamento) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (NEW.tenant_id, 'aip.versati', (NEW.data_versamento AT TIME ZONE 'UTC')::date, 1);
    END IF;
    IF nuovo_conservato AND (TG_OP = 'INSERT' OR cambio_ente OR NOT vecchio_conservato
       OR OLD.data_conservazione IS DISTINCT FROM NEW.data_conservazione) THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        VALUES (NEW.tenant_id, 'aip.conservati',
                (NEW.data_conservazione AT TIME ZONE 'UTC')::date, 1);
    END IF;
    RETURN NULL;
END $$"""

_FUNZIONE_DOCUMENTI = """
CREATE OR REPLACE FUNCTION public.vd_contatori_documenti() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        SELECT g.tenant_id, m.metrica, g.giorno, -m.valore
        FROM (SELECT a.tenant_id, (v.created_at AT TIME ZONE 'UTC')::date AS giorno,
                     count(*) AS n, COALESCE(sum(v.dimensione_bytes), 0) AS byte
              FROM vecchie v JOIN public.aip a ON a.id = v.aip_id GROUP BY 1, 2) g
        CROSS JOIN LATERAL (VALUES ('documenti.versati', g.n), ('documenti.byte', g.byte))
             AS m(metrica, valore);
    END IF;
    IF TG_OP IN ('UPDATE', 'INSERT') THEN
        INSERT INTO public.contatori_ente_delta (tenant_id, metrica, giorno, delta)
        SELECT g.tenant_id, m.metrica, g.giorno, m.valore
        FROM (SELECT a.tenant_id, (n.created_at AT TIME ZONE 'UTC')::date AS giorno,
                     count(*) AS n, COALESCE(sum(n.dimensione_bytes), 0) AS byte
              FROM nuove n JOIN public.aip a ON a.id = n.aip_id GROUP BY 1, 2) g
        CROSS JOIN LATERAL (VALUES ('documenti.versati', g.n), ('documenti.byte', g.byte))
             AS m(metrica, valore);
    END IF;
    RETURN NULL;
END $$"""


def _upgrade_contatori() -> None:
    op.execute("""
        CREATE TABLE public.contatori_ente (
            tenant_id uuid NOT NULL,
            metrica varchar(80) NOT NULL,
            giorno date NOT NULL,
            valore bigint NOT NULL,
            aggiornato_il timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT contatori_ente_pkey PRIMARY KEY (tenant_id, metrica, giorno)
        )""")
    op.execute("CREATE INDEX idx_contatori_ente_metrica_giorno ON public.contatori_ente "
               "(metrica, giorno)")
    op.execute("""
        CREATE TABLE public.contatori_ente_delta (
            id bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id uuid NOT NULL,
            metrica varchar(80) NOT NULL,
            giorno date NOT NULL,
            delta bigint NOT NULL,
            CONSTRAINT contatori_ente_delta_pkey PRIMARY KEY (id)
        )""")
    op.execute("CREATE INDEX idx_contatori_ente_delta_tenant ON public.contatori_ente_delta "
               "(tenant_id, metrica)")
    # Valori iniziali dai dati esistenti (stessa transazione dei trigger)
    op.execute("""
        INSERT INTO public.contatori_ente (tenant_id, metrica, giorno, valore)
        SELECT tenant_id, metrica, giorno, sum(n) FROM (
            SELECT tenant_id, 'pdv.stato.' || stato::text AS metrica,
                   (now() AT TIME ZONE 'UTC')::date AS giorno, count(*) AS n
            FROM public.pdv GROUP BY 1, 2
            UNION ALL
            SELECT tenant_id, 'pdv.ricevuti', (data_ricezione AT TIME ZONE 'UTC')::date, count(*)
            FROM public.pdv GROUP BY 1, 3
            UNION ALL
            SELECT tenant_id, 'aip.stato.' || stato::text, (now() AT TIME ZONE 'UTC')::date,
                   count(*)
            FROM public.aip GROUP BY 1, 2
            UNION ALL
            SELECT tenant_id, 'aip.versati', (data_versamento AT TIME ZONE 'UTC')::date, count(*)
            FROM public.aip GROUP BY 1, 3
            UNION ALL
            SELECT tenant_id, 'aip.conservati', (data_conservazione AT TIME ZONE 'UTC')::date,
                   count(*)
            FROM public.aip
            WHERE stato::text IN ('CONSERVATO', 'IN_CATENA') AND data_conservazione IS NOT NULL
            GROUP BY 1, 3
            UNION ALL
            SELECT a.tenant_id, 'documenti.versati', (d.created_at AT TIME ZONE 'UTC')::date,
                   count(*)
            FROM public.documenti d JOIN public.aip a ON a.id = d.aip_id GROUP BY 1, 3
            UNION ALL
            SELECT a.tenant_id, 'documenti.byte', (d.created_at AT TIME ZONE 'UTC')::date,
                   COALESCE(sum(d.dimensione_bytes), 0)
            FROM public.documenti d JOIN public.aip a ON a.id = d.aip_id GROUP BY 1, 3
        ) s GROUP BY tenant_id, metrica, giorno""")
    op.execute(_FUNZIONE_PDV)
    op.execute(_FUNZIONE_AIP)
    op.execute(_FUNZIONE_DOCUMENTI)
    op.execute("CREATE TRIGGER trg_contatori_pdv AFTER INSERT OR DELETE OR UPDATE OF "
               "stato, data_ricezione, tenant_id ON public.pdv "
               "FOR EACH ROW EXECUTE FUNCTION public.vd_contatori_pdv()")
    op.execute("CREATE TRIGGER trg_contatori_aip AFTER INSERT OR DELETE OR UPDATE OF "
               "stato, data_versamento, data_conservazione, tenant_id ON public.aip "
               "FOR EACH ROW EXECUTE FUNCTION public.vd_contatori_aip()")
    op.execute("CREATE TRIGGER trg_contatori_documenti_ins AFTER INSERT ON public.documenti "
               "REFERENCING NEW TABLE AS nuove FOR EACH STATEMENT "
               "EXECUTE FUNCTION public.vd_contatori_documenti()")
    op.execute("CREATE TRIGGER trg_contatori_documenti_upd AFTER UPDATE ON public.documenti "
               "REFERENCING OLD TABLE AS vecchie NEW TABLE AS nuove FOR EACH STATEMENT "
               "EXECUTE FUNCTION public.vd_contatori_documenti()")
    op.execute("CREATE TRIGGER trg_contatori_documenti_del AFTER DELETE ON public.documenti "
               "REFERENCING OLD TABLE AS vecchie FOR EACH STATEMENT "
               "EXECUTE FUNCTION public.vd_contatori_documenti()")


# ── Downgrade ───────────────────────────────────────────────────────────────

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_documenti_creazione_brin")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_aip_conservazione_brin")
    for trigger, tabella in (("trg_contatori_documenti_del", "documenti"),
                             ("trg_contatori_documenti_upd", "documenti"),
                             ("trg_contatori_documenti_ins", "documenti"),
                             ("trg_contatori_aip", "aip"), ("trg_contatori_pdv", "pdv")):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON public.{tabella}")
    for funzione in ("vd_contatori_documenti", "vd_contatori_aip", "vd_contatori_pdv"):
        op.execute(f"DROP FUNCTION IF EXISTS public.{funzione}()")
    op.execute("DROP TABLE IF EXISTS public.contatori_ente_delta")
    op.execute("DROP TABLE IF EXISTS public.contatori_ente")
    _downgrade_audit()
    _downgrade_eventi_consegne()
    _downgrade_outbox()
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def _downgrade_audit() -> None:
    op.execute("ALTER TABLE public.audit_global RENAME TO audit_global_p028")
    op.execute("ALTER INDEX public.audit_global_pkey RENAME TO audit_global_p028_pkey")
    for nome in ("idx_audit_evento", "idx_audit_tenant_evento", "idx_audit_global_cursore",
                 "idx_audit_global_tenant_cursore"):
        op.execute(f"ALTER INDEX IF EXISTS public.{nome} RENAME TO {nome}_p028")
    op.execute("""
        CREATE TABLE public.audit_global (
            id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            tenant_id uuid, user_id uuid, aip_id uuid,
            evento varchar(100) NOT NULL,
            dettagli jsonb NOT NULL DEFAULT '{}'::jsonb,
            ip_address inet, user_agent varchar(500),
            created_at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("""
        INSERT INTO public.audit_global
        SELECT id, tenant_id, user_id, aip_id, evento, dettagli, ip_address, user_agent,
               created_at FROM public.audit_global_p028""")
    op.execute("CREATE INDEX idx_audit_tenant ON public.audit_global (tenant_id)")
    op.execute("CREATE INDEX idx_audit_created ON public.audit_global (created_at)")
    op.execute("CREATE INDEX idx_audit_evento ON public.audit_global (evento)")
    op.execute("CREATE INDEX idx_audit_tenant_evento ON public.audit_global "
               "(tenant_id, evento, created_at DESC)")
    # Indici a cursore della 029 (r04-servizi): si ricreano solo se la 029 è
    # applicata (la sua downgrade li elimina), così la 024 torna identica.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num >= '20261023_029') THEN
            CREATE INDEX idx_audit_global_cursore ON public.audit_global
                (created_at DESC, id DESC);
            CREATE INDEX idx_audit_global_tenant_cursore ON public.audit_global
                (tenant_id, created_at DESC, id DESC);
          END IF;
        END $$""")
    op.execute("ALTER TABLE public.audit_global ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY audit_global_insert_only ON public.audit_global "
               "FOR INSERT WITH CHECK (true)")
    op.execute("CREATE POLICY audit_global_select_all ON public.audit_global "
               "FOR SELECT USING (true)")
    op.execute("DROP TABLE public.audit_global_p028")
    op.execute("DROP FUNCTION IF EXISTS public.vd_audit_append_only() CASCADE")


def _downgrade_eventi_consegne() -> None:
    op.execute("ALTER TABLE public.webhook_consegne RENAME TO webhook_consegne_p028")
    op.execute("ALTER TABLE public.eventi_cliente RENAME TO eventi_cliente_p028")
    op.execute("""
        DO $$ DECLARE r record; BEGIN
          FOR r IN SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                   WHERE i.indrelid IN ('public.webhook_consegne_p028'::regclass,
                                        'public.eventi_cliente_p028'::regclass) LOOP
            EXECUTE format('ALTER INDEX public.%I RENAME TO %I', r.relname,
                           left(r.relname, 50) || '_p028');
          END LOOP;
          FOR r IN SELECT conrelid::regclass::text AS t, conname FROM pg_constraint
                   WHERE conrelid IN ('public.webhook_consegne_p028'::regclass,
                                      'public.eventi_cliente_p028'::regclass)
                     AND contype IN ('f', 'c') LOOP
            EXECUTE format('ALTER TABLE %s RENAME CONSTRAINT %I TO %I', r.t, r.conname,
                           left(r.conname, 50) || '_p028');
          END LOOP;
        END $$""")
    op.execute("""
        CREATE TABLE public.eventi_cliente (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id uuid NOT NULL,
            tipo varchar(60) NOT NULL,
            risorsa_tipo varchar(40) NOT NULL,
            risorsa_id varchar(100) NOT NULL,
            dati jsonb NOT NULL DEFAULT '{}'::jsonb,
            origine varchar(200),
            creato_il timestamptz NOT NULL DEFAULT now(),
            posizione bigint,
            CONSTRAINT uq_eventi_cliente_risorsa UNIQUE (tenant_id, tipo, risorsa_id),
            CONSTRAINT fk_eventi_cliente_tenant FOREIGN KEY (tenant_id)
                REFERENCES public.tenants (id) ON DELETE RESTRICT
        )""")
    op.execute("""
        INSERT INTO public.eventi_cliente (id, tenant_id, tipo, risorsa_tipo, risorsa_id, dati,
                                           origine, creato_il, posizione)
        OVERRIDING SYSTEM VALUE
        SELECT DISTINCT ON (tenant_id, tipo, risorsa_id) id, tenant_id, tipo, risorsa_tipo,
               risorsa_id, dati, origine, creato_il, posizione
        FROM public.eventi_cliente_p028 ORDER BY tenant_id, tipo, risorsa_id, id""")
    op.execute("SELECT setval(pg_get_serial_sequence('public.eventi_cliente', 'id'), "
               "COALESCE((SELECT max(id) FROM public.eventi_cliente_p028), 0) + 1, false)")
    op.execute("CREATE UNIQUE INDEX uq_eventi_cliente_posizione ON public.eventi_cliente "
               "(posizione) WHERE posizione IS NOT NULL")
    op.execute("CREATE INDEX idx_eventi_cliente_tenant_posizione ON public.eventi_cliente "
               "(tenant_id, posizione) WHERE posizione IS NOT NULL")
    op.execute("CREATE INDEX idx_eventi_cliente_tenant_tipo_posizione ON public.eventi_cliente "
               "(tenant_id, tipo, posizione) WHERE posizione IS NOT NULL")
    op.execute("CREATE INDEX idx_eventi_cliente_da_pubblicare ON public.eventi_cliente (id) "
               "WHERE posizione IS NULL")
    op.execute("""
        CREATE TABLE public.webhook_consegne (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id uuid NOT NULL,
            sottoscrizione_id uuid NOT NULL,
            evento_id bigint NOT NULL,
            stato varchar(20) NOT NULL,
            tentativi integer NOT NULL DEFAULT 0,
            riprove integer NOT NULL DEFAULT 0,
            prossimo_tentativo timestamptz,
            lease_id uuid,
            ultimo_stato_http smallint,
            ultimo_codice varchar(20),
            ultimo_errore text,
            ultimo_tentativo_il timestamptz,
            consegnata_il timestamptz,
            creata_il timestamptz NOT NULL DEFAULT now(),
            aggiornata_il timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_webhook_consegne_evento UNIQUE (sottoscrizione_id, evento_id),
            CONSTRAINT ck_webhook_consegne_stato CHECK (stato IN
                ('IN_CODA', 'IN_CONSEGNA', 'CONSEGNATA', 'FALLITA', 'ANNULLATA')),
            CONSTRAINT fk_webhook_consegne_tenant FOREIGN KEY (tenant_id)
                REFERENCES public.tenants (id) ON DELETE RESTRICT,
            CONSTRAINT fk_webhook_consegne_sottoscrizione FOREIGN KEY (sottoscrizione_id)
                REFERENCES public.webhook_sottoscrizioni (id) ON DELETE RESTRICT,
            CONSTRAINT fk_webhook_consegne_evento FOREIGN KEY (evento_id)
                REFERENCES public.eventi_cliente (id) ON DELETE RESTRICT
        )""")
    op.execute("""
        INSERT INTO public.webhook_consegne (id, tenant_id, sottoscrizione_id, evento_id, stato,
            tentativi, riprove, prossimo_tentativo, lease_id, ultimo_stato_http, ultimo_codice,
            ultimo_errore, ultimo_tentativo_il, consegnata_il, creata_il, aggiornata_il)
        OVERRIDING SYSTEM VALUE
        SELECT c.id, c.tenant_id, c.sottoscrizione_id, c.evento_id, c.stato, c.tentativi,
               c.riprove, c.prossimo_tentativo, c.lease_id, c.ultimo_stato_http, c.ultimo_codice,
               c.ultimo_errore, c.ultimo_tentativo_il, c.consegnata_il, c.creata_il,
               c.aggiornata_il
        FROM public.webhook_consegne_p028 c
        WHERE EXISTS (SELECT 1 FROM public.eventi_cliente e WHERE e.id = c.evento_id)""")
    op.execute("SELECT setval(pg_get_serial_sequence('public.webhook_consegne', 'id'), "
               "COALESCE((SELECT max(id) FROM public.webhook_consegne_p028), 0) + 1, false)")
    op.execute("CREATE INDEX idx_webhook_consegne_da_fare ON public.webhook_consegne "
               "(prossimo_tentativo) WHERE stato IN ('IN_CODA', 'IN_CONSEGNA')")
    op.execute("CREATE INDEX idx_webhook_consegne_sottoscrizione ON public.webhook_consegne "
               "(sottoscrizione_id, id)")
    op.execute("CREATE INDEX idx_webhook_consegne_sottoscrizione_stato ON public.webhook_consegne "
               "(sottoscrizione_id, stato, id)")
    op.execute("DROP TABLE public.webhook_consegne_p028")
    op.execute("DROP TABLE public.eventi_cliente_p028")


def _downgrade_outbox() -> None:
    op.execute("ALTER TABLE public.outbox_eventi RENAME TO outbox_eventi_p028")
    op.execute("ALTER INDEX public.outbox_eventi_pkey RENAME TO outbox_eventi_p028_pkey")
    op.execute("ALTER INDEX public.idx_outbox_in_sospeso RENAME TO idx_outbox_p028_in_sospeso")
    op.execute("ALTER TABLE public.outbox_eventi_p028 RENAME CONSTRAINT "
               "outbox_eventi_tenant_id_fkey TO outbox_eventi_p028_tenant_id_fkey")
    op.execute("""
        CREATE TABLE public.outbox_eventi (
            id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            sequenza bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
            tenant_id uuid REFERENCES public.tenants (id) ON DELETE RESTRICT,
            topic varchar(200) NOT NULL,
            chiave varchar(200),
            payload jsonb NOT NULL,
            creato_il timestamptz NOT NULL DEFAULT now(),
            pubblicato_il timestamptz,
            tentativi integer NOT NULL DEFAULT 0,
            ultimo_errore text
        )""")
    op.execute("""
        INSERT INTO public.outbox_eventi (id, sequenza, tenant_id, topic, chiave, payload,
                                          creato_il, pubblicato_il, tentativi, ultimo_errore)
        OVERRIDING SYSTEM VALUE
        SELECT id, sequenza, tenant_id, topic, chiave, payload, creato_il, pubblicato_il,
               tentativi, ultimo_errore
        FROM public.outbox_eventi_p028""")
    op.execute("SELECT setval(pg_get_serial_sequence('public.outbox_eventi', 'sequenza'), "
               "COALESCE((SELECT max(sequenza) FROM public.outbox_eventi_p028), 0) + 1, false)")
    op.execute("CREATE INDEX idx_outbox_in_sospeso ON public.outbox_eventi (sequenza) "
               "WHERE pubblicato_il IS NULL")
    op.execute("DROP TABLE public.outbox_eventi_p028")
