# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lettura IN STREAMING dei file del PdV dallo storage di transito (staging).

Servizio: validation. Riferimenti: piano R03 §4.2 («nessun file in memoria»);
REGOLE_SVILUPPO §10.3 (impronte ricalcolate sui byte letti dallo storage),
§12.4 (errore tecnico ≠ esito); VD-C-008 (fail-closed).

- ``RiferimentoFile`` è un elemento di ``docs_s3_keys`` dell'evento
  ``pdv.received`` (bucket, chiave, dimensione e impronte calcolate dal gateway
  durante il trasferimento);
- ``LettoreS3.dimensione`` legge solo i metadati (HEAD): serve a non scaricare
  un file che supera il limite della classe;
- ``LettoreS3.leggi`` scorre il corpo dell'oggetto a blocchi
  (``LETTURA_BLOCCO_KB``) e aggiorna gli algoritmi richiesti: in memoria resta
  al più un blocco per file in lettura, qualunque sia la dimensione (fino ai
  5 GiB delle sessioni di caricamento);
- ogni errore dello storage diventa ``ErroreVerificaTransitorio`` (il consumer
  ritenta, poi DLQ): un file non letto non è mai un esito positivo.

Le chiamate boto3 (sincrone) girano in un thread; ``hashlib`` rilascia il GIL
sui blocchi grandi, quindi più PdV in parallelo usano più core.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import boto3
import botocore.config
import structlog

from app.config import settings
from app.validators.base import ErroreVerificaTransitorio
from libs.shared.impronte import funzione_impronta

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RiferimentoFile:
    """Posizione di un file del PdV nello staging e dati calcolati al versamento."""

    nome_file: str
    bucket: str
    chiave: str
    #: dimensione misurata dal gateway durante il trasferimento (None per eventi R02)
    dimensione_versata: int | None = None
    #: impronte calcolate dal gateway in transito, per nome d'algoritmo
    impronte_versamento: Mapping[str, str] = field(default_factory=dict)
    #: formato riconosciuto dal gateway sui primi KB del contenuto
    formato_rilevato: str | None = None
    #: R12a (VD-C-146): versione dei byte di cui il passo 5 ha calcolato le impronte
    #: (VersionId dello staging se versionato, altrimenti ETag): le letture
    #: successive (copia, testa e coda, ricerca a blocchi) si legano a quella
    version_id: str | None = None
    etag: str | None = None

    def legato_a(self, letto: FileLetto) -> RiferimentoFile:
        """Riferimento legato alla versione letta dal passo 5."""
        from dataclasses import replace

        return replace(self, version_id=letto.version_id, etag=letto.etag)

    def argomenti(self) -> dict[str, Any]:
        """Argomenti di ``get_object``: bucket, chiave e versione (VersionId, o
        ``IfMatch`` con l'ETag: un oggetto cambiato dà 412 → errore tecnico, si
        ritenta e il passo 5 ricalcola le impronte)."""
        argomenti: dict[str, Any] = {"Bucket": self.bucket, "Key": self.chiave}
        if self.version_id:
            argomenti["VersionId"] = self.version_id
        elif self.etag:
            argomenti["IfMatch"] = self.etag
        return argomenti

    @classmethod
    def da_evento(cls, info: Mapping[str, Any]) -> RiferimentoFile | None:
        """Riferimento da un elemento di ``docs_s3_keys``; None se incompleto."""
        nome, bucket, chiave = (str(info.get(k) or "")
                                for k in ("nome_file", "s3_bucket", "s3_key"))
        if not (nome and bucket and chiave):
            return None
        dimensione = info.get("size_bytes")
        impronte = info.get("impronte") if isinstance(info.get("impronte"), Mapping) else {}
        return cls(
            nome_file=nome, bucket=bucket, chiave=chiave,
            dimensione_versata=int(dimensione) if isinstance(dimensione, int) else None,
            impronte_versamento={str(k).upper(): str(v).lower() for k, v in impronte.items()},
            formato_rilevato=(str(info["formato_rilevato"])
                              if info.get("formato_rilevato") else None),
        )


