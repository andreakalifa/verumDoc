# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/aip-builder/app/services/s3_uploader.py
Scrittura del PdA sullo storage conservativo con Object Lock — VD-C-011.

La logica WORM (retention per oggetto COMPLIANCE, legal hold, nessuna
sovrascrittura, verifica Object Lock del bucket, checksum) è in
``libs/shared/storage/worm.py``, condivisa con la hashchain (RdV definitivo).
Qui restano il legame con la configurazione del servizio (S3_BUCKET_AIP, ENV,
regione), il client boto3 del processo e ``upload_aip_to_s3``.
I nomi del modulo condiviso restano importabili da qui.

R03 (b5) — file fino a 5 GiB senza tenerli in memoria:
  - ``impronte_oggetto``: impronte e dimensione calcolate LEGGENDO A BLOCCHI un
    oggetto dello storage (mai il file intero in memoria);
  - ``copia_in_worm``: copia SERVER-SIDE (CopyObject, o UploadPartCopy oltre
    ``COPIA_MULTIPART_SOGLIA_MB``) dallo staging al bucket conservativo, con
    retention COMPLIANCE per oggetto, legal hold, nessuna sovrascrittura, origine
    vincolata all'ETag letto (i byte copiati sono quelli di cui si è calcolata
    l'impronta) e verifica della copia (checksum SHA-256 dello storage o, se
    assente, rilettura a blocchi della copia);
  - ``oggetto_identico``: confronto per impronta, in streaming, con un oggetto
    già presente (ritentativi idempotenti).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import boto3
import botocore.config
import structlog
from botocore.exceptions import ClientError

from app.config import settings
from libs.shared.impronte import funzione_impronta
from libs.shared.storage import worm
from libs.shared.storage.worm import (  # noqa: F401 — riesportati per i chiamanti
    AMBIENTI_TERRAFORM,
    ENDPOINT_LOCALI,
    MODALITA_LOCK,
    PROGETTO_TERRAFORM,
    BucketSenzaObjectLock,
    ConfigurazioneStorageNonValida,
    ErroreStorageConservativo,
    OggettoGiaPresente,
    PoliticaConservazione,
    RetentionNonDeterminabile,
    RetentionOggetto,
    _bucket_verificati,
    _chiave_valida,
    aggiungi_anni,
    calcola_retention,
    nome_bucket_terraform,
)

logger = structlog.get_logger(__name__)


def problemi_configurazione(cfg: Any = settings) -> list[str]:
    """Controlli sulla configurazione dello storage del servizio (REGOLE §19.4)."""
    return worm.problemi_configurazione(cfg)


def bucket_conservativo(cfg: Any = settings) -> str:
    """Unica fonte del nome del bucket conservativo; errore se incoerente."""
    return worm.bucket_conservativo(cfg)


def verifica_bucket_conservativo(client: Any, bucket: str, *, produzione: bool) -> None:
    worm.verifica_bucket_conservativo(
        client, bucket, produzione=produzione, regione=settings.s3_region,
    )


def scrivi_oggetto_conservato(
    client: Any,
    bucket: str,
    chiave: str,
    contenuto: bytes,
    content_type: str,
    retention: RetentionOggetto,
    *,
    adesso: datetime,
    produzione: bool,
) -> dict:
    """Scrittura WORM sincrona (vedi ``libs.shared.storage.worm``)."""
    return worm.scrivi_oggetto_conservato(
        client, bucket, chiave, contenuto, content_type, retention,
        adesso=adesso, produzione=produzione, regione=settings.s3_region,
    )


