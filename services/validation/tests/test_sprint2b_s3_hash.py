# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/validation/tests/test_sprint2b_s3_hash.py
Sprint 2B FIX-015 → R08 VD-C-008 → R03 (b5): la verifica legge i file dallo
storage IN STREAMING per ricalcolare le impronte; se la lettura fallisce NON
prosegue (errore transitorio).

Test sul comportamento (REGOLE §23.1), con S3 simulato da moto.
"""
from __future__ import annotations

import hashlib

import pytest
from app.config import settings
from app.services.lettura_file import (
    FileOltreLimite,
    LettoreS3,
    RiferimentoFile,
    riferimenti_da_evento,
)
from app.validators.base import ErroreVerificaTransitorio
from moto import mock_aws


@pytest.fixture
def s3_finto(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        import boto3
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="pdv")
        yield client


def _rif(chiave: str, nome: str = "a.pdf") -> RiferimentoFile:
    return RiferimentoFile(nome_file=nome, bucket="pdv", chiave=chiave)


class _CorpoContato:
    """Avvolge il corpo boto3 e registra la dimensione dei blocchi consegnati."""

    def __init__(self, corpo, blocchi: list[int]) -> None:
        self.corpo, self.blocchi = corpo, blocchi

    def iter_chunks(self, n):
        for b in self.corpo.iter_chunks(n):
            self.blocchi.append(len(b))
            yield b

    def close(self):
        self.corpo.close()


class TestLettoreS3:
    async def test_impronte_calcolate_a_blocchi_senza_file_intero(self, s3_finto):
        """Il file si legge a blocchi della dimensione configurata: mai tutto in memoria."""
        contenuto = b"%PDF-1.7 " + bytes(range(256)) * 1000  # ~256 KB
        s3_finto.put_object(Bucket="pdv", Key="t/p/0000-a.pdf", Body=contenuto)
        blocchi: list[int] = []
        get_originale = s3_finto.get_object

        class ClientContato:
            def __getattr__(self, nome):
                return getattr(s3_finto, nome)

            def get_object(self, **kw):
                risposta = get_originale(**kw)
                return {**risposta, "Body": _CorpoContato(risposta["Body"], blocchi)}

        lettore = LettoreS3(ClientContato(), dimensione_blocco=64 * 1024)
        letto = await lettore.leggi(_rif("t/p/0000-a.pdf"), algoritmi=["SHA-256", "sha3-256"])
        assert letto.dimensione == len(contenuto)
        assert letto.impronte == {"SHA-256": hashlib.sha256(contenuto).hexdigest(),
                                  "SHA3-256": hashlib.sha3_256(contenuto).hexdigest()}
        assert len(blocchi) >= 4 and max(blocchi) <= 64 * 1024

    async def test_dimensione_dai_metadati(self, s3_finto):
        s3_finto.put_object(Bucket="pdv", Key="k", Body=b"12345")
        assert await LettoreS3(s3_finto).dimensione(_rif("k")) == 5

    async def test_lettura_interrotta_oltre_limite(self, s3_finto):
        s3_finto.put_object(Bucket="pdv", Key="k", Body=b"x" * 1000)
        with pytest.raises(FileOltreLimite) as exc:
            await LettoreS3(s3_finto, dimensione_blocco=100).leggi(
                _rif("k"), algoritmi=["SHA-256"], limite_byte=250)
        assert exc.value.letti == 300  # fermato al primo blocco oltre il limite

    async def test_oggetto_mancante_errore_transitorio(self, s3_finto):
        """VD-C-008: prima l'errore si ignorava e il passo impronte risultava superato."""
        with pytest.raises(ErroreVerificaTransitorio):
            await LettoreS3(s3_finto).leggi(_rif("inesistente"), algoritmi=["SHA-256"])
        with pytest.raises(ErroreVerificaTransitorio):
            await LettoreS3(s3_finto).dimensione(_rif("inesistente"))

    async def test_algoritmo_non_ammesso_rifiutato(self, s3_finto):
        s3_finto.put_object(Bucket="pdv", Key="k", Body=b"abc")
        with pytest.raises(ValueError):
            await LettoreS3(s3_finto).leggi(_rif("k"), algoritmi=["MD5"])


def test_riferimento_senza_chiave_saltato():
    """Riferimento incompleto: nessun file, lo Step 5 rifiuterà il documento."""
    assert riferimenti_da_evento([{"nome_file": "a.pdf", "s3_bucket": "pdv"}]) == {}


def test_riferimento_porta_dimensione_impronte_e_formato_del_versamento():
    rif = riferimenti_da_evento([{
        "nome_file": "a.pdf", "s3_bucket": "pdv", "s3_key": "k", "size_bytes": 10,
        "impronte": {"sha-256": "AB"}, "formato_rilevato": "application/pdf",
    }])["a.pdf"]
    assert (rif.dimensione_versata, dict(rif.impronte_versamento), rif.formato_rilevato) == (
        10, {"SHA-256": "ab"}, "application/pdf")


def test_config_senza_interruttore_verifica_impronte():
    """La verifica non si può spegnere: l'interruttore s3_verify_hash non esiste più."""
    assert not hasattr(settings, "s3_verify_hash")


def test_config_senza_limite_fisso_100mb():
    """R03: il limite fisso max_file_size (100 MB) non esiste più; il predefinito per i
    PdV senza fotografia è alto (5 GiB) e configurabile."""
    assert not hasattr(settings, "max_file_size")
    assert settings.validazione_max_file_mb == 5120
