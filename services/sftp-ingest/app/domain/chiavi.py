# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Politica delle chiavi SSH ammesse (regola pura).

Servizio: sftp-ingest. La stessa politica (stesse variabili d'ambiente
SFTP_TIPI_CHIAVE_AMMESSI e SFTP_RSA_BIT_MINIMI) la applica onboarding quando
la chiave viene registrata; qui si ricontrolla all'accesso, così una politica
resa più severa vale subito anche per le chiavi già registrate.
"""
from __future__ import annotations

from collections.abc import Collection


def motivo_chiave_non_ammessa(
    tipo: str, bit: int | None, *, tipi_ammessi: Collection[str], rsa_bit_minimi: int,
) -> str | None:
    """``None`` se la chiave è ammessa, altrimenti il motivo (REGOLE §9.7)."""
    if tipo not in tipi_ammessi:
        return f"tipo di chiave {tipo!r} non ammesso"
    if tipo == "ssh-rsa" and (bit is None or bit < rsa_bit_minimi):
        return f"chiave RSA di {bit} bit: ne servono almeno {rsa_bit_minimi}"
    return None
