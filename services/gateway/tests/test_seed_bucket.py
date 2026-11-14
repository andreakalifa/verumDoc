# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Seed di sviluppo: bucket dei PdA con Object Lock (VD-C-011, richiesta be-conformita)."""
from __future__ import annotations

import boto3
import pytest
from app.seed import _crea_bucket_sviluppo
from moto import mock_aws


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_AIP", "verumdoc-aip")
    monkeypatch.setenv("S3_BUCKET_PDV", "verumdoc-pdv")
    monkeypatch.setenv("S3_BUCKET_PDD", "verumdoc-pdd")
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


def test_bucket_aip_creato_con_object_lock(s3):
    esiti = _crea_bucket_sviluppo(s3)
    assert esiti == {"verumdoc-pdv": "creato", "verumdoc-aip": "creato",
                     "verumdoc-pdd": "creato"}
    lock = s3.get_object_lock_configuration(Bucket="verumdoc-aip")
    assert lock["ObjectLockConfiguration"]["ObjectLockEnabled"] == "Enabled"
    # Il bucket di transito dei versamenti resta senza lock (orfani eliminabili)
    with pytest.raises(s3.exceptions.ClientError):
        s3.get_object_lock_configuration(Bucket="verumdoc-pdv")
    # Idempotente
    assert _crea_bucket_sviluppo(s3) == {"verumdoc-pdv": "presente", "verumdoc-aip": "presente",
                                         "verumdoc-pdd": "presente"}
    # R03 i1: il bucket dei PdD (ZIP temporanei) è senza lock, come il transito
    with pytest.raises(s3.exceptions.ClientError):
        s3.get_object_lock_configuration(Bucket="verumdoc-pdd")


def test_bucket_aip_esistente_senza_lock_segnalato_non_riparato(s3):
    s3.create_bucket(Bucket="verumdoc-aip")
    esiti = _crea_bucket_sviluppo(s3)
    assert esiti["verumdoc-aip"] == "senza_object_lock"


def test_bucket_aip_accettato_dallo_scrittore_worm(s3):
    """Il bucket creato dal seed passa la verifica dello scrittore WORM condiviso."""
    from libs.shared.storage import worm

    _crea_bucket_sviluppo(s3)
    worm._bucket_verificati.discard("verumdoc-aip")
    worm.verifica_bucket_conservativo(s3, "verumdoc-aip", produzione=False)


def test_bucket_pdd_con_scadenza_degli_oggetti(s3):
    """Il seed imposta la scadenza degli ZIP dei PdD (prima la impostava minio/mc)."""
    _crea_bucket_sviluppo(s3)
    regole = s3.get_bucket_lifecycle_configuration(Bucket="verumdoc-pdd")["Rules"]
    assert regole[0]["Expiration"]["Days"] == 4 and regole[0]["Status"] == "Enabled"


def test_storage_non_raggiungibile_nessun_bucket(monkeypatch):
    """Se MinIO non risponde entro l'attesa il seed rinuncia senza errori."""
    from botocore.exceptions import EndpointConnectionError

    class _S3Giu:
        def list_buckets(self):
            raise EndpointConnectionError(endpoint_url="http://minio:9000")

    monkeypatch.setenv("SEED_ATTESA_STORAGE_SECONDI", "0")
    assert _crea_bucket_sviluppo(_S3Giu()) == {}
