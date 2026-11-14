# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Documentazione di conformità generata (R15: VD-C-177)

Revision ID: 20261111_058
Revises: 20261110_057
Create Date: 2026-11-11

Nata come ``20261110_057_documentazione`` in R15, sviluppato in parallelo a R16: all'unione
(R17) rinumerata in 058 dopo ``20261110_057_residui`` di R16.

1. ``documentazione_generazioni``: richieste di generazione della documentazione del
   servizio (console «Documentazione del servizio»): IN_CORSO → COMPLETATA | ERRORE, una
   sola IN_CORSO alla volta (indice unico parziale), scadenza oltre la quale la richiesta
   si dichiara interrotta (ERRORE) e se ne può fare un'altra. Il trigger
   ``documentazione_generazioni_protette`` ammette un solo passaggio di stato, con i soli
   campi dell'esito; nessuna DELETE.
2. ``documentazione_versioni``: versioni IMMUTABILI dei documenti generati (Markdown e HTML
   stampabile, impronte, sezioni, differenze, fonti, dati da compilare). Il trigger
   ``documentazione_versioni_nuove`` (BEFORE INSERT) pretende: versione = ultima + 1 per
   tipo e precedente = ultima riga del tipo; contenuto diverso dalla versione precedente
   (stessa impronta = nessuna versione nuova); impronte del Markdown e dell'HTML
   RICALCOLATE dal database (``sha256``); generazione esistente e IN_CORSO; colonne
   dell'archivio WORM vuote; ``created_at`` dal database. Il trigger
   ``documentazione_versioni_protette`` (BEFORE UPDATE/DELETE) vieta la DELETE e ogni
   modifica, tranne la registrazione dell'archivio WORM una sola volta (da NULL a valore),
   e solo per il gruppo ``verumdoc_documentazione_archivio`` (login della hashchain, che
   scrive ``registro/documentazione/<tipo>/<impronta>.md|.html``); TRUNCATE vietata.
3. Permesso ``documentazione:genera`` fra i mai delegabili anche nel vincolo
   ``ck_ruoli_personalizzati_non_delegabili``.
4. GRANT minimi: tolto tutto a PUBLIC e a ``verumdoc_applicazione``;
   ``verumdoc_documentazione`` (login di dashboard-api): SELECT delle due tabelle, INSERT
   delle sole colonne d'origine delle versioni, INSERT e UPDATE dei soli campi dell'esito
   delle generazioni; ``verumdoc_documentazione_archivio`` (hashchain): SELECT e UPDATE
   delle sole colonne dell'archivio; ``verumdoc_auditor`` (sola lettura, R09): SELECT delle
   due tabelle e delle sole colonne di configurazione lette da ``libs/conformita/database.py``.

Tabelle senza ente: fuori dalla Row Level Security (documenti del conservatore; motivo in
docs/architettura/sicurezza.md). Riprendibile (``IF NOT EXISTS``, ``CREATE OR REPLACE``),
``lock_timeout``; VALIDATE del vincolo dei ruoli in autocommit, fuori dal lock esclusivo.
Downgrade: RIFIUTATO se esistono versioni generate (revisione R17: sono evidenze, come le
conferme della 059); altrimenti vincolo dei ruoli di R14, tolte funzioni e tabelle.
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import os

from alembic import context, op

revision = "20261111_058"
down_revision = "20261110_057"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
GRUPPO_DOCUMENTAZIONE = "verumdoc_documentazione"
GRUPPO_ARCHIVIO = "verumdoc_documentazione_archivio"
RUOLO_AUDITOR = "verumdoc_auditor"
TABELLE_NUOVE = ("documentazione_generazioni", "documentazione_versioni")
TIPI = ("MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA", "GUIDA_FUNZIONALITA", "CATALOGO_ROUTE")
#: limiti del contenuto (il manuale generato pesa decine di kB)
MARKDOWN_MASSIMO = 5 * 1024 * 1024
HTML_MASSIMO = 8 * 1024 * 1024
#: colonne scritte da dashboard-api all'inserimento di una versione
COLONNE_ORIGINE = ("id", "tipo", "versione", "precedente_id", "generazione_id", "titolo",
                   "nome_file", "markdown", "html", "impronta_contenuto", "impronta_sha256",
                   "impronta_html", "sezioni", "differenze", "fonti", "da_compilare",
                   "da_verificare", "generata_il", "creato_da")
