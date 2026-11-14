# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/db/migrazioni_registri.py — migrazioni che toccano i registri
append-only protetti dagli event trigger (R04 terzo giro, punto 4).

Gli event trigger delle migrazioni 20261023_031, 033 e 034 rifiutano, anche
all'amministratore, ciò che una migrazione legittima a volte deve fare: cambiare
una funzione di protezione, aggiungere una politica, un trigger, un vincolo o un
indice unico a ``audit_global`` o ``verifiche_registro``. La migrazione li
SOSPENDE, fa il lavoro e li RIATTIVA nello stesso file con::

    from libs.shared.db.migrazioni_registri import registri_modificabili

    def upgrade() -> None:
        with registri_modificabili(op, motivo="indice per la ricerca per ente"):
            op.execute("ALTER TABLE public.audit_global ADD CONSTRAINT ...")

All'uscita, SEMPRE (``finally``): event trigger riattivati ``ENABLE ALWAYS``,
impronte della struttura dei registri aggiornate (``protezione.aggiorna_impronte``,
034) e controllo che siano tutti attivi. Se il blocco fallisce in una migrazione
transazionale la transazione è abortita: la riattivazione non si può eseguire, ma
non serve, perché ``ALTER EVENT TRIGGER`` è transazionale e il ROLLBACK la
annulla; l'errore originale non viene mai coperto. Serve un superutente (su RDS
``rds_superuser``): è il ruolo del Job delle migrazioni. Procedura e esempio
completo: ``docs/runbooks/migrazioni_audit.md``.

Tutto SQL in blocchi DO: funziona anche come script offline (``alembic --sql``).
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

#: Event trigger dei registri, nell'ordine in cui si sospendono
EVENT_TRIGGER_REGISTRI = (
    "vd_protezione_registri_oggetti",
    "vd_protezione_registri_stato",
    "vd_protezione_registri_alter",
    "vd_protezione_registri_drop",
)

_SOSPENDI = """
DO $sospendi$
DECLARE
    nome text;
BEGIN
    RAISE NOTICE 'event trigger dei registri sospesi: %', @MOTIVO@;
    FOREACH nome IN ARRAY ARRAY[@NOMI@] LOOP
        IF EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = nome) THEN
            EXECUTE format('ALTER EVENT TRIGGER %I DISABLE', nome);
        END IF;
    END LOOP;
END
$sospendi$;
"""

_RIATTIVA = """
DO $riattiva$
DECLARE
    nome text;
BEGIN
    FOREACH nome IN ARRAY ARRAY[@NOMI@] LOOP
        IF EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = nome) THEN
            EXECUTE format('ALTER EVENT TRIGGER %I ENABLE ALWAYS', nome);
        END IF;
    END LOOP;
END
$riattiva$;
"""

_IMPRONTE = """
DO $impronte$
BEGIN
    IF to_regprocedure('protezione.aggiorna_impronte()') IS NOT NULL THEN
        PERFORM protezione.aggiorna_impronte();
    END IF;
END
$impronte$;
"""

_CONTROLLA = """
DO $controlla$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_event_trigger
               WHERE evtname = ANY(ARRAY[@NOMI@]) AND evtenabled <> 'A') THEN
        RAISE EXCEPTION 'event trigger dei registri non riattivati (ENABLE ALWAYS)';
    END IF;
END
$controlla$;
"""


def _sql(modello: str, motivo: str = "") -> str:
    nomi = ", ".join(f"'{n}'" for n in EVENT_TRIGGER_REGISTRI)
    letterale = "'" + motivo.replace("'", "''") + "'"
    return modello.replace("@NOMI@", nomi).replace("@MOTIVO@", letterale)


@contextlib.contextmanager
def registri_modificabili(op: Any, *, motivo: str) -> Iterator[None]:
    """Sospende gli event trigger dei registri per il blocco e li riattiva in
    ``finally`` (vedi modulo). ``op``: ``alembic.op`` o un ``Operations``."""
    if not motivo.strip():
        raise ValueError("motivo obbligatorio: resta nel log della migrazione")
    op.execute(_sql(_SOSPENDI, motivo))
    in_errore = False
    try:
        yield
    except BaseException:
        in_errore = True
        raise
    finally:
        try:
            op.execute(_sql(_RIATTIVA))
            if not in_errore:
                op.execute(_IMPRONTE)
            op.execute(_sql(_CONTROLLA))
        except Exception:
            # Transazione abortita dall'errore del blocco: il ROLLBACK annulla
            # anche la sospensione. Mai coprire l'errore originale.
            if not in_errore:
                raise
