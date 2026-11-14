# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Unione dei rami R05 e RLS sulle loro tabelle nuove (R05, VD-C-080)

Rami: 036 regole, 037 ciclo di vita, 038 RLS, 039 catena.

Revision ID: 20261023_040
Revises: 20261023_036, 20261023_037, 20261023_038, 20261023_039
Create Date: 2026-10-23

I quattro rami partono tutti dalla 035 e toccano tabelle diverse: l'ordine fra
loro è indifferente (provato da ``test_r05_unione_migrazioni``). Questa
revisione li unisce e completa la Row Level Security della 038 sulle tabelle
create dagli altri rami, con la STESSA politica ``vd_ente_isolamento``
(USING e WITH CHECK):

    tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid
    OR current_setting('app.ambito', true) = 'conservatore'

- ciclo di vita (037): ``pratiche``, ``pratiche_stati``, ``pratiche_allegati``,
  ``pratiche_oggetti``, ``pratiche_esecuzioni`` (tutte ``tenant_id NOT NULL``;
  le API usano l'ambito dell'identità verificata, il lavoratore quello del
  conservatore con motivo);
- configurazione (036): ``accordi_versamento``, ``configurazione_proposte``
  (``tenant_id NOT NULL``);
- configurazione (036) con righe SENZA ente (livello CONSERVATORE):
  ``configurazione_bozze``, ``impostazioni_storico``. Decisione: nessuna
  politica di lettura in più, quindi le righe senza ente le vede e le scrive
  SOLO l'ambito conservatore (``NULL = x`` non è mai vero). È coerente con
  l'applicazione: le bozze del livello CONSERVATORE le gestiscono solo
  SUPER_ADMIN/RSC (``_visibile`` di onboarding) e lo storico si scrive soltanto,
  nell'ambito di chi applica. Un ente non vede le bozze né lo storico del
  conservatore: le proposte che lo riguardano gli arrivano come righe di
  ``configurazione_proposte`` con il SUO ente.

Restano fuori, come nella 038: ``profili_tipologia`` (configurazione condivisa,
senza ente), ``catena_foglie_interne`` e ``audit_esportazioni`` (registro delle
impronte, globale per costruzione).

Vista ``pratiche_canale_versamento`` (037): ricreata ``WITH (security_invoker =
true)``. Senza, PostgreSQL la valuta con i diritti del PROPRIETARIO (le
migrazioni, fuori dalla RLS) e la vista AGGIREREBBE la politica di
``pratiche``: il gateway, nell'ambito dell'ente del token di canale, vedrebbe
le pratiche di tutti gli enti.

Downgrade: politiche tolte, RLS disattivata sulle tabelle di questa revisione,
vista ricreata come nella 037. Le tabelle restano (appartengono ai rami).
"""
# ruff: noqa: S608 — SQL composto solo con i nomi costanti di TABELLE_ENTE_R05
from __future__ import annotations

import os

from alembic import op

revision = "20261023_040"
down_revision = ("20261023_036", "20261023_037", "20261023_038", "20261023_039")
branch_labels = None
depends_on = None

#: Tabelle dei rami R05 con la politica per ente (tutte con ``tenant_id``).
TABELLE_ENTE_R05: tuple[str, ...] = (
    "pratiche", "pratiche_stati", "pratiche_allegati", "pratiche_oggetti",
    "pratiche_esecuzioni",
    "accordi_versamento", "configurazione_proposte",
    "configurazione_bozze", "impostazioni_storico",
)

POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")

_VISTA_SELECT = (
    "SELECT DISTINCT tenant_id, creata_da AS responsabile_id, 'ATTIVO'::text AS stato "
    "FROM public.pratiche WHERE creata_da IS NOT NULL "
    "AND tipo IN ('RETTIFICA', 'RIVERSAMENTO_INGRESSO') "
    "AND stato NOT IN ('COMPLETATA', 'RESPINTA', 'ESEGUITA')")



def _limita_attesa_lock() -> None:
    """Revisione R05 (R6): ``lock_timeout`` per i DDL di questa revisione (SET
    LOCAL): un lock non ottenuto in tempo fa fallire la migrazione, che si
    riprende, invece di mettere in coda il servizio."""
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def upgrade() -> None:
    _limita_attesa_lock()
    for tabella in TABELLE_ENTE_R05:
        op.execute(f"ALTER TABLE public.{tabella} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA} ON public.{tabella} AS PERMISSIVE FOR ALL "
                   f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    # La vista eredita la RLS di ``pratiche`` solo con security_invoker (PG ≥ 15).
    op.execute("CREATE OR REPLACE VIEW public.pratiche_canale_versamento "
               "WITH (security_invoker = true) AS " + _VISTA_SELECT)


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("ALTER VIEW public.pratiche_canale_versamento RESET (security_invoker)")
    for tabella in reversed(TABELLE_ENTE_R05):
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"ALTER TABLE public.{tabella} DISABLE ROW LEVEL SECURITY")
