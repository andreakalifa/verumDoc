# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixtures per test F1.3 AIP Builder."""
import os

# Ambiente esplicito: il default delle Settings è «production» (revisione M5).
os.environ["ENV"] = "test"

import hashlib

import pytest


@pytest.fixture
def pdv_id(): return "00000000-0000-0000-0000-000000000010"
@pytest.fixture
def aip_id(): return "00000000-0000-0000-0000-000000000011"
@pytest.fixture
def tenant_id(): return "00000000-0000-0000-0000-000000000001"

@pytest.fixture
def file_bytes(): return b"%PDF-1.4 test aip content"

@pytest.fixture
def documenti_list(file_bytes):
    h = hashlib.sha256(file_bytes).hexdigest()
    return [{"nome_file": "doc.pdf", "tipo": "PRINCIPALE",
             "formato": "application/pdf", "dimensione_bytes": len(file_bytes),
             "hash_sha256": h}]
