# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/storage/worm.py — scrittura sullo storage conservativo (WORM) con
Object Lock e retention per oggetto. VD-C-011, REGOLE §10.1, §10.2, §19.4.

Modulo CONDIVISO (spostato da services/aip-builder/app/services/s3_uploader.py
perché serve anche alla hashchain per il RdV definitivo). Non legge alcuna
configurazione: il chiamante passa client boto3, bucket, ambiente e regione.

Regole applicate:
  - ogni put_object sul bucket conservativo porta ObjectLockMode=COMPLIANCE e
    ObjectLockRetainUntilDate calcolata dalla classe documentale (anni di
    conservazione) a partire dalla data di riferimento passata dal chiamante;
  - conservazione permanente: legal hold ON + retention fino all'orizzonte
    indicato dal chiamante (prorogabile, mai riducibile in COMPLIANCE);
  - legal hold opzionale per gli altri oggetti;
  - nessuna sovrascrittura: controllo di esistenza + If-None-Match: * (atomico);
  - errore esplicito se il bucket non ha Object Lock attivo (in produzione serve
    anche la retention predefinita COMPLIANCE, come in Terraform);
  - checksum SHA-256 calcolato dal client e verificato dal server.

Bucket: in staging/produzione deve coincidere con il nome creato da Terraform
(infra/terraform/modules/s3: "${project}-${environment}-aip-prod", project
"verumdoc", environment "prod"/"staging"); altrimenti nessuna scrittura.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)

MODALITA_LOCK = "COMPLIANCE"
# Convenzione di Terraform (infra/terraform/modules/s3/main.tf, variabile project)
PROGETTO_TERRAFORM = "verumdoc"
AMBIENTI_TERRAFORM = {"production": "prod", "staging": "staging"}
ENDPOINT_LOCALI = ("localhost", "127.0.0.1", "minio")


class ErroreStorageConservativo(Exception):
    """Errore esplicito dello storage conservativo: nessuna scrittura effettuata."""

    codice = "S3-500"

    def __init__(self, messaggio: str, **dettagli: Any) -> None:
        super().__init__(messaggio)
        self.messaggio = messaggio
        self.dettagli = dettagli


class ConfigurazioneStorageNonValida(ErroreStorageConservativo):
    codice = "S3-CONFIG"


class BucketSenzaObjectLock(ErroreStorageConservativo):
    codice = "S3-NO-OBJECT-LOCK"


class OggettoGiaPresente(ErroreStorageConservativo):
    codice = "S3-GIA-PRESENTE"


class RetentionNonDeterminabile(ErroreStorageConservativo):
    codice = "S3-RETENTION"


@dataclass(frozen=True)
class PoliticaConservazione:
    """Regola di conservazione della classe documentale (fotografata nel pacchetto)."""

    anni_conservazione: int | None
    permanente: bool = False
    legal_hold: bool = False


@dataclass(frozen=True)
class RetentionOggetto:
    """Retention da applicare a un oggetto: sempre COMPLIANCE."""

    conserva_fino_a: datetime
    legal_hold: bool
    modalita: str = MODALITA_LOCK


def aggiungi_anni(data: datetime, anni: int) -> datetime:
    """Somma anni di calendario. Il 29 febbraio in anno non bisestile va al 1° marzo:
    la retention non deve mai finire prima dell'obbligo di conservazione."""
    anno = data.year + anni
    if data.month == 2 and data.day == 29 and not calendar.isleap(anno):
        return data.replace(year=anno, month=3, day=1)
    return data.replace(year=anno)


def calcola_retention(
    politica: PoliticaConservazione,
    data_riferimento: datetime,
    *,
    orizzonte_permanente_anni: int | None = None,
) -> RetentionOggetto:
    """Retention per oggetto dalla classe documentale.

    - temporanea: data_riferimento + anni_conservazione (anni di calendario);
    - permanente: legal hold ON e retention fino a data_riferimento +
      orizzonte_permanente_anni (obbligatorio, nessun valore inventato qui).
    """
    if data_riferimento.tzinfo is None:
        raise RetentionNonDeterminabile("data di riferimento senza fuso orario")
    riferimento = data_riferimento.astimezone(UTC)
    if politica.permanente:
        if not orizzonte_permanente_anni or orizzonte_permanente_anni < 1:
            raise RetentionNonDeterminabile(
                "conservazione permanente: indicare l'orizzonte di retention in anni "
                "(il legal hold resta attivo oltre l'orizzonte)",
            )
        return RetentionOggetto(
            aggiungi_anni(riferimento, orizzonte_permanente_anni), legal_hold=True
        )
    anni = politica.anni_conservazione
    if anni is None or anni < 1:
        raise RetentionNonDeterminabile(
            "anni di conservazione della classe documentale mancanti o non validi",
            anni_conservazione=anni,
        )
    return RetentionOggetto(aggiungi_anni(riferimento, anni), legal_hold=politica.legal_hold)


