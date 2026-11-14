# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena/canonico.py — JSON canonico delle foglie della catena.

Riferimento: contratto K1 di R04 («JSON canonico: chiavi ordinate, UTF-8, senza
spazi»); REGOLE_SVILUPPO §12.8 (a parità di contenuto, stessa impronta).

Regole (le stesse per chi produce e per chi verifica, anche con altri linguaggi):
  - oggetti con chiavi ordinate per punto di codice Unicode; nessuno spazio
    (separatori ``,`` e ``:``);
  - testo in UTF-8 senza escape dei caratteri non ASCII (``ensure_ascii=False``):
    restano escapati solo ``"``, ``\\`` e i caratteri di controllo, come fa
    ``json.dumps`` di Python (``\\n``, ``\\u0001``…);
  - ammessi soltanto oggetti con chiavi stringa, liste, stringhe, interi,
    booleani e null. I numeri decimali sono VIETATI (la loro forma testuale non è
    univoca fra linguaggi): un valore decimale va passato come stringa.

Modulo puro (solo libreria standard).
"""
from __future__ import annotations

import json
from typing import Any


class ValoreNonCanonico(TypeError):
    """Il valore contiene un tipo non ammesso nel JSON canonico."""


def _controlla(valore: Any, percorso: str) -> None:
    if valore is None or isinstance(valore, bool | str):
        return
    if isinstance(valore, int):
        return
    if isinstance(valore, float):
        raise ValoreNonCanonico(f"{percorso}: numeri decimali non ammessi nel JSON canonico")
    if isinstance(valore, list | tuple):
        for i, elemento in enumerate(valore):
            _controlla(elemento, f"{percorso}[{i}]")
        return
    if isinstance(valore, dict):
        for chiave, elemento in valore.items():
            if not isinstance(chiave, str):
                raise ValoreNonCanonico(f"{percorso}: chiave non stringa {chiave!r}")
            _controlla(elemento, f"{percorso}.{chiave}")
        return
    raise ValoreNonCanonico(f"{percorso}: tipo {type(valore).__name__} non ammesso")


def json_canonico(obj: Any) -> bytes:
    """Serializzazione canonica (byte UTF-8) di ``obj``."""
    _controlla(obj, "$")
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


__all__ = ["ValoreNonCanonico", "json_canonico"]
