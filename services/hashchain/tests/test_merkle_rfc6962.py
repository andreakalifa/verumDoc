# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test dell'albero di Merkle RFC 6962 e della non collisione (VD-C-018).

L'oracolo è la definizione della RFC 6962 §2.1 scritta qui per esteso con
hashlib (MTH ricorsivo con prefissi 0x00/0x01), indipendente dal codice.
"""
from __future__ import annotations

import hashlib

import pytest
from app.services.merkle import (
    compute_merkle_root,
    compute_merkle_root_v1_legacy,
    genera_prova_inclusione,
    hash_nodo,
    verify_merkle_proof,
)


def _h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


def _foglia(hexd: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(hexd)).digest()


def _nodo(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + a + b).digest()


A, B, C, D, E = (_h(x) for x in "abcde")


def test_compute_merkle_root_tre_foglie_formula_rfc6962():
    """MTH({a,b,c}) = nodo(nodo(a,b), foglia(c)) — divisione k=2, niente duplicazione."""
    atteso = _nodo(_nodo(_foglia(A), _foglia(B)), _foglia(C)).hex()
    assert compute_merkle_root([A, B, C]) == atteso


def test_compute_merkle_root_cinque_foglie_formula_rfc6962():
    """MTH di 5 foglie: k=4 a sinistra, la quinta foglia a destra."""
    sinistra = _nodo(_nodo(_foglia(A), _foglia(B)), _nodo(_foglia(C), _foglia(D)))
    assert compute_merkle_root([A, B, C, D, E]) == _nodo(sinistra, _foglia(E)).hex()


def test_compute_merkle_root_lista_vuota_hash_stringa_vuota():
    assert compute_merkle_root([]) == hashlib.sha256(b"").hexdigest()


def test_compute_merkle_root_ultima_foglia_duplicata_non_collide():
    """CVE-2012-2459: root([a,b,c]) deve differire da root([a,b,c,c])."""
    assert compute_merkle_root([A, B, C]) != compute_merkle_root([A, B, C, C])


def test_compute_merkle_root_v1_legacy_collide_documentato():
    """Il formato 1 storico collide davvero: per questo non si usa più per sigillare."""
    assert compute_merkle_root_v1_legacy([A, B, C]) == compute_merkle_root_v1_legacy([A, B, C, C])


def test_compute_merkle_root_nodo_interno_come_foglia_non_collide():
    """Second-preimage: presentare il nodo interno come foglia non riproduce la radice."""
    interno = hash_nodo(_foglia(A), _foglia(B)).hex()
    assert compute_merkle_root([interno]) != compute_merkle_root([A, B])


def test_compute_merkle_root_impronta_non_esadecimale_errore():
    with pytest.raises(ValueError):
        compute_merkle_root(["non-esadecimale"])


def test_compute_merkle_root_impronta_lunghezza_errata_errore():
    with pytest.raises(ValueError):
        compute_merkle_root(["ab" * 20])


@pytest.mark.parametrize("n", list(range(1, 10)))
def test_genera_prova_inclusione_ogni_foglia_verificata(n):
    foglie = [_h(f"aip-{i}") for i in range(n)]
    radice = compute_merkle_root(foglie)
    for i, foglia in enumerate(foglie):
        prova = genera_prova_inclusione(foglie, i)
        assert verify_merkle_proof(foglia, prova, radice)


def test_verify_merkle_proof_foglia_estranea_rifiutata():
    foglie = [A, B, C, D, E]
    radice = compute_merkle_root(foglie)
    prova = genera_prova_inclusione(foglie, 2)
    assert not verify_merkle_proof(_h("estranea"), prova, radice)


def test_verify_merkle_proof_prova_alterata_rifiutata():
    foglie = [A, B, C, D]
    radice = compute_merkle_root(foglie)
    prova = genera_prova_inclusione(foglie, 1)
    impronta, lato = prova[0]
    alterata = [("00" * 32, lato)] + prova[1:]
    assert not verify_merkle_proof(B, alterata, radice)
    assert impronta != "00" * 32


def test_verify_merkle_proof_lato_sconosciuto_rifiutato():
    assert not verify_merkle_proof(A, [(B, "centro")], compute_merkle_root([A, B]))


def test_genera_prova_inclusione_indice_fuori_intervallo_errore():
    with pytest.raises(IndexError):
        genera_prova_inclusione([A, B], 2)