COLONNE_ARCHIVIO = ("worm_chiave_md", "worm_versione_md", "worm_chiave_html",
                    "worm_versione_html", "archiviata_il")
COLONNE_ESITO = ("stato", "conclusa_il", "esito", "errore")
#: configurazione letta dall'auditor (libs/conformita/database.py, LETTURE)
LETTURE_AUDITOR = {
    "classi_documentali": ("codice", "nome", "tenant_id", "tipologia_xml",
                           "anni_conservazione", "conservazione_permanente", "formati_ammessi",
                           "metadati_obbligatori", "max_dimensione_mb", "attiva"),
    "profili_tipologia": ("codice", "versione", "valido_dal", "valido_al", "adozione",
                          "norm_ref"),
    "impostazioni_valori": ("livello", "tenant_id", "classe_id", "codice", "valore",
                            "created_at"),
    "fornitori_identita": ("tipo", "nome", "attivo"),
    "fornitori_firma": ("codice", "ruolo", "tipo", "nome", "attivo", "priorita"),
    "canali_pec": ("codice", "tipo", "nome", "attivo", "priorita"),
}

_TABELLE = f"""
CREATE TABLE IF NOT EXISTS public.documentazione_generazioni (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stato varchar(12) NOT NULL DEFAULT 'IN_CORSO',
    richiesta_da uuid,
    richiesta_il timestamp with time zone NOT NULL DEFAULT now(),
    scade_il timestamp with time zone NOT NULL,
    conclusa_il timestamp with time zone,
    esito jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    errore varchar(500),
    CONSTRAINT ck_documentazione_generazioni_stato
        CHECK (stato IN ('IN_CORSO', 'COMPLETATA', 'ERRORE')),
    CONSTRAINT ck_documentazione_generazioni_conclusa
        CHECK ((stato = 'IN_CORSO') = (conclusa_il IS NULL)),
    CONSTRAINT ck_documentazione_generazioni_esito CHECK (jsonb_typeof(esito) = 'object')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_documentazione_generazioni_in_corso
    ON public.documentazione_generazioni (stato) WHERE stato = 'IN_CORSO';
CREATE TABLE IF NOT EXISTS public.documentazione_versioni (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo varchar(30) NOT NULL,
    versione integer NOT NULL,
    precedente_id uuid REFERENCES public.documentazione_versioni (id),
    generazione_id uuid NOT NULL REFERENCES public.documentazione_generazioni (id),
    titolo varchar(200) NOT NULL,
    nome_file varchar(100) NOT NULL,
    markdown bytea NOT NULL,
    html bytea NOT NULL,
    impronta_contenuto char(64) NOT NULL,
    impronta_sha256 char(64) NOT NULL,
    impronta_html char(64) NOT NULL,
    sezioni jsonb NOT NULL,
    differenze jsonb NOT NULL,
    fonti jsonb NOT NULL,
    da_compilare jsonb NOT NULL,
    da_verificare jsonb NOT NULL,
    generata_il timestamp with time zone NOT NULL,
    creato_da uuid,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    worm_chiave_md varchar(1024),
    worm_versione_md varchar(1024),
    worm_chiave_html varchar(1024),
    worm_versione_html varchar(1024),
    archiviata_il timestamp with time zone,
    CONSTRAINT uq_documentazione_versioni_tipo_versione UNIQUE (tipo, versione),
    CONSTRAINT ck_documentazione_versioni_tipo CHECK (tipo IN {TIPI}),
    CONSTRAINT ck_documentazione_versioni_versione CHECK (versione >= 1),
    CONSTRAINT ck_documentazione_versioni_markdown
        CHECK (octet_length(markdown) <= {MARKDOWN_MASSIMO}),
    CONSTRAINT ck_documentazione_versioni_html CHECK (octet_length(html) <= {HTML_MASSIMO}),
    CONSTRAINT ck_documentazione_versioni_json CHECK (
        jsonb_typeof(sezioni) = 'array' AND jsonb_typeof(differenze) = 'object'
        AND jsonb_typeof(fonti) = 'object' AND jsonb_typeof(da_compilare) = 'array'
        AND jsonb_typeof(da_verificare) = 'array'),
    CONSTRAINT ck_documentazione_versioni_archivio CHECK (
        (archiviata_il IS NULL) = (worm_chiave_md IS NULL)
        AND (archiviata_il IS NULL) = (worm_chiave_html IS NULL))
)
"""

