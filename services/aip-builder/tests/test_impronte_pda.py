# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica delle impronte sui byte e identificativi stabili (VD-C-008, VD-C-009).
"""
from __future__ import annotations

import hashlib

import pytest
from app.services.aip_engine import id_pda_per_pdv, nome_sicuro, verifica_impronta

from libs.shared.kafka.consumatore_affidabile import ErrorePermanente

DATI = b"%PDF-1.7 contenuto"


@pytest.mark.parametrize("algoritmo,funzione", [
    ("SHA-256", hashlib.sha256), ("SHA-512", hashlib.sha512), ("SHA3-256", hashlib.sha3_256),
])
def test_verifica_impronta_ogni_algoritmo_ammesso_corretta(algoritmo, funzione):
    assert verifica_impronta(DATI, algoritmo, funzione(DATI).hexdigest()) == funzione(
        DATI).hexdigest()


@pytest.mark.parametrize("algoritmo", ["SHA-256", "SHA-512", "SHA3-256"])
def test_verifica_impronta_ogni_algoritmo_ammesso_falsa_rifiutata(algoritmo):
    with pytest.raises(ErrorePermanente) as err:
        verifica_impronta(DATI, algoritmo, "ab" * 32)
    assert err.value.codice == "AIP-INTEGRITA-002"


def test_verifica_impronta_algoritmo_non_ammesso_rifiutato():
    with pytest.raises(ErrorePermanente) as err:
        verifica_impronta(DATI, "MD5", hashlib.md5(DATI, usedforsecurity=False).hexdigest())
    assert err.value.codice == "AIP-INTEGRITA-001"


def test_verifica_impronta_maiuscole_accettate():
    assert verifica_impronta(DATI, "sha-256", hashlib.sha256(DATI).hexdigest().upper())


def test_id_pda_per_pdv_stabile_e_distinto():
    assert id_pda_per_pdv("p1") == id_pda_per_pdv("p1")
    assert id_pda_per_pdv("p1") != id_pda_per_pdv("p2")


def test_nome_sicuro_niente_percorsi():
    assert "/" not in nome_sicuro("../../etc/passwd")
    assert nome_sicuro("") == "file"
