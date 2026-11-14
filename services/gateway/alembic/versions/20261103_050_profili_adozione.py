# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Profili di tipologia: adozione esplicita (R11: decisione D07, VD-C-136)

Revision ID: 20261103_050
Revises: 20261102_049
Create Date: 2026-11-03

1. Controllo prima di tutto: le versioni predefinite pubblicate con R11
   (``libs/verumdoc_rules/dati/profili/*.v2.json``) non devono avere lo stesso
   numero di una versione creata dal conservatore in ``profili_tipologia``
   (sarebbe un errore di configurazione a ogni versamento della tipologia). Se
   ci sono, la migrazione si FERMA con l'elenco (REGOLE §16.5): il runbook
   ``docs/runbooks/migrazioni_audit.md`` (R11) spiega come procedere.
2. ``profili_tipologia.adozione`` (``automatica`` | ``esplicita``, predefinito
   ``automatica`` = comportamento di R05 per le righe esistenti): una versione
   ad adozione esplicita la sceglie la classe con ``PROFILO_TIPOLOGIA_VERSIONE``.
   Colonna con predefinito costante: solo catalogo (nessuna riscrittura della
   tabella, nessun UPDATE: il trigger append-only della 036 non scatta).

Riprendibile: ``ADD COLUMN IF NOT EXISTS``, vincolo tolto e rimesso ``NOT VALID``
e convalidato in autocommit (fuori dal lock esclusivo), ``lock_timeout`` per i
DDL. In modalità offline (``--sql``) tutto in transazione (nessun CONCURRENTLY
in questa migrazione) e il controllo del punto 1 è un blocco DO nello script.

Nessun contenuto conservato viene toccato (§10.6). Downgrade: rifiutato se
esistono versioni ad adozione esplicita create dal conservatore (si
perderebbe la scelta), altrimenti toglie vincolo e colonna.
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import os

from alembic import context, op

revision = "20261103_050"
down_revision = "20261102_049"
branch_labels = None
depends_on = None

#: Versioni predefinite pubblicate con R11 (fotografia: la migrazione non legge i file).
VERSIONI_R11 = (
    ("aggregazione_documentale", 2), ("contratto", 2),
    ("documento_amministrativo_informatico", 2), ("documento_informatico", 2),
    ("fattura", 2), ("registro_protocollo", 2),
)
VINCOLO = "ck_profili_tipologia_adozione"

_CONTROLLO = """
DO $c$
DECLARE
    doppi text;
BEGIN
    SELECT string_agg(codice || ' v' || versione, ', ' ORDER BY codice, versione) INTO doppi
      FROM public.profili_tipologia WHERE (codice, versione) IN (@VERSIONI@);
    IF doppi IS NOT NULL THEN
        RAISE EXCEPTION 'migrazione 20261103_050 fermata: versioni del conservatore con lo '
            'stesso numero delle versioni predefinite R11: % (vedere il runbook '
            'migrazioni_audit.md, sezione R11)', doppi;
    END IF;
END
$c$;
""".replace("@VERSIONI@", ", ".join(f"('{c}', {v})" for c, v in VERSIONI_R11))


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


def upgrade() -> None:
    _limita_attesa_lock()
    op.execute(_CONTROLLO)
    op.execute("ALTER TABLE public.profili_tipologia ADD COLUMN IF NOT EXISTS adozione "
               "varchar(16) NOT NULL DEFAULT 'automatica'")
    op.execute(f"ALTER TABLE public.profili_tipologia DROP CONSTRAINT IF EXISTS {VINCOLO}")
    op.execute(f"ALTER TABLE public.profili_tipologia ADD CONSTRAINT {VINCOLO} "
               "CHECK (adozione IN ('automatica', 'esplicita')) NOT VALID")
    _in_autocommit(f"ALTER TABLE public.profili_tipologia VALIDATE CONSTRAINT {VINCOLO}")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.profili_tipologia WHERE adozione = 'esplicita') THEN
                RAISE EXCEPTION 'downgrade 20261103_050 impossibile: versioni di profilo ad '
                    'adozione esplicita create dal conservatore';
            END IF;
        END
        $d$;
    """)
    op.execute(f"ALTER TABLE public.profili_tipologia DROP CONSTRAINT IF EXISTS {VINCOLO}")
    op.execute("ALTER TABLE public.profili_tipologia DROP COLUMN IF EXISTS adozione")