def nome_bucket_terraform(env: str) -> str | None:
    """Nome del bucket AIP creato da Terraform per l'ambiente (None in sviluppo)."""
    ambiente = AMBIENTI_TERRAFORM.get(env.lower())
    return f"{PROGETTO_TERRAFORM}-{ambiente}-aip-prod" if ambiente else None


def problemi_configurazione(cfg: Any) -> list[str]:
    """Controlli puri sulla configurazione dello storage (REGOLE §19.4).

    ``cfg`` espone ``env``, ``s3_bucket_aip`` e ``s3_endpoint_url`` (le Settings
    del servizio chiamante)."""
    problemi: list[str] = []
    bucket = cfg.s3_bucket_aip
    atteso = nome_bucket_terraform(cfg.env)
    if not bucket:
        problemi.append("S3_BUCKET_AIP non impostato")
    elif atteso and bucket != atteso:
        problemi.append(f"S3_BUCKET_AIP={bucket!r} diverso dal bucket Terraform {atteso!r}")
    if atteso and any(h in (cfg.s3_endpoint_url or "") for h in ENDPOINT_LOCALI):
        problemi.append("endpoint S3 locale in ambiente non di sviluppo")
    return problemi


def bucket_conservativo(cfg: Any) -> str:
    """Unica fonte del nome del bucket conservativo; errore se incoerente."""
    problemi = problemi_configurazione(cfg)
    if problemi:
        raise ConfigurazioneStorageNonValida("; ".join(problemi))
    return str(cfg.s3_bucket_aip)


_bucket_verificati: set[str] = set()


