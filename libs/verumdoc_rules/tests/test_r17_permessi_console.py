# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Revisione R17 (audit R17-01/02/13): la console decide pagine e comandi dai permessi;
la copia dei modelli dei ruoli fissi in ``frontend/src/auth/permessi.ts`` (usata quando la
sessione non porta i permessi effettivi) deve coincidere con ``permessi.MODELLI``."""
from __future__ import annotations

import re
from pathlib import Path

from libs.verumdoc_rules import permessi

FILE = Path(__file__).resolve().parents[3] / "frontend/src/auth/permessi.ts"


def _modelli_ts() -> tuple[dict[str, set[str]], set[str]]:
    testo = FILE.read_text(encoding="utf-8")
    tutti = set(re.findall(r'"([a-z_]+:[a-z_]+)"',
                           testo.split("const TUTTI_I_PERMESSI = [", 1)[1].split("];", 1)[0]))
    blocco = testo.split("// MODELLI-INIZIO", 1)[1].split("// MODELLI-FINE", 1)[0]
    modelli: dict[str, set[str]] = {}
    for ruolo, corpo in re.findall(r"(\w+): (TUTTI_I_PERMESSI|\[[^\]]*\])", blocco):
        modelli[ruolo] = tutti if corpo == "TUTTI_I_PERMESSI" else set(
            re.findall(r'"([a-z_]+:[a-z_]+)"', corpo))
    return modelli, tutti


def test_copia_dei_modelli_nella_console_coincide() -> None:
    modelli, tutti = _modelli_ts()
    assert tutti == set(permessi.PERMESSI)
    assert modelli == {r: set(p) for r, p in permessi.MODELLI.items()}


def test_operatore_chiede_esibizioni_la_consultazione_no() -> None:
    assert permessi.ESIBIZIONE in permessi.MODELLI["OPERATORE"]
    assert permessi.ESIBIZIONE not in permessi.MODELLI["VIEWER"]
    assert permessi.VERSA not in permessi.MODELLI["VIEWER"]
