# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Debito interno (R14: VD-C-172, VD-C-121, VD-C-165)

Revision ID: 20261109_056
Revises: 20261108_055
Create Date: 2026-11-09

1. VD-C-172 — retention di ``notifiche_personali``: la funzione
   ``vd_pulisci_notifiche_personali(giorni, limite)`` (SECURITY DEFINER, proprietario
   della tabella, ``search_path`` fissato) cancella al più ``limite`` voci create più di
   ``giorni`` giorni fa, con un minimo NON superabile di 30 giorni (anche se il
   parametro arrivasse più basso) e un massimo di 10.000 righe per chiamata. Nessun
   ruolo riceve DELETE sulla tabella: il trigger ``notifiche_personali_protette``
   ammette la cancellazione SOLO dentro la funzione (variabile di transazione
   ``vd.pulizia_notifiche_personali``) e solo per righe più vecchie di 30 giorni; tutto
   il resto resta come in R13b (cambia solo ``letto_il``, una volta). EXECUTE al solo
   gruppo ``verumdoc_notifiche_pulizia`` (login del lavoratore della posta), tolto a
   PUBLIC. Le voci personali non sono documenti conservati: non stanno nel WORM e non
   hanno legal hold; la funzione non tocca altre tabelle (nessuna cascata).
2. ``lavori_periodici``: lease con fencing token (``generazione``) dei lavori periodici
   dei servizi che non hanno una tabella propria (la pulizia delle notifiche personali
   del lavoratore della posta), come ``ciclo_vita_periodico`` (R09, R12a). Senza ente:
   fuori dalla RLS. SELECT, INSERT, UPDATE al solo gruppo ``verumdoc_notifiche_pulizia``.
3. VD-C-121 — concorrenza della cronologia delle pratiche nel DATABASE, non solo con il
   lock applicativo: ``pratiche_stati.precedente_id`` è scritto dal trigger di continuità
   (l'id dell'ultima riga della pratica, 0 per la prima: il valore del chiamante si
   ignora) e l'indice unico parziale ``uq_pratiche_stati_precedente`` (pratica,
   precedente) fa fallire la seconda di due righe scritte insieme sulla stessa base (la
   seconda attende il commit della prima e riceve ``unique_violation``): nessun ramo
   doppio, nemmeno da un processo che salta il lock. Righe esistenti: ``precedente_id``
   nullo (fuori dall'indice); colonna senza predefinito (solo catalogo).
5. Revisione R14 (4): il permesso ``pratica:riprendi`` (ripresa di una pratica sospesa,
   solo il conservatore) entra fra i mai delegabili anche nel vincolo del database.
4. VD-C-165 — ``firme_arretrato_ipda``: cursore (una riga) del censimento degli IPdA dei
   PdA già CONSERVATI senza riga in ``firme_documenti`` (prima della migrazione 054),
   scritto dalla hashchain con il lease del registro. SELECT, INSERT, UPDATE al solo
   gruppo ``verumdoc_firme_documenti_sigillo``.

Riprendibile: ``IF NOT EXISTS``, indice ``CONCURRENTLY`` in autocommit (offline senza; un
indice rimasto INVALIDO da una corsa interrotta si elimina e si ricrea), ``lock_timeout``.
Downgrade: ripristina trigger, funzioni e vincolo dei ruoli di R13b, toglie colonna,
indice, funzione e tabelle (il cursore del censimento riparte da capo, idempotente).
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from alembic import context, op

revision = "20261109_056"
down_revision = "20261108_055"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
GRUPPO_PULIZIA = "verumdoc_notifiche_pulizia"
GRUPPO_SIGILLO = "verumdoc_firme_documenti_sigillo"
INDICE_CRONOLOGIA = "uq_pratiche_stati_precedente"
#: giorni sotto i quali una notifica personale non si cancella mai
GIORNI_MINIMI = 30
#: righe al più per chiamata della funzione di pulizia
LIMITE_MASSIMO = 10_000
TABELLE_NUOVE = ("lavori_periodici", "firme_arretrato_ipda")

_TABELLE = """
CREATE TABLE IF NOT EXISTS public.lavori_periodici (
    nome varchar(200) PRIMARY KEY,
    lease_detentore varchar(200),
    lease_scade_il timestamp with time zone,
    generazione bigint NOT NULL DEFAULT 0,
    ultimo_giro timestamp with time zone,
    ultimo_esito jsonb NOT NULL DEFAULT '{}'::jsonb,
    aggiornato_il timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_lavori_periodici_esito CHECK (jsonb_typeof(ultimo_esito) = 'object')
);
CREATE TABLE IF NOT EXISTS public.firme_arretrato_ipda (
    id smallint PRIMARY KEY DEFAULT 1,
    ultimo_aip uuid,
    inseriti bigint NOT NULL DEFAULT 0,
    completato_il timestamp with time zone,
    aggiornato_il timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_firme_arretrato_ipda_una_riga CHECK (id = 1)
)
"""


def _crea_gruppo(gruppo: str) -> str:
    return f"""
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppo}') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE {gruppo} NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo {gruppo} non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;"""


def _revoca(tabella: str) -> str:
    return (f"REVOKE ALL ON public.{tabella} FROM PUBLIC; IF EXISTS (SELECT 1 FROM pg_roles "
            f"WHERE rolname = '{RUOLO_APPLICAZIONE}') THEN REVOKE ALL ON public.{tabella} FROM "
            f"{RUOLO_APPLICAZIONE}; END IF;")


def _concedi(gruppo: str, diritti: str, oggetto: str) -> str:
    return (f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppo}') THEN "
            f"GRANT {diritti} ON {oggetto} TO {gruppo}; END IF;")


