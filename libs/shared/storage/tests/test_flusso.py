# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Caricamento in streaming sullo staging (libs/shared/storage/flusso.py) con S3 simulato (moto):
oggetto singolo, multipart, limite, annullamento su errore, impronte calcolate in transito."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from libs.shared.storage import flusso

MB = 1024 * 1024
PARTE = flusso.PARTE_MINIMA_S3


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="staging")
        yield client


async def _blocchi(dati: bytes, dimensione: int = MB):
    for i in range(0, len(dati), dimensione):
        yield dati[i : i + dimensione]


def _aperti(s3) -> list:
    return s3.list_multipart_uploads(Bucket="staging").get("Uploads", [])


async def test_carica_flusso_piccolo_oggetto_singolo_con_impronte(s3):
    dati = b"%PDF-1.7\n" + b"x" * 1000
    esito = await flusso.carica_flusso(
        _blocchi(dati, 100),
        s3=s3,
        bucket="staging",
        chiave="v/a.pdf",
        content_type="application/pdf",
    )
    assert not esito.multipart and esito.dimensione == len(dati)
    assert esito.impronte == {
        "SHA-256": hashlib.sha256(dati).hexdigest(),
        "SHA-512": hashlib.sha512(dati).hexdigest(),
        "SHA3-256": hashlib.sha3_256(dati).hexdigest(),
    }
    assert esito.testa == dati[: flusso.BYTE_TESTA_PREDEFINITI]
    assert s3.get_object(Bucket="staging", Key="v/a.pdf")["Body"].read() == dati


async def test_carica_flusso_grande_multipart_contenuto_identico(s3):
    dati = bytes(range(256)) * (11 * MB // 256) + b"coda"
    esito = await flusso.carica_flusso(
        _blocchi(dati), s3=s3, bucket="staging", chiave="v/grande.bin", dimensione_parte=PARTE
    )
    assert esito.multipart and esito.parti == 3 and esito.dimensione == len(dati)
    assert esito.impronte["SHA-256"] == hashlib.sha256(dati).hexdigest()
    assert len(esito.testa) == flusso.BYTE_TESTA_PREDEFINITI
    letto = s3.get_object(Bucket="staging", Key="v/grande.bin")["Body"].read()
    assert hashlib.sha256(letto).hexdigest() == esito.impronte["SHA-256"]
    assert _aperti(s3) == []


async def test_carica_flusso_vuoto_oggetto_di_zero_byte(s3):
    esito = await flusso.carica_flusso(_blocchi(b""), s3=s3, bucket="staging", chiave="v/vuoto")
    assert esito.dimensione == 0 and esito.testa == b""
    assert s3.head_object(Bucket="staging", Key="v/vuoto")["ContentLength"] == 0


async def test_carica_flusso_oltre_limite_si_ferma_e_annulla_il_multipart(s3):
    letti: list[int] = []

    async def sorgente():
        for i in range(30):  # 30 MB disponibili: non devono essere letti tutti
            letti.append(i)
            yield b"y" * MB

    with pytest.raises(flusso.FlussoOltreLimite) as err:
        await flusso.carica_flusso(
            sorgente(),
            s3=s3,
            bucket="staging",
            chiave="v/troppo",
            dimensione_parte=PARTE,
            limite_byte=12 * MB,
        )
    assert err.value.limite_byte == 12 * MB and len(letti) == 13
    assert _aperti(s3) == []
    assert s3.list_objects_v2(Bucket="staging").get("KeyCount") == 0


async def test_carica_flusso_oltre_limite_piccolo_nessun_oggetto(s3):
    with pytest.raises(flusso.FlussoOltreLimite):
        await flusso.carica_flusso(
            _blocchi(b"z" * 100, 10), s3=s3, bucket="staging", chiave="v/p", limite_byte=50
        )
    assert s3.list_objects_v2(Bucket="staging").get("KeyCount") == 0


async def test_carica_flusso_errore_della_sorgente_annulla_il_multipart(s3):
    async def sorgente():
        yield b"a" * (6 * MB)
        raise ConnectionResetError("client disconnesso")

    with pytest.raises(ConnectionResetError):
        await flusso.carica_flusso(
            sorgente(), s3=s3, bucket="staging", chiave="v/rotto", dimensione_parte=PARTE
        )
    assert _aperti(s3) == []


async def test_carica_flusso_bucket_assente_errore_storage(s3):
    with pytest.raises(flusso.ErroreStorageFlusso):
        await flusso.carica_flusso(_blocchi(b"abc"), s3=s3, bucket="assente", chiave="v/x")


async def test_blocchi_da_lettore():
    class Lettore:
        def __init__(self):
            self.dati = b"0123456789"

        async def read(self, n=-1):
            parte, self.dati = self.dati[:n], self.dati[n:]
            return parte

    assert [b async for b in flusso.blocchi_da_lettore(Lettore(), 4)] == [b"0123", b"4567", b"89"]


async def test_dimensione_oggetto_esistente_e_assente(s3):
    s3.put_object(Bucket="staging", Key="c/1", Body=b"12345")
    assert await flusso.dimensione_oggetto(s3, "staging", "c/1") == 5
    assert await flusso.dimensione_oggetto(s3, "staging", "c/2") is None


async def test_elimina_oggetti(s3):
    for k in ("a", "b"):
        s3.put_object(Bucket="staging", Key=k, Body=b"1")
    await flusso.elimina_oggetti(s3, "staging", ["a", "b"])
    assert s3.list_objects_v2(Bucket="staging").get("KeyCount") == 0


async def test_annulla_multipart_incompleti_solo_vecchi_e_sotto_prefisso(s3):
    s3.create_multipart_upload(Bucket="staging", Key="versamenti/t/p/0000-a")
    s3.create_multipart_upload(Bucket="staging", Key="caricamenti/t/c/1")
    # moto data ogni multipart al 2010-11-10: «passato» è prima, «futuro» dopo
    futuro = datetime.now(UTC) + timedelta(hours=1)
    passato = datetime(2001, 1, 1, tzinfo=UTC)
    assert (
        await flusso.annulla_multipart_incompleti(
            s3, "staging", prefisso="versamenti/", iniziati_prima_di=passato
        )
        == []
    )
    prova = await flusso.annulla_multipart_incompleti(
        s3, "staging", prefisso="versamenti/", iniziati_prima_di=futuro, prova=True
    )
    assert prova == ["versamenti/t/p/0000-a"] and len(_aperti(s3)) == 2
    await flusso.annulla_multipart_incompleti(
        s3, "staging", prefisso="versamenti/", iniziati_prima_di=futuro
    )
    assert [u["Key"] for u in _aperti(s3)] == ["caricamenti/t/c/1"]
