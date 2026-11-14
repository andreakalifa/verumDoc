# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Row Level Security per ente sulle tabelle dei dati (R05, VD-C-080, VD-P-02)

Revision ID: 20261023_038
Revises: 20261023_035
Create Date: 2026-10-23

Seconda difesa dell'isolamento fra enti, dietro i filtri applicativi: con il
ruolo dei servizi (login membro di ``verumdoc_applicazione``, NON proprietario
delle tabelle) una query che dimentica ``tenant_id`` non vede comunque le righe
di un altro ente. Documento: docs/architettura/sicurezza.md.

1. ``tenant_id`` proprio su ``documenti``, ``metadati_allegato5``,
   ``lifecycle_events``, ``fascicoli_aip`` (scelta: colonna, non politica per
   join; motivo nel documento: una politica con ``EXISTS`` sul padre costa una
   sotto-interrogazione per riga sulla tabella più grande, ``documenti``, e non
   si indicizza). Procedura senza blocchi lunghi:
   a. colonna nulla (solo catalogo) e chiave esterna ``NOT VALID``;
   b. trigger ``BEFORE INSERT OR UPDATE`` che RICAVA l'ente dal padre (dato
      derivabile, REGOLE §8.15): chi scrive non deve indicarlo; se lo indica
      diverso dal padre l'istruzione fallisce. Sul padre vale la RLS di chi
      scrive: non si aggancia una riga a un padre di un altro ente. Per
      ``fascicoli_aip`` l'ente viene dal fascicolo e deve coincidere con
      quello del PdA (stesso cliente, prima solo controllato dall'API);
   c. riempimento A LOTTI per intervalli di blocchi (``ctid``), una transazione
      per lotto (``VD_MIGRAZIONE_038_LOTTO_BLOCCHI``, predefinito 2000 blocchi);
   d. per tabella (revisione R05, R6): ``CHECK (tenant_id IS NOT NULL) NOT
      VALID`` in una transazione breve; ``VALIDATE`` del CHECK e della chiave
      esterna IN AUTOCOMMIT, ciascuno nella sua transazione (SHARE UPDATE
      EXCLUSIVE: letture e scritture continuano durante la scansione); poi, in
      una transazione breve, ``SET NOT NULL`` (usa il CHECK già convalidato:
      nessuna scansione sotto ACCESS EXCLUSIVE) e vincolo tolto. Ogni
      transazione con lock esclusivo ha ``lock_timeout``
      (``VD_MIGRAZIONE_LOCK_TIMEOUT``, predefinito 5 s): con un lettore lungo la
      migrazione fallisce e si riprende (passi idempotenti) invece di fermare
      il servizio;
   e. indice ``(tenant_id)`` con ``CREATE INDEX CONCURRENTLY``.
2. RLS attiva (non FORCE: il proprietario, cioè le migrazioni, resta fuori;
   i servizi non possono esserlo, guardia VD-C-113) sulle tabelle dei dati in
   ``TABELLE_ENTE`` con la politica ``vd_ente_isolamento``:

       tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid
       OR current_setting('app.ambito', true) = 'conservatore'

   per lettura e scrittura (USING e WITH CHECK). ``eventi_cliente`` ha in più
   ``vd_ente_globali_lettura`` (SELECT delle righe senza ente: eventi globali
   come ``catena.checkpoint``). Le impostazioni le scrive SOLO
   ``libs.shared.db.ambito`` con ``set_config(..., true)`` (= SET LOCAL) a ogni
   transazione: corretto con PgBouncer in modalità transaction. Senza ambito:
   nessuna riga.
Escluse, con motivo nel documento: identità e contratti (letti per
autenticare, prima che l'ente sia noto), configurazione (righe di base
condivise; area r05-regole), registri protetti (``audit_global``,
``verifiche_registro``: hanno già RLS e difese proprie), registro delle impronte
``catena_*`` (globale per costruzione: le prove usano l'albero intero), code
tecniche (outbox).

Offline (``--sql``): riempimento in un'istruzione e indici normali (finestra di
manutenzione con le scritture ferme).
"""
# ruff: noqa: S608 — SQL composto solo con i nomi costanti di TABELLE_ENTE e DERIVATE
from __future__ import annotations

import os

from alembic import context, op
from sqlalchemy import text

revision = "20261023_038"
down_revision = "20261023_035"
branch_labels = None
depends_on = None

#: Tabelle dei dati con la politica per ente (tutte con ``tenant_id``).
TABELLE_ENTE: tuple[str, ...] = (
    "pdv", "aip", "documenti", "metadati_allegato5", "lifecycle_events",
    "fascicoli", "fascicoli_aip", "versamento_impronte",
    "caricamenti", "caricamenti_parti", "compat_packer_richieste", "richieste_idempotenti",
    "notifications", "webhook_sottoscrizioni", "webhook_consegne", "eventi_cliente",
    "sftp_account", "sftp_chiavi", "sftp_lavorazioni", "sftp_segnalazioni",
    "indicizzazione_aip", "pacchetti_distribuzione", "scarti",
    "verifiche_richieste", "verifiche_programmazione", "verifiche_copertura",
    "contatori_ente", "contatori_ente_delta",
)
#: Tabelle con righe senza ente leggibili da tutti (eventi globali).
TABELLE_GLOBALI_LEGGIBILI: tuple[str, ...] = ("eventi_cliente",)

POLITICA = "vd_ente_isolamento"
POLITICA_GLOBALI = "vd_ente_globali_lettura"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")

#: Tabelle senza ente proprio: (tabella, padre, colonna verso il padre).
DERIVATE: tuple[tuple[str, str, str], ...] = (
    ("documenti", "aip", "aip_id"),
    ("metadati_allegato5", "documenti", "documento_id"),
    ("lifecycle_events", "aip", "aip_id"),
    ("fascicoli_aip", "fascicoli", "fascicolo_id"),
)


def _funzione(tabella: str, padre: str, colonna: str) -> str:
    controllo_pda = ""
    if tabella == "fascicoli_aip":
        controllo_pda = """
    SELECT tenant_id INTO ente_pda FROM public.aip WHERE id = NEW.aip_id;
    IF ente_pda IS NULL OR ente_pda <> ente THEN
        RAISE EXCEPTION 'fascicoli_aip: il PdA % non appartiene all''ente del fascicolo',
            NEW.aip_id USING ERRCODE = 'foreign_key_violation';
    END IF;"""
    return f"""
CREATE OR REPLACE FUNCTION public.vd_tenant_{tabella}()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ente uuid;
    ente_pda uuid;
BEGIN
    -- VD-C-080: ente ricavato dal padre (con la RLS di chi scrive)
    SELECT tenant_id INTO ente FROM public.{padre} WHERE id = NEW.{colonna};
    IF ente IS NULL THEN
        RAISE EXCEPTION '{tabella}: {padre} % inesistente o di un altro ente', NEW.{colonna}
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NEW.tenant_id IS NULL THEN
        NEW.tenant_id := ente;
    ELSIF NEW.tenant_id <> ente THEN
        RAISE EXCEPTION '{tabella}: tenant_id diverso da quello di {padre} %', NEW.{colonna}
            USING ERRCODE = 'check_violation';
    END IF;{controllo_pda}
    RETURN NEW;
END $$;
"""


def _colonne_trigger(tabella: str, colonna: str) -> str:
    extra = ", aip_id" if tabella == "fascicoli_aip" else ""
    return f"{colonna}, tenant_id{extra}"


def _lotto_blocchi() -> int:
    return max(1, int(os.environ.get("VD_MIGRAZIONE_038_LOTTO_BLOCCHI", "2000")))


def _riempi(tabella: str, padre: str, colonna: str) -> None:
    """Riempimento a lotti per intervallo di blocchi (una transazione per lotto)."""
    aggiorna = (f"UPDATE public.{tabella} f SET tenant_id = p.tenant_id FROM public.{padre} p "
                f"WHERE p.id = f.{colonna} AND f.tenant_id IS NULL")
    if context.is_offline_mode():
        op.execute(aggiorna)
        return
    bind = op.get_bind()
    blocchi = int(bind.execute(text(
        "SELECT pg_relation_size(CAST(:t AS regclass)) / current_setting('block_size')::int"),
        {"t": f"public.{tabella}"}).scalar_one())
    passo = _lotto_blocchi()
    with op.get_context().autocommit_block():
        for inizio in range(0, blocchi + 1, passo):
            bind.execute(text(
                f"{aggiorna} AND f.ctid >= CAST(CAST(:da AS text) AS tid) "
                f"AND f.ctid < CAST(CAST(:a AS text) AS tid)"),
                {"da": f"({inizio},0)", "a": f"({inizio + passo},0)"})
        # righe spostate durante il riempimento (aggiornamenti concorrenti)
        bind.execute(text(aggiorna))


def _indice(tabella: str) -> None:
    nome = f"idx_{tabella}_tenant"
    if context.is_offline_mode():
        op.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON public.{tabella} (tenant_id)")
        return
    with op.get_context().autocommit_block():
        # un indice CONCURRENTLY interrotto resta INVALIDO: si ricrea
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_index i
                         WHERE i.indexrelid = to_regclass('public.{nome}')
                           AND NOT i.indisvalid) THEN
                EXECUTE 'DROP INDEX public.{nome}';
              END IF;
            END $$""")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {nome} "
                   f"ON public.{tabella} (tenant_id)")


