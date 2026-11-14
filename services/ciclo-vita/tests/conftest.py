# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fixture del servizio ciclo-vita (R05, contratto Q2).

Ambiente di test esplicito (il predefinito delle Settings è «production»):
chiavi JWT effimere, lavoratore spento nel processo dell'app (i test chiamano
il lavoratore da sé), nessun Redis (limiti di richieste non applicati fuori
dalla produzione).
"""
from __future__ import annotations

import os

os.environ["ENV"] = "test"
os.environ["LAVORATORE_ATTIVO"] = "false"
os.environ.pop("VERUMDOC_DEV_AUTH", None)

import pytest  # noqa: E402

from tests.infra_reale import db_url, s3_server  # noqa: E402,F401 — fixture


@pytest.fixture(autouse=True)
def verificatore_firme_di_prova():
    """R08: il verificatore del processo usa la CA di prova come trust anchor."""
    from libs.shared.firme import imposta_verificatore
    from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
    from tests.firme_prova import PKI

    imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
    yield
    imposta_verificatore(None)


@pytest.fixture(autouse=True)
def parametri_senza_cache():
    """R09: i parametri del motore si rileggono a ogni test (cache del processo)."""
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    yield
    svuota_cache()


@pytest.fixture
def s3_ciclo(s3_server, monkeypatch):  # noqa: F811
    """S3 moto per il servizio: endpoint, credenziali e bucket nelle Settings."""
    from app.config import settings

    from libs.shared.storage import worm
    from libs.shared.storage.client import svuota_cache

    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "s3_secret_key", SecretStr("test"))
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    svuota_cache()
    worm._bucket_verificati.clear()
    yield s3_server
    svuota_cache()