_NUOVE = """
CREATE OR REPLACE FUNCTION public.documentazione_versioni_nuove()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ultima record;
    stato_generazione text;
BEGIN
    -- serializza le versioni dello stesso tipo (l'indice unico resta la seconda difesa)
    PERFORM pg_advisory_xact_lock(hashtext('documentazione_versioni:' || NEW.tipo));
    SELECT v.id, v.versione, v.impronta_contenuto INTO ultima
      FROM public.documentazione_versioni v WHERE v.tipo = NEW.tipo
     ORDER BY v.versione DESC LIMIT 1;
    IF NEW.versione IS DISTINCT FROM coalesce(ultima.versione, 0) + 1
       OR NEW.precedente_id IS DISTINCT FROM ultima.id THEN
        RAISE EXCEPTION 'documentazione_versioni: versione % del tipo % non successiva '
            'all''ultima (%)', NEW.versione, NEW.tipo, coalesce(ultima.versione, 0)
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF ultima.impronta_contenuto IS NOT NULL
       AND ultima.impronta_contenuto = NEW.impronta_contenuto THEN
        RAISE EXCEPTION 'documentazione_versioni: contenuto uguale alla versione precedente'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.impronta_sha256 IS DISTINCT FROM encode(sha256(NEW.markdown), 'hex')
       OR NEW.impronta_html IS DISTINCT FROM encode(sha256(NEW.html), 'hex') THEN
        RAISE EXCEPTION 'documentazione_versioni: impronta diversa dal contenuto'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT g.stato INTO stato_generazione FROM public.documentazione_generazioni g
     WHERE g.id = NEW.generazione_id;
    IF stato_generazione IS DISTINCT FROM 'IN_CORSO' THEN
        RAISE EXCEPTION 'documentazione_versioni: generazione % non in corso',
            NEW.generazione_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.worm_chiave_md IS NOT NULL OR NEW.worm_versione_md IS NOT NULL
       OR NEW.worm_chiave_html IS NOT NULL OR NEW.worm_versione_html IS NOT NULL
       OR NEW.archiviata_il IS NOT NULL THEN
        RAISE EXCEPTION 'documentazione_versioni: l''archivio WORM lo registra solo la hashchain'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.created_at := now();
    RETURN NEW;
END
$$;
"""

_PROTETTE = f"""
CREATE OR REPLACE FUNCTION public.documentazione_versioni_protette()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'documentazione_versioni: TRUNCATE vietata (versioni immutabili)';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'documentazione_versioni: DELETE vietata (versioni immutabili)';
    END IF;
    IF (to_jsonb(NEW) - ARRAY{list(COLONNE_ARCHIVIO)}::text[])
       IS DISTINCT FROM (to_jsonb(OLD) - ARRAY{list(COLONNE_ARCHIVIO)}::text[]) THEN
        RAISE EXCEPTION 'documentazione_versioni: la versione % è immutabile', OLD.id;
    END IF;
    IF OLD.archiviata_il IS NOT NULL THEN
        RAISE EXCEPTION 'documentazione_versioni: archivio WORM della versione % già '
            'registrato', OLD.id;
    END IF;
    IF NEW.archiviata_il IS NULL OR NEW.worm_chiave_md IS NULL OR NEW.worm_chiave_html IS NULL
       OR position(NEW.impronta_sha256 IN NEW.worm_chiave_md) = 0
       OR position(NEW.impronta_html IN NEW.worm_chiave_html) = 0 THEN
        RAISE EXCEPTION 'documentazione_versioni: archivio WORM incompleto o con chiave senza '
            'l''impronta (versione %)', OLD.id;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{GRUPPO_ARCHIVIO}')
       AND NOT pg_has_role(current_user, '{GRUPPO_ARCHIVIO}', 'MEMBER') THEN
        RAISE EXCEPTION 'documentazione_versioni: archivio registrabile solo dalla hashchain';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS documentazione_versioni_nuove ON public.documentazione_versioni;
CREATE TRIGGER documentazione_versioni_nuove BEFORE INSERT ON public.documentazione_versioni
    FOR EACH ROW EXECUTE FUNCTION public.documentazione_versioni_nuove();
DROP TRIGGER IF EXISTS documentazione_versioni_protette ON public.documentazione_versioni;
CREATE TRIGGER documentazione_versioni_protette BEFORE UPDATE OR DELETE
    ON public.documentazione_versioni
    FOR EACH ROW EXECUTE FUNCTION public.documentazione_versioni_protette();
DROP TRIGGER IF EXISTS documentazione_versioni_troncamento ON public.documentazione_versioni;
CREATE TRIGGER documentazione_versioni_troncamento BEFORE TRUNCATE
    ON public.documentazione_versioni
    FOR EACH STATEMENT EXECUTE FUNCTION public.documentazione_versioni_protette();
"""

