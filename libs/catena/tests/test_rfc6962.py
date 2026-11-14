# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prove di libs/catena/rfc6962.py e libs/catena/albero.py (contratto K4 di R04).

Vettori noti: radici e prove degli 8 dati di foglia di riferimento della
Certificate Transparency (stessi valori dei test di transparency-dev/merkle e
di google/certificate-transparency). Proprietà: inclusione e consistenza per
TUTTE le coppie fino a 64 foglie, prove alterate rifiutate, versione «da nodi
salvati» identica alla versione «da foglie».
"""
from __future__ import annotations

import hashlib

import pytest

from libs.catena import albero
from libs.catena.rfc6962 import (
    RADICE_VUOTA,
    da_esadecimale,
    esadecimale,
    hash_foglia,
    hash_nodo,
    prova_consistenza,
    prova_inclusione,
    radice,
    radice_da_inclusione,
    verifica_consistenza,
    verifica_inclusione,
)

# Dati di foglia di riferimento (esadecimale) e radici per 1…8 foglie
DATI_RIFERIMENTO = ["", "00", "10", "2021", "3031", "40414243",
                    "5051525354555657", "606162636465666768696a6b6c6d6e6f"]
RADICI_RIFERIMENTO = [
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]
FOGLIE_RIF = [hash_foglia(bytes.fromhex(d)) for d in DATI_RIFERIMENTO]


def _foglie(n: int) -> list[bytes]:
    return [hash_foglia(f"foglia-{i}".encode()) for i in range(n)]


def _mth_ricorsiva(foglie: list[bytes]) -> bytes:
    """Definizione letterale della RFC 6962 (oracolo interno, volutamente ingenuo)."""
    if not foglie:
        return hashlib.sha256(b"").digest()
    if len(foglie) == 1:
        return foglie[0]
    k = 1
    while k * 2 < len(foglie):
        k *= 2
    return hash_nodo(_mth_ricorsiva(foglie[:k]), _mth_ricorsiva(foglie[k:]))


# ── Vettori noti ─────────────────────────────────────────────────────

def test_radice_vuota_sha256_stringa_vuota():
    assert radice([]) == RADICE_VUOTA
    assert RADICE_VUOTA.hex() == \
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.mark.parametrize("n", range(1, 9))
def test_radice_vettori_noti_ct(n):
    """Radici dei vettori CT per 1…8 foglie."""
    assert radice(FOGLIE_RIF[:n]).hex() == RADICI_RIFERIMENTO[n - 1]


def test_prova_inclusione_vettori_noti():
    """Percorsi di audit noti (foglia 0 e 5 su 8, foglia 2 su 3)."""
    assert esadecimale(prova_inclusione(FOGLIE_RIF, 0)) == [
        "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
        "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
        "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4",
    ]
    assert esadecimale(prova_inclusione(FOGLIE_RIF[:3], 2)) == [
        "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    ]
    assert esadecimale(prova_inclusione(FOGLIE_RIF, 5)) == [
        "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
        "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
        "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    ]


def test_prova_consistenza_vettori_noti():
    """Prove di consistenza note: 1→8, 6→8, 2→5."""
    assert esadecimale(prova_consistenza(FOGLIE_RIF, 1, 8)) == [
        "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
        "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
        "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4",
    ]
    assert esadecimale(prova_consistenza(FOGLIE_RIF, 6, 8)) == [
        "0ebc5d3437fbe2db158b9f126a1d118e308181031d0a949f8dededebc558ef6a",
        "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
        "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    ]
    assert esadecimale(prova_consistenza(FOGLIE_RIF, 2, 5)) == [
        "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
        "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
    ]


# ── Proprietà fino a 64 foglie ───────────────────────────────────────

def test_radice_uguale_alla_definizione_ricorsiva_fino_a_130():
    foglie = _foglie(130)
    for n in range(131):
        assert radice(foglie[:n]) == _mth_ricorsiva(foglie[:n]), n


def test_inclusione_tutte_le_coppie_fino_a_64():
    """Per ogni n ≤ 64 e ogni foglia i < n: la prova si verifica, e non si verifica
    con indice, foglia o radice sbagliati."""
    foglie = _foglie(64)
    for n in range(1, 65):
        r = radice(foglie[:n])
        for i in range(n):
            prova = prova_inclusione(foglie[:n], i)
            assert verifica_inclusione(foglie[i], i, n, prova, r), (i, n)
            if n > 1:
                assert not verifica_inclusione(foglie[i], (i + 1) % n, n, prova, r), (i, n)
                assert not verifica_inclusione(foglie[(i + 1) % n], i, n, prova, r), (i, n)
            # La dimensione non è autenticata dal solo percorso (lo è dalla firma
            # del checkpoint che lega dimensione e radice): nessuna asserzione qui.
            assert not verifica_inclusione(foglie[i], i, n, prova, hash_foglia(b"x")), (i, n)


def test_consistenza_tutte_le_coppie_fino_a_64():
    """Per ogni 1 ≤ n1 ≤ n2 ≤ 64: la prova si verifica; con radici scambiate o
    dimensioni sbagliate no."""
    foglie = _foglie(64)
    radici = [radice(foglie[:n]) for n in range(65)]
    for n2 in range(1, 65):
        for n1 in range(1, n2 + 1):
            prova = prova_consistenza(foglie, n1, n2)
            assert verifica_consistenza(n1, n2, radici[n1], radici[n2], prova), (n1, n2)
            if n1 < n2:
                assert not verifica_consistenza(n1, n2, radici[n2], radici[n1], prova), (n1, n2)
                assert not verifica_consistenza(n1, n2, radici[n1], hash_foglia(b"x"), prova)
                if n1 > 1:
                    assert not verifica_consistenza(n1 - 1, n2, radici[n1 - 1], radici[n2],
                                                    prova), (n1, n2)


def test_consistenza_rileva_storia_riscritta():
    """Un registro che riscrive una foglia vecchia non produce una prova valida."""
    foglie = _foglie(20)
    riscritte = list(foglie)
    riscritte[3] = hash_foglia(b"riscritta")
    prova = prova_consistenza(riscritte, 10, 20)
    assert not verifica_consistenza(10, 20, radice(foglie[:10]), radice(riscritte), prova)


def test_ogni_elemento_alterato_della_prova_rifiutato():
    foglie = _foglie(37)
    r = radice(foglie)
    prova = prova_inclusione(foglie, 21)
    for j in range(len(prova)):
        alterata = list(prova)
        alterata[j] = bytes(b ^ 1 for b in alterata[j])
        assert not verifica_inclusione(foglie[21], 21, 37, alterata, r)
    cons = prova_consistenza(foglie, 13, 37)
    r13 = radice(foglie[:13])
    for j in range(len(cons)):
        alterata = list(cons)
        alterata[j] = bytes(b ^ 1 for b in alterata[j])
        assert not verifica_consistenza(13, 37, r13, r, alterata)


def test_prove_lunghezza_errata_rifiutate():
    foglie = _foglie(10)
    r = radice(foglie)
    prova = prova_inclusione(foglie, 4)
    assert not verifica_inclusione(foglie[4], 4, 10, prova[:-1], r)
    assert not verifica_inclusione(foglie[4], 4, 10, [*prova, prova[0]], r)
    cons = prova_consistenza(foglie, 3, 10)
    assert not verifica_consistenza(3, 10, radice(foglie[:3]), r, [*cons, cons[0]])
    assert not verifica_consistenza(3, 10, radice(foglie[:3]), r, [])


def test_casi_limite_consistenza():
    foglie = _foglie(5)
    r5 = radice(foglie)
    assert prova_consistenza(foglie, 5, 5) == []
    assert verifica_consistenza(5, 5, r5, r5, [])
    assert not verifica_consistenza(5, 5, r5, r5, [r5])
    assert prova_consistenza(foglie, 0, 5) == []
    assert verifica_consistenza(0, 5, RADICE_VUOTA, r5, [])
    assert not verifica_consistenza(0, 5, r5, r5, [])
    assert not verifica_consistenza(6, 5, r5, r5, [])
    with pytest.raises(ValueError):
        prova_consistenza(foglie, 6, 5)
    with pytest.raises(ValueError):
        prova_consistenza(foglie, 2, 6)


def test_indici_fuori_intervallo_errore_esplicito():
    foglie = _foglie(4)
    with pytest.raises(IndexError):
        prova_inclusione(foglie, 4)
    with pytest.raises(IndexError):
        prova_inclusione([], 0)
    assert not verifica_inclusione(foglie[0], 4, 4, [], radice(foglie))
    with pytest.raises(ValueError):
        radice_da_inclusione(foglie[0], 0, 4, [])


def test_hash_di_lunghezza_errata_rifiutati():
    with pytest.raises(ValueError):
        radice([b"corto"])
    assert not verifica_inclusione(b"corto", 0, 1, [], RADICE_VUOTA)
    with pytest.raises(ValueError):
        da_esadecimale(["zz"])
    with pytest.raises(ValueError):
        da_esadecimale(["ab"])
    assert da_esadecimale(esadecimale(FOGLIE_RIF)) == FOGLIE_RIF


def test_foglia_e_nodo_non_confondibili():
    """Separazione di dominio 0x00/0x01: un nodo interno non passa per foglia."""
    a, b = hash_foglia(b"a"), hash_foglia(b"b")
    assert hash_foglia(a + b) != hash_nodo(a, b)
    assert radice([a, b, b]) != radice([a, b, b, b])


# ── Albero da nodi salvati ───────────────────────────────────────────

def test_albero_da_nodi_uguale_ad_albero_da_foglie_fino_a_64():
    foglie = _foglie(64)
    nodi = albero.tutti_i_nodi(foglie)
    for n in range(0, 65):
        assert albero.radice_da_nodi(n, nodi) == radice(foglie[:n])
        for i in range(n):
            assert albero.prova_inclusione_da_nodi(i, n, nodi) == prova_inclusione(foglie[:n], i)
        for n1 in range(0, n + 1):
            assert albero.prova_consistenza_da_nodi(n1, n, nodi) == \
                prova_consistenza(foglie, n1, n)


def test_albero_nodi_necessari_logaritmici_e_sufficienti():
    """Per 100.000 foglie bastano O(log n) nodi; la sola mappa dei nodi elencati basta."""
    foglie = _foglie(1000)
    nodi = albero.tutti_i_nodi(foglie)
    for n, i, n1 in ((1000, 999, 1), (999, 500, 777), (513, 0, 512), (1000, 3, 999)):
        richiesti = albero.nodi_per_inclusione(i, n) | albero.nodi_per_radice(n) \
            | albero.nodi_per_consistenza(n1, n)
        assert len(richiesti) <= 4 * n.bit_length()
        parziale = {c: nodi[c] for c in richiesti}
        assert albero.prova_inclusione_da_nodi(i, n, parziale) == prova_inclusione(foglie[:n], i)
        assert albero.radice_da_nodi(n, parziale) == radice(foglie[:n])
        assert albero.prova_consistenza_da_nodi(n1, n, parziale) == \
            prova_consistenza(foglie, n1, n)
    assert len(albero.nodi_per_inclusione(70_001, 100_000)) <= 2 * 17


def test_aggiungi_foglie_a_lotti_uguale_a_costruzione_unica():
    """Crescita a lotti irregolari con la sola frontiera = albero costruito da zero."""
    foglie = _foglie(300)
    salvati: dict = {}
    n = 0
    for lotto in (1, 2, 5, 1, 64, 7, 100, 120):
        frontiera = {c: salvati[c] for c in albero.coordinate_frontiera(n)}
        nuovi = albero.aggiungi_foglie(n, frontiera, foglie[n:n + lotto])
        assert not set(nuovi) & set(salvati)  # append-only: nessun nodo riscritto
        salvati.update(nuovi)
        n += lotto
        assert albero.radice_da_nodi(n, salvati) == radice(foglie[:n])
    assert salvati == albero.tutti_i_nodi(foglie)
    assert len(salvati) == 2 * 300 - bin(300).count("1")


def test_aggiungi_foglie_senza_frontiera_errore():
    foglie = _foglie(3)
    with pytest.raises(albero.NodoMancante):
        albero.aggiungi_foglie(1, {}, foglie[1:])


def test_nodo_mancante_errore_esplicito():
    nodi = albero.tutti_i_nodi(_foglie(8))
    del nodi[(3, 0)]
    with pytest.raises(albero.NodoMancante) as errore:
        albero.radice_da_nodi(8, nodi)
    assert errore.value.coordinata == (3, 0)


def test_coordinate_frontiera():
    assert albero.coordinate_frontiera(0) == []
    assert albero.coordinate_frontiera(1) == [(0, 0)]
    assert albero.coordinate_frontiera(6) == [(2, 0), (1, 2)]
    assert albero.coordinate_frontiera(7) == [(2, 0), (1, 2), (0, 6)]
    assert albero.coordinate_frontiera(8) == [(3, 0)]
