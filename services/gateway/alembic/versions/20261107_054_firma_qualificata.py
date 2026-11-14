# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Firma e marca qualificate predisposte (R13a: VD-C-068, VD-C-124)

Revision ID: 20261107_054
Revises: 20261106_053
Create Date: 2026-11-07

1. ``fornitori_firma``: registro dei fornitori di firma, sigillo e marca temporale
   registrati dal conservatore in console (CSC v2, PKCS#11, TSA RFC 3161, sviluppo),
   con ruolo, priorità (ripiego) e configurazione SENZA segreti
   (``libs.shared.firma_qualificata``). Configurazione del servizio senza ente:
   fuori dalla RLS come ``fornitori_identita``. Diritti: tolti al gruppo dei
   servizi e a PUBLIC; SELECT, INSERT, UPDATE al solo gruppo
   ``verumdoc_firma_fornitori`` (login di onboarding, console), SELECT al gruppo
   ``verumdoc_firma`` (login del servizio di firma, che legge anche
   ``impostazioni_valori``). Nessun DELETE: un fornitore si disattiva.
2. ``firme_documenti``: documenti del conservatore da sigillare (IPdA, RdV,
   manuale, piano) con lo stato ``DA_FIRMARE`` → ``FIRMATO`` | ``NON_FIRMATO``, la
   busta nel WORM e i dati per la rimarcatura (scadenze dei certificati,
   algoritmo). RLS per ente (righe senza ente solo nell'ambito del conservatore).
   Diritti (revisione R13a (5); seconda revisione (1)): SELECT e INSERT delle SOLE
   colonne d'origine al gruppo ``verumdoc_firme_documenti`` (login di aip-builder
   e dashboard-api, che accodano); SELECT, INSERT, UPDATE al solo gruppo
   ``verumdoc_firme_documenti_sigillo`` (login della hashchain, l'unico che
   sigilla); nessun DELETE a nessuno. Trigger ``firme_documenti_protette``:
   origine e impronta non cambiano mai; una riga FIRMATO resta FIRMATO e cambia
   solo nelle colonne della rimarcatura, con ``rinnovi`` crescente a ogni nuova
   busta (la busta precedente si aggiunge a ``storico_buste``); nessun DELETE
   (nemmeno dal proprietario). Trigger ``firme_documenti_nuove`` (seconda
   revisione (1)): chi non è nel gruppo della hashchain inserisce solo righe
   ``DA_FIRMARE`` senza alcun dato del sigillo. Stato ``ERRORE`` (seconda revisione
   (2)): dopo ``FIRMA_TENTATIVI_MASSIMI`` errori il documento non si riprova più
   (niente firme pagate all'infinito) e scatta l'allarme.
3. ``firma_transizione_r13``: elenco dei PdA ancora in lavorazione al momento della
   migrazione (scritto SOLO qui, SELECT al gruppo della hashchain): per
   loro l'assenza della riga dell'IPdA non blocca la conservazione con
   ``FIRMA_SENZA_FORNITORE=BLOCCA`` (VD-P-131); un PdA nato dopo senza riga è un
   difetto e resta fermo.
4. ``catena_checkpoint``: marca temporale RFC 3161 dell'ultimo checkpoint
   (ancoraggio esterno, VD-C-068): ``stato_marca`` (``DA_MARCARE``, ``MARCATO``,
   ``NON_MARCATO``, ``SUPERATO``), token, chiave WORM, istante e TSA. Colonne con
   predefinito costante (solo catalogo); vincolo NOT VALID convalidato in
   autocommit; la funzione di protezione rende la marca immutabile dopo l'esito.
5. ``ruoli_personalizzati``: il permesso ``firma_fornitori:gestisci`` entra nei mai
   delegabili anche nel vincolo del database.

Riprendibile: ``IF NOT EXISTS``, vincoli NOT VALID + VALIDATE, ``lock_timeout``.
Downgrade: rifiutato se esistono documenti FIRMATO o checkpoint MARCATO
(evidenze); altrimenti toglie tabelle, colonne e ripristina vincolo e funzione.
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from alembic import context, op

revision = "20261107_054"
down_revision = "20261106_053"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
GRUPPO_FORNITORI = "verumdoc_firma_fornitori"
GRUPPO_FIRMA = "verumdoc_firma"
GRUPPO_DOCUMENTI = "verumdoc_firme_documenti"
GRUPPO_SIGILLO = "verumdoc_firme_documenti_sigillo"
POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")

MAI_DELEGABILI_053 = ("ARRAY['scarto:esegui', 'scarto:autorizza', 'annullamento:approva', "
                      "'firmatari:gestisci', 'pratica:chiudi_eccezioni', 'utenti:gestisci', "
                      "'ruoli:gestisci', 'accesso:configura']::text[]")
MAI_DELEGABILI = MAI_DELEGABILI_053.replace("'accesso:configura'",
                                            "'accesso:configura', 'firma_fornitori:gestisci'")
#: colonne che chi accoda (aip-builder, dashboard-api) può scrivere (seconda revisione (1))
COLONNE_ORIGINE = ("tipo", "tenant_id", "aip_id", "nome", "origine_bucket", "origine_chiave",
                   "origine_version_id", "contenuto", "impronta_sha256")
STATI_MARCA = ("DA_MARCARE", "MARCATO", "NON_MARCATO", "SUPERATO")


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


def _concedi(gruppo: str, diritti: str, tabella: str) -> str:
    return (f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppo}') THEN "
            f"GRANT {diritti} ON public.{tabella} TO {gruppo}; END IF;")


_PRIVILEGI = "DO $p$\nBEGIN" + "".join(_crea_gruppo(g) for g in (
    GRUPPO_FORNITORI, GRUPPO_FIRMA, GRUPPO_DOCUMENTI, GRUPPO_SIGILLO)) + "\n    " + " ".join([
        _revoca("fornitori_firma"), _revoca("firme_documenti"),
        _revoca("firma_transizione_r13"),
        _concedi(GRUPPO_FORNITORI, "SELECT, INSERT, UPDATE", "fornitori_firma"),
        _concedi(GRUPPO_FIRMA, "SELECT", "fornitori_firma"),
        _concedi(GRUPPO_FIRMA, "SELECT", "impostazioni_valori"),
        # ripresa: un INSERT su tutta la tabella di un'esecuzione precedente si toglie
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{GRUPPO_DOCUMENTI}') THEN "
        f"REVOKE INSERT, UPDATE ON public.firme_documenti FROM {GRUPPO_DOCUMENTI}; END IF;",
        _concedi(GRUPPO_DOCUMENTI, "SELECT", "firme_documenti"),
        _concedi(GRUPPO_DOCUMENTI, "INSERT (" + ", ".join(COLONNE_ORIGINE) + ")",
                 "firme_documenti"),
        _concedi(GRUPPO_SIGILLO, "SELECT, INSERT, UPDATE", "firme_documenti"),
        _concedi(GRUPPO_SIGILLO, "SELECT", "firma_transizione_r13"),
    ]) + "\nEND\n$p$;"

# Funzione di protezione di catena_checkpoint (migrazione 031) con le regole della
# marca; ``{marca}`` = blocco della marca (vuoto nel downgrade).
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
    {marca}
    RETURN NEW;
END;
$$;
"""

_REGOLE_MARCA = """
    IF OLD.stato_marca <> 'DA_MARCARE' AND (
           NEW.stato_marca IS DISTINCT FROM OLD.stato_marca
           OR NEW.motivo_marca IS DISTINCT FROM OLD.motivo_marca
           OR NEW.marca_token IS DISTINCT FROM OLD.marca_token
           OR NEW.marca_worm_chiave IS DISTINCT FROM OLD.marca_worm_chiave
           OR NEW.marcato_il IS DISTINCT FROM OLD.marcato_il
           OR NEW.marca_tsa IS DISTINCT FROM OLD.marca_tsa) THEN
        RAISE EXCEPTION 'catena_checkpoint: marca del checkpoint % già registrata',
            OLD.dimensione;
    END IF;
"""

_TABELLE = """
CREATE TABLE IF NOT EXISTS public.fornitori_firma (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codice varchar(40) NOT NULL,
    ruolo varchar(10) NOT NULL,
    tipo varchar(20) NOT NULL,
    nome varchar(200) NOT NULL,
    attivo boolean NOT NULL DEFAULT false,
    priorita integer NOT NULL DEFAULT 10,
    configurazione jsonb NOT NULL DEFAULT '{}'::jsonb,
    versione integer NOT NULL DEFAULT 1,
    aggiornato_da uuid,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT uq_fornitori_firma_codice UNIQUE (codice),
    CONSTRAINT ck_fornitori_firma_codice CHECK (codice ~ '^[a-z][a-z0-9-]{1,39}$'),
    CONSTRAINT ck_fornitori_firma_tipo CHECK (
        (ruolo = 'FIRMA' AND tipo IN ('CSC', 'PKCS11', 'SVILUPPO'))
        OR (ruolo = 'MARCA' AND tipo IN ('RFC3161', 'SVILUPPO'))),
    CONSTRAINT ck_fornitori_firma_priorita CHECK (priorita BETWEEN 1 AND 100),
    CONSTRAINT ck_fornitori_firma_configurazione CHECK (jsonb_typeof(configurazione) = 'object'),
    CONSTRAINT ck_fornitori_firma_versione CHECK (versione >= 1)
);
CREATE TABLE IF NOT EXISTS public.firme_documenti (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo varchar(10) NOT NULL,
    tenant_id uuid,
    aip_id uuid,
    nome varchar(200) NOT NULL,
    origine_bucket varchar(200),
    origine_chiave varchar(1024),
    origine_version_id varchar(1024),
    contenuto bytea,
    impronta_sha256 char(64) NOT NULL,
    stato varchar(15) NOT NULL DEFAULT 'DA_FIRMARE',
    motivo varchar(500),
    tentativi integer NOT NULL DEFAULT 0,
    prossimo_tentativo_il timestamp with time zone,
    formato varchar(10),
    livello varchar(10),
    busta_chiave varchar(1024),
    busta_version_id varchar(1024),
    busta_sha256 char(64),
    busta bytea,
    fornitore_firma varchar(40),
    fornitore_marca varchar(40),
    qualificata boolean NOT NULL DEFAULT false,
    certificato_sha256 char(64),
    certificato_soggetto varchar(500),
    certificato_scadenza timestamp with time zone,
    marca_scadenza timestamp with time zone,
    algoritmo varchar(10),
    data_marca timestamp with time zone,
    firmato_il timestamp with time zone,
    rinnovi integer NOT NULL DEFAULT 0,
    rinnovato_il timestamp with time zone,
    rinnovo_motivo varchar(300),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_firme_documenti_tipo CHECK (tipo IN ('IPDA', 'RDV', 'MANUALE', 'PIANO')),
    errori integer NOT NULL DEFAULT 0,
    storico_buste jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT ck_firme_documenti_stato CHECK (
        stato IN ('DA_FIRMARE', 'FIRMATO', 'NON_FIRMATO', 'ERRORE')),
    CONSTRAINT ck_firme_documenti_storico CHECK (jsonb_typeof(storico_buste) = 'array'),
    CONSTRAINT ck_firme_documenti_pacchetto CHECK (
        (tipo IN ('IPDA', 'RDV')) = (tenant_id IS NOT NULL AND aip_id IS NOT NULL)),
    CONSTRAINT ck_firme_documenti_origine CHECK (
        (contenuto IS NULL) = (origine_chiave IS NOT NULL)),
    CONSTRAINT ck_firme_documenti_contenuto CHECK (
        contenuto IS NULL OR octet_length(contenuto) <= 5242880),
    CONSTRAINT ck_firme_documenti_firmato CHECK (
        stato <> 'FIRMATO' OR (busta_sha256 IS NOT NULL AND firmato_il IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firme_documenti_pacchetto
    ON public.firme_documenti (tipo, aip_id) WHERE aip_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_firme_documenti_conservatore
    ON public.firme_documenti (tipo, impronta_sha256) WHERE aip_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_firme_documenti_da_firmare
    ON public.firme_documenti (prossimo_tentativo_il NULLS FIRST, created_at)
    WHERE stato <> 'FIRMATO';
CREATE INDEX IF NOT EXISTS idx_firme_documenti_scadenza
    ON public.firme_documenti (LEAST(certificato_scadenza, marca_scadenza))
    WHERE stato = 'FIRMATO';
CREATE TABLE IF NOT EXISTS public.firma_transizione_r13 (
    aip_id uuid PRIMARY KEY,
    registrato_il timestamp with time zone NOT NULL DEFAULT now()
);
"""

#: PdA ancora in lavorazione (non conservati né scartati) alla migrazione
_TRANSIZIONE = """
INSERT INTO public.firma_transizione_r13 (aip_id)
SELECT id FROM public.aip WHERE stato IN ('SU_STORAGE', 'IN_CATENA', 'IN_BUILD')
ON CONFLICT (aip_id) DO NOTHING"""

#: Revisione R13a (5): immutabilità delle righe di firme_documenti
_PROTEZIONE_DOCUMENTI = """
CREATE OR REPLACE FUNCTION public.firme_documenti_protette()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'firme_documenti: DELETE vietata (evidenza del sigillo)';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tipo IS DISTINCT FROM OLD.tipo
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.aip_id IS DISTINCT FROM OLD.aip_id
       OR NEW.nome IS DISTINCT FROM OLD.nome
       OR NEW.origine_bucket IS DISTINCT FROM OLD.origine_bucket
       OR NEW.origine_chiave IS DISTINCT FROM OLD.origine_chiave
       OR NEW.origine_version_id IS DISTINCT FROM OLD.origine_version_id
       OR NEW.contenuto IS DISTINCT FROM OLD.contenuto
       OR NEW.impronta_sha256 IS DISTINCT FROM OLD.impronta_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'firme_documenti: origine e impronta del documento % non modificabili',
            OLD.id;
    END IF;
    IF NEW.storico_buste IS DISTINCT FROM OLD.storico_buste AND NOT (
           OLD.stato = 'FIRMATO'
           AND NEW.busta_sha256 IS DISTINCT FROM OLD.busta_sha256
           AND jsonb_array_length(NEW.storico_buste) = jsonb_array_length(OLD.storico_buste) + 1
           AND (NEW.storico_buste - (jsonb_array_length(NEW.storico_buste) - 1))
               = OLD.storico_buste
           AND NEW.storico_buste -> -1 ->> 'busta_sha256' = OLD.busta_sha256
           AND NEW.storico_buste -> -1 ->> 'busta_chiave' = OLD.busta_chiave) THEN
        RAISE EXCEPTION 'firme_documenti: lo storico delle buste del documento % cresce solo '
            'con la busta precedente a ogni rimarcatura', OLD.id;
    END IF;
    IF OLD.stato = 'FIRMATO' AND NEW.busta_sha256 IS DISTINCT FROM OLD.busta_sha256
       AND NEW.storico_buste IS NOT DISTINCT FROM OLD.storico_buste THEN
        RAISE EXCEPTION 'firme_documenti: una nuova busta del documento % registra la '
            'precedente in storico_buste', OLD.id;
    END IF;
    IF NEW.rinnovi < OLD.rinnovi THEN
        RAISE EXCEPTION 'firme_documenti: rinnovi non decrescono (documento %)', OLD.id;
    END IF;
    IF OLD.stato = 'FIRMATO' THEN
        IF NEW.stato IS DISTINCT FROM OLD.stato
           OR NEW.motivo IS DISTINCT FROM OLD.motivo
           OR NEW.tentativi IS DISTINCT FROM OLD.tentativi
           OR NEW.errori IS DISTINCT FROM OLD.errori
           OR NEW.prossimo_tentativo_il IS DISTINCT FROM OLD.prossimo_tentativo_il
           OR NEW.formato IS DISTINCT FROM OLD.formato
           OR NEW.fornitore_firma IS DISTINCT FROM OLD.fornitore_firma
           OR NEW.fornitore_marca IS DISTINCT FROM OLD.fornitore_marca
           OR NEW.qualificata IS DISTINCT FROM OLD.qualificata
           OR NEW.certificato_sha256 IS DISTINCT FROM OLD.certificato_sha256
           OR NEW.certificato_soggetto IS DISTINCT FROM OLD.certificato_soggetto
           OR NEW.certificato_scadenza IS DISTINCT FROM OLD.certificato_scadenza
           OR NEW.data_marca IS DISTINCT FROM OLD.data_marca
           OR NEW.firmato_il IS DISTINCT FROM OLD.firmato_il THEN
            RAISE EXCEPTION 'firme_documenti: sigillo del documento % già registrato '
                '(cambiano solo le colonne della rimarcatura)', OLD.id;
        END IF;
        IF (NEW.busta_chiave IS DISTINCT FROM OLD.busta_chiave
            OR NEW.busta_version_id IS DISTINCT FROM OLD.busta_version_id
            OR NEW.busta_sha256 IS DISTINCT FROM OLD.busta_sha256
            OR NEW.busta IS DISTINCT FROM OLD.busta
            OR NEW.livello IS DISTINCT FROM OLD.livello
            OR NEW.algoritmo IS DISTINCT FROM OLD.algoritmo
            OR NEW.marca_scadenza IS DISTINCT FROM OLD.marca_scadenza
            OR NEW.rinnovato_il IS DISTINCT FROM OLD.rinnovato_il
            OR NEW.rinnovo_motivo IS DISTINCT FROM OLD.rinnovo_motivo)
           AND NEW.rinnovi <= OLD.rinnovi THEN
            RAISE EXCEPTION 'firme_documenti: una nuova busta del documento % richiede '
                'rinnovi crescente', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$"""
_PROTEZIONE_NUOVI = """
CREATE OR REPLACE FUNCTION public.firme_documenti_nuove()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_firme_documenti_sigillo')
       AND pg_has_role(current_user, 'verumdoc_firme_documenti_sigillo', 'MEMBER') THEN
        RETURN NEW;
    END IF;
    IF NEW.stato IS DISTINCT FROM 'DA_FIRMARE' OR NEW.motivo IS NOT NULL
       OR NEW.tentativi <> 0 OR NEW.errori <> 0 OR NEW.prossimo_tentativo_il IS NOT NULL
       OR NEW.formato IS NOT NULL OR NEW.livello IS NOT NULL
       OR NEW.busta_chiave IS NOT NULL OR NEW.busta_version_id IS NOT NULL
       OR NEW.busta_sha256 IS NOT NULL OR NEW.busta IS NOT NULL
       OR NEW.fornitore_firma IS NOT NULL OR NEW.fornitore_marca IS NOT NULL
       OR NEW.qualificata OR NEW.certificato_sha256 IS NOT NULL
       OR NEW.certificato_soggetto IS NOT NULL OR NEW.certificato_scadenza IS NOT NULL
       OR NEW.marca_scadenza IS NOT NULL OR NEW.algoritmo IS NOT NULL
       OR NEW.data_marca IS NOT NULL OR NEW.firmato_il IS NOT NULL OR NEW.rinnovi <> 0
       OR NEW.rinnovato_il IS NOT NULL OR NEW.rinnovo_motivo IS NOT NULL
       OR NEW.storico_buste <> '[]'::jsonb THEN
        RAISE EXCEPTION 'firme_documenti: un documento nuovo entra DA_FIRMARE senza dati del '
            'sigillo (solo la hashchain sigilla)';
    END IF;
    RETURN NEW;
END;
$$"""
_TRIGGER_DOCUMENTI = (
    "DROP TRIGGER IF EXISTS trg_firme_documenti_protette ON public.firme_documenti",
    "CREATE TRIGGER trg_firme_documenti_protette BEFORE UPDATE OR DELETE "
    "ON public.firme_documenti FOR EACH ROW EXECUTE FUNCTION public.firme_documenti_protette()",
    "DROP TRIGGER IF EXISTS trg_firme_documenti_nuove ON public.firme_documenti",
    "CREATE TRIGGER trg_firme_documenti_nuove BEFORE INSERT "
    "ON public.firme_documenti FOR EACH ROW EXECUTE FUNCTION public.firme_documenti_nuove()",
)

_RIFIUTA_DOWNGRADE = """
DO $d$
BEGIN
    IF EXISTS (SELECT 1 FROM public.firme_documenti WHERE stato = 'FIRMATO')
       OR EXISTS (SELECT 1 FROM public.catena_checkpoint WHERE stato_marca = 'MARCATO') THEN
        RAISE EXCEPTION 'downgrade 20261107_054 impossibile: esistono documenti sigillati o '
            'checkpoint marcati (evidenze)';
    END IF;
END
$d$;
"""


def registri_modificabili(*argomenti, **opzioni):  # noqa: ANN002, ANN003, ANN201
    """``libs.shared.db.migrazioni_registri.registri_modificabili`` importata solo
    all'esecuzione (VD-C-117): ``catena_checkpoint`` è un registro protetto dagli
    event trigger (runbook ``docs/runbooks/migrazioni_audit.md``)."""
    from libs.shared.db.migrazioni_registri import registri_modificabili as originale

    return originale(*argomenti, **opzioni)


def _funzione_031() -> str:
    """Funzione di protezione come la lascia la migrazione 031 (downgrade, regola 3
    del runbook: la versione precedente si legge dal file che l'aveva creata)."""
    percorso = Path(__file__).with_name("20261023_031_correzioni_revisione_r04.py")
    spec = importlib.util.spec_from_file_location("migrazione_031", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return str(modulo._PROTEZIONE_CHECKPOINT).replace("{extra}", modulo._REGOLE_RIFIUTO)


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
    op.execute("ALTER TABLE public.firme_documenti ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.firme_documenti")
    op.execute(f"CREATE POLICY {POLITICA} ON public.firme_documenti AS PERMISSIVE FOR ALL "
               f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    op.execute(_PRIVILEGI)
    op.execute(_PROTEZIONE_DOCUMENTI)
    op.execute(_PROTEZIONE_NUOVI)
    for istruzione in _TRIGGER_DOCUMENTI:
        op.execute(istruzione)
    op.execute(_TRANSIZIONE)
    with registri_modificabili(op, motivo="R13a: marca temporale dei checkpoint (VD-C-068)"):
        for colonna, tipo in (("stato_marca", "varchar(15) NOT NULL DEFAULT 'DA_MARCARE'"),
                              ("motivo_marca", "varchar(500)"), ("marca_token", "text"),
                              ("marca_worm_chiave", "varchar(500)"),
                              ("marcato_il", "timestamp with time zone"),
                              ("marca_tsa", "varchar(200)")):
            op.execute(f"ALTER TABLE public.catena_checkpoint ADD COLUMN IF NOT EXISTS "
                       f"{colonna} {tipo}")
        op.execute("ALTER TABLE public.catena_checkpoint DROP CONSTRAINT IF EXISTS "
                   "ck_catena_checkpoint_stato_marca")
        op.execute("ALTER TABLE public.catena_checkpoint ADD CONSTRAINT "
                   "ck_catena_checkpoint_stato_marca CHECK (stato_marca IN ("
                   + ", ".join(f"'{s}'" for s in STATI_MARCA) + ")) NOT VALID")
        op.execute(_PROTEZIONE_CHECKPOINT.replace("{marca}", _REGOLE_MARCA))
    _vincolo_ruoli(MAI_DELEGABILI)
    _in_autocommit("ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
                   "ck_ruoli_personalizzati_non_delegabili")
    if context.is_offline_mode():
        with registri_modificabili(op, motivo="R13a: convalida dello stato della marca"):
            op.execute("ALTER TABLE public.catena_checkpoint VALIDATE CONSTRAINT "
                       "ck_catena_checkpoint_stato_marca")
        return
    with op.get_context().autocommit_block(), registri_modificabili(
            op, motivo="R13a: convalida dello stato della marca"):
        op.execute("ALTER TABLE public.catena_checkpoint VALIDATE CONSTRAINT "
                   "ck_catena_checkpoint_stato_marca")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute(_RIFIUTA_DOWNGRADE)
    _vincolo_ruoli(MAI_DELEGABILI_053)
    with registri_modificabili(op, motivo="downgrade R13a: marca dei checkpoint"):
        op.execute(_funzione_031())
        op.execute("ALTER TABLE public.catena_checkpoint DROP CONSTRAINT IF EXISTS "
                   "ck_catena_checkpoint_stato_marca")
        for colonna in ("marca_tsa", "marcato_il", "marca_worm_chiave", "marca_token",
                        "motivo_marca", "stato_marca"):
            op.execute(f"ALTER TABLE public.catena_checkpoint DROP COLUMN IF EXISTS {colonna}")
    op.execute("DROP TABLE IF EXISTS public.firma_transizione_r13")
    op.execute("DROP TABLE IF EXISTS public.firme_documenti")
    op.execute("DROP FUNCTION IF EXISTS public.firme_documenti_protette()")
    op.execute("DROP FUNCTION IF EXISTS public.firme_documenti_nuove()")
    op.execute("DROP TABLE IF EXISTS public.fornitori_firma")
    _in_autocommit("ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
                   "ck_ruoli_personalizzati_non_delegabili")
