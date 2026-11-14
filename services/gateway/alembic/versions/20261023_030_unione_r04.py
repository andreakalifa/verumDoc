# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Unione dei rami di R04 «Scala e catena» (025, 027, 028, 029)

Revision ID: 20261023_030
Revises: 20261023_025, 20261023_027, 20261023_028, 20261023_029
Create Date: 2026-10-23

In R04 quattro agenti sono partiti da ``20261021_024`` con una revisione propria:
  - 025 registro delle impronte a due livelli, checkpoint, coda (r04-catena);
  - 027 verifica continua dell'integrità: richieste, programmazione, registro
    partizionato per mese, copertura (r04-verifiche);
  - 028 outbox, eventi per i clienti, consegne e audit partizionati, contatori
    per ente, BRIN (r04-dati);
  - 029 code di scarto, richieste idempotenti, avviso di scadenza dello staging,
    indici a cursore (r04-servizi).

I rami non hanno tabelle in comune tranne gli indici a cursore di
``audit_global``: la 029 li crea solo se la tabella NON è partizionata, la 028
li crea sulla tabella partizionata (e li ricrea sulla tabella semplice nel suo
downgrade se la 029 resta applicata). L'ordine di applicazione dei rami è quindi
indifferente; questa revisione non esegue operazioni e rende di nuovo univoca
``alembic upgrade head``. Il downgrade riporta alle quattro teste: ciascun ramo
si annulla poi col proprio downgrade.
"""

from __future__ import annotations

revision = "20261023_030"
down_revision = ("20261023_025", "20261023_027", "20261023_028", "20261023_029")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nessuna operazione: unione dei rami."""


def downgrade() -> None:
    """Nessuna operazione: si torna alle quattro teste dei rami."""
