# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Albero di Merkle della catena delle impronte VerumDoc.

Servizio: hashchain. Riferimento: VD-C-018 (R15), REGOLE_SVILUPPO §12.8.
Riferimento tecnico: RFC 6962 §2.1 (Certificate Transparency), «Merkle Hash Trees».

Regole dell'albero (formato blocco 2):
  - foglia  = SHA-256(0x00 || impronta binaria dell'AIP)
  - nodo    = SHA-256(0x01 || figlio_sinistro || figlio_destro)
  - albero vuoto = SHA-256("")
  - con n foglie non potenza di 2 si divide al più grande k = 2^i < n
    (MTH ricorsivo della RFC 6962): l'ultima foglia NON viene mai duplicata,
    quindi root([a,b,c]) != root([a,b,c,c]) (CVE-2012-2459 chiusa) e una
    foglia non può essere scambiata per un nodo interno (second-preimage).

Le impronte in ingresso sono stringhe esadecimali SHA-256 (64 caratteri).
L'ordine delle foglie è quello ricevuto: chi chiama deve fornire un ordine
totale e documentato (§12.8).

La funzione ``compute_merkle_root_v1_legacy`` riproduce l'algoritmo del
formato 1 (duplicazione dell'ultima foglia, concatenazione di stringhe) e
serve SOLO a verificare i blocchi già sigillati con quel formato: i blocchi
conservati non si riscrivono (§10.1).
"""
from __future__ import annotations

import hashlib

PREFISSO_FOGLIA = b"\x00"
PREFISSO_NODO = b"\x01"
LATO_SINISTRO = "left"
LATO_DESTRO = "right"


def _dati_foglia(impronta_hex: str) -> bytes:
    """Converte l'impronta esadecimale SHA-256 di un AIP nei suoi 32 byte."""
    try:
        dati = bytes.fromhex(impronta_hex)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Impronta non esadecimale: {impronta_hex!r}") from exc
    if len(dati) != 32:
        raise ValueError(
            f"Impronta SHA-256 attesa (32 byte), ricevuti {len(dati)} byte"
        )
    return dati


def hash_foglia(impronta_hex: str) -> bytes:
    """Hash di foglia RFC 6962: SHA-256(0x00 || dato)."""
    return hashlib.sha256(PREFISSO_FOGLIA + _dati_foglia(impronta_hex)).digest()


def hash_nodo(sinistro: bytes, destro: bytes) -> bytes:
    """Hash di nodo interno RFC 6962: SHA-256(0x01 || sinistro || destro)."""
    return hashlib.sha256(PREFISSO_NODO + sinistro + destro).digest()


def _divisione(n: int) -> int:
    """Il più grande k potenza di 2 con k < n (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def _radice(foglie: list[bytes]) -> bytes:
    n = len(foglie)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return foglie[0]
    k = _divisione(n)
    return hash_nodo(_radice(foglie[:k]), _radice(foglie[k:]))


def compute_merkle_root(hashes: list[str]) -> str:
    """
    Radice di Merkle (esadecimale) secondo RFC 6962.

    Solleva ValueError se un'impronta non è un SHA-256 esadecimale.
    """
    return _radice([hash_foglia(h) for h in hashes]).hex()


def _percorso(indice: int, foglie: list[bytes]) -> list[tuple[str, str]]:
    n = len(foglie)
    if n <= 1:
        return []
    k = _divisione(n)
    if indice < k:
        return _percorso(indice, foglie[:k]) + [(_radice(foglie[k:]).hex(), LATO_DESTRO)]
    return _percorso(indice - k, foglie[k:]) + [(_radice(foglie[:k]).hex(), LATO_SINISTRO)]


def genera_prova_inclusione(hashes: list[str], indice: int) -> list[tuple[str, str]]:
    """
    Prova di inclusione (audit path RFC 6962 §2.1.1) della foglia ``indice``.

    Restituisce la lista dal basso verso la radice di (impronta_fratello_hex, lato),
    con lato "left" se il fratello sta a sinistra, "right" se sta a destra.
    """
    if not 0 <= indice < len(hashes):
        raise IndexError(f"Indice foglia {indice} fuori dall'intervallo 0..{len(hashes) - 1}")
    return _percorso(indice, [hash_foglia(h) for h in hashes])


def verify_merkle_proof(
    leaf_hash: str,
    proof: list[tuple[str, str]],
    merkle_root: str,
) -> bool:
    """
    Verifica che l'impronta ``leaf_hash`` (impronta dell'AIP, non l'hash di foglia)
    sia inclusa nell'albero con radice ``merkle_root``.
    """
    try:
        corrente = hash_foglia(leaf_hash)
        for fratello_hex, lato in proof:
            fratello = bytes.fromhex(fratello_hex)
            if lato == LATO_SINISTRO:
                corrente = hash_nodo(fratello, corrente)
            elif lato == LATO_DESTRO:
                corrente = hash_nodo(corrente, fratello)
            else:
                return False
    except ValueError:
        return False
    return corrente.hex() == merkle_root


# ── Formato 1 (solo verifica dei blocchi storici) ─────────────────────

def compute_merkle_root_v1_legacy(hashes: list[str]) -> str:
    """
    Algoritmo del formato blocco 1 (difettoso: duplica l'ultima foglia e non
    separa foglie e nodi). Usato SOLO per verificare i blocchi già sigillati
    in formato 1; mai per sigillare.
    """
    if not hashes:
        return hashlib.sha256(b"").hexdigest()
    if len(hashes) == 1:
        return hashes[0]
    livello = list(hashes)
    while len(livello) > 1:
        if len(livello) % 2 == 1:
            livello.append(livello[-1])
        livello = [
            hashlib.sha256((livello[i] + livello[i + 1]).encode("utf-8")).hexdigest()
            for i in range(0, len(livello), 2)
        ]
    return livello[0]
