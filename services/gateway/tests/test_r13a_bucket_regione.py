# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Regressione: creazione dei bucket di sviluppo fuori da us-east-1 (avvio docker, R13a).

Lo storage S3 di sviluppo (versitygw, servizio «minio» del compose) è in
``eu-south-1`` come i servizi (``S3_REGION``): S3 standard rifiuta un
CreateBucket senza ``CreateBucketConfiguration.LocationConstraint`` quando la
regione del client non è us-east-1. Valgono sia il seed (``_crea_bucket_sviluppo``)
sia il versamento (``_assicura_bucket``, bucket di staging fuori produzione).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from app.seed import _crea_bucket_sviluppo
from app.services import versamento_service

RADICE = Path(__file__).resolve().parents[3]


class S3Finto:
    """Client S3 minimo: nessun bucket esistente, registra le creazioni."""

    def __init__(self, regione: str) -> None:
        self.meta = SimpleNamespace(region_name=regione)
        self.creazioni: list[dict[str, Any]] = []

    def head_bucket(self, Bucket: str) -> None:  # noqa: N803 — firma di boto3
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")

    def list_buckets(self) -> dict[str, Any]:  # storage raggiungibile (attesa del seed)
        return {"Buckets": []}

    def create_bucket(self, **argomenti: Any) -> None:
        self.creazioni.append(argomenti)

    def __getattr__(self, nome: str) -> Any:
        # configurazioni accessorie del seed (lock, ciclo di vita): ininfluenti qui
        return lambda *a, **k: {}


@pytest.fixture(autouse=True)
def _bucket(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_AIP", "verumdoc-aip")
    monkeypatch.setenv("S3_BUCKET_PDV", "verumdoc-pdv")
    monkeypatch.setenv("S3_BUCKET_PDD", "verumdoc-pdd")


def test_seed_crea_i_bucket_con_la_regione_fuori_da_us_east_1():
    s3 = S3Finto("eu-south-1")
    _crea_bucket_sviluppo(s3)
    assert {c["Bucket"] for c in s3.creazioni} == {"verumdoc-aip", "verumdoc-pdv",
                                                   "verumdoc-pdd"}
    for c in s3.creazioni:
        assert c["CreateBucketConfiguration"] == {"LocationConstraint": "eu-south-1"}
    aip = next(c for c in s3.creazioni if c["Bucket"] == "verumdoc-aip")
    assert aip["ObjectLockEnabledForBucket"] is True


def test_seed_in_us_east_1_senza_location_constraint():
    s3 = S3Finto("us-east-1")
    _crea_bucket_sviluppo(s3)
    assert s3.creazioni and all("CreateBucketConfiguration" not in c for c in s3.creazioni)


@pytest.mark.parametrize(("regione", "attesa"), [
    ("eu-south-1", {"LocationConstraint": "eu-south-1"}), ("us-east-1", None)])
def test_assicura_bucket_con_la_regione(monkeypatch, regione, attesa):
    monkeypatch.setattr(versamento_service.settings, "env", "development")
    versamento_service._BUCKET_CONTROLLATI.clear()
    s3 = S3Finto(regione)
    versamento_service._assicura_bucket(s3, "verumdoc-pdv")
    assert len(s3.creazioni) == 1
    assert s3.creazioni[0].get("CreateBucketConfiguration") == attesa
    versamento_service._BUCKET_CONTROLLATI.clear()


def test_compose_storage_nella_regione_predefinita_dei_servizi():
    """Il servizio di storage del compose dichiara la stessa regione che i servizi
    usano per impostazione predefinita (``s3_region`` delle Settings)."""
    from app.config import Settings

    compose = yaml.safe_load((RADICE / "docker-compose.yml").read_text(encoding="utf-8"))
    ambiente = compose["services"]["minio"].get("environment") or {}
    if isinstance(ambiente, list):
        ambiente = dict(v.split("=", 1) for v in ambiente)
    assert ambiente.get("VGW_REGION") == Settings.model_fields["s3_region"].default
