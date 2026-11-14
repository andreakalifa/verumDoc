# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/infrastructure/storage.py — storage conservativo (WORM) e di transito per il
ciclo di vita.

Riferimenti: contratto Q2 di R05; REGOLE_SVILUPPO §10.1-§10.3, §10.2 (ogni
scrittura conservativa porta la retention), §19.4, §27.6.

- ``scrivi_worm_da_file``: scrittura conservativa di un allegato, verbale o
  atto da un file temporaneo (mai il documento intero in memoria), con
  Object Lock COMPLIANCE, retention, checksum SHA-256 verificato dal server e
  nessuna sovrascrittura; un oggetto già presente con la stessa impronta si
  riusa (ripresa dopo un crash), con impronta diversa è un errore.
- ``versioni_del_pda``: TUTTE le versioni degli oggetti del PdA
  (``<ente>/<aip>/``) con retention e legal hold letti da ``head_object``.
- ``elimina_versione``: cancellazione di UNA versione. Il chiamante l'ha già
  controllata con ``app.domain.scarto.motivo_blocco_oggetto``; mai
  ``BypassGovernanceRetention``.
- ``LettoreS3``: file in sola lettura con ``seek`` sopra le richieste con
  intervallo (``Range``), per aprire uno ZIP grande senza scaricarlo.
- ``copia_in_worm``: copia lato server (anche a parti) di un PdD ricevuto
  nello storage conservativo con retention.
