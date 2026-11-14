# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Canali esterni predisposti (R13b: VD-C-078, VD-C-163, VD-C-162)

Revision ID: 20261108_055
Revises: 20261107_054
Create Date: 2026-11-08

1. ``canali_pec``: registro dei canali PEC del conservatore (SMTP/IMAP di un gestore
   accreditato, simulatore di sviluppo) con configurazione SENZA segreti
   (``libs.shared.pec``; i segreti nel Secret del lavoratore della posta,
   ``PEC_SEGRETI_JSON``, legati agli host). Senza ente: fuori dalla RLS come
   ``fornitori_firma``. Diritti: SELECT, INSERT, UPDATE al solo gruppo della console
   (``verumdoc_pec_canali``), SELECT al gruppo del lavoratore (``verumdoc_pec``).
   Nessun DELETE (un canale si disattiva).
2. ``pec_canali_stato``: stato onesto di ogni canale scritto dal lavoratore (segreti
   presenti, problemi, ultimo invio e ultima lettura, mai i segreti), letto dalla console.
3. ``pec_messaggi``: messaggi PEC per caso d'uso (scarto, restituzione, riversamento,
   scadenze, prova) con lo stato fino alla ricevuta di consegna (IN_CODA → IN_INVIO →
   INVIATO → ACCETTATO → CONSEGNATO | MANCATA_CONSEGNA; NON_ACCETTATO; NON_PARTITO).
   RLS per ente (la PROVA del conservatore non ha ente). Diritti: chi accoda
   (``verumdoc_pec_accoda``: consumer e console di notification) SELECT e INSERT delle
   SOLE colonne d'origine (il canale solo per la PROVA della console); il lavoratore
   (``verumdoc_pec``) SELECT e UPDATE; nessun
   DELETE. Trigger ``pec_messaggi_nuovi`` (BEFORE INSERT): chi non è il lavoratore
   inserisce solo righe IN_CODA o NON_PARTITO senza dati d'invio. Trigger
   ``pec_messaggi_protetti``: origine immutabile, ``message_id`` e canale fissati una
   volta, stati finali immutabili e — difesa in profondità — uno stato che dipende da
   una ricevuta (ACCETTATO, CONSEGNATO, NON_ACCETTATO, MANCATA_CONSEGNA) si raggiunge
   SOLO se esiste una ricevuta ASSOCIATA del tipo corrispondente; solo i passaggi di
   stato di ``PASSAGGI_PEC`` (IN_CODA solo da IN_INVIO, mai indietro).
4. ``pec_ricevute``: ogni messaggio letto dalla casella (UID IMAP, idempotente) con
   l'esito della verifica (ASSOCIATA, NON_AUTENTICA, NON_ASSOCIATA, OLTRE_LIMITE),
   i dati FIRMATI della ricevuta e il messaggio grezzo (evidenza). Append-only
   (trigger su UPDATE, DELETE e TRUNCATE); INSERT al solo lavoratore. RLS per ente.
5. ``webhook_sottoscrizioni``: segreto PRECEDENTE (cifrato, suffisso, scadenza) per la
   rotazione con doppia firma nel periodo di transizione.
6. ``notifiche_personali`` (VD-C-163): canale personale della campanella, separato
   dagli eventi dell'ente (``eventi_cliente``) e dai webhook. INSERT al gateway (gruppo
   ``verumdoc_credenziali_accesso``), SELECT e UPDATE di ``letto_il`` al gruppo
   ``verumdoc_notifiche_personali`` (notification); trigger: cambia solo ``letto_il``,
   una volta. RLS per ente.
7. ``pratiche.richiede_autorizzazione_produttore`` (VD-C-162, opzione predisposta):
   fotografia alla creazione del parametro
   ``RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE``; predefinito falso (comportamento
   di prima per ogni pratica esistente); immutabile (funzione
   ``vd_pratiche_autorizzazioni``).
