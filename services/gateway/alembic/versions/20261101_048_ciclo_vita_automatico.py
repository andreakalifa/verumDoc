# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Ciclo di vita automatico (R09: decisioni D04, D06, D20, D21, D23, D27)

Revision ID: 20261101_048
Revises: 20261031_047
Create Date: 2026-11-01

1. ``esiti_esecuzione`` (fatti scritti SOLO dal ruolo
   ``verumdoc_ciclo_vita_lavoratore``, migrazione 043): fatti nuovi
     - SCARTO / ``CHIUSO_CON_ECCEZIONI`` (D20): il conservatore ha chiuso un PdA
       in eliminazione incompleta, il lavoratore ha riverificato la richiesta;
     - SCARTO / ``METADATI_MINIMIZZATI`` (D06): dei metadati del PdA scartato
       resta l'elenco minimo;
     - SCARTO / ``PROPOSTA_GENERATA`` (D21): pratica in bozza creata dal
       lavoro periodico (prova che la creazione «SISTEMA» è del lavoratore);
     - RETENTION / ``RINNOVATA`` (D04, PdA) e ``RINNOVATA_ALLEGATO`` (allegati,
       verbali, atti delle pratiche): blocco WORM esteso, mai ridotto.
   ``pratica_id`` diventa facoltativo SOLO per i fatti RETENTION (un rinnovo
   non nasce da una pratica). Le impronte dei fatti nuovi sono obbligatorie
   (``dettagli.impronta``): sono quelle delle foglie del registro.
2. Registro delle impronte: tipi di foglia interna ``CHIUSURA_ECCEZIONI``,
   ``MINIMIZZAZIONE``, ``RINNOVO_RETENTION`` (fatti del ciclo di vita che non
   invalidano il contenuto) in ``catena_foglie_interne`` e ``catena_registro``.
3. ``aip.retention_fino_a`` (D04): termine del blocco WORM più breve fra gli
   oggetti del PdA, scritto dal rinnovo; indice parziale sui PdA permanenti
   (``anni_conservazione = 999``) creato CONCURRENTLY fuori transazione.
4. ``ciclo_vita_periodico`` (D20, D21, D04, D06): passi del lavoro periodico
   del lavoratore con lease e ultimo giro (una replica per passo; tabella
   operativa senza ente, fuori dalla Row Level Security come
   ``verifiche_stato``).
5bis. Revisione R09 (Z3): l'annullamento logico lo applica il lavoratore dopo
   la riverifica, con il fatto ANNULLAMENTO / ``AUTORIZZAZIONI_VERIFICATE``.
   Gli annullamenti già applicati dall'API prima di R09 e ancora in coda
   (``lavoro = 'REGISTRO'``) ricevono qui quel fatto (``dettagli.origine =
   migrazione_048``): erano conformi alla regola di allora (nessuna doppia
   approvazione) e non vanno bloccati dalla regola nuova. Revisione R09 (W3):
   quelli nati DOPO la migrazione e prima del Chart li recupera il lavoratore
   R09 al primo avvio (confine: istante del primo avvio, orologio del database,
   in ``ciclo_vita_periodico``, che da qui scrive solo il ruolo del lavoratore).
5. D23: un annullamento RESPINTO (doppia approvazione negata) non impedisce
   una nuova richiesta: l'indice unico vale per le pratiche non respinte
   (nuovo indice CONCURRENTLY, poi il vecchio tolto CONCURRENTLY).
6. D27 (VD-C-142): ruolo di gruppo ``verumdoc_auditor`` (NOLOGIN) in sola
   lettura su ``audit_global``, sulle tabelle del registro che la verifica
   dell'audit legge e su ``impostazioni_valori`` (finestra e giorno della verifica
   completa); ne è membro SOLO il login del CronJob ``verifica-audit`` (Secret
   ``hashchain-verifica-audit-secrets``, chiave DATABASE_URL), creato
   dall'amministratore con ``idle_in_transaction_session_timeout``.

Riprendibile: ``ADD COLUMN IF NOT EXISTS``, tabelle solo se assenti, vincoli
tolti e rimessi ``NOT VALID`` e convalidati in autocommit, indici CONCURRENTLY
fuori dalla transazione (un indice INVALID di un tentativo interrotto si toglie
e si ricrea), ``lock_timeout`` per i DDL esclusivi.

Downgrade: rifiutato se esistono fatti o foglie dei tipi nuovi (evidenze
append-only) o annullamenti respinti con una richiesta successiva.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op

revision = "20261101_048"
down_revision = "20261031_047"
branch_labels = None
depends_on = None