_PULIZIA = f"""
CREATE OR REPLACE FUNCTION public.vd_pulisci_notifiche_personali(p_giorni integer,
                                                                 p_limite integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    giorni integer := greatest(coalesce(p_giorni, {GIORNI_MINIMI}), {GIORNI_MINIMI});
    limite integer := least(greatest(coalesce(p_limite, 1), 1), {LIMITE_MASSIMO});
    cancellate integer;
BEGIN
    PERFORM set_config('vd.pulizia_notifiche_personali', 'on', true);
    DELETE FROM public.notifiche_personali
     WHERE id IN (SELECT id FROM public.notifiche_personali
                   WHERE creato_il < now() - make_interval(days => giorni)
                   ORDER BY id LIMIT limite);
    GET DIAGNOSTICS cancellate = ROW_COUNT;
    PERFORM set_config('vd.pulizia_notifiche_personali', 'off', true);
    RETURN cancellate;
END;
$$"""

_PERSONALI_PROTETTE = f"""
CREATE OR REPLACE FUNCTION public.notifiche_personali_protette()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- R14 (VD-C-172): solo dentro vd_pulisci_notifiche_personali e solo righe vecchie
        IF current_setting('vd.pulizia_notifiche_personali', true) = 'on'
           AND OLD.creato_il < now() - interval '{GIORNI_MINIMI} days' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'notifiche_personali: DELETE vietata (solo la pulizia oltre '
            '{GIORNI_MINIMI} giorni, vd_pulisci_notifiche_personali)';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.tipo IS DISTINCT FROM OLD.tipo
       OR NEW.titolo IS DISTINCT FROM OLD.titolo
       OR NEW.collegamento IS DISTINCT FROM OLD.collegamento
       OR NEW.creato_il IS DISTINCT FROM OLD.creato_il
       OR (OLD.letto_il IS NOT NULL AND NEW.letto_il IS DISTINCT FROM OLD.letto_il) THEN
        RAISE EXCEPTION 'notifiche_personali: cambia solo letto_il, una volta';
    END IF;
    RETURN NEW;
END;
$$"""

#: Trigger di continuità della cronologia (migrazione 044) con il precedente scritto dal
#: database (VD-C-121)
_CRONOLOGIA = """
CREATE OR REPLACE FUNCTION public.vd_pratiche_stati_continuita()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ente uuid;
    ultimo text;
    ultimo_id bigint;
    righe bigint;
    estranei bigint;
BEGIN
    SELECT p.tenant_id INTO ente FROM public.pratiche p WHERE p.id = NEW.pratica_id;
    IF ente IS NULL OR ente IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'pratiche_stati: ente diverso da quello della pratica'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT count(*) INTO righe FROM public.pratiche_stati s WHERE s.pratica_id = NEW.pratica_id;
    SELECT s.id, s.a_stato INTO ultimo_id, ultimo FROM public.pratiche_stati s
     WHERE s.pratica_id = NEW.pratica_id ORDER BY s.id DESC LIMIT 1;
    IF (righe = 0 AND NEW.da_stato IS NOT NULL)
       OR (righe > 0 AND NEW.da_stato IS DISTINCT FROM ultimo) THEN
        RAISE EXCEPTION 'pratiche_stati: cronologia discontinua (da % ma l''ultimo stato è %)',
            coalesce(NEW.da_stato, 'NULL'), coalesce(ultimo, 'nessuno')
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- R14 (VD-C-121): la base della riga la decide il database; l'indice unico
    -- uq_pratiche_stati_precedente rifiuta una seconda riga sulla stessa base
    NEW.precedente_id := coalesce(ultimo_id, 0);
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

_PRIVILEGI = "DO $p$\nBEGIN" + _crea_gruppo(GRUPPO_PULIZIA) + "\n    " + " ".join([
    *(_revoca(t) for t in TABELLE_NUOVE),
    "REVOKE ALL ON FUNCTION public.vd_pulisci_notifiche_personali(integer, integer) "
    "FROM PUBLIC;",
    _concedi(GRUPPO_PULIZIA, "EXECUTE",
             "FUNCTION public.vd_pulisci_notifiche_personali(integer, integer)"),
    _concedi(GRUPPO_PULIZIA, "SELECT, INSERT, UPDATE", "public.lavori_periodici"),
    _concedi(GRUPPO_SIGILLO, "SELECT, INSERT, UPDATE", "public.firme_arretrato_ipda"),
]) + "\nEND\n$p$;"


def _attesa_lock() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def _limita_attesa_lock() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_attesa_lock()}'")


def _indice_non_valido(nome: str) -> str:
    """DROP dell'indice se esiste ma NON è valido (CONCURRENTLY interrotto)."""
    return (f"DO $i$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid = "
            f"to_regclass('public.{nome}') AND NOT i.indisvalid) THEN "
            f"EXECUTE 'DROP INDEX public.{nome}'; END IF; END $i$")


