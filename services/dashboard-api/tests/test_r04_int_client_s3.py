# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — dashboard-api: lo scaricamento usa il client S3 condiviso
(nessun client per richiesta) e le chiavi vuote non diventano credenziali
statiche vuote (IRSA). Lo scaricamento è provato con un client finto al posto
del condiviso: la funzione deve chiederlo a ``client_s3``."""
from __future__ import annotations

import io

from app.config import settings
from app.routers import download

from libs.shared.storage import client as modulo_client


def test_scaricamento_usa_il_client_condiviso_con_chiavi_none(monkeypatch) -> None:
    richieste: list[dict] = []

    class Finto:
        def get_object(self, Bucket, Key):  # noqa: N803 — firma boto3
            return {"Body": io.BytesIO(f"{Bucket}/{Key}".encode())}

    finto = Finto()

    def in_cache(endpoint_url, access_key, secret_key, region, opzioni):
        richieste.append({"chiavi": (access_key, secret_key), "endpoint": endpoint_url})
        return finto

    monkeypatch.setattr(modulo_client, "_client_in_cache", in_cache)
    monkeypatch.setattr(settings, "s3_access_key", "")
    monkeypatch.setattr(settings, "s3_secret_key", "")
    assert download._boto_download("b", "k") == b"b/k"
    assert download._boto_download("b", "k2") == b"b/k2"
    assert [r["chiavi"] for r in richieste] == [(None, None), (None, None)]
