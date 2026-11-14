# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Restituzione legata alla pratica (R12b: VD-C-160)

Revision ID: 20261106_053
Revises: 20261105_052
Create Date: 2026-11-06

1. VD-C-160 — il lavoratore di ciclo-vita riverifica dal WORM, prima di eseguire
   una RESTITUZIONE (riversamento in uscita verso l'ente a fine contratto), la
   catena delle autorizzazioni come per lo scarto e ne scrive il FATTO
   ``RESTITUZIONE`` / ``AUTORIZZAZIONI_VERIFICATE`` in ``esiti_esecuzione``
   (append-only, scrivibile SOLO dal ruolo ``verumdoc_ciclo_vita_lavoratore``,
   migrazione 043): il tipo e la coppia (tipo, esito) entrano nei vincoli,
   tolti e rimessi ``NOT VALID`` e convalidati in autocommit.
2. VD-C-160 — transizione (come VD-P-115 per lo scarto): gli allegati
   ``AUTORIZZAZIONE_PRODUTTORE`` GIÀ caricati, al momento della migrazione, in
   restituzioni non concluse entrano nell'elenco ``ciclo_vita_transizione_r12``
   (migrazione 051: lo scrive SOLO il proprietario, il lavoratore ha SELECT,
   l'API nessun diritto). Per loro l'assenza dell'identificativo della pratica nel
   contenuto firmato è un avviso; il riuso dello stesso documento resta un blocco.
   Un allegato caricato dopo la migrazione non vi entra mai.

Nessun diritto nuovo: la tabella dell'elenco e quella dei fatti mantengono i
privilegi delle migrazioni 043 e 051.

Riprendibile: vincoli tolti e rimessi ``NOT VALID`` (convalida in autocommit),
``INSERT … ON CONFLICT DO NOTHING``; ``lock_timeout`` per i DDL.
Downgrade: rifiutato se esistono fatti ``RESTITUZIONE`` (evidenze append-only);
altrimenti vincoli come in 048 e righe di transizione delle restituzioni tolte.
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import os

from alembic import context, op

revision = "20261106_053"
down_revision = "20261105_052"
branch_labels = None
depends_on = None

#: Stati conclusi della restituzione (app.domain.stati: macchina RESTITUZIONE)
FINALI_RESTITUZIONE = ("ESEGUITA", "RESPINTA")

TIPI_048 = ("SCARTO", "ANNULLAMENTO", "RETTIFICA", "RETENTION")
TIPI = (*TIPI_048, "RESTITUZIONE")
COPPIE_048 = ("(tipo = 'SCARTO' AND esito IN ('ELIMINATO', 'AUTORIZZAZIONI_VERIFICATE', "
              "'CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', 'PROPOSTA_GENERATA')) "
              "OR (tipo = 'ANNULLAMENTO' AND esito IN ('ATTO_ARCHIVIATO', "
              "'AUTORIZZAZIONI_VERIFICATE')) "
              "OR (tipo = 'RETTIFICA' AND esito = 'PDV_VERSATO') "
              "OR (tipo = 'RETENTION' AND esito IN ('RINNOVATA', 'RINNOVATA_ALLEGATO'))")
COPPIE = COPPIE_048 + " OR (tipo = 'RESTITUZIONE' AND esito = 'AUTORIZZAZIONI_VERIFICATE')"
_VINCOLI = ("ck_esiti_esecuzione_tipo", "ck_esiti_esecuzione_coppia")


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


_ELENCO = f"""
INSERT INTO public.ciclo_vita_transizione_r12 (allegato_id, pratica_id, tipo)
SELECT a.id, a.pratica_id, a.tipo
FROM public.pratiche_allegati a JOIN public.pratiche p ON p.id = a.pratica_id
WHERE p.tipo = 'RESTITUZIONE' AND p.stato NOT IN ({_elenco(FINALI_RESTITUZIONE)})
  AND a.tipo = 'AUTORIZZAZIONE_PRODUTTORE'
ON CONFLICT (allegato_id) DO NOTHING"""

_TOGLI_ELENCO = """
DELETE FROM public.ciclo_vita_transizione_r12 t
USING public.pratiche p
WHERE p.id = t.pratica_id AND p.tipo = 'RESTITUZIONE'"""

_RIFIUTA_DOWNGRADE = """
DO $d$
BEGIN
    IF EXISTS (SELECT 1 FROM public.esiti_esecuzione WHERE tipo = 'RESTITUZIONE') THEN
        RAISE EXCEPTION 'downgrade 20261106_053 impossibile: esistono fatti RESTITUZIONE '
            'in esiti_esecuzione (evidenze append-only del lavoratore)';
    END IF;
END
$d$;
"""


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _vincolo(nome: str, condizione: str) -> None:
    op.execute(f"ALTER TABLE public.esiti_esecuzione DROP CONSTRAINT IF EXISTS {nome}")
    op.execute(f"ALTER TABLE public.esiti_esecuzione ADD CONSTRAINT {nome} "
               f"CHECK ({condizione}) NOT VALID")


def _convalida() -> None:
    istruzioni = [f"ALTER TABLE public.esiti_esecuzione VALIDATE CONSTRAINT {v}"
                  for v in _VINCOLI]
    if context.is_offline_mode():
        for i in istruzioni:
            op.execute(i)
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def upgrade() -> None:
    _limita_attesa_lock()
    _vincolo("ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(TIPI)})")
    _vincolo("ck_esiti_esecuzione_coppia", COPPIE)
    op.execute(_ELENCO)
    _convalida()


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute(_RIFIUTA_DOWNGRADE)
    op.execute(_TOGLI_ELENCO)
    _vincolo("ck_esiti_esecuzione_tipo", f"tipo IN ({_elenco(TIPI_048)})")
    _vincolo("ck_esiti_esecuzione_coppia", COPPIE_048)
    _convalida()
