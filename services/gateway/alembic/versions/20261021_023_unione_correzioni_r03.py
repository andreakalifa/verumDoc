# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Unione dei rami delle correzioni R03 (f1 e f2)

Revision ID: 20261021_023
Revises: 20261021_021, 20261021_022
Create Date: 2026-10-21

Le correzioni dopo la revisione indipendente di R03 sono partite entrambe da
``20261021_020`` con una revisione propria (nessuna tabella in comune):
  - 021 correzioni dei canali del gateway (f1): ``pdv.staging_eliminato_il``,
    indice ``idx_pdv_staging_da_pulire``, ``compat_packer_richieste.impronta_richiesta``;
  - 022 letture a cursore dei PdA, posizione degli eventi, segnalazioni SFTP (f2).

Questa revisione riunisce le due teste, così ``alembic upgrade head`` torna
univoco. Non esegue operazioni: upgrade e downgrade sono vuoti (il downgrade
riporta alle due teste, poi ciascun ramo si annulla col proprio downgrade).

Le quote personalizzate per ente (R03 i3) NON richiedono schema: sono righe
``impostazioni_valori`` di livello ENTE con i codici di ``libs.verumdoc_rules.quote``.
"""

from __future__ import annotations

revision = "20261021_023"
down_revision = ("20261021_021", "20261021_022")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nessuna operazione: unione dei rami."""


def downgrade() -> None:
    """Nessuna operazione: si torna alle due teste dei rami."""
