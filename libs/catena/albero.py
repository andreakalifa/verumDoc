# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena/albero.py — albero RFC 6962 con NODI SALVATI: radice, prove e
crescita in O(log n) letture, senza ricalcolare l'albero dalle foglie.

Riferimenti: contratto K1 di R04 (registro globale append-only, prove
efficienti); RFC 6962 §2.1. Usato dal sequenziatore della hashchain (tabella
``catena_nodi``) e da chiunque ricostruisca il registro dal WORM.

Nodo salvato = radice di un sottoalbero PERFETTO e ALLINEATO: il nodo
``(livello, indice)`` copre le foglie ``[indice·2^livello, (indice+1)·2^livello)``;
il livello 0 sono gli hash di foglia. Un registro di n foglie ha circa 2n nodi.

Ogni sottoalbero che compare nelle definizioni ricorsive della RFC 6962 (MTH,
PATH, SUBPROOF) per un albero che parte dalla foglia 0 si scompone in al più
O(log n) nodi salvati. Per questo ogni funzione esiste in due passi:
  1. ``nodi_per_*``: insieme delle coordinate da leggere (una sola query);
  2. ``*_da_nodi``: calcolo con la mappa coordinate → hash letta.
Il flusso di controllo non dipende dai valori degli hash: lo stesso algoritmo
elenca le coordinate (con un lettore che le registra) e poi calcola.

Modulo puro: nessun I/O.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from libs.catena.rfc6962 import (
    LUNGHEZZA_HASH,
    RADICE_VUOTA,
    controlla_dimensioni_consistenza,
    controlla_indici_inclusione,
    divisione,
    hash_nodo,
    percorso_generico,
    sottoprova_generica,
)

#: (livello, indice) di un nodo salvato.
Coordinata = tuple[int, int]
LettoreNodo = Callable[[int, int], bytes]

_SEGNAPOSTO = b"\x00" * LUNGHEZZA_HASH


class NodoMancante(LookupError):
    """Un nodo necessario non è nella mappa fornita (registro incompleto)."""

    def __init__(self, coordinata: Coordinata) -> None:
        super().__init__(f"Nodo {coordinata} mancante")
        self.coordinata = coordinata


