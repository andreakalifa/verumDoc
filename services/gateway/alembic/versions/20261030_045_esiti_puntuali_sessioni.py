# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fatti del lavoratore per annullamento e rettifica; sessioni del portale attive (R06)

Revision ID: 20261030_045
Revises: 20261024_044
Create Date: 2026-10-30

1. VD-C-119. Il riscontro della hashchain per le foglie «annullamento» e
   «rettifica» controllava solo righe che il login dell'API di ciclo-vita
   (esposta all'ingress) può scrivere. Ora pretende anche un FATTO in
   ``esiti_esecuzione`` (043: INSERT solo al ruolo
   ``verumdoc_ciclo_vita_lavoratore``):
     - ``ANNULLAMENTO`` / ``ATTO_ARCHIVIATO``: il lavoratore ha archiviato nel
       WORM l'atto di annullamento (``dettagli.atto_sha256`` = impronta della
       foglia);
     - ``RETTIFICA`` / ``PDV_VERSATO``: il lavoratore ha versato sul gateway il
       PdV di rettifica (``dettagli.pdv_rettifica`` = PdV del riferimento della
       foglia, ``dettagli.atto_sha256`` = impronta della foglia).
   Vincoli: tipi ed esiti ammessi, coppie (tipo, esito), PdA obbligatorio e
   dettagli obbligatori per i fatti nuovi. Nessun recupero dei fatti mancanti:
   le pratiche di ciclo di vita nascono con R05, non ancora in esercizio.
2. VD-C-127. ``sessioni_attive``: sessioni del portale con limite d'inattività
   fissato all'accesso e ultima attività (rinnovo a scorrimento); tabella di
   autenticazione, fuori dalla Row Level Security come ``sessioni_revocate``.

   Revisione R06 (V4): la tabella è fuori dai privilegi predefiniti di
   ``verumdoc_applicazione`` (031), che hanno TUTTI i login dei servizi,
   compreso il server SFTP esposto: diritti solo al gruppo
   ``verumdoc_sessioni_portale`` (login dei servizi del portale), con UPDATE
   della sola ``ultima_attivita`` (il limite fissato all'accesso e la scadenza
   non si cambiano); trigger: ``ultima_attivita`` mai nel futuro; limite
   d'inattività fra 1 minuto e 2 ore.

Riprendibile (S1): tabella creata solo se assente (``to_regclass``), vincoli
tolti e rimessi ``NOT VALID`` e convalidati in autocommit (come la 044),
``lock_timeout`` per i DDL esclusivi.

Downgrade: ``sessioni_attive`` tolta; ``esiti_esecuzione`` torna ai vincoli
della 044 se non ci sono righe dei tipi nuovi (altrimenti errore esplicito:
sono evidenze append-only).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261030_045"
down_revision = "20261024_044"
branch_labels = None
depends_on = None

TIPI_044 = ("SCARTO",)
ESITI_044 = ("ELIMINATO", "AUTORIZZAZIONI_VERIFICATE")
TIPI = ("SCARTO", "ANNULLAMENTO", "RETTIFICA")
ESITI = ("ELIMINATO", "AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO", "PDV_VERSATO")
#: Esiti che riguardano sempre un PdA
ESITI_CON_PDA = ("ELIMINATO", "ATTO_ARCHIVIATO", "PDV_VERSATO")
COPPIE = ("(tipo = 'SCARTO' AND esito IN ('ELIMINATO', 'AUTORIZZAZIONI_VERIFICATE')) "
          "OR (tipo = 'ANNULLAMENTO' AND esito = 'ATTO_ARCHIVIATO') "
          "OR (tipo = 'RETTIFICA' AND esito = 'PDV_VERSATO')")
DETTAGLI = ("esito NOT IN ('ATTO_ARCHIVIATO', 'PDV_VERSATO') "
            "OR (COALESCE((dettagli ->> 'atto_sha256') ~ '^[0-9a-f]{64}$', false) "
            "AND (esito <> 'PDV_VERSATO' OR (dettagli ->> 'pdv_rettifica') IS NOT NULL))")
#: Revisione R06 (V4): gruppo dei login dei servizi del portale
RUOLO_SESSIONI = "verumdoc_sessioni_portale"
RUOLO_APPLICAZIONE = "verumdoc_applicazione"
INATTIVITA_MASSIMA_SECONDI = 7200

_PRIVILEGI_SESSIONI = """
DO $p$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@SESSIONI@') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE @SESSIONI@ NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo @SESSIONI@ non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;
    REVOKE ALL ON public.sessioni_attive FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@APPLICAZIONE@') THEN
        REVOKE ALL ON public.sessioni_attive FROM @APPLICAZIONE@;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@SESSIONI@') THEN
        GRANT USAGE ON SCHEMA public TO @SESSIONI@;
        GRANT SELECT, INSERT, DELETE ON public.sessioni_attive TO @SESSIONI@;
        GRANT UPDATE (ultima_attivita) ON public.sessioni_attive TO @SESSIONI@;
    END IF;
END
$p$;
""".replace("@SESSIONI@", RUOLO_SESSIONI).replace("@APPLICAZIONE@", RUOLO_APPLICAZIONE)

