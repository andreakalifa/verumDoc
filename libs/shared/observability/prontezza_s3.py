# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Controllo di prontezza dello storage S3 (MinIO in sviluppo, S3 con Object Lock
in esercizio), con gli stessi parametri del client del servizio.

- Con ``bucket``: HeadBucket risponde (bucket esistente); bucket assente →
  NonPronto("bucket_assente"). HeadBucket richiede ``s3:ListBucket``, che i
  servizi con permessi minimi sul bucket dei PdA NON hanno (R04 quarto giro: la
  hashchain lo ha solo sul prefisso ``registro/``, gli altri per niente). S3
  risponde 404 per un bucket inesistente e 403 per un bucket esistente senza quel
  permesso: il 403 conta quindi come «bucket presente». Le credenziali sbagliate
  si vedono alla prima operazione vera, con il suo errore esplicito.
- Senza bucket (validation: il bucket arriva nel messaggio del PdV): basta che
  il servizio S3 risponda con credenziali valide; ListBuckets negato
  (AccessDenied, permessi minimi) conta come «risponde».

Timeout brevi e nessun ritentativo: la probe non deve restare appesa.
"""
from __future__ import annotations

import asyncio

from libs.shared.observability.prontezza import NonPronto


def _client(endpoint_url: str | None, access_key: str | None, secret_key: str | None,
            region: str | None, timeout: float):
    """Client della probe, riusato fra le probe (R04: niente client nuovo ogni 10 s)."""
    from libs.shared.storage.client import client_s3

    return client_s3(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key,
                     region=region, connect_timeout=timeout, read_timeout=timeout,
                     retries={"total_max_attempts": 1})


async def controlla_s3(*, endpoint_url: str | None, access_key: str | None,
                       secret_key: str | None, region: str | None = None,
                       bucket: str | None = None, timeout: float = 1.5) -> None:
    from botocore.exceptions import ClientError

    def _sincrono() -> None:
        s3 = _client(endpoint_url, access_key, secret_key, region, timeout)
        if bucket:
            try:
                s3.head_bucket(Bucket=bucket)
            except ClientError as exc:
                codice = str(exc.response.get("Error", {}).get("Code", ""))
                if codice in {"404", "NoSuchBucket", "NotFound"}:
                    raise NonPronto("bucket_assente") from exc
                if codice in {"403", "AccessDenied", "Forbidden"}:
                    return  # bucket esistente, ruolo senza s3:ListBucket (permessi minimi)
                raise
            return
        try:
            s3.list_buckets()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "AccessDenied":
                raise

    await asyncio.to_thread(_sincrono)
