# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R15 (VD-C-177): la raccolta dal database è in SOLA LETTURA (prima istruzione della
transazione) e nell'ambito del conservatore; legge solo le tabelle dichiarate in LETTURE
(le stesse dei GRANT dell'auditor nella migrazione 058)."""
from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path
from typing import Any

from libs.conformita import database

RADICE = Path(__file__).resolve().parents[3]


class _Risultato:
    def mappings(self) -> _Risultato:
        return self

    def all(self) -> list[Any]:
        return []


class _Connessione:
    def __init__(self) -> None:
        self.istruzioni: list[str] = []

    async def execute(self, sql: Any) -> _Risultato:
        self.istruzioni.append(str(sql))
        return _Risultato()


def test_sola_lettura_per_prima_e_ambito_conservatore() -> None:
    c = _Connessione()
    fatti = asyncio.run(database.raccogli(c))
    assert c.istruzioni[0] == "SET TRANSACTION READ ONLY"
    assert "set_config('app.ambito', 'conservatore', true)" in c.istruzioni[1]
    assert fatti["disponibile"] is True and fatti["classi_globali"] == []
    tabelle = {t for s in c.istruzioni[2:] for t in re.findall(r"public\.(\w+)", s)}
    assert tabelle == set(database.LETTURE)
    assert not any(re.match(r"\s*(INSERT|UPDATE|DELETE)", s, re.I) for s in c.istruzioni)


def test_letture_uguali_ai_grant_dell_auditor() -> None:
    percorso = (RADICE / "services/gateway/alembic/versions/20261111_058_documentazione.py")
    spec = importlib.util.spec_from_file_location("m058", percorso)
    assert spec is not None and spec.loader is not None
    testo = percorso.read_text(encoding="utf-8")
    assert "LETTURE_AUDITOR" in testo
    # il modulo importa alembic.op: si confronta il dizionario dal sorgente
    ambito: dict[str, Any] = {}
    inizio = testo.index("LETTURE_AUDITOR = {")
    fine = testo.index("}\n", inizio) + 1
    exec(testo[inizio:fine], ambito)  # noqa: S102 — costante del modulo di migrazione
    assert ambito["LETTURE_AUDITOR"] == database.LETTURE