_ORA_SERVER = """
CREATE OR REPLACE FUNCTION public.sessioni_attive_ora_server()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- nessun rinnovo «nel futuro»: la sessione non si allunga oltre l'adesso
    IF NEW.ultima_attivita IS NULL OR NEW.ultima_attivita > now() THEN
        NEW.ultima_attivita := now();
    END IF;
    RETURN NEW;
END;
$$
"""
_TRIGGER_ORA_SERVER = (
    "DROP TRIGGER IF EXISTS trg_sessioni_attive_ora_server ON public.sessioni_attive",
    "CREATE TRIGGER trg_sessioni_attive_ora_server BEFORE INSERT OR UPDATE ON "
    "public.sessioni_attive FOR EACH ROW EXECUTE FUNCTION public.sessioni_attive_ora_server()",
)

VINCOLI = ("ck_esiti_esecuzione_tipo", "ck_esiti_esecuzione_esito", "ck_esiti_esecuzione_aip",
           "ck_esiti_esecuzione_coppia", "ck_esiti_esecuzione_dettagli")


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _gia_applicato(oggetto: str) -> bool:
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def _vincolo(nome: str, condizione: str | None) -> None:
    op.execute(f"ALTER TABLE public.esiti_esecuzione DROP CONSTRAINT IF EXISTS {nome}")
    if condizione is not None:
        op.execute(f"ALTER TABLE public.esiti_esecuzione ADD CONSTRAINT {nome} "
                   f"CHECK ({condizione}) NOT VALID")


def _convalida(*vincoli: str) -> None:
    istruzioni = [f"ALTER TABLE public.esiti_esecuzione VALIDATE CONSTRAINT {v}"
                  for v in vincoli]
    if context.is_offline_mode():
        for i in istruzioni:
            op.execute(i)
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def upgrade() -> None:
    _limita_attesa_lock()
    # 1. VD-C-119: fatti del lavoratore per annullamento e rettifica
    _vincolo("ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(TIPI)})")
    _vincolo("ck_esiti_esecuzione_esito", f"esito IN ({_elenco(ESITI)})")
    _vincolo("ck_esiti_esecuzione_aip",
             f"esito NOT IN ({_elenco(ESITI_CON_PDA)}) OR aip_id IS NOT NULL")
    _vincolo("ck_esiti_esecuzione_coppia", COPPIE)
    _vincolo("ck_esiti_esecuzione_dettagli", DETTAGLI)
    # 2. VD-C-127: sessioni del portale attive
    if not _gia_applicato("public.sessioni_attive"):
        _sessioni()
    # revisione R06 (V4): privilegi minimi e orario del server (anche in ripresa)
    op.execute("ALTER TABLE public.sessioni_attive DROP CONSTRAINT IF EXISTS "
               "ck_sessioni_attive_inattivita")
    op.execute("ALTER TABLE public.sessioni_attive ADD CONSTRAINT ck_sessioni_attive_inattivita "
               f"CHECK (inattivita_secondi BETWEEN 60 AND {INATTIVITA_MASSIMA_SECONDI})")
    op.execute(_ORA_SERVER)
    for istruzione in _TRIGGER_ORA_SERVER:
        op.execute(istruzione)
    op.execute(_PRIVILEGI_SESSIONI)
    _convalida(*VINCOLI)


def _sessioni() -> None:
    op.create_table(
        "sessioni_attive",
        sa.Column("impronta", sa.String(64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ruolo", sa.String(40), nullable=False, server_default=sa.text("''")),
        sa.Column("inattivita_secondi", sa.Integer, nullable=False),
        sa.Column("ultima_attivita", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("scade_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_sessioni_attive_impronta"),
        schema="public",
    )
    op.create_index("idx_sessioni_attive_utente", "sessioni_attive", ["user_id"],
                    schema="public", postgresql_where=sa.text("user_id IS NOT NULL"))
    op.create_index("idx_sessioni_attive_scadenza", "sessioni_attive", ["scade_il"],
                    schema="public")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("DROP TABLE IF EXISTS public.sessioni_attive")
    op.execute("DROP FUNCTION IF EXISTS public.sessioni_attive_ora_server()")
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.esiti_esecuzione WHERE tipo <> 'SCARTO') THEN
                RAISE EXCEPTION 'downgrade 20261030_045 impossibile: esiti_esecuzione ha fatti '
                    'di annullamento o rettifica (evidenze append-only del lavoratore)';
            END IF;
        END
        $d$;
    """)
    _vincolo("ck_esiti_esecuzione_dettagli", None)
    _vincolo("ck_esiti_esecuzione_coppia", None)
    _vincolo("ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(TIPI_044)})")
    _vincolo("ck_esiti_esecuzione_esito", f"esito IN ({_elenco(ESITI_044)})")
    _vincolo("ck_esiti_esecuzione_aip", "esito <> 'ELIMINATO' OR aip_id IS NOT NULL")
    _convalida("ck_esiti_esecuzione_tipo", "ck_esiti_esecuzione_esito",
               "ck_esiti_esecuzione_aip")
