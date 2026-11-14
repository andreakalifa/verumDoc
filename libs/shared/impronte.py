# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/impronte.py — algoritmi d'impronta ammessi (UNICA tabella).

Usata da gateway (versamento), validation (Step 5) e aip-builder (ricalcolo sui
byte del PdA): prima la stessa tabella era copiata nei tre servizi.
Riferimenti: Allegato 6 LG AgID (SHA-256 minimo); REGOLE §10.3.
TODO registro impostazioni (§8.1, voce ALGORITMO_IMPRONTA): quando esisterà,
questa tabella resta l'elenco di ciò che il codice SA calcolare; il registro
sceglierà fra questi.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

ALGORITMI_IMPRONTA: MappingProxyType[str, Callable[..., Any]] = MappingProxyType({
    "SHA-256": hashlib.sha256,
    "SHA-512": hashlib.sha512,
    "SHA3-256": hashlib.sha3_256,
})

#: Espressione regolare (per Pydantic) dei nomi ammessi, nell'ordine della tabella.
#: (i nomi contengono solo lettere, cifre e «-»: nessun carattere speciale da proteggere)
PATTERN_ALGORITMI = "^(" + "|".join(ALGORITMI_IMPRONTA) + ")$"


def funzione_impronta(algoritmo: str | None) -> Callable[..., Any] | None:
    """Costruttore hashlib per il nome dichiarato (maiuscole/minuscole indifferenti),
    ``None`` se l'algoritmo non è ammesso."""
    return ALGORITMI_IMPRONTA.get((algoritmo or "").strip().upper())


def calcola_impronta(algoritmo: str, contenuto: bytes) -> str:
    """Impronta esadecimale; ``ValueError`` se l'algoritmo non è ammesso."""
    funzione = funzione_impronta(algoritmo)
    if funzione is None:
        raise ValueError(f"algoritmo d'impronta non ammesso: {algoritmo!r}")
    return funzione(contenuto).hexdigest()