@lru_cache(maxsize=1)
def _client() -> Any:
    """Client boto3 unico per processo (B-A12)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        config=botocore.config.Config(signature_version="s3v4", retries={"max_attempts": 3}),
        region_name=settings.s3_region,
    )



async def upload_aip_to_s3(
    aip_id: str,
    tenant_id: str,
    content: bytes,
    key_suffix: str,
    content_type: str = "application/octet-stream",
    *,
    retention: RetentionOggetto,
) -> tuple[str, str, str | None]:
    """Carica un oggetto del PdA sul bucket conservativo con Object Lock COMPLIANCE.

    `retention` è obbligatoria (REGOLE §10.2): si calcola con calcola_retention()
    dalla classe documentale. Restituisce (s3_key, bucket, version_id).
    Solleva ErroreStorageConservativo (o sottoclassi) senza aver scritto nulla.
    """
    bucket = bucket_conservativo()
    s3_key = f"{tenant_id}/{aip_id}/{key_suffix}"
    resp = await asyncio.to_thread(
        scrivi_oggetto_conservato,
        _client(),
        bucket,
        s3_key,
        content,
        content_type,
        retention,
        adesso=datetime.now(UTC),
        produzione=settings.env.lower() == "production",
    )
    version_id = resp.get("VersionId")
    logger.info(
        "s3_upload_conservativo_ok",
        bucket=bucket,
        key=s3_key,
        size=len(content),
        version_id=version_id,
        retention_fino_a=retention.conserva_fino_a.isoformat(),
        legal_hold=retention.legal_hold,
    )
    return s3_key, bucket, version_id


def compute_aip_hash(content: bytes) -> tuple[str, str]:
    """Calcola SHA-256 e SHA-512 del contenuto."""
    sha256 = hashlib.sha256(content).hexdigest()
    sha512 = hashlib.sha512(content).hexdigest()
    return sha256, sha512


# ── R03 (b5): impronte in streaming e copia server-side nello storage WORM ──

MIB = 1024 * 1024


class OrigineModificata(ErroreStorageConservativo):
    """L'oggetto d'origine è cambiato fra il calcolo delle impronte e la copia
    (ETag diverso): errore transitorio, il nuovo tentativo ricalcola tutto."""

    codice = "S3-ORIGINE-MODIFICATA"


class CopiaNonCorrispondente(ErroreStorageConservativo):
    """La copia nello storage conservativo non ha l'impronta o la dimensione attese."""

    codice = "S3-COPIA-DIVERSA"


@dataclass(frozen=True)
class ImpronteOggetto:
    """Dimensione e impronte di un oggetto, calcolate leggendolo a blocchi."""

    dimensione: int
    #: impronte esadecimali per nome d'algoritmo (``SHA-256``, ``SHA-512``, …)
    impronte: dict[str, str]
    etag: str | None = None
    version_id: str | None = None


def _calcolatori(algoritmi: Sequence[str]) -> dict[str, Any]:
    risultato = {}
    for nome in algoritmi:
        funzione = funzione_impronta(nome)
        if funzione is None:
            raise ValueError(f"algoritmo d'impronta non ammesso: {nome!r}")
        risultato[nome.strip().upper()] = funzione()
    return risultato


def impronte_oggetto_sync(
    client: Any, bucket: str, chiave: str, algoritmi: Sequence[str], *,
    version_id: str | None = None, dimensione_blocco: int | None = None,
) -> ImpronteOggetto:
    """Legge l'oggetto a blocchi e calcola dimensione e impronte (in memoria al più
    un blocco). Un corpo troncato è un errore (ConnectionError: si ritenta)."""
    calc = _calcolatori(algoritmi)
    blocco = dimensione_blocco or settings.lettura_blocco_kb * 1024
    kwargs: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
    if version_id:
        kwargs["VersionId"] = version_id
    risposta = client.get_object(**kwargs)
    corpo = risposta["Body"]
    letti = 0
    try:
        for parte in corpo.iter_chunks(blocco):
            letti += len(parte)
            for c in calc.values():
                c.update(parte)
    finally:
        corpo.close()
    attesa = risposta.get("ContentLength")
    if attesa is not None and int(attesa) != letti:
        raise ConnectionError(f"letti {letti} byte su {attesa} di {chiave!r}")
    return ImpronteOggetto(
        dimensione=letti, impronte={n: c.hexdigest() for n, c in calc.items()},
        etag=risposta.get("ETag"), version_id=risposta.get("VersionId"),
    )