8. ``ruoli_personalizzati``: ``pec_canali:gestisci`` fra i mai delegabili.
9. ``esiti_esecuzione``: il fatto ``RIVERSAMENTO_USCITA`` / ``AUTORIZZAZIONI_VERIFICATE``
   del lavoratore (riverifica dell'autorizzazione del produttore, VD-C-162 opzione).

Riprendibile: ``IF NOT EXISTS``, vincoli NOT VALID + VALIDATE in autocommit,
``lock_timeout``. Downgrade: rifiutato se esistono messaggi PEC inviati o ricevute
associate (evidenze); altrimenti toglie tabelle e colonne e ripristina funzione e vincolo.
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from alembic import context, op

revision = "20261108_055"
down_revision = "20261107_054"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
GRUPPO_CANALI = "verumdoc_pec_canali"
GRUPPO_LAVORATORE = "verumdoc_pec"
GRUPPO_ACCODA = "verumdoc_pec_accoda"
GRUPPO_PERSONALI = "verumdoc_notifiche_personali"
GRUPPO_GATEWAY = "verumdoc_credenziali_accesso"
POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
TABELLE_RLS = ("pec_messaggi", "pec_ricevute", "notifiche_personali")
TABELLE_NUOVE = ("canali_pec", "pec_canali_stato", "pec_messaggi", "pec_ricevute",
                 "notifiche_personali")

MAI_DELEGABILI_054 = ("ARRAY['scarto:esegui', 'scarto:autorizza', 'annullamento:approva', "
                      "'firmatari:gestisci', 'pratica:chiudi_eccezioni', 'utenti:gestisci', "
                      "'ruoli:gestisci', 'accesso:configura', "
                      "'firma_fornitori:gestisci']::text[]")
MAI_DELEGABILI = MAI_DELEGABILI_054.replace("'firma_fornitori:gestisci'",
                                            "'firma_fornitori:gestisci', 'pec_canali:gestisci'")
STATI_PEC = ("IN_CODA", "IN_INVIO", "INVIATO", "ACCETTATO", "CONSEGNATO", "NON_ACCETTATO",
             "MANCATA_CONSEGNA", "NON_PARTITO")
CASI_USO = ("SCARTO", "RESTITUZIONE", "RIVERSAMENTO", "SCADENZE", "PROVA")
ESITI_RICEVUTA = ("ASSOCIATA", "NON_AUTENTICA", "NON_ASSOCIATA", "OLTRE_LIMITE")
#: colonne che chi accoda (consumer e console di notification) può scrivere
COLONNE_ORIGINE = ("tenant_id", "caso_uso", "oggetto_id", "destinatario", "oggetto", "testo",
                   "stato", "errore", "creato_da", "canale_codice")


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


_TABELLE = f"""
CREATE TABLE IF NOT EXISTS public.canali_pec (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codice varchar(40) NOT NULL,
    tipo varchar(20) NOT NULL,
    nome varchar(200) NOT NULL,
    attivo boolean NOT NULL DEFAULT false,
    priorita integer NOT NULL DEFAULT 10,
    configurazione jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    versione integer NOT NULL DEFAULT 1,
    aggiornato_da uuid,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT uq_canali_pec_codice UNIQUE (codice),
    CONSTRAINT ck_canali_pec_codice CHECK (codice ~ '^[a-z][a-z0-9-]{{1,39}}$'),
    CONSTRAINT ck_canali_pec_tipo CHECK (tipo IN ('SMTP_IMAP', 'SVILUPPO')),
    CONSTRAINT ck_canali_pec_priorita CHECK (priorita BETWEEN 1 AND 100),
    CONSTRAINT ck_canali_pec_configurazione CHECK (jsonb_typeof(configurazione) = 'object'),
    CONSTRAINT ck_canali_pec_versione CHECK (versione >= 1)
);
CREATE TABLE IF NOT EXISTS public.pec_canali_stato (
    canale_codice varchar(40) PRIMARY KEY,
    segreti_presenti jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    problemi jsonb NOT NULL DEFAULT '[]'::jsonb,
    ultimo_invio_il timestamp with time zone,
    ultima_lettura_il timestamp with time zone,
    ultimo_errore varchar(500),
    aggiornato_il timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_pec_canali_stato_problemi CHECK (jsonb_typeof(problemi) = 'array')
);
CREATE TABLE IF NOT EXISTS public.pec_messaggi (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid,
    caso_uso varchar(20) NOT NULL,
    oggetto_id varchar(200) NOT NULL,
    destinatario varchar(320) NOT NULL,
    oggetto varchar(300) NOT NULL,
    testo text NOT NULL,
    stato varchar(20) NOT NULL DEFAULT 'IN_CODA',
    canale_codice varchar(40),
    message_id varchar(500),
    tentativi integer NOT NULL DEFAULT 0,
    prossimo_tentativo timestamp with time zone,
    errore varchar(500),
    inviato_il timestamp with time zone,
    accettato_il timestamp with time zone,
    consegnato_il timestamp with time zone,
    esito_il timestamp with time zone,
    creato_da uuid,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT fk_pec_messaggi_tenant FOREIGN KEY (tenant_id)
        REFERENCES public.tenants (id) ON DELETE RESTRICT,
    CONSTRAINT ck_pec_messaggi_stato CHECK (stato IN ({_elenco(STATI_PEC)})),
    CONSTRAINT ck_pec_messaggi_caso CHECK (caso_uso IN ({_elenco(CASI_USO)})),
    CONSTRAINT ck_pec_messaggi_ente CHECK (caso_uso = 'PROVA' OR tenant_id IS NOT NULL),
    CONSTRAINT ck_pec_messaggi_testo CHECK (octet_length(testo) <= 65536)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pec_messaggi_dedup
    ON public.pec_messaggi (tenant_id, caso_uso, destinatario, oggetto_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pec_messaggi_message_id
    ON public.pec_messaggi (message_id) WHERE message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pec_messaggi_da_inviare
    ON public.pec_messaggi (prossimo_tentativo NULLS FIRST, created_at)
    WHERE stato IN ('IN_CODA', 'IN_INVIO');
CREATE INDEX IF NOT EXISTS idx_pec_messaggi_cursore
    ON public.pec_messaggi (created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS public.pec_ricevute (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    canale_codice varchar(40) NOT NULL,
    imap_uidvalidity bigint NOT NULL,
    imap_uid bigint NOT NULL,
    esito varchar(20) NOT NULL,
    motivo varchar(500),
    messaggio_id uuid,
    tenant_id uuid,
    tipo varchar(30),
    msgid varchar(500),
    identificativo varchar(300),
    gestore varchar(300),
    consegna varchar(320),
    errore varchar(40),
    errore_esteso varchar(500),
    data_ricevuta timestamp with time zone,
    certificato_sha256 char(64),
    certificato_soggetto varchar(500),
    impronta_sha256 char(64),
    dimensione integer NOT NULL DEFAULT 0,
    eml bytea,
    ricevuta_il timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT fk_pec_ricevute_messaggio FOREIGN KEY (messaggio_id)
        REFERENCES public.pec_messaggi (id) ON DELETE RESTRICT,
    CONSTRAINT uq_pec_ricevute_imap UNIQUE (canale_codice, imap_uidvalidity, imap_uid),
    CONSTRAINT ck_pec_ricevute_esito CHECK (esito IN ({_elenco(ESITI_RICEVUTA)})),
    CONSTRAINT ck_pec_ricevute_associata CHECK (
        (esito = 'ASSOCIATA') = (messaggio_id IS NOT NULL)),
    CONSTRAINT ck_pec_ricevute_eml CHECK (eml IS NULL OR octet_length(eml) <= 52428800)
);
CREATE INDEX IF NOT EXISTS idx_pec_ricevute_messaggio
    ON public.pec_ricevute (messaggio_id) WHERE messaggio_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pec_ricevute_non_associate
    ON public.pec_ricevute (id DESC) WHERE esito <> 'ASSOCIATA';
CREATE TABLE IF NOT EXISTS public.notifiche_personali (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    tipo varchar(60) NOT NULL,
    titolo varchar(300) NOT NULL,
    collegamento varchar(300) NOT NULL DEFAULT '/preferenze/credenziali',
    creato_il timestamp with time zone NOT NULL DEFAULT now(),
    letto_il timestamp with time zone,
    CONSTRAINT fk_notifiche_personali_utente FOREIGN KEY (user_id)
        REFERENCES public.users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_notifiche_personali_tenant FOREIGN KEY (tenant_id)
        REFERENCES public.tenants (id) ON DELETE RESTRICT,
    CONSTRAINT ck_notifiche_personali_collegamento CHECK (collegamento LIKE '/%')
);
CREATE INDEX IF NOT EXISTS idx_notifiche_personali_utente
    ON public.notifiche_personali (user_id, creato_il DESC)
"""

_COLONNE_WEBHOOK = (
    ("segreto_precedente_cifrato", "text"),
    ("segreto_precedente_suffisso", "varchar(8)"),
    ("segreto_precedente_scade_il", "timestamp with time zone"),
    ("segreto_ruotato_il", "timestamp with time zone"),
)


def _crea_gruppo(gruppo: str) -> str:
    return f"""
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppo}') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE {gruppo} NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo {gruppo} non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;"""


def _revoca(tabella: str) -> str:
    return (f"REVOKE ALL ON public.{tabella} FROM PUBLIC; IF EXISTS (SELECT 1 FROM pg_roles "
            f"WHERE rolname = '{RUOLO_APPLICAZIONE}') THEN REVOKE ALL ON public.{tabella} FROM "
            f"{RUOLO_APPLICAZIONE}; REVOKE ALL ON SEQUENCE public.{tabella}_id_seq FROM "
            f"{RUOLO_APPLICAZIONE}; END IF;"
            if tabella in ("pec_ricevute", "notifiche_personali") else
            f"REVOKE ALL ON public.{tabella} FROM PUBLIC; IF EXISTS (SELECT 1 FROM pg_roles "
            f"WHERE rolname = '{RUOLO_APPLICAZIONE}') THEN REVOKE ALL ON public.{tabella} FROM "
            f"{RUOLO_APPLICAZIONE}; END IF;")


def _concedi(gruppo: str, diritti: str, oggetto: str) -> str:
    return (f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppo}') THEN "
            f"GRANT {diritti} ON {oggetto} TO {gruppo}; END IF;")