def _controlla_fascicoli_misti() -> None:
    """REGOLE §16.5: un vincolo nuovo su dati esistenti si ferma con l'elenco."""
    if context.is_offline_mode():
        return
    misti = op.get_bind().execute(text(
        "SELECT fa.fascicolo_id, fa.aip_id FROM public.fascicoli_aip fa "
        "JOIN public.fascicoli f ON f.id = fa.fascicolo_id "
        "JOIN public.aip a ON a.id = fa.aip_id WHERE a.tenant_id <> f.tenant_id "
        "ORDER BY 1, 2 LIMIT 50")).all()
    if misti:
        elenco = ", ".join(f"(fascicolo {r[0]}, PdA {r[1]})" for r in misti)
        raise RuntimeError("fascicoli_aip collega fascicoli e PdA di enti diversi: correggere "
                           f"prima della migrazione 20261023_038. Prime coppie: {elenco}")


def _lock_timeout() -> str:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    return valore if valore.replace("ms", "").replace("s", "").isdigit() else "5s"


def _limita_attesa_lock() -> None:
    """``lock_timeout`` per la transazione corrente (SET LOCAL): un DDL che non
    ottiene il lock in tempo fallisce invece di mettere in coda tutto il servizio."""
    op.execute(f"SET LOCAL lock_timeout = '{_lock_timeout()}'")


