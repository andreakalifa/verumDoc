# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Scarto legato alla pratica e inviti senza segreti in chiaro (R12a: VD-C-145, VD-C-152)

Revision ID: 20261104_051
Revises: 20261103_050
Create Date: 2026-11-04

1. VD-C-145 — transizione: ``ciclo_vita_transizione_r12`` elenca gli allegati
   autorizzativi (autorizzazione allo scarto, nulla osta) GIÀ caricati, al momento
   della migrazione, in pratiche di scarto non concluse. Da R12a il documento deve
   citare l'identificativo della pratica nel contenuto firmato; per questi soli
   allegati il lavoratore registra un avviso invece di bloccare (VD-P-115). Il
   confine non è falsificabile: la tabella la scrive SOLO questa migrazione (il
   proprietario); il lavoratore ha SELECT, l'API nessun diritto. Un allegato
   caricato dopo la migrazione non vi entra mai.
2. VD-C-145 — indice ``idx_pratiche_allegati_impronta`` (ente, impronta) per il
   controllo del riuso di un documento in un'altra pratica (CONCURRENTLY in
   autocommit; offline in transazione senza CONCURRENTLY).
3. VD-C-152 — minimizzazione dei collegamenti d'invito già in chiaro: nelle
   notifiche già INVIATE e negli eventi dell'outbox già PUBBLICATI il frammento
   ``#invito=…`` si sostituisce con un segnaposto (il collegamento resta valido
   per chi ha ricevuto l'e-mail: nessuna regressione sugli inviti emessi). Gli
   eventi non ancora pubblicati e le notifiche non ancora inviate restano come
   sono e partono come prima; da R12a onboarding sigilla il collegamento.

Riprendibile: ``CREATE TABLE IF NOT EXISTS``, ``INSERT … ON CONFLICT DO NOTHING``,
UPDATE idempotenti, indice ``IF NOT EXISTS`` con rimozione di un indice INVALID
lasciato da un CONCURRENTLY interrotto; ``lock_timeout`` per i DDL.
Downgrade: toglie tabella e indice; la minimizzazione del punto 3 non si annulla
(nessun dato da ripristinare: il collegamento in chiaro non deve tornare).
"""
# ruff: noqa: S608 — SQL composto solo con costanti di questo modulo
from __future__ import annotations

import os

from alembic import context, op

revision = "20261104_051"
down_revision = "20261103_050"
branch_labels = None
depends_on = None

RUOLO_LAVORATORE = "verumdoc_ciclo_vita_lavoratore"
RUOLO_APPLICAZIONE = "verumdoc_applicazione"
INDICE = "idx_pratiche_allegati_impronta"
#: Stati conclusi dello scarto (app.domain.stati: ESEGUITA, RESPINTA, CHIUSA_CON_ECCEZIONI)
FINALI_SCARTO = ("ESEGUITA", "RESPINTA", "CHIUSA_CON_ECCEZIONI")
TIPI_LEGATI = ("AUTORIZZAZIONE_PRODUTTORE", "NULLA_OSTA")
SEGNAPOSTO = "#invito=[rimosso dopo l''invio]"
#: il token è «<ente esadecimale>.<base64url>» (niente «:» nel testo: op.execute usa text())
REGEX = "#invito=[A-Za-z0-9._~-]+"


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


_TABELLA = """
CREATE TABLE IF NOT EXISTS public.ciclo_vita_transizione_r12 (
    allegato_id uuid PRIMARY KEY,
    pratica_id uuid NOT NULL,
    tipo varchar(40) NOT NULL,
    registrato_il timestamp with time zone NOT NULL DEFAULT now()
)"""

_ELENCO = f"""
INSERT INTO public.ciclo_vita_transizione_r12 (allegato_id, pratica_id, tipo)
SELECT a.id, a.pratica_id, a.tipo
FROM public.pratiche_allegati a JOIN public.pratiche p ON p.id = a.pratica_id
WHERE p.tipo = 'SCARTO' AND p.stato NOT IN ({_elenco(FINALI_SCARTO)})
  AND a.tipo IN ({_elenco(TIPI_LEGATI)})
ON CONFLICT (allegato_id) DO NOTHING"""

_PRIVILEGI = """
DO $p$
BEGIN
    REVOKE ALL ON public.ciclo_vita_transizione_r12 FROM PUBLIC;
    -- i privilegi predefiniti danno arwd al gruppo dei servizi: si tolgono
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@APPLICAZIONE@') THEN
        REVOKE ALL ON public.ciclo_vita_transizione_r12 FROM @APPLICAZIONE@;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@LAVORATORE@') THEN
        GRANT SELECT ON public.ciclo_vita_transizione_r12 TO @LAVORATORE@;
    END IF;
END
$p$;
""".replace("@LAVORATORE@", RUOLO_LAVORATORE).replace("@APPLICAZIONE@", RUOLO_APPLICAZIONE)

_MINIMIZZA_NOTIFICHE = f"""
UPDATE public.notifications
SET payload_json = jsonb_set(payload_json, '{{testo}}', to_jsonb(regexp_replace(
        payload_json->>'testo', '{REGEX}', '{SEGNAPOSTO}', 'g')))
WHERE tipo = 'accesso.invito' AND stato = 'INVIATA'
  AND payload_json->>'testo' ~ '#invito=[A-Za-z0-9]'"""

_MINIMIZZA_OUTBOX = f"""
UPDATE public.outbox_eventi
SET payload = jsonb_set(payload, '{{testo}}', to_jsonb(regexp_replace(
        payload->>'testo', '{REGEX}', '{SEGNAPOSTO}', 'g')))
WHERE topic = 'notification.send' AND pubblicato_il IS NOT NULL
  AND payload->>'evento' = 'accesso.invito' AND payload->>'testo' ~ '#invito=[A-Za-z0-9]'"""


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _in_autocommit(*istruzioni: str) -> None:
    if context.is_offline_mode():
        # script SQL offline: tutto in transazione, quindi indici senza CONCURRENTLY
        for i in istruzioni:
            op.execute(i.replace(" CONCURRENTLY", ""))
        return
    with op.get_context().autocommit_block():
        for i in istruzioni:
            op.execute(i)


def _indice_non_valido(nome: str) -> str:
    """Un indice CONCURRENTLY interrotto resta INVALID: si toglie prima di ricrearlo."""
    return (f"DO $i$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c "
            f"ON c.oid = i.indexrelid WHERE c.relname = '{nome}' AND NOT i.indisvalid) THEN "
            f"EXECUTE 'DROP INDEX CONCURRENTLY public.{nome}'; END IF; END $i$")


def upgrade() -> None:
    _limita_attesa_lock()
    op.execute(_TABELLA)
    op.execute(_ELENCO)
    op.execute(_PRIVILEGI)
    op.execute(_MINIMIZZA_NOTIFICHE)
    op.execute(_MINIMIZZA_OUTBOX)
    _in_autocommit(
        _indice_non_valido(INDICE),
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDICE} "
        "ON public.pratiche_allegati (tenant_id, sha256)")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("DROP TABLE IF EXISTS public.ciclo_vita_transizione_r12")
    _in_autocommit(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDICE}")
