# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — client S3 riusato per processo e chiavi vuote → catena di
credenziali di boto3 (IRSA). Nessuna rete: si ispezionano i client creati."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from libs.shared.storage import client as modulo
from libs.shared.storage.client import client_creati, client_s3, svuota_cache


@pytest.fixture(autouse=True)
def _cache_pulita():
    svuota_cache()
    yield
    svuota_cache()


def test_stessi_parametri_stesso_client_parametri_diversi_client_nuovo() -> None:
    a = client_s3(endpoint_url="http://s3.locale:9000", access_key="k", secret_key="s",
                  region="eu-south-1")
    b = client_s3(endpoint_url="http://s3.locale:9000", access_key="k", secret_key="s",
                  region="eu-south-1")
    assert a is b and client_creati() == 1
    c = client_s3(endpoint_url="http://altro:9000", access_key="k", secret_key="s",
                  region="eu-south-1")
    assert c is not a and client_creati() == 2
    d = client_s3(endpoint_url="http://s3.locale:9000", access_key="k", secret_key="s",
                  region="eu-south-1", connect_timeout=1.5, retries={"total_max_attempts": 1})
    assert d is not a
    assert d.meta.config.connect_timeout == 1.5
    assert d.meta.config.retries["total_max_attempts"] == 1
    assert d.meta.config.signature_version == "s3v4"
    assert d is client_s3(endpoint_url="http://s3.locale:9000", access_key="k", secret_key="s",
                          region="eu-south-1", retries={"total_max_attempts": 1},
                          connect_timeout=1.5)


def test_chiavi_vuote_usano_la_catena_di_credenziali(monkeypatch) -> None:
    """Con la stringa vuota boto3 firmerebbe con credenziali vuote (IRSA rotto):
    qui diventano None e le credenziali vengono dalla catena (variabili, ruolo)."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "DALLA_CATENA")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "segreto-catena")
    s3 = client_s3(endpoint_url="", access_key="", secret_key="  ", region="eu-south-1")
    credenziali = s3._request_signer._credentials  # noqa: SLF001 — ispezione nel test
    assert credenziali.access_key == "DALLA_CATENA"
    assert s3.meta.endpoint_url.startswith("https://s3.")  # endpoint vuoto → AWS
    statiche = client_s3(access_key=SecretStr("STATICA"), secret_key=SecretStr("x"),
                         region="eu-south-1")
    assert statiche._request_signer._credentials.access_key == "STATICA"  # noqa: SLF001


def test_o_none() -> None:
    assert modulo._o_none(None) is None  # noqa: SLF001
    assert modulo._o_none("") is None  # noqa: SLF001
    assert modulo._o_none(SecretStr("")) is None  # noqa: SLF001
    assert modulo._o_none("x") == "x"  # noqa: SLF001
