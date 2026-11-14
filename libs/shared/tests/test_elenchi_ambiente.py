# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Formato unico degli elenchi nelle variabili d'ambiente (R03 i1)."""
from __future__ import annotations

import pytest

from libs.shared.elenchi_ambiente import elenco_da_ambiente


@pytest.mark.parametrize("valore,atteso", [
    ("ssh-ed25519,ecdsa-sha2-nistp256", ["ssh-ed25519", "ecdsa-sha2-nistp256"]),
    (" ssh-ed25519 , , ssh-rsa ", ["ssh-ed25519", "ssh-rsa"]),
    ('["ssh-ed25519", "ssh-rsa"]', ["ssh-ed25519", "ssh-rsa"]),
    ("ssh-ed25519", ["ssh-ed25519"]),
    ("", []),
    (["gia", "elenco"], ["gia", "elenco"]),
])
def test_virgole_e_json_danno_lo_stesso_elenco(valore, atteso):
    assert elenco_da_ambiente(valore) == atteso


@pytest.mark.parametrize("valore", ['["a", 1]', "[non json", '[{"a": 1}]'])
def test_json_non_valido_rifiutato(valore):
    with pytest.raises(ValueError):
        elenco_da_ambiente(valore)
