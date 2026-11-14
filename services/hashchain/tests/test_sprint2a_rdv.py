# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/hashchain/tests/test_sprint2a_rdv.py
Sprint 2A FIX-011: regression test — RdV Definitiva per singolo AIP.

Verifica che:
  - _build_rdv_definitiva genera XML con tutti i campi obbligatori
  - Ogni AIP ha la propria RdV (non batch)
  - RdV contiene il tipo DEFINITIVA
  - RdV contiene URN, hash, numero blocco
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.services.block_sealer import _build_rdv_definitiva, compute_block_hash


class TestRdVDefinitiva:
    """FIX-011: RdV Definitiva generata per ogni singolo AIP."""

    def _make_rdv(self, aip_id: str = "aip-test-001") -> str:
        return _build_rdv_definitiva(
            aip_id=aip_id,
            urn="urn:IT:00000000000:FATTURA:2026:0000000001",
            hash_sha256="abc123" * 8,
            numero_blocco=42,
            merkle_root="merkle_root_test",
            hash_blocco="hash_blocco_test",
            data_conservazione="2026-03-20T12:00:00+00:00",
        )

    def test_rdv_is_valid_xml(self):
        """RdV deve essere XML valido parsabile."""
        rdv = self._make_rdv()
        root = ET.fromstring(rdv)
        assert root.tag == "RicevutaVersamento"

    def test_rdv_tipo_definitiva(self):
        """RdV deve avere tipo=DEFINITIVA."""
        rdv = self._make_rdv()
        root = ET.fromstring(rdv)
        assert root.get("tipo") == "DEFINITIVA"

    def test_rdv_contains_aip_id(self):
        """RdV deve contenere l'ID dell'AIP specifico."""
        rdv = self._make_rdv("aip-specific-123")
        root = ET.fromstring(rdv)
        elem = root.find("IdentificativoAIP")
        assert elem is not None
        assert elem.text == "aip-specific-123"

    def test_rdv_contains_urn(self):
        """RdV deve contenere l'URN dell'AIP."""
        rdv = self._make_rdv()
        root = ET.fromstring(rdv)
        elem = root.find("URN")
        assert elem is not None
        assert elem.text.startswith("urn:IT:")

    def test_rdv_contains_hash(self):
        """RdV deve contenere l'hash SHA-256 dell'AIP."""
        rdv = self._make_rdv()
        root = ET.fromstring(rdv)
        elem = root.find("HashAIP")
        assert elem is not None
        assert len(elem.text) > 10

    def test_rdv_contains_blocco_info(self):
        """RdV deve contenere numero blocco, merkle root, hash blocco."""
        rdv = self._make_rdv()
        root = ET.fromstring(rdv)
        assert root.find("NumeroBloccoHashchain").text == "42"
        assert root.find("MerkleRoot").text == "merkle_root_test"
        assert root.find("HashBlocco").text == "hash_blocco_test"

    def test_rdv_contains_conservatore(self):
        """RdV deve contenere ID conservatore."""
        rdv = self._make_rdv()
        root = ET.fromstring(rdv)
        elem = root.find("ConservatoreId")
        assert elem is not None
        assert "VerumDoc" in elem.text

    def test_rdv_not_batch(self):
        """RdV NON deve avere aip_id='BATCH' (FIX-011 regression)."""
        rdv = self._make_rdv("aip-real-001")
        assert "BATCH" not in rdv

    def test_rdv_different_per_aip(self):
        """Due AIP diversi devono generare RdV diverse."""
        rdv1 = self._make_rdv("aip-001")
        rdv2 = self._make_rdv("aip-002")
        assert rdv1 != rdv2
        assert "aip-001" in rdv1
        assert "aip-002" in rdv2


class TestBlockHash:
    """Verifica correttezza compute_block_hash."""

    def test_deterministic(self):
        h1 = compute_block_hash(1, "prev", "merkle")
        h2 = compute_block_hash(1, "prev", "merkle")
        assert h1 == h2

    def test_different_input_different_hash(self):
        h1 = compute_block_hash(1, "prev", "merkle1")
        h2 = compute_block_hash(1, "prev", "merkle2")
        assert h1 != h2
