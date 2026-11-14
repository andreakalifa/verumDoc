# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/storage/flusso.py — caricamento IN STREAMING sullo storage di transito
(staging) con impronte calcolate durante il trasferimento.

Riferimenti: piano R03 §3.1 e §4.2 («nessun file in memoria»); REGOLE_SVILUPPO
§8.15 (impronta, dimensione e formato si calcolano), §10.3, §19.5;
spec-verumdoc debolezza 9 (PdV letto tutto in memoria).

Comportamento di ``carica_flusso``:
  - legge i blocchi di un flusso asincrono e tiene in memoria AL PIÙ una parte
    (``dimensione_parte``, es. 8 MB) più i primi ``byte_testa`` byte (per il
    riconoscimento del formato dal contenuto);
  - calcola durante il trasferimento la dimensione e le impronte di TUTTI gli
    algoritmi ammessi (``libs.shared.impronte``);
  - un file che sta in una parte si scrive con un solo ``put_object``; uno più
    grande con l'upload multipart di S3 (parte minima S3: 5 MB, tranne l'ultima);
  - oltre ``limite_byte`` si ferma SUBITO (il resto del flusso non si legge) e
    solleva ``FlussoOltreLimite``;
  - su qualunque errore o annullamento il multipart si annulla
    (``abort_multipart_upload``): nessuna parte resta a pagamento nello storage;
  - le chiamate boto3 (sincrone) girano in un thread (``asyncio.to_thread``).

Il modulo NON legge configurazione: client S3, bucket e chiave li passa il chiamante.
NON scrive sul bucket conservativo (WORM): per quello c'è ``worm.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import structlog

from libs.shared.impronte import ALGORITMI_IMPRONTA

logger = structlog.get_logger(__name__)

#: Parte minima ammessa da S3 per le parti non finali di un multipart.
PARTE_MINIMA_S3 = 5 * 1024 * 1024
#: Byte iniziali conservati per riconoscere il formato dal contenuto.
BYTE_TESTA_PREDEFINITI = 8 * 1024
#: Blocco di lettura dai flussi con interfaccia ``read(n)``.
BLOCCO_LETTURA = 1024 * 1024


class FlussoOltreLimite(Exception):
    """Il flusso supera ``limite_byte``: trasferimento interrotto, nulla resta sullo storage."""

    def __init__(self, limite_byte: int, letti: int) -> None:
        super().__init__(f"flusso oltre il limite di {limite_byte} byte")
        self.limite_byte = limite_byte
        self.letti = letti


class ErroreStorageFlusso(Exception):
    """Lo storage non ha accettato il caricamento (dopo l'annullamento del multipart)."""


class LettoreAsincrono(Protocol):
    """Oggetto con ``read(n)`` asincrono (es. ``UploadFile`` di Starlette)."""

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class EsitoCaricamento:
    """Oggetto scritto nello staging e ciò che si è calcolato durante il trasferimento."""

    bucket: str
    chiave: str
    dimensione: int
    #: impronte esadecimali per nome d'algoritmo (``SHA-256``, ``SHA-512``, ``SHA3-256``)
    impronte: Mapping[str, str]
    #: primi byte del contenuto (al più ``byte_testa``)
    testa: bytes
    multipart: bool = False
    parti: int = field(default=1)


async def blocchi_da_lettore(
    lettore: LettoreAsincrono, dimensione_blocco: int = BLOCCO_LETTURA
) -> AsyncIterator[bytes]:
    """Trasforma un oggetto con ``read(n)`` asincrono in un flusso di blocchi."""
    while True:
        blocco = await lettore.read(dimensione_blocco)
        if not blocco:
            return
        yield blocco