TIPI_047 = ("SCARTO", "ANNULLAMENTO", "RETTIFICA")
ESITI_047 = ("ELIMINATO", "AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO", "PDV_VERSATO")
TIPI = (*TIPI_047, "RETENTION")
ESITI_NUOVI = ("CHIUSO_CON_ECCEZIONI", "METADATI_MINIMIZZATI", "PROPOSTA_GENERATA",
               "RINNOVATA", "RINNOVATA_ALLEGATO")
ESITI = (*ESITI_047, *ESITI_NUOVI)
ESITI_CON_PDA_047 = ("ELIMINATO", "ATTO_ARCHIVIATO", "PDV_VERSATO")
ESITI_CON_PDA = (*ESITI_CON_PDA_047, "CHIUSO_CON_ECCEZIONI", "METADATI_MINIMIZZATI", "RINNOVATA")
#: Fatti nuovi con l'impronta della foglia del registro (o del rinnovo)
ESITI_CON_IMPRONTA = ("CHIUSO_CON_ECCEZIONI", "METADATI_MINIMIZZATI", "RINNOVATA",
                      "RINNOVATA_ALLEGATO")
COPPIE_047 = ("(tipo = 'SCARTO' AND esito IN ('ELIMINATO', 'AUTORIZZAZIONI_VERIFICATE')) "
              "OR (tipo = 'ANNULLAMENTO' AND esito = 'ATTO_ARCHIVIATO') "
              "OR (tipo = 'RETTIFICA' AND esito = 'PDV_VERSATO')")
COPPIE = ("(tipo = 'SCARTO' AND esito IN ('ELIMINATO', 'AUTORIZZAZIONI_VERIFICATE', "
          "'CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', 'PROPOSTA_GENERATA')) "
          "OR (tipo = 'ANNULLAMENTO' AND esito IN ('ATTO_ARCHIVIATO', "
          "'AUTORIZZAZIONI_VERIFICATE')) "
          "OR (tipo = 'RETTIFICA' AND esito = 'PDV_VERSATO') "
          "OR (tipo = 'RETENTION' AND esito IN ('RINNOVATA', 'RINNOVATA_ALLEGATO'))")
PRATICA = "pratica_id IS NOT NULL OR (tipo = 'RETENTION' AND esito = 'RINNOVATA')"
IMPRONTA = ("esito NOT IN ('CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', 'RINNOVATA', "
            "'RINNOVATA_ALLEGATO') "
            "OR COALESCE((dettagli ->> 'impronta') ~ '^[0-9a-f]{64}$', false)")

TIPI_INTERNI_047 = ("SCARTO", "ANNULLAMENTO", "RETTIFICA", "AUDIT")
TIPI_INTERNI = (*TIPI_INTERNI_047, "CHIUSURA_ECCEZIONI", "MINIMIZZAZIONE", "RINNOVO_RETENTION")
TIPI_REGISTRO_047 = ("PDA", "RIVERSAMENTO", *TIPI_INTERNI_047)
TIPI_REGISTRO = ("PDA", "RIVERSAMENTO", *TIPI_INTERNI)

RUOLO_AUDITOR = "verumdoc_auditor"
#: Tabelle lette dalla verifica dell'audit (app.registro.audit_worm.verifica)
TABELLE_AUDITOR = ("audit_global", "catena_foglie_interne", "catena_registro",
                   "catena_checkpoint", "impostazioni_valori")

INDICE_RETENTION = "idx_aip_retention_permanente"
INDICE_ANNULLAMENTO = "uq_pratiche_annullamento_attivo"

_PRIVILEGI_AUDITOR = """
DO $p$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@AUDITOR@') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE @AUDITOR@ NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo @AUDITOR@ non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@AUDITOR@') THEN
        GRANT USAGE ON SCHEMA public TO @AUDITOR@;
        @GRANT@
    END IF;
END
$p$;
""".replace("@AUDITOR@", RUOLO_AUDITOR).replace("@GRANT@", " ".join(
    f"GRANT SELECT ON public.{t} TO {RUOLO_AUDITOR};" for t in TABELLE_AUDITOR))


RUOLO_APPLICAZIONE = "verumdoc_applicazione"
RUOLO_LAVORATORE = "verumdoc_ciclo_vita_lavoratore"

