# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Rinnovo WORM del registro e fencing dei passi periodici (R12a: VD-C-148, VD-C-151)

Revision ID: 20261105_052
Revises: 20261104_051
Create Date: 2026-11-05

1. VD-C-148 — ``registro_worm_oggetti``: oggetti ``registro/`` della hashchain
   (segmenti, checkpoint, controfirme, sigilli, audit) con il termine del blocco
   WORM, per il rinnovo «mai in riduzione» (``app.registro.rinnovo_worm``). Gli
   oggetti scritti prima di R12a li aggiunge il censimento della hashchain
   (cursore in ``registro_worm_censimento``, una riga). Tabelle operative senza
   ente: fuori dalla RLS come ``ciclo_vita_periodico``. Diritti (revisione R12a):
   tolti al gruppo dei servizi e a PUBLIC, concessi SOLO al gruppo
   ``verumdoc_registro_worm`` (NOLOGIN, creato qui se il migratore ha CREATEROLE),
   di cui l'amministratore del DB fa membro il solo login della hashchain.
2. VD-C-151 — ``ciclo_vita_periodico.generazione``: fencing token del lease dei
   passi periodici (cresce a ogni presa; ogni scrittura dei fatti lo riverifica).
   Colonna con predefinito costante: solo catalogo, nessuna riscrittura.

Riprendibile: ``CREATE TABLE IF NOT EXISTS``, ``ADD COLUMN IF NOT EXISTS``, indice
``IF NOT EXISTS`` (CONCURRENTLY in autocommit, tabella nuova; offline senza),
``lock_timeout``. Downgrade: toglie tabelle, indice e colonna (l'elenco si
ricostruisce dal censimento).
"""
from __future__ import annotations

import os

from alembic import context, op

revision = "20261105_052"
down_revision = "20261104_051"
branch_labels = None
depends_on = None

INDICE = "idx_registro_worm_oggetti_termine"
RUOLO_REGISTRO_WORM = "verumdoc_registro_worm"
RUOLO_APPLICAZIONE = "verumdoc_applicazione"
TABELLE = ("registro_worm_oggetti", "registro_worm_censimento")

_PRIVILEGI = """
DO $p$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@GRUPPO@') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE @GRUPPO@ NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo @GRUPPO@ non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;
    @REVOCHE@
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@GRUPPO@') THEN
        @CONCESSIONI@
    END IF;
END
$p$;
""".replace("@GRUPPO@", RUOLO_REGISTRO_WORM).replace("@REVOCHE@", " ".join(
    f"REVOKE ALL ON public.{t} FROM PUBLIC; IF EXISTS (SELECT 1 FROM pg_roles WHERE "
    f"rolname = '{RUOLO_APPLICAZIONE}') THEN REVOKE ALL ON public.{t} FROM "
    f"{RUOLO_APPLICAZIONE}; END IF;" for t in TABELLE)).replace("@CONCESSIONI@", " ".join(
    f"GRANT SELECT, INSERT, UPDATE ON public.{t} TO {RUOLO_REGISTRO_WORM};" for t in TABELLE))


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _in_autocommit(*istruzioni: str) -> None:
    if context.is_offline_mode():
        for i in istruzioni:
            op.execute(i.replace(" CONCURRENTLY", ""))
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def _indice_non_valido(nome: str) -> str:
    return (f"DO $i$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c "
            f"ON c.oid = i.indexrelid WHERE c.relname = '{nome}' AND NOT i.indisvalid) THEN "
            f"EXECUTE 'DROP INDEX CONCURRENTLY public.{nome}'; END IF; END $i$")


def upgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.registro_worm_oggetti (
            chiave varchar(500) NOT NULL,
            version_id varchar(200) NOT NULL DEFAULT '',
            conserva_fino_a timestamp with time zone,
            rinnovato_il timestamp with time zone,
            aggiornato_il timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT pk_registro_worm_oggetti PRIMARY KEY (chiave, version_id),
            CONSTRAINT ck_registro_worm_oggetti_prefisso CHECK (chiave LIKE 'registro/%')
        )""")
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.registro_worm_censimento (
            id smallint PRIMARY KEY,
            ultima_chiave varchar(500),
            completato_il timestamp with time zone,
            aggiornato_il timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT ck_registro_worm_censimento_unica CHECK (id = 1)
        )""")
    op.execute(_PRIVILEGI)
    op.execute("ALTER TABLE public.ciclo_vita_periodico ADD COLUMN IF NOT EXISTS generazione "
               "bigint NOT NULL DEFAULT 0")
    _in_autocommit(
        _indice_non_valido(INDICE),
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDICE} ON public.registro_worm_oggetti "
        "(conserva_fino_a NULLS FIRST, chiave)")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("ALTER TABLE public.ciclo_vita_periodico DROP COLUMN IF EXISTS generazione")
    op.execute("DROP TABLE IF EXISTS public.registro_worm_censimento")
    op.execute("DROP TABLE IF EXISTS public.registro_worm_oggetti")