async def carica_flusso(
    blocchi: AsyncIterator[bytes],
    *,
    s3: Any,
    bucket: str,
    chiave: str,
    content_type: str = "application/octet-stream",
    dimensione_parte: int = 8 * 1024 * 1024,
    limite_byte: int | None = None,
    byte_testa: int = BYTE_TESTA_PREDEFINITI,
    algoritmi: Mapping[str, Callable[..., Any]] = ALGORITMI_IMPRONTA,
) -> EsitoCaricamento:
    """Carica il flusso in ``bucket/chiave``; vedi la docstring del modulo."""
    if dimensione_parte < 1:
        raise ValueError("dimensione_parte deve essere positiva")
    calcolatori = {nome: costruttore() for nome, costruttore in algoritmi.items()}
    buffer = bytearray()
    testa = bytearray()
    letti = 0
    upload_id: str | None = None
    parti: list[dict[str, Any]] = []

    async def _invia_parte(dati: bytes) -> None:
        nonlocal upload_id
        if upload_id is None:
            risposta = await _storage(
                s3.create_multipart_upload, Bucket=bucket, Key=chiave, ContentType=content_type
            )
            upload_id = str(risposta["UploadId"])
        numero = len(parti) + 1
        risposta = await _storage(
            s3.upload_part,
            Bucket=bucket,
            Key=chiave,
            UploadId=upload_id,
            PartNumber=numero,
            Body=dati,
        )
        parti.append({"ETag": risposta["ETag"], "PartNumber": numero})

    try:
        async for blocco in blocchi:
            if not blocco:
                continue
            letti += len(blocco)
            if limite_byte is not None and letti > limite_byte:
                raise FlussoOltreLimite(limite_byte, letti)
            for calcolatore in calcolatori.values():
                calcolatore.update(blocco)
            if len(testa) < byte_testa:
                testa.extend(blocco[: byte_testa - len(testa)])
            buffer.extend(blocco)
            while len(buffer) >= dimensione_parte:
                parte = bytes(buffer[:dimensione_parte])
                del buffer[:dimensione_parte]
                await _invia_parte(parte)
        if upload_id is None:
            await _storage(
                s3.put_object,
                Bucket=bucket,
                Key=chiave,
                Body=bytes(buffer),
                ContentType=content_type,
            )
        else:
            if buffer:
                await _invia_parte(bytes(buffer))
                buffer.clear()
            await _storage(
                s3.complete_multipart_upload,
                Bucket=bucket,
                Key=chiave,
                UploadId=upload_id,
                MultipartUpload={"Parts": parti},
            )
    except BaseException:
        # limite superato, storage che rifiuta, sorgente interrotta o task annullato:
        # nessuna parte resta aperta nello storage
        if upload_id is not None:
            await _annulla(s3, bucket, chiave, upload_id)
        raise
    return EsitoCaricamento(
        bucket=bucket,
        chiave=chiave,
        dimensione=letti,
        impronte={nome: c.hexdigest() for nome, c in calcolatori.items()},
        testa=bytes(testa),
        multipart=upload_id is not None,
        parti=max(1, len(parti)),
    )


async def _storage(funzione: Callable[..., Any], **argomenti: Any) -> Any:
    """Chiamata boto3 in un thread; ogni suo errore diventa ``ErroreStorageFlusso``
    (gli errori della SORGENTE invece si propagano così come sono)."""
    try:
        return await asyncio.to_thread(funzione, **argomenti)
    except Exception as exc:
        raise ErroreStorageFlusso(f"caricamento non riuscito: {type(exc).__name__}") from exc


async def _annulla(s3: Any, bucket: str, chiave: str, upload_id: str) -> None:
    try:
        await asyncio.to_thread(
            s3.abort_multipart_upload, Bucket=bucket, Key=chiave, UploadId=upload_id
        )
        logger.info("staging_multipart_annullato", bucket=bucket, chiave=chiave)
    except Exception as exc:  # noqa: BLE001 — resta la pulizia periodica degli incompleti
        logger.error(
            "staging_multipart_annullamento_fallito",
            bucket=bucket,
            chiave=chiave,
            errore=type(exc).__name__,
        )


async def dimensione_oggetto(s3: Any, bucket: str, chiave: str) -> int | None:
    """Dimensione dell'oggetto, ``None`` se non esiste (altri errori si propagano)."""
    try:
        risposta = await asyncio.to_thread(s3.head_object, Bucket=bucket, Key=chiave)
    except Exception as exc:
        codice = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if codice in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return int(risposta["ContentLength"])


async def elimina_oggetti(s3: Any, bucket: str, chiavi: list[str]) -> None:
    """Elimina gli oggetti indicati (a lotti di 1000, limite di ``delete_objects``)."""
    for i in range(0, len(chiavi), 1000):
        await asyncio.to_thread(
            s3.delete_objects,
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in chiavi[i : i + 1000]], "Quiet": True},
        )


async def annulla_multipart_incompleti(
    s3: Any, bucket: str, *, prefisso: str, iniziati_prima_di: datetime, prova: bool = False
) -> list[str]:
    """Annulla i caricamenti multipart rimasti aperti sotto ``prefisso`` (processo morto
    durante il trasferimento). Restituisce le chiavi interessate."""

    def _elenca() -> list[dict[str, Any]]:
        aperti: list[dict[str, Any]] = []
        paginatore = s3.get_paginator("list_multipart_uploads")
        for pagina in paginatore.paginate(Bucket=bucket, Prefix=prefisso):
            aperti.extend(pagina.get("Uploads", []))
        return aperti

    vecchi = [u for u in await asyncio.to_thread(_elenca) if u["Initiated"] < iniziati_prima_di]
    if not prova:
        for u in vecchi:
            await _annulla(s3, bucket, u["Key"], u["UploadId"])
    return sorted(u["Key"] for u in vecchi)
