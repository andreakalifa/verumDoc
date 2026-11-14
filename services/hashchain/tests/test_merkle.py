# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test Merkle Tree. Riferimento piano: F1.4"""
import hashlib

from app.services.merkle import compute_merkle_root


class TestMerkleRoot:
    def test_lista_vuota(self):
        root = compute_merkle_root([])
        assert root == hashlib.sha256(b"").hexdigest()

    def test_singolo_elemento(self, sample_hashes):
        """RFC 6962: la radice di una sola foglia è l'hash di foglia SHA-256(0x00 || dato)."""
        root = compute_merkle_root([sample_hashes[0]])
        atteso = hashlib.sha256(b"\x00" + bytes.fromhex(sample_hashes[0])).hexdigest()
        assert root == atteso

    def test_due_elementi(self, sample_hashes):
        root = compute_merkle_root(sample_hashes[:2])
        assert isinstance(root, str)
        assert len(root) == 64

    def test_quattro_elementi(self, sample_hashes):
        root = compute_merkle_root(sample_hashes)
        assert len(root) == 64

    def test_deterministico(self, sample_hashes):
        r1 = compute_merkle_root(sample_hashes)
        r2 = compute_merkle_root(sample_hashes)
        assert r1 == r2

    def test_ordine_conta(self, sample_hashes):
        r1 = compute_merkle_root(sample_hashes)
        r2 = compute_merkle_root(list(reversed(sample_hashes)))
        assert r1 != r2

    def test_numero_dispari(self, sample_hashes):
        """Con 3 elementi l'albero si divide 2+1 (RFC 6962), senza duplicare l'ultima foglia."""
        root = compute_merkle_root(sample_hashes[:3])
        assert len(root) == 64

    def test_root_diversa_per_dati_diversi(self):
        h1 = [hashlib.sha256(b"a").hexdigest()]
        h2 = [hashlib.sha256(b"b").hexdigest()]
        assert compute_merkle_root(h1) != compute_merkle_root(h2)


class TestBlockSealer:
    def test_compute_block_hash(self):
        from app.services.block_sealer import compute_block_hash
        h = compute_block_hash(1, "prev_hash_0000", "merkle_root_0000")
        assert len(h) == 64
        assert isinstance(h, str)

    def test_block_hash_deterministico(self):
        from app.services.block_sealer import compute_block_hash
        h1 = compute_block_hash(5, "prev", "merkle")
        h2 = compute_block_hash(5, "prev", "merkle")
        assert h1 == h2

    def test_block_hash_diverso_per_numero_diverso(self):
        from app.services.block_sealer import compute_block_hash
        h1 = compute_block_hash(1, "prev", "merkle")
        h2 = compute_block_hash(2, "prev", "merkle")
        assert h1 != h2

    def test_block_hash_diverso_per_prev_diverso(self):
        from app.services.block_sealer import compute_block_hash
        h1 = compute_block_hash(1, "prev_a", "merkle")
        h2 = compute_block_hash(1, "prev_b", "merkle")
        assert h1 != h2