_PRIVILEGI = "DO $p$\nBEGIN" + "".join(_crea_gruppo(g) for g in (
    GRUPPO_CANALI, GRUPPO_LAVORATORE, GRUPPO_ACCODA, GRUPPO_PERSONALI)) + "\n    " + " ".join([
        *(_revoca(t) for t in TABELLE_NUOVE),
        _concedi(GRUPPO_CANALI, "SELECT, INSERT, UPDATE", "public.canali_pec"),
        _concedi(GRUPPO_CANALI, "SELECT", "public.pec_canali_stato"),
        _concedi(GRUPPO_CANALI, "SELECT", "public.pec_messaggi"),
        _concedi(GRUPPO_CANALI, "SELECT", "public.pec_ricevute"),
        _concedi(GRUPPO_LAVORATORE, "SELECT", "public.canali_pec"),
        _concedi(GRUPPO_LAVORATORE, "SELECT, INSERT, UPDATE", "public.pec_canali_stato"),
        _concedi(GRUPPO_LAVORATORE, "SELECT, UPDATE", "public.pec_messaggi"),
        _concedi(GRUPPO_LAVORATORE, "SELECT, INSERT", "public.pec_ricevute"),
        _concedi(GRUPPO_LAVORATORE, "USAGE", "SEQUENCE public.pec_ricevute_id_seq"),
        _concedi(GRUPPO_ACCODA, "SELECT", "public.pec_messaggi"),
        _concedi(GRUPPO_ACCODA, "INSERT (" + ", ".join(COLONNE_ORIGINE) + ")",
                 "public.pec_messaggi"),
        _concedi(GRUPPO_PERSONALI, "SELECT", "public.notifiche_personali"),
        _concedi(GRUPPO_PERSONALI, "UPDATE (letto_il)", "public.notifiche_personali"),
        _concedi(GRUPPO_GATEWAY, "INSERT", "public.notifiche_personali"),
        _concedi(GRUPPO_GATEWAY, "USAGE", "SEQUENCE public.notifiche_personali_id_seq"),
    ]) + "\nEND\n$p$;"

