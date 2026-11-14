# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Deduplica delle notifiche: indice unico sulla coda di uscita (VD-C-024)

Revision ID: 20261020_013
Revises: 20261020_012
Create Date: 2026-10-20

Contenuto (agente g2-pipeline):
  - public.notifications: indice UNICO ``uq_notifications_dedup`` su
    (tenant_id, tipo, destinatario, payload_json->>'oggetto_id').

Perché questa chiave. Il servizio notification consuma Kafka «almeno una
volta» e può girare in più repliche: lo stesso evento può arrivare più volte o
in parallelo. Una notifica è la STESSA se riguarda lo stesso oggetto (PdV o PdA,
``oggetto_id``; per i messaggi espliciti senza oggetto, l'impronta del
contenuto calcolata dal servizio), per lo stesso tipo di evento, verso lo
stesso destinatario, per lo stesso cliente. Il cliente fa parte della chiave
per l'isolamento (REGOLE §14): nessuna collisione fra clienti diversi. Le righe
NON_PARTITA (destinatario vuoto, con motivo) seguono la stessa regola: un
evento riletto non duplica il motivo. Prima la deduplica era un
``WHERE NOT EXISTS`` non atomico: due repliche potevano inserire entrambe.
Ora il servizio usa ``ON CONFLICT ... DO NOTHING`` su questo indice.

Vincolo nuovo su dati esistenti (§16.5): prima si controllano i doppioni e, se
ce ne sono, la migrazione si ferma con l'elenco (nessuna riga cancellata).
Nessun contenuto conservato viene toccato (§10.6).

Modello: ``libs.shared.models.Notification`` deve dichiarare lo stesso indice
(altrimenti ``alembic check`` lo segnala come da rimuovere):
    Index("uq_notifications_dedup", "tenant_id", "tipo", "destinatario",
          text("(payload_json ->> 'oggetto_id'::text)"), unique=True)
"""
from __future__ import annotations

from alembic import op

revision = "20261020_013"
down_revision = "20261020_012"
branch_labels = None
depends_on = None

INDICE = "uq_notifications_dedup"
_CHIAVE = "tenant_id, tipo, destinatario, (payload_json->>'oggetto_id')"


def upgrade() -> None:
    # Fermata con l'elenco dei doppioni (al massimo 20 gruppi nel messaggio)
    op.execute(
        "DO $$ DECLARE elenco text; n integer; BEGIN "
        "SELECT count(*), string_agg(gruppo, '; ') INTO n, elenco FROM ("
        "  SELECT format('tenant=%s tipo=%s destinatario=%s oggetto=%s righe=%s', "
        "         tenant_id, tipo, destinatario, payload_json->>'oggetto_id', count(*)) AS gruppo "
        "  FROM public.notifications "
        f"  GROUP BY {_CHIAVE} HAVING count(*) > 1 LIMIT 20) d; "
        "IF n > 0 THEN RAISE EXCEPTION "
        "'Migrazione 20261020_013 interrotta: notifiche doppie da risolvere a mano (% gruppi): %', "
        "n, elenco; END IF; END $$;"
    )
    op.execute(
        f"CREATE UNIQUE INDEX {INDICE} ON public.notifications ({_CHIAVE})"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS public.{INDICE}")