def _codice(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def verifica_bucket_conservativo(
    client: Any, bucket: str, *, produzione: bool, regione: str | None = None,
) -> None:
    """Verifica che il bucket abbia Object Lock (e in produzione retention COMPLIANCE).

    In sviluppo un bucket mancante si crea CON Object Lock; un bucket esistente
    senza Object Lock non si «ripara»: errore esplicito.
    """
    if bucket in _bucket_verificati:
        return
    try:
        conf = client.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
    except ClientError as exc:
        codice = _codice(exc)
        if codice == "NoSuchBucket" and not produzione:
            kwargs: dict[str, Any] = {"Bucket": bucket, "ObjectLockEnabledForBucket": True}
            if regione and regione != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": regione}
            client.create_bucket(**kwargs)
            logger.info("s3_bucket_creato_con_object_lock", bucket=bucket)
            conf = client.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
        elif codice == "ObjectLockConfigurationNotFoundError":
            raise BucketSenzaObjectLock(
                f"il bucket {bucket!r} non ha Object Lock: scrittura conservativa rifiutata",
                bucket=bucket,
            ) from exc
        else:
            raise ErroreStorageConservativo(
                f"impossibile verificare Object Lock sul bucket {bucket!r}: {codice}",
                bucket=bucket,
                codice_s3=codice,
            ) from exc
    if conf.get("ObjectLockEnabled") != "Enabled":
        raise BucketSenzaObjectLock(f"Object Lock non attivo sul bucket {bucket!r}", bucket=bucket)
    if produzione:
        modo = conf.get("Rule", {}).get("DefaultRetention", {}).get("Mode")
        if modo != MODALITA_LOCK:
            raise BucketSenzaObjectLock(
                f"il bucket {bucket!r} non ha retention predefinita COMPLIANCE",
                bucket=bucket,
                modalita=modo,
            )
    _bucket_verificati.add(bucket)


def _chiave_valida(chiave: str) -> str:
    """Rifiuta chiavi pericolose (path traversal, caratteri di controllo)."""
    parti = chiave.split("/")
    if (
        not chiave
        or chiave.startswith("/")
        or any(p in ("", ".", "..") for p in parti)
        or any(ord(c) < 32 or ord(c) == 127 for c in chiave)
    ):
        raise ErroreStorageConservativo("chiave S3 non valida", chiave=chiave[:200])
    return chiave


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
    regione: str | None = None,
) -> dict:
    """Scrittura WORM sincrona: verifica bucket, nessuna sovrascrittura, retention per oggetto."""
    _chiave_valida(chiave)
    if retention.modalita != MODALITA_LOCK:
        raise RetentionNonDeterminabile("ammessa solo la modalità COMPLIANCE")
    if retention.conserva_fino_a <= adesso:
        raise RetentionNonDeterminabile("la retention deve terminare nel futuro")
    verifica_bucket_conservativo(client, bucket, produzione=produzione, regione=regione)
    try:
        client.head_object(Bucket=bucket, Key=chiave)
    except ClientError as exc:
        if _codice(exc) not in ("404", "NoSuchKey", "NotFound"):
            raise ErroreStorageConservativo(
                f"controllo di esistenza fallito: {_codice(exc)}",
                bucket=bucket,
                chiave=chiave,
            ) from exc
    else:
        raise OggettoGiaPresente(
            "oggetto già presente: sovrascrittura vietata", bucket=bucket, chiave=chiave
        )
    sha256_b64 = base64.b64encode(hashlib.sha256(contenuto).digest()).decode("ascii")
    # Revisione 2 di R05 (S4): l'intestazione del legal hold si invia SOLO per
    # metterlo (ON). Con «OFF» S3 chiede comunque s3:PutObjectLegalHold: chi
    # scrive senza legal hold (registro/ della hashchain) non deve averlo.
    blocco_legale = {"ObjectLockLegalHoldStatus": "ON"} if retention.legal_hold else {}
    try:
        resp = client.put_object(
            Bucket=bucket,
            Key=chiave,
            Body=contenuto,
            ContentType=content_type,
            ObjectLockMode=retention.modalita,
            ObjectLockRetainUntilDate=retention.conserva_fino_a,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=sha256_b64,
            IfNoneMatch="*",
            **blocco_legale,
        )
    except ClientError as exc:
        if _codice(exc) in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
            raise OggettoGiaPresente(
                "oggetto creato in concorrenza: sovrascrittura vietata",
                bucket=bucket,
                chiave=chiave,
            ) from exc
        raise ErroreStorageConservativo(
            f"scrittura rifiutata dallo storage: {_codice(exc)}", bucket=bucket, chiave=chiave
        ) from exc
    ricevuto = resp.get("ChecksumSHA256")
    if ricevuto and ricevuto != sha256_b64:
        raise ErroreStorageConservativo(
            "checksum SHA-256 restituito diverso da quello inviato", bucket=bucket, chiave=chiave
        )
    return resp




def leggi_oggetto(client: Any, bucket: str, chiave: str) -> bytes:
    """Legge un oggetto già conservato (es. per riusarlo dopo OggettoGiaPresente)."""
    _chiave_valida(chiave)
    try:
        return client.get_object(Bucket=bucket, Key=chiave)["Body"].read()
    except ClientError as exc:
        raise ErroreStorageConservativo(
            f"lettura dell'oggetto conservato fallita: {_codice(exc)}",
            bucket=bucket, chiave=chiave,
        ) from exc


def estendi_retention(client: Any, bucket: str, chiave: str, version_id: str | None,
                      fino_a: datetime) -> datetime | None:
    """R09 (D04), R12a (VD-C-148): ESTENDE la retention COMPLIANCE di UNA versione fino
    a ``fino_a``. Mai in riduzione (seconda guardia, nello storage): la retention
    attuale si rilegge e, se è già pari o successiva, non si tocca nulla.
    Restituisce la retention dopo l'operazione (riletta), ``None`` se l'oggetto
    non ne ha. Una sola implementazione per ciclo-vita e hashchain (§9.1)."""
    argomenti: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
    if version_id:
        argomenti["VersionId"] = version_id
    attuale = client.head_object(**argomenti).get("ObjectLockRetainUntilDate")
    if attuale is not None and attuale >= fino_a:
        return attuale  # type: ignore[no-any-return]
    client.put_object_retention(**argomenti, Retention={"Mode": "COMPLIANCE",
                                                        "RetainUntilDate": fino_a})
    return client.head_object(**argomenti).get("ObjectLockRetainUntilDate")  # type: ignore[no-any-return]