_MESSAGGI_NUOVI = f"""
CREATE OR REPLACE FUNCTION public.pec_messaggi_nuovi()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{GRUPPO_LAVORATORE}')
       AND pg_has_role(current_user, '{GRUPPO_LAVORATORE}', 'MEMBER') THEN
        RETURN NEW;
    END IF;
    IF NEW.stato NOT IN ('IN_CODA', 'NON_PARTITO') OR NEW.message_id IS NOT NULL
       OR (NEW.canale_codice IS NOT NULL AND NEW.caso_uso <> 'PROVA') OR NEW.tentativi <> 0
       OR NEW.inviato_il IS NOT NULL OR NEW.accettato_il IS NOT NULL
       OR NEW.consegnato_il IS NOT NULL OR NEW.esito_il IS NOT NULL THEN
        RAISE EXCEPTION 'pec_messaggi: un messaggio nuovo entra IN_CODA (o NON_PARTITO con il '
            'motivo) senza dati d''invio: solo il lavoratore della posta li scrive';
    END IF;
    RETURN NEW;
END;
$$"""

#: Passaggi di stato ammessi (revisione R13b, rilievo 5). Uguale a
#: ``services/notification/app/domain/pec.py::PASSAGGI`` (test che li confronta). IN_CODA
#: solo da IN_INVIO (ritentativo o recupero); nessun ritorno indietro; finali immutabili.
PASSAGGI_PEC: tuple[tuple[str, str], ...] = (
    ("IN_CODA", "IN_INVIO"), ("IN_CODA", "NON_PARTITO"),
    ("IN_INVIO", "IN_CODA"), ("IN_INVIO", "INVIATO"), ("IN_INVIO", "NON_PARTITO"),
    ("IN_INVIO", "ACCETTATO"), ("IN_INVIO", "NON_ACCETTATO"), ("IN_INVIO", "CONSEGNATO"),
    ("IN_INVIO", "MANCATA_CONSEGNA"),
    ("INVIATO", "ACCETTATO"), ("INVIATO", "NON_ACCETTATO"), ("INVIATO", "CONSEGNATO"),
    ("INVIATO", "MANCATA_CONSEGNA"),
    ("ACCETTATO", "CONSEGNATO"), ("ACCETTATO", "MANCATA_CONSEGNA"),
)
_PASSAGGI_SQL = ", ".join(f"('{da}', '{a}')" for da, a in PASSAGGI_PEC)