def riferimenti_da_evento(docs_s3_keys: Sequence[Mapping[str, Any]]) -> dict[str, RiferimentoFile]:
    """``{nome_file: riferimento}``; i riferimenti incompleti si saltano (lo Step 5
    rifiuterà il documento come «file_non_disponibile»)."""
    riferimenti: dict[str, RiferimentoFile] = {}
    for info in docs_s3_keys:
        if not isinstance(info, Mapping):
            continue
        rif = RiferimentoFile.da_evento(info)
        if rif is None:
            logger.warning("s3_riferimento_mancante", nome_file=str(info.get("nome_file", "")))
            continue
        riferimenti[rif.nome_file] = rif
    return riferimenti


@dataclass(frozen=True)
class FileLetto:
    """Ciò che si è calcolato leggendo l'intero file a blocchi."""

    dimensione: int
    #: impronte esadecimali (minuscole) per nome d'algoritmo
    impronte: Mapping[str, str]
    #: R12a (VD-C-146): versione letta (VersionId reale dello staging, o ETag)
    version_id: str | None = None
    etag: str | None = None


class FileOltreLimite(Exception):
    """Durante la lettura il file ha superato il limite: lettura interrotta."""

    def __init__(self, limite_byte: int, letti: int) -> None:
        super().__init__(f"file oltre il limite di {limite_byte} byte")
        self.limite_byte = limite_byte
        self.letti = letti


class LettoreFile(Protocol):
    """Accesso ai file del PdV (S3 in esercizio, memoria nei test)."""

    async def dimensione(self, rif: RiferimentoFile) -> int: ...

    async def leggi(
        self, rif: RiferimentoFile, *, algoritmi: Sequence[str], limite_byte: int | None = None,
    ) -> FileLetto: ...

    async def copia_in(self, rif: RiferimentoFile, destinazione: Path, *,
                       limite_byte: int) -> int: ...

    async def testa_coda(self, rif: RiferimentoFile, n: int) -> tuple[bytes, bytes]: ...

    async def contiene(self, rif: RiferimentoFile, marcatore: bytes) -> bool: ...


def calcolatori(algoritmi: Sequence[str]) -> dict[str, Any]:
    """Oggetti hashlib per i nomi indicati; ValueError se uno non è ammesso."""
    risultato: dict[str, Any] = {}
    for nome in algoritmi:
        funzione = funzione_impronta(nome)
        if funzione is None:
            raise ValueError(f"algoritmo d'impronta non ammesso: {nome!r}")
        risultato[nome.strip().upper()] = funzione()
    return risultato


