# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Catena delle autorizzazioni dello scarto verificabile dal lavoratore (revisione 4 di R05, U2)

Revision ID: 20261024_044
Revises: 20261023_043
Create Date: 2026-10-24

Revisione 4 di R05 (U2). Il pod API di ciclo-vita (esposto all'ingress) scrive
``pratiche`` e ``pratiche_oggetti`` con il suo login: compromesso, poteva
fabbricare una pratica di SCARTO già ``ESEGUIBILE`` (anche di un ente PA, senza
nulla osta) che il lavoratore legittimo eseguiva davvero. Ora il lavoratore,
prima di cancellare, verifica da sé la catena delle autorizzazioni
(``services/ciclo-vita/app/application/autorizzazioni.py``); questa migrazione
rende quella catena verificabile e non riscrivibile:

1. ``pratiche_oggetti.esito_piano`` / ``motivo_piano``: esito e motivo del
   PIANO approvato, copiati dal trigger all'INSERT (chi scrive non li sceglie) e
   poi immutabili, come ``pratica_id`` e ``aip_id``. L'``esito`` resta lo stato
   di esecuzione (mutabile); il lavoratore ricalcola l'impronta del piano da
   ``(aip_id, esito_piano, motivo_piano)`` e cancella solo oggetti con
   ``esito_piano = SELEZIONATO``;
2. ``pratiche``: ``impronta_piano`` immutabile una volta scritta;
   ``richiede_nulla_osta`` non torna mai da vero a falso;
3. ``pratiche_stati`` (già append-only, 037): ogni riga nuova deve CONTINUARE la
   cronologia (``da_stato`` = ``a_stato`` dell'ultima riga, NULL solo per la
   prima), stesso ente della pratica, allegati citati solo della stessa pratica;
4. ``esiti_esecuzione`` (043, scritta SOLO dal ruolo del lavoratore): esito
   ``AUTORIZZAZIONI_VERIFICATE`` (senza PdA) per registrare che il lavoratore ha
   verificato la catena prima di un giro di esecuzione (vincolo
   ``ck_esiti_esecuzione_aip``: ``ELIMINATO`` ha sempre il PdA).

I trigger valgono per ogni login (anche il proprietario): sono difese del dato,
non permessi. Oggetti di pratiche precedenti alla migrazione hanno
``esito_piano`` NULL: il lavoratore non li verifica e blocca la pratica (le
pratiche di scarto nascono con R05, non ancora in esercizio).

Riprendibile (S1): segmento transazionale idempotente (colonne IF NOT EXISTS,
CREATE OR REPLACE, DROP ... IF EXISTS), vincoli ``NOT VALID`` convalidati in
autocommit (come la 038); ``lock_timeout`` per i DDL esclusivi.

Downgrade: trigger, funzioni e colonne tolti; ``esiti_esecuzione`` torna al
vincolo della 043 se non ci sono righe ``AUTORIZZAZIONI_VERIFICATE``
(altrimenti errore esplicito: sono evidenze append-only).
"""
from __future__ import annotations

import os

from alembic import context, op

revision = "20261024_044"
down_revision = "20261023_043"
branch_labels = None
depends_on = None

ESITI_043 = ("ELIMINATO",)
ESITI = ("ELIMINATO", "AUTORIZZAZIONI_VERIFICATE")

_PIANO = """
CREATE OR REPLACE FUNCTION public.vd_pratiche_oggetti_piano()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- il piano è quello dell'inserimento: chi scrive non lo sceglie
        NEW.esito_piano := NEW.esito;
        NEW.motivo_piano := NEW.motivo;
        RETURN NEW;
    END IF;
    IF NEW.esito_piano IS DISTINCT FROM OLD.esito_piano
       OR NEW.motivo_piano IS DISTINCT FROM OLD.motivo_piano
       OR NEW.pratica_id IS DISTINCT FROM OLD.pratica_id
       OR NEW.aip_id IS DISTINCT FROM OLD.aip_id THEN
        RAISE EXCEPTION 'pratiche_oggetti: piano congelato (pratica, PdA, esito e motivo del '
            'piano non si modificano)' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

_PRATICHE = """
CREATE OR REPLACE FUNCTION public.vd_pratiche_autorizzazioni()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.impronta_piano IS NOT NULL
       AND NEW.impronta_piano IS DISTINCT FROM OLD.impronta_piano THEN
        RAISE EXCEPTION 'pratiche: impronta del piano approvato non modificabile'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.richiede_nulla_osta AND NOT NEW.richiede_nulla_osta THEN
        RAISE EXCEPTION 'pratiche: il nulla osta richiesto non si toglie'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

_CRONOLOGIA = """
CREATE OR REPLACE FUNCTION public.vd_pratiche_stati_continuita()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ente uuid;
    ultimo text;
    righe bigint;
    estranei bigint;
BEGIN
    SELECT p.tenant_id INTO ente FROM public.pratiche p WHERE p.id = NEW.pratica_id;
    IF ente IS NULL OR ente IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'pratiche_stati: ente diverso da quello della pratica'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT count(*) INTO righe FROM public.pratiche_stati s WHERE s.pratica_id = NEW.pratica_id;
    SELECT s.a_stato INTO ultimo FROM public.pratiche_stati s
     WHERE s.pratica_id = NEW.pratica_id ORDER BY s.id DESC LIMIT 1;
    IF (righe = 0 AND NEW.da_stato IS NOT NULL)
       OR (righe > 0 AND NEW.da_stato IS DISTINCT FROM ultimo) THEN
        RAISE EXCEPTION 'pratiche_stati: cronologia discontinua (da % ma l''ultimo stato è %)',
            coalesce(NEW.da_stato, 'NULL'), coalesce(ultimo, 'nessuno')
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF jsonb_typeof(NEW.allegati) <> 'array' THEN
        RAISE EXCEPTION 'pratiche_stati: allegati deve essere un elenco'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT count(*) INTO estranei FROM jsonb_array_elements_text(NEW.allegati) x(id)
     WHERE NOT EXISTS (SELECT 1 FROM public.pratiche_allegati a
                        WHERE a.pratica_id = NEW.pratica_id AND a.id::text = x.id);
    IF estranei > 0 THEN
        RAISE EXCEPTION 'pratiche_stati: allegati citati che non sono della pratica'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
"""


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def _vincoli_esiti(esiti: tuple[str, ...], *, con_aip: bool) -> None:
    op.execute("ALTER TABLE public.esiti_esecuzione DROP CONSTRAINT IF EXISTS "
               "ck_esiti_esecuzione_esito")
    op.execute("ALTER TABLE public.esiti_esecuzione ADD CONSTRAINT ck_esiti_esecuzione_esito "
               f"CHECK (esito IN ({_elenco(esiti)})) NOT VALID")
    op.execute("ALTER TABLE public.esiti_esecuzione DROP CONSTRAINT IF EXISTS "
               "ck_esiti_esecuzione_aip")
    if con_aip:
        op.execute("ALTER TABLE public.esiti_esecuzione ADD CONSTRAINT ck_esiti_esecuzione_aip "
                   "CHECK (esito <> 'ELIMINATO' OR aip_id IS NOT NULL) NOT VALID")


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
    # 1. piano congelato degli oggetti
    op.execute("ALTER TABLE public.pratiche_oggetti ADD COLUMN IF NOT EXISTS esito_piano "
               "varchar(20)")
    op.execute("ALTER TABLE public.pratiche_oggetti ADD COLUMN IF NOT EXISTS motivo_piano "
               "varchar(60)")
    op.execute(_PIANO)
    op.execute("DROP TRIGGER IF EXISTS trg_pratiche_oggetti_piano ON public.pratiche_oggetti")
    op.execute("CREATE TRIGGER trg_pratiche_oggetti_piano BEFORE INSERT OR UPDATE "
               "ON public.pratiche_oggetti FOR EACH ROW "
               "EXECUTE FUNCTION public.vd_pratiche_oggetti_piano()")
    # 2. impronta del piano e nulla osta
    op.execute(_PRATICHE)
    op.execute("DROP TRIGGER IF EXISTS trg_pratiche_autorizzazioni ON public.pratiche")
    op.execute("CREATE TRIGGER trg_pratiche_autorizzazioni BEFORE UPDATE ON public.pratiche "
               "FOR EACH ROW EXECUTE FUNCTION public.vd_pratiche_autorizzazioni()")
    # 3. cronologia continua
    op.execute(_CRONOLOGIA)
    op.execute("DROP TRIGGER IF EXISTS trg_pratiche_stati_continuita ON public.pratiche_stati")
    op.execute("CREATE TRIGGER trg_pratiche_stati_continuita BEFORE INSERT "
               "ON public.pratiche_stati FOR EACH ROW "
               "EXECUTE FUNCTION public.vd_pratiche_stati_continuita()")
    # 4. esito «autorizzazioni verificate» del lavoratore
    op.execute("ALTER TABLE public.esiti_esecuzione ALTER COLUMN aip_id DROP NOT NULL")
    _vincoli_esiti(ESITI, con_aip=True)
    _convalida("ck_esiti_esecuzione_esito", "ck_esiti_esecuzione_aip")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("""
        DO $d$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.esiti_esecuzione
                       WHERE esito = 'AUTORIZZAZIONI_VERIFICATE' OR aip_id IS NULL) THEN
                RAISE EXCEPTION 'downgrade 20261024_044 impossibile: esiti_esecuzione ha righe '
                    'AUTORIZZAZIONI_VERIFICATE (evidenze append-only del lavoratore)';
            END IF;
        END
        $d$;
    """)
    _vincoli_esiti(ESITI_043, con_aip=False)
    op.execute("ALTER TABLE public.esiti_esecuzione ALTER COLUMN aip_id SET NOT NULL")
    op.execute("DROP TRIGGER IF EXISTS trg_pratiche_stati_continuita ON public.pratiche_stati")
    op.execute("DROP FUNCTION IF EXISTS public.vd_pratiche_stati_continuita()")
    op.execute("DROP TRIGGER IF EXISTS trg_pratiche_autorizzazioni ON public.pratiche")
    op.execute("DROP FUNCTION IF EXISTS public.vd_pratiche_autorizzazioni()")
    op.execute("DROP TRIGGER IF EXISTS trg_pratiche_oggetti_piano ON public.pratiche_oggetti")
    op.execute("DROP FUNCTION IF EXISTS public.vd_pratiche_oggetti_piano()")
    op.execute("ALTER TABLE public.pratiche_oggetti DROP COLUMN IF EXISTS motivo_piano")
    op.execute("ALTER TABLE public.pratiche_oggetti DROP COLUMN IF EXISTS esito_piano")
    _convalida("ck_esiti_esecuzione_esito")