def _in_autocommit(*istruzioni: str) -> None:
    if context.is_offline_mode():
        for i in istruzioni:
            op.execute(i.replace(" CONCURRENTLY", ""))
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def _modulo(nome: str):  # noqa: ANN202
    percorso = Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(percorso.stem, percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _vincolo_ruoli(elenco: str) -> None:
    op.execute("ALTER TABLE public.ruoli_personalizzati DROP CONSTRAINT IF EXISTS "
               "ck_ruoli_personalizzati_non_delegabili")
    op.execute("ALTER TABLE public.ruoli_personalizzati ADD CONSTRAINT "
               f"ck_ruoli_personalizzati_non_delegabili CHECK (NOT (permessi && {elenco})) "
               "NOT VALID")


def upgrade() -> None:
    _limita_attesa_lock()
    for istruzione in (s.strip() for s in _TABELLE.split(";")):
        if istruzione:
            op.execute(istruzione)
    op.execute(_PULIZIA)
    op.execute(_PERSONALI_PROTETTE)
    op.execute(_PRIVILEGI)
    # VD-C-121: colonna senza predefinito (solo catalogo), poi funzione del trigger
    op.execute("ALTER TABLE public.pratiche_stati ADD COLUMN IF NOT EXISTS precedente_id bigint")
    op.execute(_CRONOLOGIA)
    m055 = _modulo("20261108_055_canali_esterni.py")
    _vincolo_ruoli(m055.MAI_DELEGABILI.replace("'pec_canali:gestisci'",
                                               "'pec_canali:gestisci', 'pratica:riprendi'"))
    # revisione R14 (3): un CREATE INDEX CONCURRENTLY interrotto lascia l'indice INVALIDO,
    # che IF NOT EXISTS terrebbe: la ripresa lo elimina prima di ricrearlo (come 036–052)
    _in_autocommit(
        f"SET lock_timeout = '{_attesa_lock()}'",
        _indice_non_valido(INDICE_CRONOLOGIA),
        f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDICE_CRONOLOGIA} "
        "ON public.pratiche_stati (pratica_id, precedente_id) WHERE precedente_id IS NOT NULL",
        "RESET lock_timeout",
        "ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
        "ck_ruoli_personalizzati_non_delegabili")


def downgrade() -> None:
    _limita_attesa_lock()
    _vincolo_ruoli(_modulo("20261108_055_canali_esterni.py").MAI_DELEGABILI)
    op.execute("ALTER TABLE public.ruoli_personalizzati VALIDATE CONSTRAINT "
               "ck_ruoli_personalizzati_non_delegabili")
    op.execute(_modulo("20261024_044_autorizzazioni_scarto.py")._CRONOLOGIA)
    op.execute(f"DROP INDEX IF EXISTS public.{INDICE_CRONOLOGIA}")
    op.execute("ALTER TABLE public.pratiche_stati DROP COLUMN IF EXISTS precedente_id")
    op.execute(_modulo("20261108_055_canali_esterni.py")._PERSONALI_PROTETTE)
    op.execute("DROP FUNCTION IF EXISTS public.vd_pulisci_notifiche_personali(integer, integer)")
    for tabella in reversed(TABELLE_NUOVE):
        op.execute(f"DROP TABLE IF EXISTS public.{tabella}")