@lru_cache(maxsize=1)
def _client_s3() -> Any:
    """Client boto3 unico per processo (i client boto3 sono utilizzabili da più thread)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        config=botocore.config.Config(
            signature_version="s3v4", retries={"max_attempts": 3},
            max_pool_connections=max(10, settings.concorrenza_pdv * 2),
        ),
        region_name=settings.s3_region,
    )


class LettoreS3:
    """Lettore a blocchi dallo storage S3 del versamento."""

    def __init__(self, client: Any | None = None, *, dimensione_blocco: int | None = None) -> None:
        self._client = client
        self.dimensione_blocco = dimensione_blocco or settings.lettura_blocco_kb * 1024

    def _s3(self) -> Any:
        return self._client if self._client is not None else _client_s3()

    async def dimensione(self, rif: RiferimentoFile) -> int:
        try:
            risposta = await asyncio.to_thread(
                self._s3().head_object, Bucket=rif.bucket, Key=rif.chiave)
        except Exception as exc:  # noqa: BLE001 — errore tecnico, non esito della verifica
            raise ErroreVerificaTransitorio(
                f"Lettura dallo storage non riuscita per {rif.nome_file!r}: {type(exc).__name__}"
            ) from exc
        return int(risposta["ContentLength"])

    async def leggi(
        self, rif: RiferimentoFile, *, algoritmi: Sequence[str], limite_byte: int | None = None,
    ) -> FileLetto:
        try:
            return await asyncio.to_thread(self._leggi_sync, rif, list(algoritmi), limite_byte)
        except (FileOltreLimite, ValueError):
            raise
        except Exception as exc:  # noqa: BLE001 — errore tecnico, non esito della verifica
            raise ErroreVerificaTransitorio(
                f"Lettura dallo storage non riuscita per {rif.nome_file!r}: {type(exc).__name__}"
            ) from exc

    def _leggi_sync(
        self, rif: RiferimentoFile, algoritmi: list[str], limite_byte: int | None,
    ) -> FileLetto:
        calc = calcolatori(algoritmi)
        risposta = self._s3().get_object(**rif.argomenti())
        corpo = risposta["Body"]
        letti = 0
        try:
            for blocco in corpo.iter_chunks(self.dimensione_blocco):
                letti += len(blocco)
                if limite_byte is not None and letti > limite_byte:
                    raise FileOltreLimite(limite_byte, letti)
                for c in calc.values():
                    c.update(blocco)
        finally:
            corpo.close()
        attesa = risposta.get("ContentLength")
        if attesa is not None and int(attesa) != letti:
            # Corpo troncato (connessione interrotta): errore tecnico, si ritenta
            raise ConnectionError(f"letti {letti} byte su {attesa}")
        versione = risposta.get("VersionId")
        return FileLetto(dimensione=letti, impronte={n: c.hexdigest() for n, c in calc.items()},
                         version_id=str(versione) if versione and versione != "null" else None,
                         etag=str(risposta["ETag"]) if risposta.get("ETag") else None)

    async def copia_in(self, rif: RiferimentoFile, destinazione: Path, *,
                       limite_byte: int) -> int:
        """R08: copia locale a blocchi (riconoscimento del formato e firme);
        ``FileOltreLimite`` oltre ``limite_byte`` (il file parziale si toglie)."""
        try:
            return await asyncio.to_thread(self._copia_sync, rif, destinazione, limite_byte)
        except FileOltreLimite:
            destinazione.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 — errore tecnico, non esito della verifica
            destinazione.unlink(missing_ok=True)
            raise ErroreVerificaTransitorio(
                f"Copia dallo storage non riuscita per {rif.nome_file!r}: {type(exc).__name__}"
            ) from exc

    async def testa_coda(self, rif: RiferimentoFile, n: int) -> tuple[bytes, bytes]:
        """Revisione X5: primi e ultimi ``n`` byte (richieste con Range) di un
        file oltre il limite della copia locale, per rilevare se è firmato."""
        def leggi(intervallo: str) -> bytes:
            risposta = self._s3().get_object(**rif.argomenti(), Range=intervallo)
            try:
                return bytes(risposta["Body"].read(n + 1))[:n]
            finally:
                risposta["Body"].close()
        try:
            return (await asyncio.to_thread(leggi, f"bytes=0-{n - 1}"),
                    await asyncio.to_thread(leggi, f"bytes=-{n}"))
        except Exception as exc:  # noqa: BLE001 — errore tecnico, non esito della verifica
            raise ErroreVerificaTransitorio(
                f"Lettura dallo storage non riuscita per {rif.nome_file!r}: {type(exc).__name__}"
            ) from exc

    async def contiene(self, rif: RiferimentoFile, marcatore: bytes) -> bool:
        """Revisione Y3: il marcatore compare nel file (lettura a blocchi di
        tutto l'oggetto, memoria costante): firma PAdES di un PDF oltre il
        limite della copia locale."""
        from libs.shared.firme.rilevazione import cerca_in_blocchi

        def cerca() -> bool:
            risposta = self._s3().get_object(**rif.argomenti())
            corpo = risposta["Body"]
            try:
                return cerca_in_blocchi(corpo.iter_chunks(self.dimensione_blocco), marcatore)
            finally:
                corpo.close()
        try:
            return await asyncio.to_thread(cerca)
        except Exception as exc:  # noqa: BLE001 — errore tecnico, non esito della verifica
            raise ErroreVerificaTransitorio(
                f"Lettura dallo storage non riuscita per {rif.nome_file!r}: {type(exc).__name__}"
            ) from exc

    def _copia_sync(self, rif: RiferimentoFile, destinazione: Path, limite_byte: int) -> int:
        risposta = self._s3().get_object(**rif.argomenti())
        corpo = risposta["Body"]
        scritti = 0
        try:
            with destinazione.open("wb") as f:
                for blocco in corpo.iter_chunks(self.dimensione_blocco):
                    scritti += len(blocco)
                    if scritti > limite_byte:
                        raise FileOltreLimite(limite_byte, scritti)
                    f.write(blocco)
        finally:
            corpo.close()
        return scritti