_GENERAZIONI = """
CREATE OR REPLACE FUNCTION public.documentazione_generazioni_protette()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'documentazione_generazioni: DELETE vietata (traccia delle richieste)';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.stato <> 'IN_CORSO' OR NEW.conclusa_il IS NOT NULL OR NEW.esito <> '{}'::jsonb
           OR NEW.errore IS NOT NULL OR NEW.scade_il <= now() THEN
            RAISE EXCEPTION 'documentazione_generazioni: una richiesta nasce IN_CORSO, vuota '
                'e con una scadenza futura';
        END IF;
        NEW.richiesta_il := now();
        RETURN NEW;
    END IF;
    IF OLD.stato <> 'IN_CORSO' THEN
        RAISE EXCEPTION 'documentazione_generazioni: la richiesta % è già conclusa', OLD.id;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.richiesta_da IS DISTINCT FROM OLD.richiesta_da
       OR NEW.richiesta_il IS DISTINCT FROM OLD.richiesta_il
       OR NEW.scade_il IS DISTINCT FROM OLD.scade_il OR NEW.stato = 'IN_CORSO' THEN
        RAISE EXCEPTION 'documentazione_generazioni: cambia solo lo stato finale con l''esito';
    END IF;
    NEW.conclusa_il := now();
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS documentazione_generazioni_protette ON public.documentazione_generazioni;
CREATE TRIGGER documentazione_generazioni_protette BEFORE INSERT OR UPDATE OR DELETE
    ON public.documentazione_generazioni
    FOR EACH ROW EXECUTE FUNCTION public.documentazione_generazioni_protette();
"""


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


def _colonne(diritto: str, colonne: tuple[str, ...]) -> str:
    return f"{diritto} ({', '.join(colonne)})"


_PRIVILEGI = "DO $p$\nBEGIN" + _crea_gruppo(GRUPPO_DOCUMENTAZIONE) + _crea_gruppo(
    GRUPPO_ARCHIVIO) + "\n    " + " ".join([
        *(_revoca(t) for t in TABELLE_NUOVE),
        _concedi(GRUPPO_DOCUMENTAZIONE, "SELECT", "public.documentazione_versioni"),
        _concedi(GRUPPO_DOCUMENTAZIONE, _colonne("INSERT", COLONNE_ORIGINE),
                 "public.documentazione_versioni"),
        _concedi(GRUPPO_DOCUMENTAZIONE, "SELECT, INSERT", "public.documentazione_generazioni"),
        _concedi(GRUPPO_DOCUMENTAZIONE, _colonne("UPDATE", COLONNE_ESITO),
                 "public.documentazione_generazioni"),
        _concedi(GRUPPO_ARCHIVIO, "SELECT", "public.documentazione_versioni"),
        _concedi(GRUPPO_ARCHIVIO, _colonne("UPDATE", COLONNE_ARCHIVIO),
                 "public.documentazione_versioni"),
        _concedi(RUOLO_AUDITOR, "SELECT", "public.documentazione_versioni"),
        _concedi(RUOLO_AUDITOR, "SELECT", "public.documentazione_generazioni"),
        *(_concedi(RUOLO_AUDITOR, _colonne("SELECT", colonne), f"public.{tabella}")
          for tabella, colonne in LETTURE_AUDITOR.items()),
    ]) + "\nEND\n$p$;"