_MESSAGGI_PROTETTI = """
CREATE OR REPLACE FUNCTION public.pec_messaggi_protetti()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    attesa text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pec_messaggi: DELETE vietata (evidenza della comunicazione)';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.caso_uso IS DISTINCT FROM OLD.caso_uso
       OR NEW.oggetto_id IS DISTINCT FROM OLD.oggetto_id
       OR NEW.destinatario IS DISTINCT FROM OLD.destinatario
       OR NEW.oggetto IS DISTINCT FROM OLD.oggetto OR NEW.testo IS DISTINCT FROM OLD.testo
       OR NEW.creato_da IS DISTINCT FROM OLD.creato_da
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'pec_messaggi: origine del messaggio % non modificabile', OLD.id;
    END IF;
    IF (OLD.message_id IS NOT NULL AND NEW.message_id IS DISTINCT FROM OLD.message_id)
       OR (OLD.canale_codice IS NOT NULL
           AND NEW.canale_codice IS DISTINCT FROM OLD.canale_codice) THEN
        RAISE EXCEPTION 'pec_messaggi: Message-ID e canale del messaggio % fissati', OLD.id;
    END IF;
    IF OLD.stato IN ('CONSEGNATO', 'NON_ACCETTATO', 'MANCATA_CONSEGNA', 'NON_PARTITO') THEN
        IF NEW.stato IS DISTINCT FROM OLD.stato OR NEW.errore IS DISTINCT FROM OLD.errore
           OR NEW.inviato_il IS DISTINCT FROM OLD.inviato_il
           OR NEW.consegnato_il IS DISTINCT FROM OLD.consegnato_il
           OR NEW.esito_il IS DISTINCT FROM OLD.esito_il
           OR (OLD.accettato_il IS NOT NULL
               AND NEW.accettato_il IS DISTINCT FROM OLD.accettato_il) THEN
            RAISE EXCEPTION 'pec_messaggi: esito del messaggio % già registrato', OLD.id;
        END IF;
    END IF;
    IF NEW.stato IS DISTINCT FROM OLD.stato AND NOT EXISTS (
            SELECT 1 FROM (VALUES __PASSAGGI__) AS p(da, a)
            WHERE p.da = OLD.stato AND p.a = NEW.stato) THEN
        RAISE EXCEPTION 'pec_messaggi: passaggio di stato % → % non ammesso (messaggio %)',
            OLD.stato, NEW.stato, OLD.id;
    END IF;
    IF NEW.stato IS DISTINCT FROM OLD.stato
       AND NEW.stato IN ('ACCETTATO', 'CONSEGNATO', 'NON_ACCETTATO', 'MANCATA_CONSEGNA') THEN
        attesa := CASE NEW.stato WHEN 'ACCETTATO' THEN 'ACCETTAZIONE'
                                 WHEN 'CONSEGNATO' THEN 'AVVENUTA_CONSEGNA'
                                 WHEN 'NON_ACCETTATO' THEN 'NON_ACCETTAZIONE'
                                 ELSE 'ERRORE_CONSEGNA' END;
        IF NOT EXISTS (SELECT 1 FROM public.pec_ricevute r WHERE r.messaggio_id = NEW.id
                       AND r.esito = 'ASSOCIATA' AND r.tipo = attesa) THEN
            RAISE EXCEPTION 'pec_messaggi: lo stato % del messaggio % richiede una ricevuta % '
                'autentica e associata', NEW.stato, OLD.id, attesa;
        END IF;
    END IF;
    RETURN NEW;
END;
$$""".replace("__PASSAGGI__", _PASSAGGI_SQL)

