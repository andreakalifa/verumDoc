# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Blocco «controllo di coerenza non verificabile» (R04 quarto giro, F1)

Revision ID: 20261023_035
Revises: 20261023_034
Create Date: 2026-10-23

``catena_blocchi_registro.causa`` ammette ``COERENZA_NON_VERIFICABILE``: il
controllo di coerenza del registro con il WORM e il testimone non si può fare
(ruolo IAM senza ``s3:ListBucket`` sul prefisso ``registro/``, WORM
irraggiungibile). Prima l'errore restava nei log del passo e il registro, anche
sano, non scriveva mai senza alcun allarme; ora il blocco è esplicito
(allarme ``RegistroBloccato``) e si chiude da solo quando il controllo passa.
Solo catalogo (vincolo CHECK); downgrade: i blocchi di questa causa si
eliminano (prima della 035 non esistevano).
"""
from __future__ import annotations

from alembic import op

revision = "20261023_035"
down_revision = "20261023_034"
branch_labels = None
depends_on = None

_VINCOLO = "ck_catena_blocchi_registro_causa"
_CAUSE_034 = "'REGISTRO_INDIETRO', 'REGISTRO_DIVERGENTE', 'WORM_CONTENUTO_DIVERSO'"


def upgrade() -> None:
    op.drop_constraint(_VINCOLO, "catena_blocchi_registro", schema="public", type_="check")
    op.create_check_constraint(
        _VINCOLO, "catena_blocchi_registro",
        f"causa IN ({_CAUSE_034}, 'COERENZA_NON_VERIFICABILE')", schema="public")


def downgrade() -> None:
    op.execute("DELETE FROM public.catena_blocchi_registro "
               "WHERE causa = 'COERENZA_NON_VERIFICABILE'")
    op.drop_constraint(_VINCOLO, "catena_blocchi_registro", schema="public", type_="check")
    op.create_check_constraint(_VINCOLO, "catena_blocchi_registro", f"causa IN ({_CAUSE_034})",
                               schema="public")
