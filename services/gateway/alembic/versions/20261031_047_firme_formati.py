# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Firme e formati verificati (R08: decisioni D01, D02, D22)

Revision ID: 20261031_047
Revises: 20261030_046
Create Date: 2026-10-31

1. D01. ``aip.rapporto_firme_key`` e ``aip.rapporto_firme_sha256``: chiave WORM
   e impronta SHA-256 del rapporto di verifica delle firme conservato nel PdA
   (``rapporto_verifica_firme.json``), citato nel rapporto di versamento.
   Colonne nullable: i PdA costruiti prima di R08, o con livello NESSUNA, non
   hanno rapporto. Vincolo: l'impronta è esadecimale di 64 caratteri e c'è
   insieme alla chiave.
2. D22. ``pratiche_allegati.tipo`` ammette ``RICHIESTA_ANNULLAMENTO``: il
   documento firmato che accompagna (facoltativo o obbligatorio per ente) una
   richiesta di annullamento, verificato dall'API e di nuovo dal lavoratore.

Il catalogo dei formati e i parametri delle firme NON hanno tabelle proprie:
sono impostazioni del motore (``impostazioni_valori``, generate dal registro).

Riprendibile: ``ADD COLUMN IF NOT EXISTS``, vincoli tolti e rimessi ``NOT VALID``
e convalidati in autocommit, ``lock_timeout`` per i DDL esclusivi (come la 046).

Downgrade: rifiutato se esistono allegati ``RICHIESTA_ANNULLAMENTO`` (il vincolo
precedente non li ammetterebbe) o PdA con rapporto delle firme (si perderebbe la
citazione del rapporto conservato).
"""
from __future__ import annotations

import os

from alembic import context, op

revision = "20261031_047"
down_revision = "20261030_046"
branch_labels = None
depends_on = None

TIPI_046 = ("AUTORIZZAZIONE_PRODUTTORE", "NULLA_OSTA", "VERBALE_SCARTO", "ATTO", "MANIFESTO",
            "PDD_RICEVUTO", "EXPORT_REGISTRI", "ALTRO")
TIPI = (*TIPI_046, "RICHIESTA_ANNULLAMENTO")


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
        for i in istruzioni:
            op.execute(i)
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def upgrade() -> None:
    _limita_attesa_lock()
    op.execute("ALTER TABLE public.aip ADD COLUMN IF NOT EXISTS rapporto_firme_key "
               "varchar(500)")
    op.execute("ALTER TABLE public.aip ADD COLUMN IF NOT EXISTS rapporto_firme_sha256 "
               "varchar(64)")
    _vincolo("aip", "ck_aip_rapporto_firme",
             "(rapporto_firme_key IS NULL) = (rapporto_firme_sha256 IS NULL) AND "
             "(rapporto_firme_sha256 IS NULL OR rapporto_firme_sha256 ~ '^[0-9a-f]{64}$')")
    _vincolo("pratiche_allegati", "ck_pratiche_allegati_tipo", f"tipo IN ({_elenco(TIPI)})")
    _in_autocommit(
        "ALTER TABLE public.aip VALIDATE CONSTRAINT ck_aip_rapporto_firme",
        "ALTER TABLE public.pratiche_allegati VALIDATE CONSTRAINT ck_pratiche_allegati_tipo",
    )


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.pratiche_allegati
                       WHERE tipo = 'RICHIESTA_ANNULLAMENTO') THEN
                RAISE EXCEPTION 'downgrade 20261031_047 impossibile: esistono richieste di '
                    'annullamento firmate';
            END IF;
            IF EXISTS (SELECT 1 FROM public.aip WHERE rapporto_firme_key IS NOT NULL) THEN
                RAISE EXCEPTION 'downgrade 20261031_047 impossibile: esistono PdA con il '
                    'rapporto di verifica delle firme';
            END IF;
        END
        $d$;
    """)
    _vincolo("pratiche_allegati", "ck_pratiche_allegati_tipo", f"tipo IN ({_elenco(TIPI_046)})")
    _vincolo("aip", "ck_aip_rapporto_firme", None)
    op.execute("ALTER TABLE public.aip DROP COLUMN IF EXISTS rapporto_firme_sha256")
    op.execute("ALTER TABLE public.aip DROP COLUMN IF EXISTS rapporto_firme_key")
    _in_autocommit("ALTER TABLE public.pratiche_allegati VALIDATE CONSTRAINT "
                   "ck_pratiche_allegati_tipo")