"""
from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, Any

import structlog
from botocore.exceptions import ClientError

from app.config import settings
from app.domain.scarto import OggettoWorm
from libs.shared.storage.client import client_s3
from libs.shared.storage.worm import (
    MODALITA_LOCK,
    ErroreStorageConservativo,
    OggettoGiaPresente,
    RetentionNonDeterminabile,
    bucket_conservativo,
    verifica_bucket_conservativo,
)

logger = structlog.get_logger(__name__)

BLOCCO = 1024 * 1024
#: Retention «già scaduta» dei marcatori di cancellazione (non hanno contenuto)
_INIZIO = datetime(1970, 1, 1, tzinfo=UTC)


def client() -> Any:
    return client_s3(endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
                     secret_key=settings.s3_secret_key, region=settings.s3_region,
                     retries={"max_attempts": 3, "mode": "standard"})


def bucket_worm() -> str:
    """Bucket conservativo, coerente con Terraform fuori dallo sviluppo (§19.4)."""
    return bucket_conservativo(settings)


def _codice(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


@dataclass(frozen=True)
class OggettoScritto:
    bucket: str
    chiave: str
    version_id: str | None
    sha256: str
    dimensione: int
    riusato: bool


def sha256_file(f: IO[bytes]) -> tuple[str, int]:
    """Impronta e dimensione di un file aperto (letto a blocchi, poi riavvolto)."""
    f.seek(0)
    h = hashlib.sha256()
    n = 0
    while blocco := f.read(BLOCCO):
        h.update(blocco)
        n += len(blocco)
    f.seek(0)
    return h.hexdigest(), n


def scrivi_worm_da_file(s3: Any, bucket: str, chiave: str, f: IO[bytes], *,
                        content_type: str, conserva_fino_a: datetime,
                        adesso: datetime) -> OggettoScritto:
    """Scrittura conservativa sincrona (da eseguire in un thread)."""
    if conserva_fino_a <= adesso:
        raise RetentionNonDeterminabile("la retention deve terminare nel futuro")
    verifica_bucket_conservativo(s3, bucket, produzione=settings.in_produzione,
                                 regione=settings.s3_region)
    sha_hex, dimensione = sha256_file(f)
    try:
        esistente = s3.head_object(Bucket=bucket, Key=chiave, ChecksumMode="ENABLED")
    except ClientError as exc:
        if _codice(exc) not in ("404", "NoSuchKey", "NotFound"):
            raise ErroreStorageConservativo(f"controllo di esistenza fallito: {_codice(exc)}",
                                            bucket=bucket, chiave=chiave) from exc
    else:
        # Ripresa dopo un crash: stesso contenuto = stesso documento, si riusa
        if _sha256_oggetto(s3, bucket, chiave, esistente) == sha_hex:
            return OggettoScritto(bucket, chiave, esistente.get("VersionId"), sha_hex,
                                  int(esistente.get("ContentLength", dimensione)), True)
        raise OggettoGiaPresente("oggetto già presente con contenuto diverso: sovrascrittura "
                                 "vietata", bucket=bucket, chiave=chiave)
    checksum = base64.b64encode(bytes.fromhex(sha_hex)).decode("ascii")
    try:
        risposta = s3.put_object(
            Bucket=bucket, Key=chiave, Body=f, ContentType=content_type,
            ContentLength=dimensione,
            ObjectLockMode=MODALITA_LOCK, ObjectLockRetainUntilDate=conserva_fino_a,
            ChecksumAlgorithm="SHA256", ChecksumSHA256=checksum, IfNoneMatch="*")
    except ClientError as exc:
        if _codice(exc) in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
            raise OggettoGiaPresente("oggetto creato in concorrenza", bucket=bucket,
                                     chiave=chiave) from exc
        raise ErroreStorageConservativo(f"scrittura rifiutata dallo storage: {_codice(exc)}",
                                        bucket=bucket, chiave=chiave) from exc
    if risposta.get("ChecksumSHA256") and risposta["ChecksumSHA256"] != checksum:
        raise ErroreStorageConservativo("checksum restituito diverso da quello inviato",
                                        bucket=bucket, chiave=chiave)
    return OggettoScritto(bucket, chiave, risposta.get("VersionId"), sha_hex, dimensione, False)


def _sha256_oggetto(s3: Any, bucket: str, chiave: str, testa: dict[str, Any]) -> str:
    """SHA-256 di un oggetto: dal checksum dello storage se c'è, altrimenti
    rileggendolo a blocchi (impronta sui byte, §10.3)."""
    checksum = testa.get("ChecksumSHA256")
    if checksum and "-" not in checksum:
        return base64.b64decode(checksum).hex()
    h = hashlib.sha256()
    corpo = s3.get_object(Bucket=bucket, Key=chiave)["Body"]
    for blocco in corpo.iter_chunks(BLOCCO):
        h.update(blocco)
    return h.hexdigest()


def versioni_del_pda(s3: Any, bucket: str, prefisso: str) -> list[OggettoWorm]:
    """Tutte le versioni (e i marcatori di cancellazione) sotto ``prefisso``,
    con retention e legal hold. Un errore dello storage si propaga: nessuna
    decisione su dati parziali."""
    if not prefisso.endswith("/") or prefisso.count("/") < 2:
        raise ErroreStorageConservativo("prefisso del PdA non valido", prefisso=prefisso)
    oggetti: list[OggettoWorm] = []
    pagine = s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefisso)
    for pagina in pagine:
        for v in pagina.get("Versions", []) or []:
            testa = s3.head_object(Bucket=bucket, Key=v["Key"], VersionId=v["VersionId"])
            oggetti.append(OggettoWorm(
                chiave=v["Key"], version_id=v["VersionId"],
                conserva_fino_a=testa.get("ObjectLockRetainUntilDate"),
                legal_hold=testa.get("ObjectLockLegalHoldStatus") == "ON"))
        for m in pagina.get("DeleteMarkers", []) or []:
            # Un marcatore non ha contenuto né retention: si toglie con il resto
            oggetti.append(OggettoWorm(chiave=m["Key"], version_id=m["VersionId"],
                                       conserva_fino_a=_INIZIO, legal_hold=False))
    return sorted(oggetti, key=lambda o: (o.chiave, o.version_id))


def elimina_versione(s3: Any, bucket: str, oggetto: OggettoWorm) -> None:
    """Cancella UNA versione già controllata dal chiamante (mai forzata)."""
    s3.delete_object(Bucket=bucket, Key=oggetto.chiave, VersionId=oggetto.version_id)


def stato_versione(s3: Any, bucket: str, oggetto: OggettoWorm) -> OggettoWorm | None:
    """Revisione 2 di R05 (S3): stato ATTUALE di una versione (retention e legal
    hold riletti adesso), ``None`` se la versione non esiste più. Serve a
    classificare un rifiuto della cancellazione: blocco definitivo (legal hold
    arrivato dopo l'elenco, retention) o errore dello storage."""
    try:
        testa = s3.head_object(Bucket=bucket, Key=oggetto.chiave, VersionId=oggetto.version_id)
    except ClientError as exc:
        if _codice(exc) in ("404", "NoSuchKey", "NoSuchVersion", "NotFound"):
            return None
        if _codice(exc) in ("405", "MethodNotAllowed"):   # marcatore di cancellazione
            return oggetto
        raise
    return OggettoWorm(chiave=oggetto.chiave, version_id=oggetto.version_id,
                       conserva_fino_a=testa.get("ObjectLockRetainUntilDate"),
                       legal_hold=testa.get("ObjectLockLegalHoldStatus") == "ON")


def e_marcatore(oggetto: OggettoWorm) -> bool:
    """Marcatore di cancellazione (nessun contenuto né retention)."""
    return oggetto.conserva_fino_a == _INIZIO


def estendi_retention(s3: Any, bucket: str, chiave: str, version_id: str | None,
                      fino_a: datetime) -> datetime | None:
    """R09 (D04): ESTENDE la retention COMPLIANCE di UNA versione fino a
    ``fino_a``, mai in riduzione (rilettura nello storage). R12a: implementazione
    unica in ``libs/shared/storage/worm.estendi_retention`` (anche per la hashchain)."""
    from libs.shared.storage.worm import estendi_retention as estendi

    return estendi(s3, bucket, chiave, version_id, fino_a)


@dataclass(frozen=True)
class VersioneLetta:
    """Revisione 4 di R05 (U2): una versione di un documento del WORM riletta
    dal lavoratore (impronta calcolata sui byte, retention e legal hold)."""

    sha256: str
    dimensione: int
    conserva_fino_a: datetime | None
    legal_hold: bool


def leggi_versione(s3: Any, bucket: str, chiave: str, version_id: str | None,
                   f: IO[bytes]) -> VersioneLetta | None:
    """Scarica UNA versione su ``f`` (a blocchi) calcolandone l'impronta;
    ``None`` se la versione non esiste. Un altro errore dello storage si
    propaga (temporaneo: il lavoratore ritenta)."""
    argomenti: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
    if version_id:
        argomenti["VersionId"] = version_id
    try:
        risposta = s3.get_object(**argomenti)
    except ClientError as exc:
        if _codice(exc) in ("404", "NoSuchKey", "NoSuchVersion", "NotFound"):
            return None
        raise
    h = hashlib.sha256()
    n = 0
    for blocco in risposta["Body"].iter_chunks(BLOCCO):
        h.update(blocco)
        f.write(blocco)
        n += len(blocco)
    f.seek(0)
    return VersioneLetta(sha256=h.hexdigest(), dimensione=n,
                         conserva_fino_a=risposta.get("ObjectLockRetainUntilDate"),
                         legal_hold=risposta.get("ObjectLockLegalHoldStatus") == "ON")


class LettoreS3(io.RawIOBase):
    """File in sola lettura con ``seek`` su un oggetto S3 (richieste ``Range``)."""

    def __init__(self, s3: Any, bucket: str, chiave: str, *, dimensione: int | None = None,
                 version_id: str | None = None) -> None:
        self._s3, self._bucket, self._chiave, self._vid = s3, bucket, chiave, version_id
        if dimensione is None:
            argomenti = {"Bucket": bucket, "Key": chiave}
            if version_id:
                argomenti["VersionId"] = version_id
            dimensione = int(s3.head_object(**argomenti)["ContentLength"])
        self.dimensione = dimensione
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self.dimensione + offset
        self._pos = max(0, self._pos)
        return self._pos

    def readinto(self, buffer: Any) -> int:
        dati = self.read(len(buffer))
        buffer[: len(dati)] = dati
        return len(dati)

    def read(self, n: int = -1) -> bytes:
        if self._pos >= self.dimensione:
            return b""
        fine = self.dimensione if n is None or n < 0 else min(self.dimensione, self._pos + n)
        argomenti = {"Bucket": self._bucket, "Key": self._chiave,
                     "Range": f"bytes={self._pos}-{fine - 1}"}
        if self._vid:
            argomenti["VersionId"] = self._vid
        dati = self._s3.get_object(**argomenti)["Body"].read()
        self._pos += len(dati)
        return bytes(dati)


#: Oltre questa dimensione la copia è a parti (CopyObject arriva a 5 GB)
SOGLIA_COPIA_MULTIPART = 1024 * 1024 * 1024
PARTE_COPIA = 256 * 1024 * 1024
#: Storage S3-compatibili (moto) che chiedono l'intestazione del checksum anche
#: su CopyObject con Object Lock; AWS no. Stessa compatibilità dell'aip-builder.
CODICI_CHECKSUM_RICHIESTO = ("MissingUploadObjectWithObjectLockHeaders",)


def _copia_con_checksum(s3: Any, argomenti: dict[str, Any]) -> dict[str, Any]:
    try:
        return dict(s3.copy_object(**argomenti))
    except ClientError as exc:
        if _codice(exc) not in CODICI_CHECKSUM_RICHIESTO:
            raise
    import uuid

    def _aggiungi(request: Any, **_: Any) -> None:
        request.headers["x-amz-sdk-checksum-algorithm"] = "SHA256"

    id_gestore = f"verumdoc-cv-checksum-copia-{uuid.uuid4().hex}"
    s3.meta.events.register("before-sign.s3.CopyObject", _aggiungi, unique_id=id_gestore)
    try:
        return dict(s3.copy_object(**argomenti))
    finally:
        s3.meta.events.unregister("before-sign.s3.CopyObject", unique_id=id_gestore)


def copia_in_worm(s3: Any, origine_bucket: str, origine_chiave: str, bucket: str, chiave: str,
                  *, conserva_fino_a: datetime, content_type: str) -> str | None:
    """Copia lato server (a parti oltre ``SOGLIA_COPIA_MULTIPART``) con retention
    COMPLIANCE; nessun byte passa dal servizio. Nessuna sovrascrittura: se
    l'oggetto c'è già si riusa (ripresa) solo con la stessa dimensione.
    Restituisce la versione creata. (Stessa tecnica di
    ``aip-builder/app/services/s3_uploader.copia_oggetto_conservato``: proposta di
    spostarla in ``libs/shared/storage`` nel rapporto R05.)"""
    verifica_bucket_conservativo(s3, bucket, produzione=settings.in_produzione,
                                 regione=settings.s3_region)
    sorgente = s3.head_object(Bucket=origine_bucket, Key=origine_chiave)
    dimensione = int(sorgente["ContentLength"])
    try:
        esistente = s3.head_object(Bucket=bucket, Key=chiave)
    except ClientError as exc:
        if _codice(exc) not in ("404", "NoSuchKey", "NotFound"):
            raise
    else:
        if int(esistente["ContentLength"]) == dimensione:
            return esistente.get("VersionId")
        raise OggettoGiaPresente("copia già presente con dimensione diversa", bucket=bucket,
                                 chiave=chiave)
    origine = {"Bucket": origine_bucket, "Key": origine_chiave}
    vincolo = {"CopySourceIfMatch": sorgente["ETag"]} if sorgente.get("ETag") else {}
    # Revisione R05 (R3): nessuna intestazione di legal hold (OFF è il
    # predefinito): il ruolo IAM di ciclo-vita non ha s3:PutObjectLegalHold
    lock = {"ObjectLockMode": MODALITA_LOCK, "ObjectLockRetainUntilDate": conserva_fino_a}
    if dimensione <= SOGLIA_COPIA_MULTIPART:
        risposta = _copia_con_checksum(s3, {
            "Bucket": bucket, "Key": chiave, "CopySource": origine,
            "MetadataDirective": "REPLACE", "ContentType": content_type,
            "ChecksumAlgorithm": "SHA256", **vincolo, **lock})
        return risposta.get("VersionId")
    avvio = s3.create_multipart_upload(Bucket=bucket, Key=chiave, ContentType=content_type,
                                       **lock)
    upload_id = avvio["UploadId"]
    try:
        parti = []
        for numero, inizio in enumerate(range(0, dimensione, PARTE_COPIA), start=1):
            fine = min(inizio + PARTE_COPIA, dimensione) - 1
            r = s3.upload_part_copy(Bucket=bucket, Key=chiave, UploadId=upload_id,
                                    PartNumber=numero, CopySource=origine,
                                    CopySourceRange=f"bytes={inizio}-{fine}", **vincolo)
            parti.append({"ETag": r["CopyPartResult"]["ETag"], "PartNumber": numero})
        fatto = s3.complete_multipart_upload(Bucket=bucket, Key=chiave, UploadId=upload_id,
                                             MultipartUpload={"Parts": parti}, IfNoneMatch="*")
    except BaseException:
        try:
            s3.abort_multipart_upload(Bucket=bucket, Key=chiave, UploadId=upload_id)
        except Exception as exc:  # noqa: BLE001 — l'errore originale prevale
            logger.warning("abort_multipart_non_riuscito", chiave=chiave,
                           errore=type(exc).__name__)
        raise
    return fatto.get("VersionId")


def iter_blocchi(f: IO[bytes], dimensione: int = BLOCCO) -> Iterator[bytes]:
    while blocco := f.read(dimensione):
        yield blocco


__all__ = ["LettoreS3", "OggettoScritto", "bucket_worm", "client", "copia_in_worm",
           "e_marcatore", "elimina_versione", "estendi_retention", "iter_blocchi",
           "scrivi_worm_da_file", "sha256_file", "versioni_del_pda"]
