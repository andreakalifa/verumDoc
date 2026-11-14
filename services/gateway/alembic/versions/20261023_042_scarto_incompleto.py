# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Scarto con cancellazione incompleta (revisione 2 di R05, rilievo S3)

Revision ID: 20261023_042
Revises: 20261023_041
Create Date: 2026-10-23

Revisione 2 di R05 (S3): uno scarto avviato che incontra una versione non
cancellabile (legal hold arrivato durante lo scarto, retention, errore
persistente dello storage) non resta più in esecuzione per sempre:

1. ``aip_stato``: valore ``ELIMINAZIONE_INCOMPLETA`` (PdA con una cancellazione
   iniziata e non completata; allarme ``ScartoEliminazioneIncompleta``; si
   riprende con il runbook ``docs/runbooks/scarto_incompleto.md``). Lo stato
   della pratica ``ESEGUITA_CON_ECCEZIONI`` è un valore di ``pratiche.stato``
   (testo, nessun vincolo: macchina a stati di ciclo-vita).
2. ``pratiche_oggetti.esito``: valore ``NON_COMPLETATO`` (vincolo
   ``ck_pratiche_oggetti_esito`` ricreato ``NOT VALID`` e convalidato in
   autocommit, come la 038).

Riprendibile dopo un errore in qualunque punto (S1): ``ADD VALUE IF NOT
EXISTS``, vincolo tolto e rimesso nella stessa transazione, ``VALIDATE``
idempotente; ``lock_timeout`` per i DDL esclusivi.

Downgrade: il valore dell'enum resta (PostgreSQL non toglie valori, REGOLE
§16.5); il vincolo torna quello della 037 se nessun oggetto è NON_COMPLETATO
(altrimenti errore esplicito) e nessun PdA è ELIMINAZIONE_INCOMPLETA.
"""
from __future__ import annotations

import os

from alembic import context, op

revision = "20261023_042"
down_revision = "20261023_041"
branch_labels = None
depends_on = None

ESITI_037 = ("SELEZIONATO", "BLOCCATO", "IN_CORSO", "ELIMINATO", "ESPORTATO", "VERSATO",
             "ERRORE")
ESITI = (*ESITI_037, "NON_COMPLETATO")


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _vincolo(esiti: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE public.pratiche_oggetti DROP CONSTRAINT IF EXISTS "
               "ck_pratiche_oggetti_esito")
    op.execute("ALTER TABLE public.pratiche_oggetti ADD CONSTRAINT ck_pratiche_oggetti_esito "
               f"CHECK (esito IN ({_elenco(esiti)})) NOT VALID")


def _convalida() -> None:
    istruzione = ("ALTER TABLE public.pratiche_oggetti VALIDATE CONSTRAINT "
                  "ck_pratiche_oggetti_esito")
    if context.is_offline_mode():
        op.execute(istruzione)
        return
    with op.get_context().autocommit_block():
        op.execute(istruzione)


def upgrade() -> None:
    _limita_attesa_lock()
    op.execute("ALTER TYPE public.aip_stato ADD VALUE IF NOT EXISTS 'ELIMINAZIONE_INCOMPLETA'")
    _vincolo(ESITI)
    _convalida()


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.pratiche_oggetti WHERE esito = 'NON_COMPLETATO')
               OR EXISTS (SELECT 1 FROM public.aip
                          WHERE stato::text = 'ELIMINAZIONE_INCOMPLETA') THEN
                RAISE EXCEPTION 'downgrade 20261023_042 impossibile: ci sono PdA con '
                    'cancellazione incompleta (riprenderli prima: runbook scarto_incompleto)';
            END IF;
        END
        $d$;
    """)
    _vincolo(ESITI_037)
    _convalida()
