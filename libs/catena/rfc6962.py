# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena/rfc6962.py — albero di Merkle della RFC 6962 (Certificate Transparency).

Riferimenti: RFC 6962 §2.1 (Merkle Hash Trees), RFC 9162 §2.1.3.2 e §2.1.4.2
(algoritmi di verifica di inclusione e di consistenza); contratto K4 di R04;
REGOLE_SVILUPPO §12.8 (ordine totale delle foglie).

Definizioni (identiche alla RFC):
  - hash di foglia  = SHA-256(0x00 || dati della foglia)
  - hash di nodo    = SHA-256(0x01 || sinistro || destro)
  - MTH({})         = SHA-256("")
  - MTH(D[n]), n>1  = hash_nodo(MTH(D[0:k]), MTH(D[k:n])) con k la più grande
    potenza di 2 minore di n (l'ultima foglia non si duplica mai).

Convenzione di questo modulo: le funzioni che ricevono ``foglie`` vogliono gli
HASH DI FOGLIA (32 byte ciascuno, già calcolati con ``hash_foglia``), non i dati
grezzi; così il chiamante sceglie una sola volta come costruire le foglie.
Tutti gli hash sono ``bytes``; i formati JSON delle prove (esadecimale
minuscolo) si ottengono con ``esadecimale``.

Modulo puro (solo libreria standard): nessun I/O, nessun orologio.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence

PREFISSO_FOGLIA = b"\x00"
PREFISSO_NODO = b"\x01"
LUNGHEZZA_HASH = 32
#: Radice dell'albero vuoto: SHA-256 della stringa vuota.
RADICE_VUOTA = hashlib.sha256(b"").digest()

#: Funzione «radice del sottoalbero delle foglie [a, b)».
FunzioneSottoalbero = Callable[[int, int], bytes]


def hash_foglia(dati: bytes) -> bytes:
    """Hash di foglia RFC 6962: SHA-256(0x00 || dati)."""
    return hashlib.sha256(PREFISSO_FOGLIA + bytes(dati)).digest()


def hash_nodo(sinistro: bytes, destro: bytes) -> bytes:
    """Hash di nodo interno RFC 6962: SHA-256(0x01 || sinistro || destro)."""
    return hashlib.sha256(PREFISSO_NODO + sinistro + destro).digest()


def divisione(n: int) -> int:
    """La più grande potenza di 2 strettamente minore di ``n`` (n >= 2)."""
    if n < 2:
        raise ValueError(f"divisione definita per n >= 2, ricevuto {n}")
    return 1 << ((n - 1).bit_length() - 1)


def _controlla_hash(valore: bytes, nome: str) -> None:
    if not isinstance(valore, bytes | bytearray) or len(valore) != LUNGHEZZA_HASH:
        raise ValueError(f"{nome}: atteso un hash SHA-256 di {LUNGHEZZA_HASH} byte")


def radice(foglie: Sequence[bytes]) -> bytes:
    """MTH delle foglie (hash di foglia) in O(n) tempo e O(log n) memoria."""
    if not foglie:
        return RADICE_VUOTA
    # Pila di sottoalberi perfetti (dimensione, hash), dimensioni decrescenti
    pila: list[tuple[int, bytes]] = []
    for foglia in foglie:
        _controlla_hash(foglia, "foglia")
        dimensione, corrente = 1, bytes(foglia)
        while pila and pila[-1][0] == dimensione:
            _, sinistro = pila.pop()
            corrente = hash_nodo(sinistro, corrente)
            dimensione *= 2
        pila.append((dimensione, corrente))
    # Ripiegamento da destra: MTH dei sottoalberi perfetti residui
    _, corrente = pila.pop()
    while pila:
        _, sinistro = pila.pop()
        corrente = hash_nodo(sinistro, corrente)
    return corrente


# ── Algoritmi generici (RFC 6962 §2.1.1 e §2.1.2) ──────────────────────
# ``mth(a, b)`` restituisce la radice del sottoalbero delle foglie [a, b): la
# fornisce chi ha le foglie in memoria (questo modulo) o chi ha i nodi salvati
# (``libs.catena.albero``). Il flusso di controllo non dipende dai valori degli
# hash, quindi lo stesso algoritmo serve anche a elencare i nodi necessari.

def percorso_generico(m: int, a: int, b: int, mth: FunzioneSottoalbero) -> list[bytes]:
    """PATH(m, D[a:b]) della RFC 6962 §2.1.1, con ``m`` indice assoluto della foglia."""
    n = b - a
    if n <= 1:
        return []
    k = divisione(n)
    if m - a < k:
        return percorso_generico(m, a, a + k, mth) + [mth(a + k, b)]
    return percorso_generico(m, a + k, b, mth) + [mth(a, a + k)]


def sottoprova_generica(
    m: int, a: int, b: int, completo: bool, mth: FunzioneSottoalbero,
) -> list[bytes]:
    """SUBPROOF(m, D[a:b], completo) della RFC 6962 §2.1.2 (``m`` relativo ad ``a``)."""
    n = b - a
    if m == n:
        return [] if completo else [mth(a, b)]
    k = divisione(n)
    if m <= k:
        return sottoprova_generica(m, a, a + k, completo, mth) + [mth(a + k, b)]
    return sottoprova_generica(m - k, a + k, b, False, mth) + [mth(a, a + k)]


def controlla_indici_inclusione(indice: int, dimensione: int) -> None:
    """Errore esplicito se la foglia ``indice`` non esiste in un albero di ``dimensione``."""
    if dimensione < 1 or not 0 <= indice < dimensione:
        raise IndexError(
            f"Foglia {indice} fuori dall'albero di dimensione {dimensione}"
        )


def controlla_dimensioni_consistenza(n1: int, n2: int) -> None:
    """Errore esplicito se ``n1``/``n2`` non descrivono un albero cresciuto."""
    if n1 < 0 or n2 < 0 or n1 > n2:
        raise ValueError(
            f"Consistenza definita per 0 <= da <= a, ricevuti da={n1}, a={n2}"
        )


def _mth_da_foglie(foglie: Sequence[bytes]) -> FunzioneSottoalbero:
    def mth(a: int, b: int) -> bytes:
        return radice(foglie[a:b])
    return mth


def prova_inclusione(foglie: Sequence[bytes], indice: int) -> list[bytes]:
    """Percorso di audit (RFC 6962 §2.1.1) della foglia ``indice``, dal basso verso la radice."""
    controlla_indici_inclusione(indice, len(foglie))
    return percorso_generico(indice, 0, len(foglie), _mth_da_foglie(foglie))


def prova_consistenza(foglie: Sequence[bytes], n1: int, n2: int) -> list[bytes]:
    """Prova di consistenza (RFC 6962 §2.1.2) fra l'albero delle prime ``n1``
    foglie e quello delle prime ``n2`` (``foglie`` ne contiene almeno ``n2``).
    Vuota se ``n1`` è 0 o uguale a ``n2``."""
    controlla_dimensioni_consistenza(n1, n2)
    if n2 > len(foglie):
        raise ValueError(f"Servono almeno {n2} foglie, ricevute {len(foglie)}")
    if n1 in (0, n2):
        return []
    return sottoprova_generica(n1, 0, n2, True, _mth_da_foglie(foglie))


# ── Verifiche (RFC 9162 §2.1.3.2 e §2.1.4.2) ───────────────────────────

def radice_da_inclusione(
    hash_della_foglia: bytes, indice: int, dimensione: int, percorso: Sequence[bytes],
) -> bytes:
    """Radice ricalcolata dal percorso di audit; ValueError se il percorso non è
    della lunghezza giusta per (indice, dimensione)."""
    controlla_indici_inclusione(indice, dimensione)
    _controlla_hash(hash_della_foglia, "hash della foglia")
    fn, sn = indice, dimensione - 1
    r = bytes(hash_della_foglia)
    for p in percorso:
        _controlla_hash(p, "elemento del percorso")
        if sn == 0:
            raise ValueError("Percorso di inclusione troppo lungo")
        if fn & 1 or fn == sn:
            r = hash_nodo(p, r)
            if not fn & 1:
                while fn != 0 and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = hash_nodo(r, p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        raise ValueError("Percorso di inclusione troppo corto")
    return r


def verifica_inclusione(
    hash_della_foglia: bytes,
    indice: int,
    dimensione: int,
    percorso: Sequence[bytes],
    radice_attesa: bytes,
) -> bool:
    """True se la foglia (hash di foglia) è la numero ``indice`` dell'albero di
    ``dimensione`` foglie con radice ``radice_attesa``."""
    try:
        return radice_da_inclusione(hash_della_foglia, indice, dimensione, percorso) \
            == bytes(radice_attesa)
    except (ValueError, IndexError, TypeError):
        return False


def verifica_consistenza(
    n1: int, n2: int, radice1: bytes, radice2: bytes, percorso: Sequence[bytes],
) -> bool:
    """True se l'albero (n2, radice2) estende l'albero (n1, radice1) senza
    riscriverne le foglie (RFC 9162 §2.1.4.2).

    Casi limite: n1 == n2 → percorso vuoto e radici uguali; n1 == 0 → percorso
    vuoto e radice1 uguale alla radice dell'albero vuoto."""
    try:
        controlla_dimensioni_consistenza(n1, n2)
        for valore in (radice1, radice2, *percorso):
            _controlla_hash(valore, "hash")
    except (ValueError, TypeError):
        return False
    if n1 == n2:
        return not percorso and bytes(radice1) == bytes(radice2)
    if n1 == 0:
        return not percorso and bytes(radice1) == RADICE_VUOTA
    if not percorso:
        return False
    cammino = [bytes(x) for x in percorso]
    if n1 & (n1 - 1) == 0:  # n1 potenza di 2: la sua radice apre il percorso
        cammino.insert(0, bytes(radice1))
    fn, sn = n1 - 1, n2 - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = cammino[0]
    for c in cammino[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = hash_nodo(c, fr)
            sr = hash_nodo(c, sr)
            if not fn & 1:
                while fn != 0 and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = hash_nodo(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == bytes(radice1) and sr == bytes(radice2)


# ── Formati JSON (esadecimale minuscolo) ───────────────────────────────

def esadecimale(valori: Sequence[bytes]) -> list[str]:
    """Hash in esadecimale minuscolo, come nei formati JSON delle prove (K1)."""
    return [bytes(v).hex() for v in valori]


def da_esadecimale(valori: Sequence[str]) -> list[bytes]:
    """Inverso di ``esadecimale``; ValueError se un valore non è un SHA-256 esadecimale."""
    risultato: list[bytes] = []
    for v in valori:
        try:
            dati = bytes.fromhex(v)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Hash non esadecimale: {v!r}") from exc
        _controlla_hash(dati, "hash")
        risultato.append(dati)
    return risultato


__all__ = [
    "LUNGHEZZA_HASH", "PREFISSO_FOGLIA", "PREFISSO_NODO", "RADICE_VUOTA",
    "controlla_dimensioni_consistenza", "controlla_indici_inclusione",
    "da_esadecimale", "divisione", "esadecimale", "hash_foglia", "hash_nodo",
    "percorso_generico", "prova_consistenza", "prova_inclusione", "radice",
    "radice_da_inclusione", "sottoprova_generica", "verifica_consistenza",
    "verifica_inclusione",
]