async def impronte_oggetto(
    bucket: str, chiave: str, algoritmi: Sequence[str] = ("SHA-256", "SHA-512"), *,
    client: Any | None = None, version_id: str | None = None,
) -> ImpronteOggetto:
    """Versione asincrona (thread) di ``impronte_oggetto_sync``."""
    return await asyncio.to_thread(
        impronte_oggetto_sync, client or _client(), bucket, chiave, list(algoritmi),
        version_id=version_id,
    )


async def oggetto_identico(
    bucket: str, chiave: str, sha256_atteso: str, *, client: Any | None = None,
) -> bool:
    """True se l'oggetto già presente ha l'impronta SHA-256 attesa (lettura a blocchi)."""
    letto = await impronte_oggetto(bucket, chiave, ("SHA-256",), client=client)
    return letto.impronte["SHA-256"].lower() == sha256_atteso.lower()


def _codice_s3(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _assicura_retention(
    client: Any, bucket: str, chiave: str, version_id: str | None, retention: RetentionOggetto,
) -> None:
    """Verifica che la copia porti la retention COMPLIANCE (e il legal hold) richiesti;
    se lo storage non li ha applicati alla creazione li applica ora (si può solo
    estendere, mai ridurre) e riverifica. Errore esplicito se non ci riesce."""
    kv: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
    if version_id:
        kv["VersionId"] = version_id

    def _conforme(testa: dict) -> bool:
        fino_a = testa.get("ObjectLockRetainUntilDate")
        ok = (testa.get("ObjectLockMode") == retention.modalita and fino_a is not None
              and fino_a >= retention.conserva_fino_a)
        if retention.legal_hold:
            ok = ok and testa.get("ObjectLockLegalHoldStatus") == "ON"
        return ok

    if _conforme(client.head_object(**kv)):
        return
    logger.warning("retention_applicata_dopo_la_copia", bucket=bucket, key=chiave)
    client.put_object_retention(
        **kv, Retention={"Mode": retention.modalita, "RetainUntilDate": retention.conserva_fino_a},
        ChecksumAlgorithm="SHA256",
    )
    if retention.legal_hold:
        client.put_object_legal_hold(**kv, LegalHold={"Status": "ON"}, ChecksumAlgorithm="SHA256")
    if not _conforme(client.head_object(**kv)):
        raise worm.ErroreStorageConservativo(
            "retention COMPLIANCE non applicata alla copia", bucket=bucket, chiave=chiave,
        )


def copia_oggetto_conservato(
    client: Any,
    *,
    bucket_origine: str,
    chiave_origine: str,
    bucket: str,
    chiave: str,
    content_type: str,
    retention: RetentionOggetto,
    origine: ImpronteOggetto,
    adesso: datetime,
    produzione: bool,
    soglia_multipart: int | None = None,
    dimensione_parte: int | None = None,
) -> dict:
    """
    Copia server-side WORM sincrona (nessun byte passa dal servizio).

    Stesse garanzie di ``worm.scrivi_oggetto_conservato``: chiave valida, solo
    COMPLIANCE, retention nel futuro, bucket con Object Lock, nessuna
    sovrascrittura. In più: origine vincolata all'ETag letto, verifica della copia.
    Restituisce ``{"VersionId", "verificata_dallo_storage"}``.
    """
    worm._chiave_valida(chiave)
    if retention.modalita != MODALITA_LOCK:
        raise RetentionNonDeterminabile("ammessa solo la modalità COMPLIANCE")
    if retention.conserva_fino_a <= adesso:
        raise RetentionNonDeterminabile("la retention deve terminare nel futuro")
    worm.verifica_bucket_conservativo(client, bucket, produzione=produzione,
                                      regione=settings.s3_region)
    try:
        client.head_object(Bucket=bucket, Key=chiave)
    except ClientError as exc:
        if _codice_s3(exc) not in ("404", "NoSuchKey", "NotFound"):
            raise ErroreStorageConservativo(
                f"controllo di esistenza fallito: {_codice_s3(exc)}", bucket=bucket, chiave=chiave,
            ) from exc
    else:
        raise OggettoGiaPresente("oggetto già presente: sovrascrittura vietata",
                                 bucket=bucket, chiave=chiave)

    sha256_hex = origine.impronte["SHA-256"]
    sha256_b64 = base64.b64encode(bytes.fromhex(sha256_hex)).decode("ascii")
    sorgente: dict[str, Any] = {"Bucket": bucket_origine, "Key": chiave_origine}
    if origine.version_id:
        sorgente["VersionId"] = origine.version_id
    vincolo = {"CopySourceIfMatch": origine.etag} if origine.etag else {}
    lock = {
        "ObjectLockMode": retention.modalita,
        "ObjectLockRetainUntilDate": retention.conserva_fino_a,
        "ObjectLockLegalHoldStatus": "ON" if retention.legal_hold else "OFF",
    }
    soglia = (soglia_multipart if soglia_multipart is not None
              else settings.copia_multipart_soglia_mb * MIB)
    parte = dimensione_parte or settings.copia_parte_mb * MIB
    verificata = False
    try:
        if origine.dimensione <= soglia:
            argomenti = dict(
                Bucket=bucket, Key=chiave, CopySource=sorgente, MetadataDirective="REPLACE",
                ContentType=content_type, ChecksumAlgorithm="SHA256", **vincolo, **lock,
            )
            try:
                risposta = client.copy_object(**argomenti)
            except ClientError as exc:
                if _codice_s3(exc) not in CODICI_CHECKSUM_RICHIESTO:
                    raise
                # Compatibilità: alcuni storage S3-compatibili (es. il simulatore usato
                # nei test e2e) chiedono l'intestazione del checksum anche sulla copia
                # con Object Lock; AWS no. Si ripete UNA volta aggiungendola.
                risposta = _copia_con_intestazione_checksum(client, argomenti)
            version_id = risposta.get("VersionId")
            checksum = (risposta.get("CopyObjectResult") or {}).get("ChecksumSHA256")
            if checksum is not None:
                if checksum != sha256_b64:
                    raise CopiaNonCorrispondente(
                        "checksum SHA-256 della copia diverso dall'impronta calcolata",
                        bucket=bucket, chiave=chiave,
                    )
                verificata = True
        else:
            version_id = _copia_multipart(
                client, sorgente, bucket, chiave, content_type, lock, vincolo,
                origine.dimensione, parte,
            )
    except ClientError as exc:
        codice = _codice_s3(exc)
        if codice in ("PreconditionFailed", "412") and vincolo:
            # CopySourceIfMatch fallito, oppure (multipart) If-None-Match sulla destinazione
            raise OrigineModificata(
                "origine modificata o destinazione creata in concorrenza",
                bucket=bucket, chiave=chiave,
            ) from exc
        if codice in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
            raise OggettoGiaPresente("oggetto creato in concorrenza: sovrascrittura vietata",
                                     bucket=bucket, chiave=chiave) from exc
        raise ErroreStorageConservativo(
            f"copia rifiutata dallo storage: {codice}", bucket=bucket, chiave=chiave,
        ) from exc
    _assicura_retention(client, bucket, chiave, version_id, retention)
    return {"VersionId": version_id, "verificata_dallo_storage": verificata}


#: Codici con cui uno storage S3-compatibile chiede l'intestazione del checksum
#: anche su CopyObject con Object Lock (vedi copia_oggetto_conservato).
CODICI_CHECKSUM_RICHIESTO = ("MissingUploadObjectWithObjectLockHeaders",)


def _copia_con_intestazione_checksum(client: Any, argomenti: dict) -> dict:
    """CopyObject con ``x-amz-sdk-checksum-algorithm: SHA256`` aggiunta solo a questa
    chiamata (gestore botocore registrato e rimosso subito; id univoco per chiamata)."""
    import uuid

    def _aggiungi(request: Any, **_: Any) -> None:
        request.headers["x-amz-sdk-checksum-algorithm"] = "SHA256"

    id_gestore = f"verumdoc-checksum-copia-{uuid.uuid4().hex}"
    evento = "before-sign.s3.CopyObject"
    client.meta.events.register(evento, _aggiungi, unique_id=id_gestore)
    try:
        return client.copy_object(**argomenti)
    finally:
        client.meta.events.unregister(evento, unique_id=id_gestore)


def _copia_multipart(
    client: Any, sorgente: dict, bucket: str, chiave: str, content_type: str,
    lock: dict, vincolo: dict, dimensione: int, parte: int,
) -> str | None:
    """UploadPartCopy a intervalli; completamento con If-None-Match: * (nessuna
    sovrascrittura atomica); multipart annullato su ogni errore."""
    avvio = client.create_multipart_upload(
        Bucket=bucket, Key=chiave, ContentType=content_type, **lock)
    upload_id = avvio["UploadId"]
    try:
        parti = []
        for numero, inizio in enumerate(range(0, dimensione, parte), start=1):
            fine = min(inizio + parte, dimensione) - 1
            r = client.upload_part_copy(
                Bucket=bucket, Key=chiave, UploadId=upload_id, PartNumber=numero,
                CopySource=sorgente, CopySourceRange=f"bytes={inizio}-{fine}", **vincolo,
            )
            parti.append({"ETag": r["CopyPartResult"]["ETag"], "PartNumber": numero})
        fatto = client.complete_multipart_upload(
            Bucket=bucket, Key=chiave, UploadId=upload_id,
            MultipartUpload={"Parts": parti}, IfNoneMatch="*",
        )
    except BaseException:
        try:
            client.abort_multipart_upload(Bucket=bucket, Key=chiave, UploadId=upload_id)
        except Exception as exc:  # noqa: BLE001 — si registra, l'errore originale prevale
            logger.warning("abort_multipart_non_riuscito", key=chiave, errore=type(exc).__name__)
        raise
    return fatto.get("VersionId")


async def copia_in_worm(
    *,
    aip_id: str,
    tenant_id: str,
    bucket_origine: str,
    chiave_origine: str,
    key_suffix: str,
    content_type: str,
    retention: RetentionOggetto,
    origine: ImpronteOggetto,
    client: Any | None = None,
) -> tuple[str, str, str | None]:
    """
    Copia server-side di un file dello staging nel bucket conservativo, con la
    stessa chiave di ``upload_aip_to_s3`` (``{tenant}/{aip}/{suffisso}``).

    Se lo storage non ha verificato la copia col proprio checksum (copia
    multipart, o storage che non lo restituisce) la copia si RILEGGE a blocchi e
    se ne confrontano impronta e dimensione. Restituisce (s3_key, bucket, version_id).
    Solleva ``OggettoGiaPresente`` (il chiamante decide se è identico),
    ``OrigineModificata`` (transitorio), ``CopiaNonCorrispondente``.
    """
    s3 = client or _client()
    bucket = bucket_conservativo()
    s3_key = f"{tenant_id}/{aip_id}/{key_suffix}"
    esito = await asyncio.to_thread(
        copia_oggetto_conservato, s3,
        bucket_origine=bucket_origine, chiave_origine=chiave_origine,
        bucket=bucket, chiave=s3_key, content_type=content_type, retention=retention,
        origine=origine, adesso=datetime.now(UTC),
        produzione=settings.env.lower() == "production",
    )
    version_id = esito.get("VersionId")
    if not esito["verificata_dallo_storage"]:
        copia = await impronte_oggetto(bucket, s3_key, ("SHA-256",), client=s3,
                                       version_id=version_id)
        if (copia.dimensione != origine.dimensione
                or copia.impronte["SHA-256"] != origine.impronte["SHA-256"]):
            raise CopiaNonCorrispondente(
                "la copia nello storage conservativo non corrisponde all'origine",
                bucket=bucket, chiave=s3_key,
            )
    logger.info(
        "s3_copia_conservativa_ok", bucket=bucket, key=s3_key, size=origine.dimensione,
        version_id=version_id, retention_fino_a=retention.conserva_fino_a.isoformat(),
        legal_hold=retention.legal_hold, verificata_dallo_storage=esito["verificata_dallo_storage"],
    )
    return s3_key, bucket, version_id
