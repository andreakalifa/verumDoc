# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — distribution: client S3 unico per processo e chiavi vuote
→ catena di credenziali di boto3 (IRSA, richiesta di r04-infra)."""
from __future__ import annotations

from app.config import settings
from app.services import s3_client

from libs.shared.storage.client import svuota_cache


def test_client_unico_e_chiavi_vuote_senza_credenziali_statiche(monkeypatch) -> None:
    svuota_cache()
    try:
        assert s3_client._get_s3() is s3_client._get_s3()
        monkeypatch.setattr(settings, "s3_access_key", "")
        monkeypatch.setattr(settings, "s3_secret_key", "")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "RUOLO_IRSA")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
        s3 = s3_client._get_s3()
        assert s3._request_signer._credentials.access_key == "RUOLO_IRSA"  # noqa: SLF001
    finally:
        svuota_cache()