def _conferma() -> None:
    """Chiude la transazione corrente e ne rilascia i lock (Alembic ne apre una nuova)."""
    if not context.is_offline_mode():
        with op.get_context().autocommit_block():
            pass


def _convalida(tabella: str, *vincoli: str) -> None:
    """``VALIDATE CONSTRAINT`` in autocommit, uno per transazione: la scansione
    prende solo SHARE UPDATE EXCLUSIVE (revisione R05, R6)."""
    istruzioni = [f"ALTER TABLE public.{tabella} VALIDATE CONSTRAINT {v}" for v in vincoli]
    if context.is_offline_mode():
        for istruzione in istruzioni:
            op.execute(istruzione)
        return
    with op.get_context().autocommit_block():
        for istruzione in istruzioni:
            op.execute(istruzione)


def upgrade() -> None:
    _limita_attesa_lock()
    _controlla_fascicoli_misti()
    # 1a-1b. colonne, chiavi esterne NOT VALID, trigger
    for tabella, padre, colonna in DERIVATE:
        op.execute(f"ALTER TABLE public.{tabella} ADD COLUMN IF NOT EXISTS tenant_id uuid")
        # idempotente: una migrazione interrotta dopo il riempimento si riprende
        op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT IF EXISTS fk_{tabella}_tenant")
        op.execute(f"ALTER TABLE public.{tabella} ADD CONSTRAINT fk_{tabella}_tenant "
                   f"FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) "
                   f"ON DELETE RESTRICT NOT VALID")
        op.execute(_funzione(tabella, padre, colonna))
        op.execute(f"CREATE OR REPLACE TRIGGER trg_{tabella}_tenant BEFORE INSERT OR UPDATE OF "
                   f"{_colonne_trigger(tabella, colonna)} ON public.{tabella} FOR EACH ROW "
                   f"EXECUTE FUNCTION public.vd_tenant_{tabella}()")
    # 1c. riempimento a lotti (fuori dalla transazione della migrazione), in
    # ordine di dipendenza: metadati_allegato5 legge documenti già riempiti
    for tabella, padre, colonna in DERIVATE:
        _riempi(tabella, padre, colonna)
    # 1d. NOT NULL e chiave esterna: scansioni in autocommit, mai sotto il lock
    # esclusivo (revisione R05, R6)
    for tabella, _padre, _colonna in DERIVATE:
        vincolo = f"ck_{tabella}_tenant_presente"
        _limita_attesa_lock()
        op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT IF EXISTS {vincolo}")
        op.execute(f"ALTER TABLE public.{tabella} ADD CONSTRAINT {vincolo} "
                   f"CHECK (tenant_id IS NOT NULL) NOT VALID")
        _conferma()
        _convalida(tabella, vincolo, f"fk_{tabella}_tenant")
        _limita_attesa_lock()
        op.execute(f"ALTER TABLE public.{tabella} ALTER COLUMN tenant_id SET NOT NULL")
        op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT {vincolo}")
        _conferma()
    # 1e. indici senza bloccare le scritture
    for tabella, _padre, _colonna in DERIVATE:
        _indice(tabella)
    # 2. Row Level Security (ENABLE: ACCESS EXCLUSIVE per un istante)
    _limita_attesa_lock()
    for tabella in TABELLE_ENTE:
        op.execute(f"ALTER TABLE public.{tabella} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA} ON public.{tabella} AS PERMISSIVE FOR ALL "
                   f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")
    for tabella in TABELLE_GLOBALI_LEGGIBILI:
        op.execute(f"DROP POLICY IF EXISTS {POLITICA_GLOBALI} ON public.{tabella}")
        op.execute(f"CREATE POLICY {POLITICA_GLOBALI} ON public.{tabella} AS PERMISSIVE "
                   f"FOR SELECT TO PUBLIC USING (tenant_id IS NULL)")


def downgrade() -> None:
    _limita_attesa_lock()
    for tabella in TABELLE_GLOBALI_LEGGIBILI:
        op.execute(f"DROP POLICY IF EXISTS {POLITICA_GLOBALI} ON public.{tabella}")
    for tabella in TABELLE_ENTE:
        op.execute(f"DROP POLICY IF EXISTS {POLITICA} ON public.{tabella}")
        op.execute(f"ALTER TABLE public.{tabella} DISABLE ROW LEVEL SECURITY")
    for tabella, _padre, _colonna in reversed(DERIVATE):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tabella}_tenant ON public.{tabella}")
        op.execute(f"DROP FUNCTION IF EXISTS public.vd_tenant_{tabella}()")
        op.execute(f"DROP INDEX IF EXISTS public.idx_{tabella}_tenant")
        op.execute(f"ALTER TABLE public.{tabella} DROP CONSTRAINT IF EXISTS fk_{tabella}_tenant")
        op.execute(f"ALTER TABLE public.{tabella} DROP COLUMN IF EXISTS tenant_id")
