# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — prestazioni e IRSA del gateway (richieste di r04-infra dopo la
prova di carico):
  - ``_s3_client()`` restituisce lo STESSO client finché la configurazione non
    cambia (prima: un client e una connessione nuovi a ogni versamento);
  - fuori produzione la HEAD del bucket di staging si fa una volta per processo;
  - chiavi S3 vuote → nessuna credenziale statica (catena di boto3, IRSA).
Contro un server S3 reale (moto) per la HEAD e la creazione del bucket.
"""
from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.services import versamento_service as vs

from libs.shared.storage.client import svuota_cache
from tests.infra_reale import s3_server  # noqa: F401 — fixture


@pytest.fixture(autouse=True)
def _pulito():
    svuota_cache()
    vs._BUCKET_CONTROLLATI.clear()
    yield
    svuota_cache()
    vs._BUCKET_CONTROLLATI.clear()


def test_client_riusato_finche_la_configurazione_non_cambia(monkeypatch, s3_server) -> None:
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    primo = vs._s3_client()
    assert all(vs._s3_client() is primo for _ in range(50))
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server + "/")
    assert vs._s3_client() is not primo


def test_head_del_bucket_una_volta_per_processo(monkeypatch, s3_server) -> None:
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "env", "development")
    # create_bucket senza LocationConstraint
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    s3 = vs._s3_client()
    chiamate: list[str] = []
    originale = s3.head_bucket

    def conta(**kw):
        chiamate.append(kw["Bucket"])
        return originale(**kw)

    monkeypatch.setattr(s3, "head_bucket", conta)
    bucket = f"staging-{uuid.uuid4().hex[:8]}"
    for _ in range(5):
        vs._assicura_bucket(s3, bucket)
    assert chiamate == [bucket]  # una HEAD (assente → creato), poi nulla
    assert any(b["Name"] == bucket for b in s3.list_buckets()["Buckets"])
    monkeypatch.setattr(settings, "env", "production")
    vs._assicura_bucket(s3, "mai-controllato")
    assert chiamate == [bucket]


def test_chiavi_vuote_nessuna_credenziale_statica(monkeypatch) -> None:
    monkeypatch.setattr(settings, "s3_access_key", "")
    monkeypatch.setattr(settings, "s3_secret_key", "")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "RUOLO_IRSA")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    s3 = vs._s3_client()
    assert s3._request_signer._credentials.access_key == "RUOLO_IRSA"  # noqa: SLF001