_RICEVUTE_PROTETTE = """
CREATE OR REPLACE FUNCTION public.pec_ricevute_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'pec_ricevute è append-only: % vietata', TG_OP;
END;
$$"""

_PERSONALI_PROTETTE = """
CREATE OR REPLACE FUNCTION public.notifiche_personali_protette()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'notifiche_personali: DELETE vietata';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.tipo IS DISTINCT FROM OLD.tipo
       OR NEW.titolo IS DISTINCT FROM OLD.titolo
       OR NEW.collegamento IS DISTINCT FROM OLD.collegamento
       OR NEW.creato_il IS DISTINCT FROM OLD.creato_il
       OR (OLD.letto_il IS NOT NULL AND NEW.letto_il IS DISTINCT FROM OLD.letto_il) THEN
        RAISE EXCEPTION 'notifiche_personali: cambia solo letto_il, una volta';
    END IF;
    RETURN NEW;
END;
$$"""

_TRIGGER = (
    "DROP TRIGGER IF EXISTS trg_pec_messaggi_nuovi ON public.pec_messaggi",
    "CREATE TRIGGER trg_pec_messaggi_nuovi BEFORE INSERT ON public.pec_messaggi "
    "FOR EACH ROW EXECUTE FUNCTION public.pec_messaggi_nuovi()",
    "DROP TRIGGER IF EXISTS trg_pec_messaggi_protetti ON public.pec_messaggi",
    "CREATE TRIGGER trg_pec_messaggi_protetti BEFORE UPDATE OR DELETE ON public.pec_messaggi "
    "FOR EACH ROW EXECUTE FUNCTION public.pec_messaggi_protetti()",
    "DROP TRIGGER IF EXISTS trg_pec_ricevute_append_only ON public.pec_ricevute",
    "CREATE TRIGGER trg_pec_ricevute_append_only BEFORE UPDATE OR DELETE ON public.pec_ricevute "
    "FOR EACH ROW EXECUTE FUNCTION public.pec_ricevute_append_only()",
    "DROP TRIGGER IF EXISTS trg_pec_ricevute_truncate ON public.pec_ricevute",
    "CREATE TRIGGER trg_pec_ricevute_truncate BEFORE TRUNCATE ON public.pec_ricevute "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.pec_ricevute_append_only()",
    "DROP TRIGGER IF EXISTS trg_notifiche_personali_protette ON public.notifiche_personali",
    "CREATE TRIGGER trg_notifiche_personali_protette BEFORE UPDATE OR DELETE "
    "ON public.notifiche_personali FOR EACH ROW "
    "EXECUTE FUNCTION public.notifiche_personali_protette()",
)

