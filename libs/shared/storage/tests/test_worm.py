# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Scrittore WORM condiviso (libs/shared/storage/worm.py): parti senza configurazione
di servizio. Le regole di retention e scrittura sono provate anche in
services/aip-builder/tests/test_s3_uploader.py (tramite il modulo del servizio)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from libs.shared.storage import worm

ADESSO = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def s3():
    with mock_aws():
        worm._bucket_verificati.clear()
        yield boto3.client("s3", region_name="eu-south-1")
    worm._bucket_verificati.clear()


def test_bucket_sviluppo_creato_con_lock_nella_regione(s3):
    worm.verifica_bucket_conservativo(s3, "aip-svil", produzione=False, regione="eu-south-1")
    assert s3.get_bucket_location(Bucket="aip-svil")["LocationConstraint"] == "eu-south-1"
    conf = s3.get_object_lock_configuration(Bucket="aip-svil")["ObjectLockConfiguration"]
    assert conf["ObjectLockEnabled"] == "Enabled"


def test_bucket_mancante_in_produzione_errore(s3):
    with pytest.raises(worm.ErroreStorageConservativo):
        worm.verifica_bucket_conservativo(s3, "assente", produzione=True, regione="eu-south-1")


def test_scrittura_e_rilettura(s3):
    r = worm.calcola_retention(worm.PoliticaConservazione(anni_conservazione=5), ADESSO)
    worm.scrivi_oggetto_conservato(s3, "aip-svil", "t/a/RdV.xml", b"<rdv/>", "application/xml",
                                   r, adesso=ADESSO, produzione=False, regione="eu-south-1")
    assert worm.leggi_oggetto(s3, "aip-svil", "t/a/RdV.xml") == b"<rdv/>"
    with pytest.raises(worm.OggettoGiaPresente):
        worm.scrivi_oggetto_conservato(s3, "aip-svil", "t/a/RdV.xml", b"<altro/>",
                                       "application/xml", r, adesso=ADESSO, produzione=False,
                                       regione="eu-south-1")
    with pytest.raises(worm.ErroreStorageConservativo):
        worm.leggi_oggetto(s3, "aip-svil", "../fuori")


def test_configurazione_dal_chiamante():
    cfg = SimpleNamespace(env="staging", s3_bucket_aip="verumdoc-staging-aip-prod",
                          s3_endpoint_url="")
    assert worm.bucket_conservativo(cfg) == "verumdoc-staging-aip-prod"
    with pytest.raises(worm.ConfigurazioneStorageNonValida):
        worm.bucket_conservativo(SimpleNamespace(env="staging", s3_bucket_aip="altro",
                                                 s3_endpoint_url=""))


class _Registratore:
    """Client finto: registra gli argomenti di put_object (nessun oggetto esiste)."""

    def __init__(self) -> None:
        self.chiamate: list[dict] = []

    def head_object(self, **_k):  # noqa: ANN201
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def put_object(self, **k):  # noqa: ANN201
        self.chiamate.append(k)
        return {}


def test_legal_hold_off_senza_intestazione_on_con_intestazione(monkeypatch):
    """Revisione 2 di R05 (S4): con legal hold OFF nessuna intestazione
    ``ObjectLockLegalHoldStatus`` (S3 chiederebbe s3:PutObjectLegalHold anche
    per «OFF»: la hashchain non ha più quel permesso sui suoi prefissi); con ON
    l'intestazione c'è."""
    monkeypatch.setattr(worm, "verifica_bucket_conservativo", lambda *a, **k: None)
    finto = _Registratore()
    senza = worm.RetentionOggetto(ADESSO.replace(year=2036), legal_hold=False)
    con = worm.RetentionOggetto(ADESSO.replace(year=2126), legal_hold=True)
    worm.scrivi_oggetto_conservato(finto, "b", "registro/x.txt", b"x", "text/plain", senza,
                                   adesso=ADESSO, produzione=False, regione="eu-south-1")
    worm.scrivi_oggetto_conservato(finto, "b", "t/a/RdV.xml", b"y", "application/xml", con,
                                   adesso=ADESSO, produzione=False, regione="eu-south-1")
    assert "ObjectLockLegalHoldStatus" not in finto.chiamate[0]
    assert finto.chiamate[0]["ObjectLockMode"] == "COMPLIANCE"
    assert finto.chiamate[1]["ObjectLockLegalHoldStatus"] == "ON"