#: Revisione R09 (W3): ``ciclo_vita_periodico`` la scrive SOLO il lavoratore (i
#: lease dei passi e l'istante del primo avvio R09, confine del recupero degli
#: annullamenti R08); l'API la legge (stato del lavoro automatico, metriche)
_PRIVILEGI_PERIODICO = """
DO $p$
BEGIN
    REVOKE ALL ON public.ciclo_vita_periodico FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@APPLICAZIONE@') THEN
        REVOKE ALL ON public.ciclo_vita_periodico FROM @APPLICAZIONE@;
        GRANT SELECT ON public.ciclo_vita_periodico TO @APPLICAZIONE@;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@LAVORATORE@') THEN
        GRANT SELECT, INSERT, UPDATE ON public.ciclo_vita_periodico TO @LAVORATORE@;
    END IF;
END
$p$;
""".replace("@APPLICAZIONE@", RUOLO_APPLICAZIONE).replace("@LAVORATORE@", RUOLO_LAVORATORE)

_ANNULLAMENTI_IN_CODA = """
INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, dettagli)
SELECT 'ANNULLAMENTO', p.id, p.tenant_id, NULL, 'AUTORIZZAZIONI_VERIFICATE',
       jsonb_build_object('origine', 'migrazione_048',
                          'motivo', 'annullamento applicato prima di R09 (regola di allora)')
FROM public.pratiche p
WHERE p.tipo = 'ANNULLAMENTO' AND p.stato = 'ESEGUITA' AND p.lavoro = 'REGISTRO'
  AND NOT EXISTS (SELECT 1 FROM public.esiti_esecuzione e WHERE e.tipo = 'ANNULLAMENTO'
                  AND e.esito = 'AUTORIZZAZIONI_VERIFICATE' AND e.pratica_id = p.id)
"""


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def _vincolo(tabella: str, nome: str, condizione: str | None) -> None:
    op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT IF EXISTS {nome}")
    if condizione is not None:
        op.execute(f"ALTER TABLE public.{tabella} ADD CONSTRAINT {nome} "
                   f"CHECK ({condizione}) NOT VALID")


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


def _indice_non_valido(nome: str) -> str:
    """Un indice CONCURRENTLY interrotto resta INVALID: si toglie prima di ricrearlo."""
    return (f"DO $i$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c "
            f"ON c.oid = i.indexrelid WHERE c.relname = '{nome}' AND NOT i.indisvalid) THEN "
            f"EXECUTE 'DROP INDEX CONCURRENTLY public.{nome}'; END IF; END $i$")


_VINCOLI_ESITI = ("ck_esiti_esecuzione_tipo", "ck_esiti_esecuzione_esito",
                  "ck_esiti_esecuzione_aip", "ck_esiti_esecuzione_coppia",
                  "ck_esiti_esecuzione_pratica", "ck_esiti_esecuzione_impronta")