def _potenza_di_due(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def mth_da_nodi(a: int, b: int, nodo: LettoreNodo) -> bytes:
    """Radice del sottoalbero delle foglie [a, b) dai nodi salvati."""
    n = b - a
    if n <= 0:
        return RADICE_VUOTA
    if _potenza_di_due(n) and a % n == 0:
        livello = n.bit_length() - 1
        return nodo(livello, a >> livello)
    k = divisione(n)
    return hash_nodo(mth_da_nodi(a, a + k, nodo), mth_da_nodi(a + k, b, nodo))


def _lettore(nodi: Mapping[Coordinata, bytes]) -> LettoreNodo:
    def nodo(livello: int, indice: int) -> bytes:
        try:
            return nodi[(livello, indice)]
        except KeyError:
            raise NodoMancante((livello, indice)) from None
    return nodo


def _registratore() -> tuple[set[Coordinata], LettoreNodo]:
    richieste: set[Coordinata] = set()

    def nodo(livello: int, indice: int) -> bytes:
        richieste.add((livello, indice))
        return _SEGNAPOSTO
    return richieste, nodo


# ── Radice ────────────────────────────────────────────────────────────

def nodi_per_radice(dimensione: int) -> set[Coordinata]:
    """Coordinate da leggere per la radice dell'albero di ``dimensione`` foglie."""
    richieste, nodo = _registratore()
    mth_da_nodi(0, dimensione, nodo)
    return richieste


def radice_da_nodi(dimensione: int, nodi: Mapping[Coordinata, bytes]) -> bytes:
    """Radice (MTH) dell'albero delle prime ``dimensione`` foglie."""
    return mth_da_nodi(0, dimensione, _lettore(nodi))


# ── Inclusione ────────────────────────────────────────────────────────

def nodi_per_inclusione(indice: int, dimensione: int) -> set[Coordinata]:
    """Coordinate da leggere per la prova d'inclusione della foglia ``indice``."""
    controlla_indici_inclusione(indice, dimensione)
    richieste, nodo = _registratore()
    percorso_generico(indice, 0, dimensione, lambda a, b: mth_da_nodi(a, b, nodo))
    return richieste


def prova_inclusione_da_nodi(
    indice: int, dimensione: int, nodi: Mapping[Coordinata, bytes],
) -> list[bytes]:
    """Percorso di audit RFC 6962 §2.1.1 (uguale a ``rfc6962.prova_inclusione``)."""
    controlla_indici_inclusione(indice, dimensione)
    nodo = _lettore(nodi)
    return percorso_generico(indice, 0, dimensione, lambda a, b: mth_da_nodi(a, b, nodo))


# ── Consistenza ───────────────────────────────────────────────────────

def nodi_per_consistenza(n1: int, n2: int) -> set[Coordinata]:
    """Coordinate da leggere per la prova di consistenza da ``n1`` a ``n2``."""
    controlla_dimensioni_consistenza(n1, n2)
    if n1 in (0, n2):
        return set()
    richieste, nodo = _registratore()
    sottoprova_generica(n1, 0, n2, True, lambda a, b: mth_da_nodi(a, b, nodo))
    return richieste


def prova_consistenza_da_nodi(
    n1: int, n2: int, nodi: Mapping[Coordinata, bytes],
) -> list[bytes]:
    """Prova di consistenza RFC 6962 §2.1.2 (uguale a ``rfc6962.prova_consistenza``)."""
    controlla_dimensioni_consistenza(n1, n2)
    if n1 in (0, n2):
        return []
    nodo = _lettore(nodi)
    return sottoprova_generica(n1, 0, n2, True, lambda a, b: mth_da_nodi(a, b, nodo))


# ── Crescita del registro ─────────────────────────────────────────────

def coordinate_frontiera(dimensione: int) -> list[Coordinata]:
    """Sottoalberi perfetti massimali dell'albero di ``dimensione`` foglie, da
    sinistra a destra (uno per bit a 1 della dimensione): sono gli unici nodi
    esistenti che servono per aggiungere foglie."""
    coordinate: list[Coordinata] = []
    inizio = 0
    for livello in range(dimensione.bit_length() - 1, -1, -1):
        if dimensione & (1 << livello):
            coordinate.append((livello, inizio >> livello))
            inizio += 1 << livello
    return coordinate


def aggiungi_foglie(
    dimensione: int,
    frontiera: Mapping[Coordinata, bytes],
    foglie_nuove: Iterable[bytes],
) -> dict[Coordinata, bytes]:
    """
    Nodi NUOVI (livello 0 compreso) prodotti aggiungendo ``foglie_nuove`` (hash di
    foglia) a un albero di ``dimensione`` foglie, data la sua frontiera
    (``coordinate_frontiera(dimensione)`` → hash). I nodi esistenti non cambiano
    mai: il registro è append-only anche nei nodi.
    """
    disponibili: dict[Coordinata, bytes] = dict(frontiera)
    nuovi: dict[Coordinata, bytes] = {}
    i = dimensione
    for foglia in foglie_nuove:
        if len(foglia) != LUNGHEZZA_HASH:
            raise ValueError("Hash di foglia di lunghezza errata")
        corrente = bytes(foglia)
        livello, posizione = 0, i
        nuovi[(0, i)] = disponibili[(0, i)] = corrente
        while posizione & 1:  # figlio destro: il genitore è completo
            sinistro = disponibili.get((livello, posizione - 1))
            if sinistro is None:
                raise NodoMancante((livello, posizione - 1))
            corrente = hash_nodo(sinistro, corrente)
            livello, posizione = livello + 1, posizione >> 1
            nuovi[(livello, posizione)] = disponibili[(livello, posizione)] = corrente
        i += 1
    return nuovi


def tutti_i_nodi(foglie: Iterable[bytes]) -> dict[Coordinata, bytes]:
    """Tutti i nodi salvabili di un albero costruito da zero (ricostruzione dal WORM)."""
    return aggiungi_foglie(0, {}, foglie)


__all__ = [
    "Coordinata", "NodoMancante", "aggiungi_foglie", "coordinate_frontiera",
    "mth_da_nodi", "nodi_per_consistenza", "nodi_per_inclusione", "nodi_per_radice",
    "prova_consistenza_da_nodi", "prova_inclusione_da_nodi", "radice_da_nodi",
    "tutti_i_nodi",
]
