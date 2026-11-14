# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Rilettura in streaming di un oggetto dello storage conservativo (S3 Object Lock).

- Una sola ``GetObject`` per oggetto: il corpo si legge a blocchi
  (``VERIFICA_BLOCCO_LETTURA_BYTE``) e l'impronta SHA-256 si calcola blocco per
  blocco: nessun file intero in memoria (piano §4.2). Solo il manifest del PdA,
  piccolo, si trattiene (fino a ``trattieni_max`` byte) per leggerne l'impronta
  attesa dell'IPdA.
- La retention viene dalle intestazioni della stessa risposta
  (``x-amz-object-lock-mode`` / ``-retain-until-date``). S3 le restituisce solo a
  chi ha il permesso ``s3:GetObjectRetention``: se mancano si chiede
  ``GetObjectRetention`` sulla versione letta; «nessuna configurazione» =
  retention assente, un rifiuto o un guasto = errore di lettura (mai un
  RETENTION_ASSENTE per un permesso mancante).
- Esito della lettura: LETTO, MANCANTE (chiave o versione inesistente) oppure
  ERRORE (rete, permessi, interruzione a metà, risposta non interpretabile).

Le chiamate boto3 sono sincrone: girano in un thread (``asyncio.to_thread``),
un blocco per volta, con il budget di letture e di byte (``budget.py``).
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from botocore.exceptions import BotoCoreError, ClientError

from app.verifica_continua.budget import Budget

logger = structlog.get_logger(__name__)

LETTO = "LETTO"
NON_TROVATO = "MANCANTE"
ERRORE = "ERRORE"

_CODICI_ASSENTE = frozenset({"NoSuchKey", "404", "NotFound", "NoSuchVersion"})
_CODICI_SENZA_RETENTION = frozenset({
    "NoSuchObjectLockConfiguration", "ObjectLockConfigurationNotFoundError",
})


@dataclass(frozen=True)
class Lettura:
    """Risultato della rilettura di un oggetto."""

    stato: str
    sha256: str | None = None
    dimensione: int | None = None
    retention_modalita: str | None = None
    retention_fino_a: datetime | None = None
    versione: str | None = None
    contenuto: bytes | None = None  # solo se trattenuto (manifest)
    motivo: str | None = None


def _codice(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


async def _retention_esplicita(
    client: Any, bucket: str, chiave: str, versione: str | None,
) -> tuple[str | None, datetime | None, str | None]:
    """(modalità, termine, motivo d'errore) da GetObjectRetention."""
    argomenti: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
    if versione:
        argomenti["VersionId"] = versione
    try:
        risposta = await asyncio.to_thread(client.get_object_retention, **argomenti)
    except ClientError as exc:
        codice = _codice(exc)
        if codice in _CODICI_SENZA_RETENTION:
            return None, None, None
        return None, None, f"retention non leggibile: {codice or 'errore S3'}"
    except BotoCoreError as exc:
        return None, None, f"retention non leggibile: {type(exc).__name__}"
    dati = risposta.get("Retention") or {}
    return dati.get("Mode"), dati.get("RetainUntilDate"), None


async def leggi_oggetto(
    client: Any,
    bucket: str,
    chiave: str,
    *,
    budget_letture: Budget,
    budget_byte: Budget,
    blocco: int,
    trattieni_max: int = 0,
) -> Lettura:
    """Rilegge ``bucket/chiave`` in streaming; vedi la docstring del modulo."""
    await budget_letture.consuma(1)
    try:
        risposta = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=chiave)
    except ClientError as exc:
        codice = _codice(exc)
        if codice in _CODICI_ASSENTE:
            return Lettura(NON_TROVATO, motivo="oggetto assente dallo storage")
        return Lettura(ERRORE, motivo=f"lettura rifiutata dallo storage: {codice or 'errore S3'}")
    except BotoCoreError as exc:
        return Lettura(ERRORE, motivo=f"storage non raggiungibile: {type(exc).__name__}")

    corpo = risposta["Body"]
    impronta = hashlib.sha256()
    letti = 0
    trattenuto: bytearray | None = bytearray() if trattieni_max > 0 else None
    try:
        while True:
            dati = await asyncio.to_thread(corpo.read, blocco)
            if not dati:
                break
            await budget_byte.consuma(len(dati))
            impronta.update(dati)
            letti += len(dati)
            if trattenuto is not None:
                if len(trattenuto) + len(dati) <= trattieni_max:
                    trattenuto.extend(dati)
                else:
                    trattenuto = None  # oltre il limite: non si trattiene
    except Exception as exc:  # noqa: BLE001 — qualunque guasto del flusso (urllib3,
        # botocore, rete) è un errore di lettura da ritentare, mai un esito d'integrità
        logger.warning("verifica_lettura_interrotta", bucket=bucket, errore=type(exc).__name__)
        return Lettura(ERRORE, motivo=f"lettura interrotta: {type(exc).__name__}")
    finally:
        chiudi = getattr(corpo, "close", None)
        if chiudi is not None:
            try:
                chiudi()
            except Exception as exc:  # noqa: BLE001 — chiusura del flusso: solo log
                logger.debug("verifica_chiusura_flusso_fallita", errore=type(exc).__name__)

    versione = risposta.get("VersionId")
    modalita = risposta.get("ObjectLockMode")
    fino_a = risposta.get("ObjectLockRetainUntilDate")
    if modalita is None or fino_a is None:
        modalita, fino_a, errore = await _retention_esplicita(client, bucket, chiave, versione)
        if errore:
            return Lettura(ERRORE, sha256=impronta.hexdigest(), dimensione=letti,
                           versione=versione, motivo=errore)
    return Lettura(
        LETTO, sha256=impronta.hexdigest(), dimensione=letti,
        retention_modalita=modalita, retention_fino_a=fino_a, versione=versione,
        contenuto=bytes(trattenuto) if trattenuto is not None else None,
    )