def upgrade() -> None:
    _limita_attesa_lock()
    # 1. fatti del lavoratore
    op.execute("ALTER TABLE public.esiti_esecuzione ALTER COLUMN pratica_id DROP NOT NULL")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(TIPI)})")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_esito", f"esito IN ({_elenco(ESITI)})")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_aip",
             f"esito NOT IN ({_elenco(ESITI_CON_PDA)}) OR aip_id IS NOT NULL")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_coppia", COPPIE)
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_pratica", PRATICA)
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_impronta", IMPRONTA)
    # 2. tipi di foglia interna
    _vincolo("catena_foglie_interne", "ck_catena_foglie_interne_tipo",
             f"tipo IN ({_elenco(TIPI_INTERNI)})")
    _vincolo("catena_registro", "ck_catena_registro_tipo", f"tipo IN ({_elenco(TIPI_REGISTRO)})")
    # 3. retention dei PdA
    op.execute("ALTER TABLE public.aip ADD COLUMN IF NOT EXISTS retention_fino_a "
               "timestamp with time zone")
    # 4. passi del lavoro periodico
    if not _gia_applicato("public.ciclo_vita_periodico"):
        op.execute("""
            CREATE TABLE public.ciclo_vita_periodico (
                nome varchar(200) PRIMARY KEY,
                ultimo_giro timestamp with time zone,
                ultimo_esito jsonb NOT NULL DEFAULT '{}'::jsonb,
                lease_detentore varchar(200),
                lease_scade_il timestamp with time zone,
                aggiornato_il timestamp with time zone NOT NULL DEFAULT now()
            )""")
    op.execute(_PRIVILEGI_PERIODICO)
    # 5bis. annullamenti applicati dall'API prima di R09 e ancora in coda (Z3);
    # quelli nati fra la migrazione e il Chart li recupera il lavoratore R09 al
    # primo avvio (revisione R09, W3: ``puntuali.recupera_annullamenti_r08``)
    op.execute(_ANNULLAMENTI_IN_CODA)
    # 6. ruolo dell'auditor in sola lettura
    op.execute(_PRIVILEGI_AUDITOR)
    _in_autocommit(*(f"ALTER TABLE public.esiti_esecuzione VALIDATE CONSTRAINT {v}"
                     for v in _VINCOLI_ESITI),
                   "ALTER TABLE public.catena_foglie_interne VALIDATE CONSTRAINT "
                   "ck_catena_foglie_interne_tipo",
                   "ALTER TABLE public.catena_registro VALIDATE CONSTRAINT "
                   "ck_catena_registro_tipo",
                   # 3. indice dei PdA permanenti (fuori dalla transazione, CONCURRENTLY)
                   _indice_non_valido(INDICE_RETENTION),
                   f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDICE_RETENTION} ON public.aip "
                   "(retention_fino_a NULLS FIRST, id) WHERE anni_conservazione = 999",
                   # 5. annullamento: una pratica NON respinta per PdV
                   _indice_non_valido(INDICE_ANNULLAMENTO),
                   f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDICE_ANNULLAMENTO} "
                   "ON public.pratiche (pdv_id) WHERE tipo = 'ANNULLAMENTO' "
                   "AND stato <> 'RESPINTA'",
                   "DROP INDEX CONCURRENTLY IF EXISTS public.uq_pratiche_annullamento")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.esiti_esecuzione
                       WHERE tipo = 'RETENTION' OR esito IN ('CHIUSO_CON_ECCEZIONI',
                             'METADATI_MINIMIZZATI', 'PROPOSTA_GENERATA')
                       OR (tipo = 'ANNULLAMENTO' AND esito = 'AUTORIZZAZIONI_VERIFICATE')) THEN
                RAISE EXCEPTION 'downgrade 20261101_048 impossibile: esistono fatti del ciclo '
                    'di vita automatico (evidenze append-only del lavoratore)';
            END IF;
            IF EXISTS (SELECT 1 FROM public.catena_foglie_interne
                       WHERE tipo IN ('CHIUSURA_ECCEZIONI', 'MINIMIZZAZIONE',
                                      'RINNOVO_RETENTION')) THEN
                RAISE EXCEPTION 'downgrade 20261101_048 impossibile: esistono foglie del '
                    'registro dei tipi nuovi';
            END IF;
            IF EXISTS (SELECT 1 FROM public.pratiche WHERE tipo = 'ANNULLAMENTO'
                       GROUP BY pdv_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'downgrade 20261101_048 impossibile: versamenti con un '
                    'annullamento respinto e una nuova richiesta';
            END IF;
        END
        $d$;
    """)
    op.execute("DROP TABLE IF EXISTS public.ciclo_vita_periodico")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_impronta", None)
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_pratica", None)
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_coppia", COPPIE_047)
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_aip",
             f"esito NOT IN ({_elenco(ESITI_CON_PDA_047)}) OR aip_id IS NOT NULL")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_esito", f"esito IN ({_elenco(ESITI_047)})")
    _vincolo("esiti_esecuzione", "ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(TIPI_047)})")
    op.execute("ALTER TABLE public.esiti_esecuzione ALTER COLUMN pratica_id SET NOT NULL")
    _vincolo("catena_foglie_interne", "ck_catena_foglie_interne_tipo",
             f"tipo IN ({_elenco(TIPI_INTERNI_047)})")
    _vincolo("catena_registro", "ck_catena_registro_tipo",
             f"tipo IN ({_elenco(TIPI_REGISTRO_047)})")
    # l'indice dei PdA permanenti se ne va con la colonna
    op.execute("ALTER TABLE public.aip DROP COLUMN IF EXISTS retention_fino_a")
    _in_autocommit(
        *(f"ALTER TABLE public.esiti_esecuzione VALIDATE CONSTRAINT {v}"
          for v in ("ck_esiti_esecuzione_tipo", "ck_esiti_esecuzione_esito",
                    "ck_esiti_esecuzione_aip", "ck_esiti_esecuzione_coppia")),
        "ALTER TABLE public.catena_foglie_interne VALIDATE CONSTRAINT "
        "ck_catena_foglie_interne_tipo",
        "ALTER TABLE public.catena_registro VALIDATE CONSTRAINT ck_catena_registro_tipo",
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_pratiche_annullamento "
        "ON public.pratiche (pdv_id) WHERE tipo = 'ANNULLAMENTO'",
        f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDICE_ANNULLAMENTO}")
