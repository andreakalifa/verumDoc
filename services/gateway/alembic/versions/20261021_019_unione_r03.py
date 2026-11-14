# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Unione dei rami dell'ondata B di R03

Revision ID: 20261021_019
Revises: 20261021_015, 20261021_016, 20261021_017, 20261021_018
Create Date: 2026-10-21

Gli agenti dell'ondata B di R03 sono partiti tutti da ``20261021_014`` con una
revisione propria (rami indipendenti, nessuna tabella in comune):
  - 015 sessioni di caricamento e adattatori (b1);
  - 016 canale SFTP (b2);
  - 017 eventi per i clienti e webhook (b3);
  - 018 accesso, ricerca e pacchetti di distribuzione (b4).

Questa revisione riunisce le quattro teste in una sola, così ``alembic upgrade
head`` torna univoco. Non esegue operazioni: upgrade e downgrade sono vuoti
(il downgrade riporta alle quattro teste, poi ciascun ramo si annulla col
proprio downgrade).
"""

from __future__ import annotations

revision = "20261021_019"
down_revision = ("20261021_015", "20261021_016", "20261021_017", "20261021_018")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nessuna operazione: unione dei rami."""


def downgrade() -> None:
    """Nessuna operazione: si torna alle quattro teste dei rami."""