#: Funzione di protezione delle pratiche (migrazione 044) con la fotografia della
#: richiesta di autorizzazione del produttore per il riversamento in uscita
_PRATICHE = """
CREATE OR REPLACE FUNCTION public.vd_pratiche_autorizzazioni()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.impronta_piano IS NOT NULL
       AND NEW.impronta_piano IS DISTINCT FROM OLD.impronta_piano THEN
        RAISE EXCEPTION 'pratiche: impronta del piano approvato non modificabile'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.richiede_nulla_osta AND NOT NEW.richiede_nulla_osta THEN
        RAISE EXCEPTION 'pratiche: il nulla osta richiesto non si toglie'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.richiede_autorizzazione_produttore
       IS DISTINCT FROM OLD.richiede_autorizzazione_produttore THEN
        RAISE EXCEPTION 'pratiche: la richiesta di autorizzazione del produttore è fissata '
            'alla creazione' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

_RIFIUTA_DOWNGRADE = """
DO $d$
BEGIN
    IF EXISTS (SELECT 1 FROM public.pec_messaggi WHERE message_id IS NOT NULL)
       OR EXISTS (SELECT 1 FROM public.pec_ricevute WHERE esito = 'ASSOCIATA') THEN
        RAISE EXCEPTION 'downgrade 20261108_055 impossibile: esistono messaggi PEC inviati o '
            'ricevute associate (evidenze)';
    END IF;
    IF EXISTS (SELECT 1 FROM public.pratiche WHERE richiede_autorizzazione_produttore)
       OR EXISTS (SELECT 1 FROM public.esiti_esecuzione WHERE tipo = 'RIVERSAMENTO_USCITA') THEN
        RAISE EXCEPTION 'downgrade 20261108_055 impossibile: esistono pratiche con '
            'l''autorizzazione del produttore richiesta';
    END IF;
