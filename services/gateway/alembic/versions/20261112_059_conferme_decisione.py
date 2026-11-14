# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Collaudo finale (R17: VD-P-164 conferma firmata delle decisioni sensibili, VD-P-155)

Revision ID: 20261112_059
Revises: 20261111_058
Create Date: 2026-11-12

1. ``conferme_decisione``: conferme delle decisioni sensibili FIRMATE DAL GATEWAY
   (``libs/shared/conferme_decisione.py``) e registrate dall'API di ciclo-vita nella stessa
   transazione della decisione. Il trigger ``conferme_decisione_nuove`` (BEFORE INSERT)
   fissa ``registrata_il`` (orologio del database, non del chiamante) e
   ``versione_pratica`` (versione della pratica in quel momento; 0 per le azioni che creano
   la pratica), e pretende che nonce, azione, chiave, utente ed ente delle colonne siano
   quelli del payload firmato e che l'ente sia quello della pratica. Nessun UPDATE, DELETE o
   TRUNCATE (trigger ``conferme_decisione_protette``). Nonce UNICO: una conferma non si
   registra due volte.
2. ``conferme_lavoratore``: fatti del SOLO lavoratore (append-only): ``VERIFICATA`` (conferma
   verificata e nonce consumato: un indice unico sul nonce, così lo stesso nonce non vale per
   un'altra conferma), ``SOSPESA`` (pratica sospesa dopo i tentativi esauriti: la ripresa
   richiede una conferma ``PRATICA_RIPRENDI`` registrata dopo), ``GIRO_CHIUSO`` (un lavoro è
   terminato: un nuovo giro di esecuzione richiede una conferma registrata dopo) e
   ``RIPRESA_SISTEMA`` (ripresa automatica chiesta dal lavoro periodico, che riusa la conferma
   del giro precedente). L'API non la scrive né la legge. Revisione R17: per le pratiche già
   sospese al momento della migrazione si scrive un fatto ``SOSPESA``.
3. ``conferme_transizione_r17``: pratiche APERTE al momento della migrazione con le azioni
   già decise prima (scritta SOLO da questa migrazione, letta dal lavoratore): per quelle
   azioni non si pretende una conferma che allora non esisteva.
4. VD-P-155: ``RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE`` diventa RICHIESTA come valore
   generale e riservata al conservatore anche per ente. Tolti (con la riga di storico): il
   valore NON_RICHIESTA del conservatore (vale il nuovo predefinito), i limiti per ente
   ``LIMITE.<codice>`` (voce non più nel registro) e i valori NON_RICHIESTA degli enti che
   il conservatore non aveva concesso con il limite; resta come deroga il valore
   NON_RICHIESTA di un ente a cui il conservatore aveva concesso il limite NON_RICHIESTA.
   Le pratiche già aperte mantengono la fotografia ``richiede_autorizzazione_produttore``.
