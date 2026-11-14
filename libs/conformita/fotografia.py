# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/fotografia.py — fotografia dei fatti del repository (R15, VD-C-177).

La raccoglie ``libs/conformita/repository.py`` (strumento del rilascio, legge YAML e i
sorgenti); qui solo il formato, la serializzazione stabile e la lettura, senza dipendenze
oltre la libreria standard: è il modulo che usa la console (dashboard-api), che non ha il
repository né PyYAML.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FOTOGRAFIA = Path(__file__).resolve().parent / "dati" / "fonti_repository.json"
FORMATO = "verumdoc-fonti-repository-1"


def serializza(fatti: dict[str, Any]) -> str:
    """JSON stabile della fotografia (chiavi ordinate, a capo finale)."""
    return json.dumps(fatti, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def leggi_fotografia(percorso: Path = FOTOGRAFIA) -> dict[str, Any]:
    """Fotografia salvata dal rilascio (usata dalla console)."""
    dati: dict[str, Any] = json.loads(percorso.read_text(encoding="utf-8"))
    if dati.get("formato") != FORMATO:
        raise ValueError(f"fotografia del repository di formato inatteso: {dati.get('formato')}")
    return dati


__all__ = ["FORMATO", "FOTOGRAFIA", "leggi_fotografia", "serializza"]