END
$d$;
"""


def _modulo_053():  # noqa: ANN202
    percorso = Path(__file__).with_name("20261106_053_restituzione_legata.py")
    spec = importlib.util.spec_from_file_location("migrazione_053", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _vincoli_esiti(tipi: tuple[str, ...], coppie: str) -> None:
    for nome, condizione in (("ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(tipi)})"),
                             ("ck_esiti_esecuzione_coppia", coppie)):
        op.execute(f"ALTER TABLE public.esiti_esecuzione DROP CONSTRAINT IF EXISTS {nome}")
        op.execute(f"ALTER TABLE public.esiti_esecuzione ADD CONSTRAINT {nome} "
                   f"CHECK ({condizione}) NOT VALID")


_CONVALIDA_ESITI = tuple(f"ALTER TABLE public.esiti_esecuzione VALIDATE CONSTRAINT {v}"
                         for v in ("ck_esiti_esecuzione_tipo", "ck_esiti_esecuzione_coppia"))


def _funzione_044() -> str:
    """Funzione delle pratiche come la lascia la migrazione 044 (downgrade, regola 3
    del runbook migrazioni_audit: la versione precedente si legge dal suo file)."""
    percorso = Path(__file__).with_name("20261024_044_autorizzazioni_scarto.py")
    spec = importlib.util.spec_from_file_location("migrazione_044", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return str(modulo._PRATICHE)


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _in_autocommit(*istruzioni: str) -> None:
    if context.is_offline_mode():
        for i in istruzioni:
            op.execute(i)
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def _vincolo_ruoli(elenco: str) -> None:
    op.execute("ALTER TABLE public.ruoli_personalizzati DROP CONSTRAINT IF EXISTS "
               "ck_ruoli_personalizzati_non_delegabili")
    op.execute("ALTER TABLE public.ruoli_personalizzati ADD CONSTRAINT "
               f"ck_ruoli_personalizzati_non_delegabili CHECK (NOT (permessi && {elenco})) "
               "NOT VALID")


def upgrade() -> None:
    _limita_attesa_lock()
    for istruzione in (s.strip() for s in _TABELLE.split(";")):
        if istruzione:
            op.execute(istruzione)
    for tabella in TABELLE_RLS:
        op.execute(f"ALTER TABLE public.{tabella} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA} ON public.{tabella} AS PERMISSIVE FOR ALL "
                   f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    op.execute(_PRIVILEGI)
    for funzione in (_MESSAGGI_NUOVI, _MESSAGGI_PROTETTI, _RICEVUTE_PROTETTE,
                     _PERSONALI_PROTETTE):
        op.execute(funzione)
    for istruzione in _TRIGGER:
        op.execute(istruzione)
    # webhook: segreto precedente (colonne senza predefinito: solo catalogo)
    for colonna, tipo in _COLONNE_WEBHOOK:
        op.execute(f"ALTER TABLE public.webhook_sottoscrizioni ADD COLUMN IF NOT EXISTS "
                   f"{colonna} {tipo}")
    op.execute("ALTER TABLE public.webhook_sottoscrizioni DROP CONSTRAINT IF EXISTS "
               "ck_webhook_sottoscrizioni_precedente")
    op.execute("ALTER TABLE public.webhook_sottoscrizioni ADD CONSTRAINT "
               "ck_webhook_sottoscrizioni_precedente CHECK ((segreto_precedente_cifrato IS NULL)"
               " = (segreto_precedente_scade_il IS NULL)) NOT VALID")
    # pratiche: predefinito costante (solo catalogo, nessuna riscrittura della tabella)
    op.execute("ALTER TABLE public.pratiche ADD COLUMN IF NOT EXISTS "
               "richiede_autorizzazione_produttore boolean NOT NULL DEFAULT false")
    op.execute(_PRATICHE)
    _vincolo_ruoli(MAI_DELEGABILI)
    m053 = _modulo_053()
    _vincoli_esiti((*m053.TIPI, "RIVERSAMENTO_USCITA"),
                   m053.COPPIE + " OR (tipo = 'RIVERSAMENTO_USCITA' AND esito = "
                                 "'AUTORIZZAZIONI_VERIFICATE')")
    _in_autocommit(
        "ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
        "ck_ruoli_personalizzati_non_delegabili",
        "ALTER TABLE public.webhook_sottoscrizioni VALIDATE CONSTRAINT "
        "ck_webhook_sottoscrizioni_precedente", *_CONVALIDA_ESITI)


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute(_RIFIUTA_DOWNGRADE)
    _vincolo_ruoli(MAI_DELEGABILI_054)
    m053 = _modulo_053()
    _vincoli_esiti(m053.TIPI, m053.COPPIE)
    op.execute(_funzione_044())
    op.execute("ALTER TABLE public.pratiche DROP COLUMN IF EXISTS "
               "richiede_autorizzazione_produttore")
    op.execute("ALTER TABLE public.webhook_sottoscrizioni DROP CONSTRAINT IF EXISTS "
               "ck_webhook_sottoscrizioni_precedente")
    for colonna, _tipo in reversed(_COLONNE_WEBHOOK):
        op.execute(f"ALTER TABLE public.webhook_sottoscrizioni DROP COLUMN IF EXISTS {colonna}")
    for tabella in ("notifiche_personali", "pec_ricevute", "pec_messaggi", "pec_canali_stato",
                    "canali_pec"):
        op.execute(f"DROP TABLE IF EXISTS public.{tabella}")
    for funzione in ("pec_messaggi_nuovi", "pec_messaggi_protetti", "pec_ricevute_append_only",
                     "notifiche_personali_protette"):
        op.execute(f"DROP FUNCTION IF EXISTS public.{funzione}()")
    _in_autocommit("ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
                   "ck_ruoli_personalizzati_non_delegabili", *_CONVALIDA_ESITI)
