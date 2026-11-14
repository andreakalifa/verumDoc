# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Chiusura dei residui interni (R16: VD-C-166, VD-C-153)

Revision ID: 20261110_057
Revises: 20261109_056
Create Date: 2026-11-10

NOTA DI RILASCIO: R15 si sviluppa in parallelo e potrebbe avere anch'essa una revisione
057 sopra la 056. Al merge dei due rami questa revisione si RINUMERA (nome del file,
``revision`` e ``down_revision`` della successiva) in modo che la catena resti lineare;
il contenuto non dipende da R15 (nessuna tabella di R15 è citata qui).

1. VD-C-166 — ``credenziali_firma_personali`` (VD-P-140): credenziale di firma PERSONALE
   di una persona (identificativo CSC ``credentialID`` presso un fornitore di firma CSC del
   registro ``fornitori_firma`` e impronta SHA-256 ATTESA del suo certificato), registrata
   dal conservatore in console. Nessun segreto: il cliente OAuth2 è quello del fornitore
   (Secret del servizio di firma), PIN e OTP li dà la persona a ogni firma. Una sola
   credenziale attiva per persona (indice unico parziale). Senza ente (persone del
   conservatore): fuori dalla RLS come ``fornitori_firma``. Diritti: SELECT, INSERT,
   UPDATE al gruppo ``verumdoc_firma_fornitori`` (onboarding), SELECT al gruppo
   ``verumdoc_firma`` (servizio di firma) e ``verumdoc_firme_documenti_sigillo``
   (hashchain, riverifica prima dell'archiviazione). Nessun DELETE: si disattiva.
2. VD-C-166 — ``firme_responsabile``: punto d'aggancio GENERICO «documento da firmare dal
   responsabile» (manuale, piano, nomine o qualunque ``tipo`` in maiuscolo) con stato
   ``DA_FIRMARE`` → ``FIRMATO`` (busta prodotta e verificata dal servizio di firma con
   l'OTP della persona) → ``ARCHIVIATO`` (busta nel WORM dalla hashchain, chiave con
   l'impronta della busta) | ``RESPINTO`` (la riverifica della hashchain non torna);
   ``DA_FIRMARE`` → ``ANNULLATO``. Trigger ``firme_responsabile_protette`` su INSERT,
   UPDATE e DELETE (§31 R13a (3)): una riga nuova entra solo ``DA_FIRMARE`` senza dati
   della firma; contenuto, impronta e richiesta non cambiano mai; ogni passaggio fuori
   dall'elenco è rifiutato; ``FIRMATO`` pretende busta con ``busta_sha256`` uguale alla
   sua impronta, firmatario, credenziale e certificato uguale a quello ATTESO dalla
   credenziale (letta nel database: chi scrive non può dichiararne un altro);
   ``ARCHIVIATO`` pretende una chiave WORM che contiene l'impronta del contenuto e la
   prima parte dell'impronta della busta; dopo ``FIRMATO`` la firma non cambia più;
   nessun DELETE (nemmeno dal proprietario). Diritti per colonna: il gruppo nuovo
   ``verumdoc_firme_responsabile`` (login di dashboard-api) inserisce le sole colonne
   della richiesta e aggiorna solo quelle della firma; la hashchain
   (``verumdoc_firme_documenti_sigillo``) aggiorna solo quelle dell'archiviazione.
3. VD-C-153 — ``versioni_accesso``: contatori per ambito (``*`` = conservatore, o
   l'ente) e tipo (``METODI``, ``CONCEDIBILI``) alzati dal trigger
   ``vd_versioni_accesso`` di ``impostazioni_valori`` (SECURITY DEFINER, ``search_path``
   fissato) a ogni INSERT, UPDATE o DELETE di ``ACCESSO_METODI_AMMESSI`` o
   ``RUOLI_PERMESSI_CONCEDIBILI`` (anche ``LIMITE.<codice>``, anche dalle modifiche
   programmate applicate alla decorrenza). Il token porta la somma dei contatori del
   conservatore e dell'ente all'emissione; la revoca la confronta con la stessa query già
   fatta per utente ed ente (nessun costo in più per richiesta). SELECT al gruppo dei
   servizi; nessuna scrittura diretta (solo il trigger).

Riprendibile: ``IF NOT EXISTS``, ``CREATE OR REPLACE``, ``lock_timeout``. Downgrade:
rifiutato se esistono firme personali FIRMATO o ARCHIVIATO (evidenze); altrimenti toglie
trigger, funzioni e tabelle.
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import os

from alembic import op

revision = "20261110_057"
down_revision = "20261109_056"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
GRUPPO_FORNITORI = "verumdoc_firma_fornitori"
GRUPPO_FIRMA = "verumdoc_firma"
GRUPPO_SIGILLO = "verumdoc_firme_documenti_sigillo"
GRUPPO_RESPONSABILE = "verumdoc_firme_responsabile"
TABELLE_NUOVE = ("credenziali_firma_personali", "firme_responsabile", "versioni_accesso")
#: dimensione massima del contenuto da firmare (come i documenti del conservatore)
CONTENUTO_MASSIMO = 5 * 1024 * 1024
STATI = ("DA_FIRMARE", "FIRMATO", "ARCHIVIATO", "RESPINTO", "ANNULLATO")
#: passaggi ammessi (ribaditi dal dominio: ``libs.shared.firme_responsabile.PASSAGGI``)
PASSAGGI = (("DA_FIRMARE", "FIRMATO"), ("DA_FIRMARE", "ANNULLATO"),
            ("FIRMATO", "ARCHIVIATO"), ("FIRMATO", "RESPINTO"))
#: colonne della richiesta (chi accoda) e della firma (dashboard-api dopo l'OTP)
COLONNE_RICHIESTA = ("tipo", "titolo", "nome", "contenuto", "impronta_sha256",
                     "richiesto_da", "firmatario_atteso")
COLONNE_FIRMA = ("stato", "firmato_da", "firmato_il", "credenziale_id", "busta",
                 "busta_sha256", "certificato_sha256", "certificato_soggetto", "livello",
                 "annullato_da", "motivo", "updated_at")
COLONNE_ARCHIVIO = ("stato", "busta_chiave", "busta_version_id", "archiviato_il", "motivo",
                    "tentativi", "prossimo_tentativo_il", "updated_at")
CODICI_ACCESSO = ("ACCESSO_METODI_AMMESSI", "RUOLI_PERMESSI_CONCEDIBILI")

_TABELLE = f"""
CREATE TABLE IF NOT EXISTS public.credenziali_firma_personali (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.users(id),
    fornitore_codice varchar(40) NOT NULL,
    credential_id varchar(200) NOT NULL,
    certificato_sha256 char(64) NOT NULL,
    descrizione varchar(200),
    attiva boolean NOT NULL DEFAULT true,
    versione integer NOT NULL DEFAULT 1,
    aggiornato_da uuid,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_credenziali_firma_personali_cert CHECK (certificato_sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT ck_credenziali_firma_personali_cred CHECK (length(btrim(credential_id)) > 0),
    CONSTRAINT ck_credenziali_firma_personali_versione CHECK (versione >= 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_credenziali_firma_personali_attiva
    ON public.credenziali_firma_personali (user_id) WHERE attiva;
CREATE TABLE IF NOT EXISTS public.firme_responsabile (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo varchar(30) NOT NULL,
    titolo varchar(300) NOT NULL,
    nome varchar(200) NOT NULL,
    contenuto bytea NOT NULL,
    impronta_sha256 char(64) NOT NULL,
    stato varchar(12) NOT NULL DEFAULT 'DA_FIRMARE',
    richiesto_da uuid,
    firmatario_atteso uuid,
    firmato_da uuid,
    firmato_il timestamp with time zone,
    credenziale_id uuid REFERENCES public.credenziali_firma_personali(id),
    busta bytea,
    busta_sha256 char(64),
    certificato_sha256 char(64),
    certificato_soggetto varchar(500),
    livello varchar(10),
    busta_chiave varchar(1024),
    busta_version_id varchar(1024),
    archiviato_il timestamp with time zone,
    annullato_da uuid,
    motivo varchar(500),
    tentativi integer NOT NULL DEFAULT 0,
    prossimo_tentativo_il timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_firme_responsabile_tipo CHECK (tipo ~ '^[A-Z][A-Z0-9_]{{1,29}}$'),
    CONSTRAINT ck_firme_responsabile_stato CHECK (stato IN ({", ".join(f"'{s}'" for s in STATI)})),
    CONSTRAINT ck_firme_responsabile_contenuto CHECK (octet_length(contenuto) BETWEEN 1 AND {
        CONTENUTO_MASSIMO}),
    CONSTRAINT ck_firme_responsabile_impronta CHECK (impronta_sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT ck_firme_responsabile_firmato CHECK (stato NOT IN ('FIRMATO', 'ARCHIVIATO')
        OR (busta IS NOT NULL AND busta_sha256 IS NOT NULL AND firmato_da IS NOT NULL
            AND firmato_il IS NOT NULL AND credenziale_id IS NOT NULL
            AND certificato_sha256 IS NOT NULL)),
    CONSTRAINT ck_firme_responsabile_archiviato CHECK (stato <> 'ARCHIVIATO'
        OR (busta_chiave IS NOT NULL AND archiviato_il IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firme_responsabile_documento
    ON public.firme_responsabile (tipo, impronta_sha256)
    WHERE stato NOT IN ('ANNULLATO', 'RESPINTO');
CREATE INDEX IF NOT EXISTS idx_firme_responsabile_da_archiviare
    ON public.firme_responsabile (prossimo_tentativo_il NULLS FIRST, created_at)
    WHERE stato = 'FIRMATO';
CREATE TABLE IF NOT EXISTS public.versioni_accesso (
    ambito varchar(40) NOT NULL,
    tipo varchar(12) NOT NULL,
    versione bigint NOT NULL DEFAULT 0,
    aggiornato_il timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_versioni_accesso PRIMARY KEY (ambito, tipo),
    CONSTRAINT ck_versioni_accesso_tipo CHECK (tipo IN ('METODI', 'CONCEDIBILI'))
)
"""

_PASSAGGI_SQL = " OR ".join(f"(OLD.stato = '{a}' AND NEW.stato = '{b}')" for a, b in PASSAGGI)

_PROTEZIONE = f"""
CREATE OR REPLACE FUNCTION public.firme_responsabile_protette()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    atteso text;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'firme_responsabile: % vietata (evidenza della firma)', TG_OP;
    END IF;
    IF TG_OP = 'INSERT' THEN
        -- §31 R13a (3): chi accoda non inserisce righe già «fatte»
        IF NEW.stato IS DISTINCT FROM 'DA_FIRMARE' OR NEW.firmato_da IS NOT NULL
           OR NEW.firmato_il IS NOT NULL OR NEW.credenziale_id IS NOT NULL
           OR NEW.busta IS NOT NULL OR NEW.busta_sha256 IS NOT NULL
           OR NEW.certificato_sha256 IS NOT NULL OR NEW.certificato_soggetto IS NOT NULL
           OR NEW.livello IS NOT NULL OR NEW.busta_chiave IS NOT NULL
           OR NEW.busta_version_id IS NOT NULL OR NEW.archiviato_il IS NOT NULL
           OR NEW.annullato_da IS NOT NULL OR NEW.tentativi <> 0 THEN
            RAISE EXCEPTION 'firme_responsabile: un documento nuovo entra DA_FIRMARE senza '
                'dati della firma';
        END IF;
        IF encode(sha256(NEW.contenuto), 'hex') IS DISTINCT FROM NEW.impronta_sha256 THEN
            RAISE EXCEPTION 'firme_responsabile: impronta diversa dal contenuto';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tipo IS DISTINCT FROM OLD.tipo
       OR NEW.titolo IS DISTINCT FROM OLD.titolo OR NEW.nome IS DISTINCT FROM OLD.nome
       OR NEW.contenuto IS DISTINCT FROM OLD.contenuto
       OR NEW.impronta_sha256 IS DISTINCT FROM OLD.impronta_sha256
       OR NEW.richiesto_da IS DISTINCT FROM OLD.richiesto_da
       OR NEW.firmatario_atteso IS DISTINCT FROM OLD.firmatario_atteso
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'firme_responsabile: la richiesta non cambia';
    END IF;
    IF NEW.stato IS DISTINCT FROM OLD.stato AND NOT ({_PASSAGGI_SQL}) THEN
        RAISE EXCEPTION 'firme_responsabile: passaggio % -> % non ammesso', OLD.stato, NEW.stato;
    END IF;
    -- archiviazione e rifiuto solo dalla hashchain (che non può scrivere la firma)
    IF NEW.stato IN ('ARCHIVIATO', 'RESPINTO') AND NEW.stato IS DISTINCT FROM OLD.stato
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{GRUPPO_SIGILLO}')
       AND NOT pg_has_role(current_user, '{GRUPPO_SIGILLO}', 'MEMBER') THEN
        RAISE EXCEPTION 'firme_responsabile: solo la hashchain archivia o respinge';
    END IF;
    IF OLD.stato <> 'DA_FIRMARE' AND (
           NEW.firmato_da IS DISTINCT FROM OLD.firmato_da
           OR NEW.firmato_il IS DISTINCT FROM OLD.firmato_il
           OR NEW.credenziale_id IS DISTINCT FROM OLD.credenziale_id
           OR NEW.busta IS DISTINCT FROM OLD.busta
           OR NEW.busta_sha256 IS DISTINCT FROM OLD.busta_sha256
           OR NEW.certificato_sha256 IS DISTINCT FROM OLD.certificato_sha256
           OR NEW.certificato_soggetto IS DISTINCT FROM OLD.certificato_soggetto
           OR NEW.livello IS DISTINCT FROM OLD.livello
           OR NEW.annullato_da IS DISTINCT FROM OLD.annullato_da) THEN
        RAISE EXCEPTION 'firme_responsabile: la firma del documento % è già registrata', OLD.id;
    END IF;
    IF OLD.stato IN ('ARCHIVIATO', 'RESPINTO', 'ANNULLATO') AND (
           NEW.busta_chiave IS DISTINCT FROM OLD.busta_chiave
           OR NEW.busta_version_id IS DISTINCT FROM OLD.busta_version_id
           OR NEW.archiviato_il IS DISTINCT FROM OLD.archiviato_il
           OR NEW.motivo IS DISTINCT FROM OLD.motivo) THEN
        RAISE EXCEPTION 'firme_responsabile: documento % in stato finale', OLD.id;
    END IF;
    IF OLD.stato = 'DA_FIRMARE' AND NEW.stato = 'FIRMATO' THEN
        IF NEW.busta IS NULL OR encode(sha256(NEW.busta), 'hex') IS DISTINCT FROM NEW.busta_sha256
           THEN
            RAISE EXCEPTION 'firme_responsabile: busta assente o con impronta diversa';
        END IF;
        SELECT c.certificato_sha256 INTO atteso FROM public.credenziali_firma_personali c
         WHERE c.id = NEW.credenziale_id AND c.user_id = NEW.firmato_da AND c.attiva;
        IF atteso IS NULL OR atteso IS DISTINCT FROM NEW.certificato_sha256 THEN
            RAISE EXCEPTION 'firme_responsabile: credenziale non attiva, non della persona o '
                'certificato diverso da quello atteso';
        END IF;
        IF OLD.firmatario_atteso IS NOT NULL
           AND NEW.firmato_da IS DISTINCT FROM OLD.firmatario_atteso THEN
            RAISE EXCEPTION 'firme_responsabile: firmatario diverso da quello indicato';
        END IF;
    END IF;
    IF NEW.stato = 'ARCHIVIATO' AND OLD.stato = 'FIRMATO' AND (
           NEW.busta_chiave IS NULL
           OR strpos(NEW.busta_chiave, OLD.impronta_sha256) = 0
           OR strpos(NEW.busta_chiave, substr(OLD.busta_sha256, 1, 24)) = 0) THEN
        RAISE EXCEPTION 'firme_responsabile: chiave WORM senza le impronte del documento e '
            'della busta';
    END IF;
    RETURN NEW;
END;
$$"""

_TRIGGER = (
    "DROP TRIGGER IF EXISTS trg_firme_responsabile_protette ON public.firme_responsabile",
    "CREATE TRIGGER trg_firme_responsabile_protette BEFORE INSERT OR UPDATE OR DELETE "
    "ON public.firme_responsabile FOR EACH ROW EXECUTE FUNCTION "
    "public.firme_responsabile_protette()",
    "DROP TRIGGER IF EXISTS trg_firme_responsabile_troncamento ON public.firme_responsabile",
    "CREATE TRIGGER trg_firme_responsabile_troncamento BEFORE TRUNCATE "
    "ON public.firme_responsabile FOR EACH STATEMENT EXECUTE FUNCTION "
    "public.firme_responsabile_protette()",
)

_CODICI_SQL = ", ".join(f"'{c}'" for c in CODICI_ACCESSO) + ", " + ", ".join(
    f"'LIMITE.{c}'" for c in CODICI_ACCESSO)

_VERSIONI = f"""
CREATE OR REPLACE FUNCTION public.vd_alza_versione_accesso(p_codice text, p_ente uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    INSERT INTO public.versioni_accesso AS v (ambito, tipo, versione, aggiornato_il)
    VALUES (coalesce(p_ente::text, '*'),
            CASE WHEN p_codice LIKE '%ACCESSO_METODI_AMMESSI' THEN 'METODI'
                 ELSE 'CONCEDIBILI' END, 1, now())
    ON CONFLICT (ambito, tipo) DO UPDATE SET versione = v.versione + 1, aggiornato_il = now();
END;
$$;
CREATE OR REPLACE FUNCTION public.vd_versioni_accesso()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    codici text[] := ARRAY[{_CODICI_SQL}];
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.codice = ANY(codici) THEN
        PERFORM public.vd_alza_versione_accesso(NEW.codice, NEW.tenant_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.codice = ANY(codici)
       AND (TG_OP = 'DELETE' OR OLD.codice IS DISTINCT FROM NEW.codice
            OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id) THEN
        PERFORM public.vd_alza_versione_accesso(OLD.codice, OLD.tenant_id);
    END IF;
    RETURN NULL;
END;
$$"""

_TRIGGER_VERSIONI = (
    "DROP TRIGGER IF EXISTS trg_vd_versioni_accesso ON public.impostazioni_valori",
    "CREATE TRIGGER trg_vd_versioni_accesso AFTER INSERT OR UPDATE OR DELETE "
    "ON public.impostazioni_valori FOR EACH ROW EXECUTE FUNCTION public.vd_versioni_accesso()",
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
            f"{RUOLO_APPLICAZIONE}; END IF;")


def _concedi(gruppo: str, diritti: str, oggetto: str) -> str:
    return (f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppo}') THEN "
            f"GRANT {diritti} ON {oggetto} TO {gruppo}; END IF;")


_PRIVILEGI = "DO $p$\nBEGIN" + _crea_gruppo(GRUPPO_RESPONSABILE) + "\n    " + " ".join([
    *(_revoca(t) for t in TABELLE_NUOVE),
    "REVOKE ALL ON FUNCTION public.vd_versioni_accesso() FROM PUBLIC;",
    "REVOKE ALL ON FUNCTION public.vd_alza_versione_accesso(text, uuid) FROM PUBLIC;",
    _concedi(GRUPPO_FORNITORI, "SELECT, INSERT, UPDATE", "public.credenziali_firma_personali"),
    _concedi(GRUPPO_FIRMA, "SELECT", "public.credenziali_firma_personali"),
    _concedi(GRUPPO_SIGILLO, "SELECT", "public.credenziali_firma_personali"),
    _concedi(GRUPPO_RESPONSABILE, "SELECT", "public.credenziali_firma_personali"),
    _concedi(GRUPPO_RESPONSABILE, "SELECT", "public.firme_responsabile"),
    _concedi(GRUPPO_RESPONSABILE, "INSERT (" + ", ".join(COLONNE_RICHIESTA) + ")",
             "public.firme_responsabile"),
    _concedi(GRUPPO_RESPONSABILE, "UPDATE (" + ", ".join(COLONNE_FIRMA) + ")",
             "public.firme_responsabile"),
    _concedi(GRUPPO_SIGILLO, "SELECT", "public.firme_responsabile"),
    _concedi(GRUPPO_SIGILLO, "UPDATE (" + ", ".join(COLONNE_ARCHIVIO) + ")",
             "public.firme_responsabile"),
    _concedi(RUOLO_APPLICAZIONE, "SELECT", "public.versioni_accesso"),
]) + "\nEND\n$p$;"

_RIFIUTA_DOWNGRADE = """
DO $d$
BEGIN
    IF to_regclass('public.firme_responsabile') IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.firme_responsabile WHERE stato IN ('FIRMATO', 'ARCHIVIATO')) THEN
        RAISE EXCEPTION 'downgrade rifiutato: esistono firme personali registrate (evidenze)';
    END IF;
END
$d$;
"""


def _attesa_lock() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")
    for istruzione in (s.strip() for s in _TABELLE.split(";")):
        if istruzione:
            op.execute(istruzione)
    op.execute(_PROTEZIONE)
    for istruzione in _TRIGGER:
        op.execute(istruzione)
    for istruzione in _VERSIONI.split("$$;\n"):
        op.execute(istruzione if istruzione.rstrip().endswith("$$") else istruzione + "$$")
    for istruzione in _TRIGGER_VERSIONI:
        op.execute(istruzione)
    op.execute(_PRIVILEGI)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")
    op.execute(_RIFIUTA_DOWNGRADE)
    op.execute("DROP TRIGGER IF EXISTS trg_vd_versioni_accesso ON public.impostazioni_valori")
    op.execute("DROP FUNCTION IF EXISTS public.vd_versioni_accesso()")
    op.execute("DROP FUNCTION IF EXISTS public.vd_alza_versione_accesso(text, uuid)")
    op.execute("DROP TABLE IF EXISTS public.firme_responsabile")
    op.execute("DROP FUNCTION IF EXISTS public.firme_responsabile_protette()")
    op.execute("DROP TABLE IF EXISTS public.credenziali_firma_personali")
    op.execute("DROP TABLE IF EXISTS public.versioni_accesso")
