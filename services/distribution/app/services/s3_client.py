# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/s3_client.py — accesso allo storage per distribution.

F4-01b: boto3 sincrono in thread. R03 b4 (VD-C-077): letture IN STREAMING a
blocchi (mai un file intero in memoria) con impronta SHA-256 calcolata durante
la lettura, collegamenti prefirmati con ``Content-Disposition: attachment``
(REGOLE §19.6), lettura limitata dei file piccoli (manifesto).
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote

import structlog

from app.config import settings
from libs.shared.storage.client import client_s3

logger = structlog.get_logger(__name__)

#: Blocco di lettura dallo storage (byte).
BLOCCO_LETTURA = 1024 * 1024


class ImprontaNonCorrispondente(Exception):
    """I byte letti non corrispondono all'impronta registrata (integrità)."""

    def __init__(self, chiave: str, attesa: str, calcolata: str,
                 dimensione_attesa: int | None, dimensione_letta: int) -> None:
        super().__init__(f"impronta di {chiave} non corrispondente")
        self.chiave = chiave
        self.attesa = attesa
        self.calcolata = calcolata
        self.dimensione_attesa = dimensione_attesa
        self.dimensione_letta = dimensione_letta


def _get_s3() -> Any:
    """Client S3 unico per processo e configurazione (R04); chiavi vuote → IRSA."""
    return client_s3(endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
                     secret_key=settings.s3_secret_key, region=settings.s3_region)


def blocchi_oggetto(bucket: str, key: str, client: Any | None = None) -> Iterator[bytes]:
    """Byte dell'oggetto a blocchi (sincrono: da usare in un thread)."""
    s3 = client or _get_s3()
    corpo = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        yield from corpo.iter_chunks(BLOCCO_LETTURA)
    finally:
        corpo.close()


def verifica_impronta_sync(bucket: str, key: str, attesa_sha256: str,
                           dimensione_attesa: int | None = None,
                           *, client: Any | None = None,
                           consumatore: Callable[[bytes], None] | None = None) -> int:
    """Legge l'oggetto a blocchi, calcola SHA-256 e lo confronta con l'atteso;
    ``consumatore`` riceve i blocchi (es. scrittura nello ZIP). Restituisce la
    dimensione letta; ``ImprontaNonCorrispondente`` se diverge."""
    h = hashlib.sha256()
    letti = 0
    for blocco in blocchi_oggetto(bucket, key, client):
        h.update(blocco)
        letti += len(blocco)
        if consumatore is not None:
            consumatore(blocco)
    calcolata = h.hexdigest()
    if calcolata != (attesa_sha256 or "").lower() or (
            dimensione_attesa is not None and int(dimensione_attesa) != letti):
        raise ImprontaNonCorrispondente(key, attesa_sha256, calcolata, dimensione_attesa, letti)
    return letti


async def verifica_impronta(bucket: str, key: str, attesa_sha256: str,
                            dimensione_attesa: int | None = None) -> int:
    return await asyncio.to_thread(verifica_impronta_sync, bucket, key, attesa_sha256,
                                   dimensione_attesa)


def leggi_piccolo_sync(bucket: str, key: str, massimo: int, client: Any | None = None) -> bytes:
    """Contenuto di un oggetto piccolo (manifesto); ValueError oltre ``massimo`` byte."""
    dati = bytearray()
    for blocco in blocchi_oggetto(bucket, key, client):
        dati.extend(blocco)
        if len(dati) > massimo:
            raise ValueError(f"oggetto {key} oltre {massimo} byte")
    return bytes(dati)


async def get_aip_file(bucket: str, key: str, massimo: int = 16 * 1024 * 1024) -> bytes:
    """Scarica un file PICCOLO dell'AIP (limite in byte: niente file grandi in memoria)."""
    return await asyncio.to_thread(leggi_piccolo_sync, bucket, key, massimo)


async def generate_signed_url(bucket: str, key: str,
                              expiry: int | None = None, nome_file: str | None = None) -> str:
    """URL prefirmato GET con scadenza e ``Content-Disposition: attachment``."""
    def _sign() -> str:
        parametri: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if nome_file:
            parametri["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{quote(nome_file, safe='')}")
        return _get_s3().generate_presigned_url(
            "get_object", Params=parametri, ExpiresIn=expiry or settings.signed_url_expiry,
        )
    url = await asyncio.to_thread(_sign)
    # In dev MinIO usa hostname interno — sostituisci con localhost per il browser
    if settings.env.lower() != "production":
        url = url.replace("http://minio:9000", "http://localhost:9000")
    return url


def compute_pdd_hash(componenti: list[str]) -> str:
    """
    Impronta del PdD sulle impronte/identificativi dei suoi componenti.
    Ordinamento e separatore fissi: stesso insieme → stessa impronta.
    """
    canonico = "\n".join(sorted(str(c) for c in componenti))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
