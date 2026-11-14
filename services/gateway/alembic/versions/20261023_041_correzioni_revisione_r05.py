# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Correzioni della revisione indipendente di R05 (rilievi R7, R9, R12, R14)

Revision ID: 20261023_041
Revises: 20261023_040
Create Date: 2026-10-23

1. R9 — ``tenants.natura`` (``PA`` | ``PRIVATO``, nullable): decide se lo scarto
   richiede il nulla osta della Soprintendenza archivistica (D.lgs. 42/2004,
   art. 21). NULL = ente trattato come pubblica amministrazione (il caso più
   prudente, REGOLE §14.3): il codice IPA (facoltativo) non decide più nulla.
   Colonna senza valore predefinito (solo catalogo) e vincolo CHECK ``NOT VALID``
   convalidato fuori dal lock esclusivo.
2. R12 — ``sessioni_revocate``: uscita dal portale lato server. All'uscita il
   gateway registra l'impronta SHA-256 del token della sessione e il suo ``jti``
   fino alla scadenza del token; ``libs.shared.security.revoca`` (token
   verificati) e ``MiddlewareSessioneCookie`` (cookie della sessione) li
   rifiutano in tutti i servizi. Tabella di autenticazione: fuori dalla RLS come
   ``users`` e ``api_keys`` (si legge prima che l'ente sia noto).
3. R7 — ``vd_transazioni_aperte()``: istante d'inizio della transazione aperta
   più vecchia del database (``pg_stat_activity.xact_start``, escluse la
   sessione che chiama e le connessioni non client) e numero delle transazioni
   di cui il chiamante NON vede l'inizio. ``audit_global.created_at`` vale
   ``now()``, cioè proprio ``xact_start`` della transazione che scrive: l'export
   dell'audit nel WORM chiude un periodo solo quando nessuna transazione
   iniziata prima della sua fine è ancora aperta (nessuna riga può più entrare
   nel periodo). SECURITY DEFINER con ``search_path`` fisso: restituisce solo un
   istante e un conteggio, nessun testo di query né utente; EXECUTE solo a
   ``verumdoc_applicazione``. Il proprietario (chi esegue le migrazioni) deve
   vedere ``xact_start`` di tutte le sessioni (superutente o membro di
   ``pg_read_all_stats``; su RDS l'utente master lo è): altrimenti
   ``non_visibili`` > 0 e l'export si ferma (fail-closed).
4. R14 — Row Level Security su ``classi_documentali``, ``impostazioni_valori``,
   ``pdnd_fruitori``, ``rsc_nominations`` con la politica ``vd_ente_isolamento``
   della 038 (USING e WITH CHECK). Righe GLOBALI (``tenant_id`` NULL: classi di
   base e valori del conservatore) leggibili da tutti con
   ``vd_ente_globali_lettura`` (solo SELECT); le scrive soltanto l'ambito
   conservatore. ``catena_pda`` resta fuori: è una tabella del registro
   protetta (migrazione 033: l'event trigger ``vd_protezione_registri_oggetti``
   rifiuta ogni politica sulle tabelle della catena, che restano append-only e
   globali per costruzione come ``catena_registro``). Documento:
   docs/architettura/sicurezza.md.

Ogni DDL che prende un lock esclusivo gira con ``lock_timeout`` (predefinito 5 s,
``VD_MIGRAZIONE_LOCK_TIMEOUT``): con un lettore lungo la migrazione fallisce e si
riprova, invece di fermare il servizio in coda al lock.

Downgrade: politiche tolte e RLS spenta su quelle tabelle, funzione, tabella e
colonna eliminate.

Revisione 2 di R05 (S1): riprendibile dopo un errore in qualunque punto. Con
``transaction_per_migration`` (env.py) il segmento transazionale che precede un
``autocommit_block`` si salta se è già stato confermato (``_gia_applicato``:
controllo di catalogo, il segmento è atomico); indici CONCURRENTLY rimasti
invalidi si ricreano; ``VALIDATE`` è idempotente.
"""
# ruff: noqa: S608 — SQL composto solo con i nomi costanti di questo modulo
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261023_041"
down_revision = "20261023_040"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
POLITICA = "vd_ente_isolamento"
POLITICA_GLOBALI = "vd_ente_globali_lettura"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
#: R14: tabelle con ``tenant_id`` ancora fuori dalla RLS dopo la 040
TABELLE_ENTE_041: tuple[str, ...] = (
    "classi_documentali", "impostazioni_valori", "pdnd_fruitori", "rsc_nominations",
)
#: Righe senza ente leggibili da tutti (configurazione globale del conservatore)
TABELLE_GLOBALI_LEGGIBILI_041: tuple[str, ...] = ("classi_documentali", "impostazioni_valori")

_FUNZIONE_TRANSAZIONI = """
CREATE OR REPLACE FUNCTION public.vd_transazioni_aperte(
    OUT inizio_piu_vecchio timestamptz, OUT non_visibili integer)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT min(a.xact_start) FILTER (WHERE a.state IS NOT NULL),
           (count(*) FILTER (WHERE a.state IS NULL))::integer
    FROM pg_catalog.pg_stat_activity a
    WHERE a.datname = pg_catalog.current_database()
      AND a.backend_type = 'client backend'
      AND a.pid <> pg_catalog.pg_backend_pid()
$$;
"""

_PRIVILEGI_FUNZIONE = """
DO $r$
BEGIN
    REVOKE ALL ON FUNCTION public.vd_transazioni_aperte() FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@RUOLO@') THEN
        GRANT EXECUTE ON FUNCTION public.vd_transazioni_aperte() TO @RUOLO@;
    END IF;
END
$r$;
""".replace("@RUOLO@", RUOLO_APPLICAZIONE)


def _lock_timeout() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def _limita_attesa_lock() -> None:
    """``lock_timeout`` per la transazione corrente (SET LOCAL)."""
    op.execute(f"SET LOCAL lock_timeout = '{_lock_timeout()}'")


def _conferma() -> None:
    """Chiude la transazione corrente (rilascia i lock); Alembic ne apre una nuova."""
    if not context.is_offline_mode():
        with op.get_context().autocommit_block():
            pass


def _gia_applicato(oggetto: str) -> bool:
    """Revisione R05 (S1): True se il segmento transazionale che crea
    ``oggetto`` è già stato confermato da una corsa precedente interrotta (il
    segmento è UNA transazione: o tutto o niente; ``alembic_version`` si
    aggiorna solo a fine revisione)."""
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def upgrade() -> None:
    _limita_attesa_lock()
    # Segmenti (revisione R05, S1): [1-4, una transazione] → commit →
    # [VALIDATE in autocommit, idempotente] → [versione]. Il primo si salta se
    # una corsa interrotta l'ha già confermato.
    if not _gia_applicato("public.sessioni_revocate"):
        _catalogo()
    _conferma()
    # Convalida del vincolo fuori dalla transazione con i lock esclusivi
    # (SHARE UPDATE EXCLUSIVE: letture e scritture continuano)
    if context.is_offline_mode():
        op.execute("ALTER TABLE public.tenants VALIDATE CONSTRAINT ck_tenants_natura")
    else:
        with op.get_context().autocommit_block():
            op.execute("ALTER TABLE public.tenants VALIDATE CONSTRAINT ck_tenants_natura")


def _catalogo() -> None:
    # 1. R9: natura dell'ente (solo catalogo) e vincolo NOT VALID
    op.add_column("tenants", sa.Column("natura", sa.String(16)), schema="public")
    op.execute("ALTER TABLE public.tenants ADD CONSTRAINT ck_tenants_natura "
               "CHECK (natura IS NULL OR natura IN ('PA', 'PRIVATO')) NOT VALID")
    # 2. R12: sessioni del portale revocate all'uscita
    op.create_table(
        "sessioni_revocate",
        sa.Column("impronta", sa.String(64), primary_key=True),
        sa.Column("jti", sa.String(200)),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("scade_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revocata_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_sessioni_revocate_impronta"),
        schema="public",
    )
    op.create_index("idx_sessioni_revocate_jti", "sessioni_revocate", ["jti"], schema="public",
                    postgresql_where=sa.text("jti IS NOT NULL"))
    op.create_index("idx_sessioni_revocate_scadenza", "sessioni_revocate", ["scade_il"],
                    schema="public")
    # 3. R7: orizzonte delle transazioni aperte per l'export dell'audit
    op.execute(_FUNZIONE_TRANSAZIONI)
    op.execute(_PRIVILEGI_FUNZIONE)
    # 4. R14: Row Level Security (ENABLE prende ACCESS EXCLUSIVE per un istante)
    for tabella in TABELLE_ENTE_041:
        op.execute(f"ALTER TABLE public.{tabella} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA} ON public.{tabella} AS PERMISSIVE FOR ALL "
                   f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    for tabella in TABELLE_GLOBALI_LEGGIBILI_041:
        op.execute(f"DROP POLICY IF EXISTS {POLITICA_GLOBALI} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA_GLOBALI} ON public.{tabella} AS PERMISSIVE "
                   f"FOR SELECT TO PUBLIC USING (tenant_id IS NULL)")


def downgrade() -> None:
    _limita_attesa_lock()
    for tabella in TABELLE_GLOBALI_LEGGIBILI_041:
        op.execute(f"DROP POLICY IF EXISTS {POLITICA_GLOBALI} ON public.{tabella}")
    for tabella in TABELLE_ENTE_041:
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"ALTER TABLE public.{tabella} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS public.vd_transazioni_aperte()")
    op.drop_index("idx_sessioni_revocate_scadenza", table_name="sessioni_revocate",
                  schema="public")
    op.drop_index("idx_sessioni_revocate_jti", table_name="sessioni_revocate", schema="public")
    op.drop_table("sessioni_revocate", schema="public")
    op.execute("ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS ck_tenants_natura")
    op.drop_column("tenants", "natura", schema="public")
