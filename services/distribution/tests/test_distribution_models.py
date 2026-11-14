# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test modelli e logica distribution service.
Riferimento piano: F2.1.2
"""
import hashlib

import pytest
from app.models.distribution import DocumentoDistribuito, RichiestaDistribuzione, TipoPdD
from app.services.s3_client import compute_pdd_hash
from pydantic import ValidationError


class TestTipoPdD:
    def test_tutti_i_tipi_definiti(self):
        tipi = [t.value for t in TipoPdD]
        assert "COMPLETO" in tipi
        assert "ESTRATTO" in tipi
        assert "ESIBIZIONE" in tipi
        assert "MIGRAZIONE" in tipi
        assert len(tipi) == 4

    def test_tipo_completo_non_richiede_file(self):
        req = RichiestaDistribuzione(
            aip_id="00000000-0000-0000-0000-000000000001",
            tipo=TipoPdD.COMPLETO,
            motivo="Richiesta archivio",
            richiedente_id="user-123",
        )
        assert req.tipo == TipoPdD.COMPLETO
        assert req.file_richiesti is None

    def test_tipo_estratto_con_file(self):
        req = RichiestaDistribuzione(
            aip_id="00000000-0000-0000-0000-000000000001",
            tipo=TipoPdD.ESTRATTO,
            motivo="Richiesta specifica",
            richiedente_id="user-123",
            file_richiesti=["fattura.pdf", "allegato.xml"],
        )
        assert len(req.file_richiesti) == 2

    def test_motivo_troppo_corto(self):
        with pytest.raises(ValidationError):
            RichiestaDistribuzione(
                aip_id="00000000-0000-0000-0000-000000000001",
                tipo=TipoPdD.COMPLETO,
                motivo="x",
                richiedente_id="u",
            )

    def test_url_expiry_range_valido(self):
        req = RichiestaDistribuzione(
            aip_id="00000000-0000-0000-0000-000000000001",
            tipo=TipoPdD.ESIBIZIONE,
            motivo="Esibizione per ufficio",
            richiedente_id="user-123",
            url_expiry_seconds=1800,
        )
        assert req.url_expiry_seconds == 1800

    def test_url_expiry_troppo_basso(self):
        with pytest.raises(ValidationError):
            RichiestaDistribuzione(
                aip_id="00000000-0000-0000-0000-000000000001",
                tipo=TipoPdD.ESIBIZIONE,
                motivo="Test esibizione breve",
                richiedente_id="user-123",
                url_expiry_seconds=30,  # < 60 → invalido
            )


class TestPdDHash:
    def test_hash_deterministico(self):
        hashes = ["abc123", "def456", "ghi789"]
        h1 = compute_pdd_hash(hashes)
        h2 = compute_pdd_hash(hashes)
        assert h1 == h2

    def test_hash_indipendente_da_ordine(self):
        """L'ordinamento garantisce determinismo indipendente dall'ordine."""
        h1 = compute_pdd_hash(["a", "b", "c"])
        h2 = compute_pdd_hash(["c", "a", "b"])
        assert h1 == h2

    def test_hash_diverso_per_contenuti_diversi(self):
        h1 = compute_pdd_hash(["aip-1"])
        h2 = compute_pdd_hash(["aip-2"])
        assert h1 != h2

    def test_hash_lunghezza_sha256(self):
        h = compute_pdd_hash(["test-hash"])
        assert len(h) == 64


class TestDocumentoDistribuito:
    def test_documento_con_s3_key(self):
        doc = DocumentoDistribuito(
            nome_file="fattura.pdf",
            tipo="PRINCIPALE",
            formato_mime="application/pdf",
            hash_sha256=hashlib.sha256(b"content").hexdigest(),
            dimensione_bytes=1024,
            s3_key="tenant-1/aip-1/fattura.pdf",
        )
        assert doc.s3_key is not None
        assert doc.signed_url is None

    def test_documento_con_signed_url(self):
        doc = DocumentoDistribuito(
            nome_file="fattura.pdf",
            tipo="ESIBIZIONE",
            formato_mime="application/pdf",
            hash_sha256=hashlib.sha256(b"content").hexdigest(),
            dimensione_bytes=1024,
            signed_url="https://s3.example.com/signed?token=abc",
        )
        assert doc.signed_url is not None
        assert doc.s3_key is None
