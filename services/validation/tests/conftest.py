# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixtures per test F1.2 Validation Service."""
from __future__ import annotations

import os

# Ambiente esplicito: il default delle Settings è «production» (revisione M5).
os.environ["ENV"] = "test"
# Revisione R08 (X11): Siegfried non disponibile è un errore di servizio, non un
# ripiego silenzioso: le suite usano Siegfried reale se c'è, altrimenti la firma
# binaria scelta esplicitamente
if os.environ.get("VD_TEST_SIEGFRIED_BIN"):
    os.environ.setdefault("SIEGFRIED_BIN", os.environ["VD_TEST_SIEGFRIED_BIN"])
    os.environ.setdefault("SIEGFRIED_HOME", os.environ.get("VD_TEST_SIEGFRIED_HOME", ""))
else:
    os.environ.setdefault("FORMATI_RICONOSCITORE", "firma_binaria")
import hashlib

import pytest


@pytest.fixture
def pdv_id() -> str:
    return "00000000-0000-0000-0000-000000000042"


@pytest.fixture
def tenant_id() -> str:
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def file_pdf() -> bytes:
    return b"%PDF-1.4 test content for verumdoc validation"


@pytest.fixture
def indice_valido(file_pdf) -> dict:
    h = hashlib.sha256(file_pdf).hexdigest()
    return {
        "classe_documentale": "fattura",
        "chiave": {"numero": "FT-2026-001", "anno": "2026", "registro": "FATTURE"},
        "documenti": [{
            "id": "doc-001",
            "tipo": "PRINCIPALE",
            "nome_file": "fattura.pdf",
            "formato": "application/pdf",
            "hash": {"impronta": h, "algoritmo": "SHA-256"},
        }],
    }