5. GRANT minimi: nulla a PUBLIC né al gruppo comune dei servizi; gruppo nuovo
   ``verumdoc_conferme_registra`` (SOLO il login dell'API di ciclo-vita): SELECT e INSERT
   delle sole colonne d'origine delle conferme; ``verumdoc_ciclo_vita_lavoratore``: SELECT
   delle conferme e della transizione, SELECT e INSERT delle sole colonne d'origine dei fatti
   del lavoratore.

Riprendibile (``IF NOT EXISTS``, ``CREATE OR REPLACE``), ``lock_timeout``, trigger anche su
INSERT. Tabelle con ente: Row Level Security per ente (``vd_ente_isolamento``). Downgrade:
rifiutato se esistono conferme registrate (evidenze delle decisioni); i valori tolti al punto
4 non si ripristinano (il predefinito di prima torna con il codice di prima).
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import os

from alembic import op

revision = "20261112_059"
down_revision = "20261111_058"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
GRUPPO_REGISTRA = "verumdoc_conferme_registra"
GRUPPO_LAVORATORE = "verumdoc_ciclo_vita_lavoratore"
TABELLE_NUOVE = ("conferme_decisione", "conferme_lavoratore", "conferme_transizione_r17")
TABELLE_RLS = ("conferme_decisione", "conferme_lavoratore")
POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
AZIONI = ("SCARTO_APPROVA", "SCARTO_ESEGUI", "RIVERSAMENTO_USCITA_ESEGUI",
          "ANNULLAMENTO_RICHIEDI", "ANNULLAMENTO_APPROVA", "RETTIFICA_RICHIEDI",
          "PRATICA_RIPRENDI")
AZIONI_CREAZIONE = ("ANNULLAMENTO_RICHIEDI", "RETTIFICA_RICHIEDI")
COLONNE_CONFERMA = ("pratica_id", "tenant_id", "azione", "kid", "nonce", "utente_id",
                    "payload", "firma")
COLONNE_FATTO = ("pratica_id", "tenant_id", "evento", "conferma_id", "nonce", "azione")
CODICE_155 = "RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE"


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


_TABELLE = f"""
CREATE TABLE IF NOT EXISTS public.conferme_decisione (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pratica_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    azione varchar(40) NOT NULL,
    kid varchar(64) NOT NULL,
    nonce char(32) NOT NULL,
    utente_id uuid NOT NULL,
    payload bytea NOT NULL,
    firma bytea NOT NULL,
    versione_pratica integer NOT NULL DEFAULT 0,
    registrata_il timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_conferme_decisione_pratica FOREIGN KEY (pratica_id)
        REFERENCES public.pratiche (id) ON DELETE RESTRICT,
    CONSTRAINT fk_conferme_decisione_ente FOREIGN KEY (tenant_id)
        REFERENCES public.tenants (id) ON DELETE RESTRICT,
    CONSTRAINT ck_conferme_decisione_azione CHECK (azione IN ({_elenco(AZIONI)})),
    CONSTRAINT ck_conferme_decisione_nonce CHECK (nonce ~ '^[0-9a-f]{{32}}$'),
    CONSTRAINT ck_conferme_decisione_firma CHECK (octet_length(firma) = 64),
    CONSTRAINT ck_conferme_decisione_payload CHECK (octet_length(payload) BETWEEN 2 AND 4096),
    CONSTRAINT uq_conferme_decisione_nonce UNIQUE (nonce)
);
CREATE INDEX IF NOT EXISTS idx_conferme_decisione_pratica
    ON public.conferme_decisione (pratica_id);
CREATE TABLE IF NOT EXISTS public.conferme_lavoratore (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pratica_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    evento varchar(20) NOT NULL,
    conferma_id uuid,
    nonce char(32),
    azione varchar(40),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_conferme_lavoratore_pratica FOREIGN KEY (pratica_id)
        REFERENCES public.pratiche (id) ON DELETE RESTRICT,
    CONSTRAINT fk_conferme_lavoratore_conferma FOREIGN KEY (conferma_id)
        REFERENCES public.conferme_decisione (id) ON DELETE RESTRICT,
    CONSTRAINT ck_conferme_lavoratore_evento CHECK (evento IN ('VERIFICATA', 'SOSPESA', 'GIRO_CHIUSO',
                                                               'RIPRESA_SISTEMA')),
    CONSTRAINT ck_conferme_lavoratore_campi CHECK ((evento = 'VERIFICATA') = (
        conferma_id IS NOT NULL AND nonce IS NOT NULL AND azione IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_conferme_lavoratore_nonce
    ON public.conferme_lavoratore (nonce) WHERE evento = 'VERIFICATA';
CREATE INDEX IF NOT EXISTS idx_conferme_lavoratore_pratica
    ON public.conferme_lavoratore (pratica_id, id);
CREATE TABLE IF NOT EXISTS public.conferme_transizione_r17 (
    pratica_id uuid PRIMARY KEY,
    azioni text[] NOT NULL,
    registrata_il timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_conferme_transizione_r17_pratica FOREIGN KEY (pratica_id)
        REFERENCES public.pratiche (id) ON DELETE RESTRICT
)
"""

_FUNZIONI = (f"""
CREATE OR REPLACE FUNCTION public.conferme_decisione_nuove() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    contenuto jsonb;
    versione_attuale integer;
    ente_pratica uuid;
BEGIN
    NEW.registrata_il := clock_timestamp();
    BEGIN
        contenuto := convert_from(NEW.payload, 'UTF8')::jsonb;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION 'conferme_decisione: payload non JSON';
    END;
    IF contenuto ->> 'nonce' IS DISTINCT FROM NEW.nonce
            OR contenuto ->> 'azione' IS DISTINCT FROM NEW.azione
            OR contenuto ->> 'kid' IS DISTINCT FROM NEW.kid
            OR contenuto ->> 'utente' IS DISTINCT FROM NEW.utente_id::text
            OR contenuto ->> 'ente' IS DISTINCT FROM NEW.tenant_id::text THEN
        RAISE EXCEPTION 'conferme_decisione: colonne diverse dal payload firmato';
    END IF;
    SELECT versione, tenant_id INTO versione_attuale, ente_pratica
      FROM public.pratiche WHERE id = NEW.pratica_id;
    IF NOT FOUND OR ente_pratica IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'conferme_decisione: pratica assente o di un altro ente';
    END IF;
    NEW.versione_pratica := CASE WHEN NEW.azione IN ({_elenco(AZIONI_CREAZIONE)}) THEN 0
                                 ELSE versione_attuale END;
    RETURN NEW;
END;
$$""", """
CREATE OR REPLACE FUNCTION public.conferme_decisione_protette() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% vietata: le conferme delle decisioni sono append-only', TG_OP;
END;
$$""", """
CREATE OR REPLACE FUNCTION public.conferme_lavoratore_nuove() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$""")

_TRIGGER = (
    "DROP TRIGGER IF EXISTS trg_conferme_decisione_nuove ON public.conferme_decisione",
    "CREATE TRIGGER trg_conferme_decisione_nuove BEFORE INSERT ON public.conferme_decisione "
    "FOR EACH ROW EXECUTE FUNCTION public.conferme_decisione_nuove()",
    "DROP TRIGGER IF EXISTS trg_conferme_decisione_protette ON public.conferme_decisione",
    "CREATE TRIGGER trg_conferme_decisione_protette BEFORE UPDATE OR DELETE "
    "ON public.conferme_decisione FOR EACH ROW "
    "EXECUTE FUNCTION public.conferme_decisione_protette()",
    "DROP TRIGGER IF EXISTS trg_conferme_decisione_troncamento ON public.conferme_decisione",
    "CREATE TRIGGER trg_conferme_decisione_troncamento BEFORE TRUNCATE "
    "ON public.conferme_decisione FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.conferme_decisione_protette()",
    "DROP TRIGGER IF EXISTS trg_conferme_lavoratore_nuove ON public.conferme_lavoratore",
    "CREATE TRIGGER trg_conferme_lavoratore_nuove BEFORE INSERT ON public.conferme_lavoratore "
    "FOR EACH ROW EXECUTE FUNCTION public.conferme_lavoratore_nuove()",
    "DROP TRIGGER IF EXISTS trg_conferme_lavoratore_protette ON public.conferme_lavoratore",
    "CREATE TRIGGER trg_conferme_lavoratore_protette BEFORE UPDATE OR DELETE "
    "ON public.conferme_lavoratore FOR EACH ROW "
    "EXECUTE FUNCTION public.conferme_decisione_protette()",
    "DROP TRIGGER IF EXISTS trg_conferme_lavoratore_troncamento ON public.conferme_lavoratore",
    "CREATE TRIGGER trg_conferme_lavoratore_troncamento BEFORE TRUNCATE "
    "ON public.conferme_lavoratore FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.conferme_decisione_protette()",
    "DROP TRIGGER IF EXISTS trg_conferme_transizione_r17_protetta "
    "ON public.conferme_transizione_r17",
    "CREATE TRIGGER trg_conferme_transizione_r17_protetta BEFORE UPDATE OR DELETE "
    "ON public.conferme_transizione_r17 FOR EACH ROW "
    "EXECUTE FUNCTION public.conferme_decisione_protette()",
)

#: pratiche aperte con le azioni già decise prima della migrazione (solo questa la scrive)
_TRANSIZIONE = """
INSERT INTO public.conferme_transizione_r17 (pratica_id, azioni)
SELECT p.id, a.azioni
  FROM public.pratiche p
  CROSS JOIN LATERAL (SELECT array_remove(ARRAY[
      CASE WHEN p.tipo = 'SCARTO' AND p.stato IN ('ESEGUIBILE', 'ESEGUITA_CON_ECCEZIONI')
           THEN 'SCARTO_APPROVA' END,
      CASE WHEN p.tipo = 'SCARTO' AND (p.lavoro IS NOT NULL
                OR p.ultimo_errore ->> 'codice' = 'LC-409-TENTATIVI-ESAURITI')
           THEN 'SCARTO_ESEGUI' END,
      CASE WHEN p.tipo = 'RIVERSAMENTO_USCITA' AND (p.lavoro IS NOT NULL
                OR p.ultimo_errore ->> 'codice' = 'LC-409-TENTATIVI-ESAURITI')
           THEN 'RIVERSAMENTO_USCITA_ESEGUI' END,
      CASE WHEN p.tipo = 'ANNULLAMENTO' THEN 'ANNULLAMENTO_RICHIEDI' END,
      CASE WHEN p.tipo = 'ANNULLAMENTO' AND p.stato = 'APPROVATA'
           THEN 'ANNULLAMENTO_APPROVA' END,
      CASE WHEN p.tipo = 'RETTIFICA' THEN 'RETTIFICA_RICHIEDI' END
  ]::text[], NULL) AS azioni) a
 WHERE (p.conclusa_il IS NULL OR p.stato = 'ESEGUITA_CON_ECCEZIONI')
   AND cardinality(a.azioni) > 0
ON CONFLICT (pratica_id) DO NOTHING
"""

#: revisione R17: le pratiche già sospese ripartono solo con una ripresa confermata
_SOSPESE = """
INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento)
SELECT p.id, p.tenant_id, 'SOSPESA'
  FROM public.pratiche p
 WHERE p.lavoro IS NULL AND p.ultimo_errore ->> 'codice' = 'LC-409-TENTATIVI-ESAURITI'
   AND NOT EXISTS (SELECT 1 FROM public.conferme_lavoratore c
                    WHERE c.pratica_id = p.id AND c.evento = 'SOSPESA')
"""

#: VD-P-155: valori che con la voce riservata diventerebbero deroghe non concesse
_DEROGHE_155 = f"""
WITH tolte AS (
    DELETE FROM public.impostazioni_valori v
     WHERE v.codice = 'LIMITE.{CODICE_155}'
        OR (v.codice = '{CODICE_155}' AND v.livello = 'CONSERVATORE'
            AND v.valore = '"NON_RICHIESTA"'::jsonb)
        OR (v.codice = '{CODICE_155}' AND v.livello = 'ENTE'
            AND v.valore = '"NON_RICHIESTA"'::jsonb
            AND NOT EXISTS (SELECT 1 FROM public.impostazioni_valori l
                             WHERE l.codice = 'LIMITE.{CODICE_155}' AND l.livello = 'ENTE'
                               AND l.tenant_id = v.tenant_id
                               AND l.valore = '"NON_RICHIESTA"'::jsonb))
    RETURNING v.livello, v.tenant_id, v.classe_id, v.codice, v.valore)
INSERT INTO public.impostazioni_storico (livello, tenant_id, classe_id, codice, valore_prima,
                                         valore_dopo, motivo)
SELECT livello, tenant_id, classe_id, codice, valore, NULL,
       'R17 (VD-P-155, decisione del committente del 01/11/2026): autorizzazione del '
       'produttore RICHIESTA come valore generale; deroga per ente solo dal conservatore'
  FROM tolte
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


_PRIVILEGI = "DO $p$\nBEGIN" + _crea_gruppo(GRUPPO_REGISTRA) + "\n    " + " ".join([
    *(_revoca(t) for t in TABELLE_NUOVE),
    "REVOKE ALL ON FUNCTION public.conferme_decisione_nuove() FROM PUBLIC;",
    "REVOKE ALL ON FUNCTION public.conferme_decisione_protette() FROM PUBLIC;",
    "REVOKE ALL ON FUNCTION public.conferme_lavoratore_nuove() FROM PUBLIC;",
    _concedi(GRUPPO_REGISTRA, "SELECT", "public.conferme_decisione"),
    _concedi(GRUPPO_REGISTRA, "INSERT (" + ", ".join(COLONNE_CONFERMA) + ")",
             "public.conferme_decisione"),
    _concedi(GRUPPO_LAVORATORE, "SELECT", "public.conferme_decisione"),
    _concedi(GRUPPO_LAVORATORE, "SELECT", "public.conferme_lavoratore"),
    _concedi(GRUPPO_LAVORATORE, "INSERT (" + ", ".join(COLONNE_FATTO) + ")",
             "public.conferme_lavoratore"),
    _concedi(GRUPPO_LAVORATORE, "SELECT", "public.conferme_transizione_r17"),
]) + "\nEND\n$p$;"

_RIFIUTA_DOWNGRADE = """
DO $d$
BEGIN
    IF to_regclass('public.conferme_decisione') IS NOT NULL
            AND EXISTS (SELECT 1 FROM public.conferme_decisione) THEN
        RAISE EXCEPTION 'downgrade rifiutato: esistono conferme di decisioni registrate '
            '(evidenze)';
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
    for funzione in _FUNZIONI:
        op.execute(funzione)
    for istruzione in _TRIGGER:
        op.execute(istruzione)
    for tabella in TABELLE_RLS:
        op.execute(f"ALTER TABLE public.{tabella} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA} ON public.{tabella} AS PERMISSIVE FOR ALL "
                   f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    op.execute(_TRANSIZIONE)
    op.execute(_SOSPESE)
    op.execute(_DEROGHE_155)
    op.execute(_PRIVILEGI)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")
    op.execute(_RIFIUTA_DOWNGRADE)
    op.execute("DROP TABLE IF EXISTS public.conferme_transizione_r17")
    op.execute("DROP TABLE IF EXISTS public.conferme_lavoratore")
    op.execute("DROP TABLE IF EXISTS public.conferme_decisione")
    op.execute("DROP FUNCTION IF EXISTS public.conferme_lavoratore_nuove()")
    op.execute("DROP FUNCTION IF EXISTS public.conferme_decisione_protette()")
    op.execute("DROP FUNCTION IF EXISTS public.conferme_decisione_nuove()")