def _attesa_lock() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def _limita_attesa_lock() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")


#: mai delegabili di R14 (056) più ``documentazione:genera``
MAI_DELEGABILI_R14 = ("ARRAY['scarto:esegui', 'scarto:autorizza', 'annullamento:approva', "
                      "'firmatari:gestisci', 'pratica:chiudi_eccezioni', 'utenti:gestisci', "
                      "'ruoli:gestisci', 'accesso:configura', 'firma_fornitori:gestisci', "
                      "'pec_canali:gestisci', 'pratica:riprendi']::text[]")
MAI_DELEGABILI = MAI_DELEGABILI_R14.replace("'pratica:riprendi'",
                                            "'pratica:riprendi', 'documentazione:genera'")


def _vincolo_ruoli(elenco: str) -> None:
    """Vincolo NOT VALID nella transazione della revisione; VALIDATE fuori dal lock
    esclusivo (autocommit), come la 056 (REGOLE §31 R05: VALIDATE mai sotto lock esclusivo)."""
    op.execute("ALTER TABLE public.ruoli_personalizzati DROP CONSTRAINT IF EXISTS "
               "ck_ruoli_personalizzati_non_delegabili")
    op.execute("ALTER TABLE public.ruoli_personalizzati ADD CONSTRAINT "
               f"ck_ruoli_personalizzati_non_delegabili CHECK (NOT (permessi && {elenco})) "
               "NOT VALID")


def _valida_vincolo_ruoli() -> None:
    istruzione = ("ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
                  "ck_ruoli_personalizzati_non_delegabili")
    if context.is_offline_mode():
        op.execute(istruzione)
        return
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_attesa_lock()}'")
        op.execute(istruzione)
        op.execute("RESET lock_timeout")


def _esegui_blocco(sql: str) -> None:
    """Una funzione PL/pgSQL (fino a ``$$;``) e poi le istruzioni dei trigger, una per volta
    (asyncpg non accetta più comandi in una sola istruzione preparata)."""
    funzione, _, resto = sql.partition("\n$$;")
    op.execute(funzione + "\n$$")
    for istruzione in (x.strip() for x in resto.split(";")):
        if istruzione:
            op.execute(istruzione)


def upgrade() -> None:
    _limita_attesa_lock()
    for istruzione in (s.strip() for s in _TABELLE.split(";")):
        if istruzione:
            op.execute(istruzione)
    _esegui_blocco(_NUOVE)
    _esegui_blocco(_PROTETTE)
    _esegui_blocco(_GENERAZIONI)
    op.execute(_PRIVILEGI)
    _vincolo_ruoli(MAI_DELEGABILI)
    _valida_vincolo_ruoli()


#: revisione R17 (sicurezza A5): le versioni generate sono evidenze; il downgrade non le
#: cancella in silenzio
_RIFIUTA_DOWNGRADE = """
DO $d$
BEGIN
    IF to_regclass('public.documentazione_versioni') IS NOT NULL
            AND EXISTS (SELECT 1 FROM public.documentazione_versioni) THEN
        RAISE EXCEPTION 'downgrade rifiutato: esistono versioni della documentazione generate '
            '(evidenze)';
    END IF;
END
$d$;
"""


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute(_RIFIUTA_DOWNGRADE)
    _vincolo_ruoli(MAI_DELEGABILI_R14)
    _valida_vincolo_ruoli()
    for tabella in reversed(TABELLE_NUOVE):
        op.execute(f"DROP TABLE IF EXISTS public.{tabella}")
    for funzione in ("documentazione_versioni_nuove", "documentazione_versioni_protette",
                     "documentazione_generazioni_protette"):
        op.execute(f"DROP FUNCTION IF EXISTS public.{funzione}()")
