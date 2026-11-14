# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Tabella unica degli algoritmi d'impronta (libs/shared/impronte.py)."""
from __future__ import annotations

import hashlib
import re

import pytest

from libs.shared.impronte import (
    ALGORITMI_IMPRONTA,
    PATTERN_ALGORITMI,
    calcola_impronta,
    funzione_impronta,
)


def test_algoritmi_ammessi_e_tabella_non_modificabile() -> None:
    assert list(ALGORITMI_IMPRONTA) == ["SHA-256", "SHA-512", "SHA3-256"]
    with pytest.raises(TypeError):
        ALGORITMI_IMPRONTA["MD5"] = hashlib.md5  # type: ignore[index]


@pytest.mark.parametrize("nome,atteso", [
    ("SHA-256", hashlib.sha256(b"x").hexdigest()),
    ("sha-512", hashlib.sha512(b"x").hexdigest()),
    (" SHA3-256 ", hashlib.sha3_256(b"x").hexdigest()),
])
def test_calcola_impronta(nome: str, atteso: str) -> None:
    assert calcola_impronta(nome, b"x") == atteso


@pytest.mark.parametrize("nome", ["MD5", "SHA-1", "", None, "SHA256"])
def test_algoritmo_non_ammesso(nome) -> None:
    assert funzione_impronta(nome) is None
    with pytest.raises(ValueError):
        calcola_impronta(nome or "", b"x")


def test_pattern_coerente_con_la_tabella() -> None:
    for nome in ALGORITMI_IMPRONTA:
        assert re.fullmatch(PATTERN_ALGORITMI, nome)
    assert not re.fullmatch(PATTERN_ALGORITMI, "SHA-1")
    assert not re.fullmatch(PATTERN_ALGORITMI, "SHA3256")
