# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prova dello storage S3 REALE di sviluppo e CI (R04 infra, VD-C-081).

Esegue lo scrittore WORM (``libs/shared/storage/worm.py``) contro l'endpoint
indicato da ``VD_TEST_S3_URL`` (MinIO nella CI, oppure un'alternativa da
valutare come SeaweedFS): bucket con Object Lock, retention COMPLIANCE per
oggetto, legal hold, nessuna sovrascrittura, eliminazione rifiutata, checksum
SHA-256. È la prova di ammissione di qualsiasi storage di sviluppo: se fallisce,
quello storage non può sostituire MinIO (lo scrittore WORM richiede e verifica
queste funzioni).

Variabili: ``VD_TEST_S3_URL`` (es. http://127.0.0.1:9000), ``VD_TEST_S3_ACCESS_KEY``,
``VD_TEST_S3_SECRET_KEY``, ``VD_TEST_S3_REGIONE`` (predefinita us-east-1).
Senza ``VD_TEST_S3_URL`` la prova è saltata con il motivo (la CI la imposta).
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

URL = os.environ.get("VD_TEST_S3_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="VD_TEST_S3_URL non impostata: nessuno storage S3 reale")


@pytest.fixture
def client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=URL,
        aws_access_key_id=os.environ.get("VD_TEST_S3_ACCESS_KEY", "test"),
        aws_secret_access_key=os.environ.get("VD_TEST_S3_SECRET_KEY", "test"),
        region_name=os.environ.get("VD_TEST_S3_REGIONE", "us-east-1"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
    )


@pytest.fixture
def bucket(client):
    """Bucket effimero creato dallo scrittore WORM (sviluppo: lo crea con Object Lock)."""
    from libs.shared.storage import worm

    nome = f"vd-prova-worm-{uuid.uuid4().hex[:12]}"
    worm.verifica_bucket_conservativo(client, nome, produzione=False,
                                      regione=os.environ.get("VD_TEST_S3_REGIONE", "us-east-1"))
    return nome


def _scrivi(client, bucket, chiave, dati, *, giorni=1, legal_hold=False):
    from libs.shared.storage import worm

    adesso = datetime.now(UTC)
    retention = worm.RetentionOggetto(adesso + timedelta(days=giorni), legal_hold=legal_hold)
    return worm.scrivi_oggetto_conservato(
        client, bucket, chiave, dati, "application/pdf", retention,
                                          adesso=adesso, produzione=False)


def test_storage_reale_bucket_con_object_lock(client, bucket) -> None:
    conf = client.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
    assert conf["ObjectLockEnabled"] == "Enabled"


def test_storage_reale_retention_compliance_per_oggetto(client, bucket) -> None:
    dati = b"%PDF-1.7\n" + os.urandom(2048)
    _scrivi(client, bucket, "pda/prova/doc.pdf", dati, giorni=2)
    ret = client.get_object_retention(Bucket=bucket, Key="pda/prova/doc.pdf")["Retention"]
    assert ret["Mode"] == "COMPLIANCE"
    assert ret["RetainUntilDate"] > datetime.now(UTC) + timedelta(days=1)
    corpo = client.get_object(Bucket=bucket, Key="pda/prova/doc.pdf")["Body"].read()
    assert hashlib.sha256(corpo).digest() == hashlib.sha256(dati).digest()


def test_storage_reale_legal_hold(client, bucket) -> None:
    _scrivi(client, bucket, "pda/prova/permanente.pdf", b"%PDF-1.7\nx", legal_hold=True)
    stato = client.get_object_legal_hold(Bucket=bucket, Key="pda/prova/permanente.pdf")
    assert stato["LegalHold"]["Status"] == "ON"


def test_storage_reale_nessuna_sovrascrittura(client, bucket) -> None:
    from libs.shared.storage import worm

    _scrivi(client, bucket, "pda/prova/unico.pdf", b"%PDF-1.7\nprimo")
    with pytest.raises(worm.OggettoGiaPresente):
        _scrivi(client, bucket, "pda/prova/unico.pdf", b"%PDF-1.7\nsecondo")


def test_storage_reale_eliminazione_della_versione_rifiutata(client, bucket) -> None:
    """COMPLIANCE: nemmeno con il bypass la versione protetta si elimina."""
    from botocore.exceptions import ClientError

    risposta = _scrivi(client, bucket, "pda/prova/protetto.pdf", b"%PDF-1.7\nprotetto")
    versione = risposta.get("VersionId")
    assert versione, "lo storage non restituisce VersionId: Object Lock richiede il versioning"
    with pytest.raises(ClientError):
        client.delete_object(Bucket=bucket, Key="pda/prova/protetto.pdf", VersionId=versione,
                             BypassGovernanceRetention=True)
    assert client.head_object(Bucket=bucket, Key="pda/prova/protetto.pdf", VersionId=versione)


def test_storage_reale_retention_non_accorciabile(client, bucket) -> None:
    from botocore.exceptions import ClientError

    _scrivi(client, bucket, "pda/prova/fisso.pdf", b"%PDF-1.7\nfisso", giorni=3)
    with pytest.raises(ClientError):
        client.put_object_retention(Bucket=bucket, Key="pda/prova/fisso.pdf", Retention={
            "Mode": "COMPLIANCE", "RetainUntilDate": datetime.now(UTC) + timedelta(hours=1)})
