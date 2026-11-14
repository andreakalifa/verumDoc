# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Seed dei dati iniziali — eseguito dall'entrypoint del gateway DOPO
`alembic upgrade head` (lo schema lo crea solo Alembic, REGOLE §16.2).
Riferimento piano: F0.4 — Bootstrap DB; REGOLE §16.6.

Due insiemi separati:
  - dati di riferimento (sempre): le classi documentali di base del servizio
    (tenant_id NULL), che solo SUPER_ADMIN può modificare;
  - dati dimostrativi (solo ENV development/test, mai in staging/produzione):
    un cliente dimostrativo con dati palesemente fittizi e i bucket MinIO
    di sviluppo.

Idempotente con chiave stabile (id del cliente, codice della classe di base):
una riga già presente non si aggiorna, così le modifiche fatte dagli utenti
restano (REGOLE §16.6). Un errore di database ferma l'esecuzione con codice
diverso da zero (niente «Seed skipped» silenzioso).

Uso:  python -m app.seed      (DATABASE_URL e ENV dall'ambiente)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Ambienti in cui si caricano i dati dimostrativi.
AMBIENTI_DEMO: frozenset[str] = frozenset({"development", "test"})

DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001"

_PDF_P7M = ["application/pdf", "application/pkcs7-mime"]


@dataclass(frozen=True)
class ClasseBase:
    codice: str
    nome: str
    anni_conservazione: int
    tipologia_xml: str
    formati_ammessi: tuple[str, ...]
    conservazione_permanente: bool = False


#: Classi di base del servizio. Coincidono con quelle create dalla migrazione
#: 20260319_003 (più documento_tecnico): su un database migrato il seed
#: aggiunge solo quelle mancanti.
CLASSI_BASE: tuple[ClasseBase, ...] = (
    ClasseBase("fattura", "Fattura", 10, "DAI",
               ("application/pdf", "application/pkcs7-mime", "text/plain")),
    ClasseBase("contratto", "Contratto", 10, "DAI", tuple(_PDF_P7M)),
    ClasseBase("delibera", "Delibera", 30, "DAI", tuple(_PDF_P7M)),
    ClasseBase("determinazione", "Determinazione", 10, "DAI", tuple(_PDF_P7M)),
    ClasseBase("verbale", "Verbale", 10, "DAI", tuple(_PDF_P7M)),
    ClasseBase("circolare", "Circolare", 10, "DAI", ("application/pdf",)),
    ClasseBase("decreto", "Decreto", 30, "DAI", tuple(_PDF_P7M)),
    ClasseBase("documento_tecnico", "Documento tecnico", 5, "DI", tuple(_PDF_P7M)),
    ClasseBase("altro", "Altro documento", 10, "DI",
               ("application/pdf", "application/xml", "text/plain",
                "image/tiff", "image/jpeg", "image/png")),
)

#: Cliente dimostrativo: dati volutamente fittizi (nessun ente reale).
DEMO_TENANT: dict[str, Any] = {
    "id": DEMO_TENANT_ID,
    "codice_fiscale": "00000000001",
    "ragione_sociale": "Ente dimostrativo (dati fittizi)",
    "schema_name": "tenant_00000000",
    "email_referente": "demo@verumdoc.invalid",
}


def dsn_da_ambiente() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL non impostata: seed impossibile")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def demo_abilitato(env: str | None = None) -> bool:
    valore = env if env is not None else os.environ.get("ENV", "production")
    return valore.strip().lower() in AMBIENTI_DEMO


async def seed_classi_base(conn: Any) -> int:
    """Inserisce le classi di base mancanti. Restituisce quante ne ha create."""
    create = 0
    for c in CLASSI_BASE:
        stato = await conn.execute(
            """
            INSERT INTO public.classi_documentali
                (tenant_id, codice, nome, anni_conservazione,
                 conservazione_permanente, tipologia_xml, formati_ammessi)
            VALUES (NULL, $1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT ON CONSTRAINT uq_classi_tenant_codice DO NOTHING
            """,
            c.codice, c.nome, c.anni_conservazione, c.conservazione_permanente,
            c.tipologia_xml, json.dumps(list(c.formati_ammessi)),
        )
        if stato.endswith(" 1"):
            create += 1
    return create


async def seed_demo(conn: Any) -> bool:
    """Crea il cliente dimostrativo se manca. True se l'ha creato."""
    stato = await conn.execute(
        """
        INSERT INTO public.tenants
            (id, codice_fiscale, ragione_sociale, schema_name, email_referente)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (id) DO NOTHING
        """,
        DEMO_TENANT["id"], DEMO_TENANT["codice_fiscale"], DEMO_TENANT["ragione_sociale"],
        DEMO_TENANT["schema_name"], DEMO_TENANT["email_referente"],
    )
    return stato.endswith(" 1")


def _client_s3_sviluppo() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://minio:9000"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "minioadmin_dev_only"),
        config=Config(signature_version="s3v4"),
        region_name=os.environ.get("S3_REGION", "eu-south-1"),
    )


# Scadenza degli ZIP dei PdD in sviluppo, come la regola del modulo Terraform s3
# (prima la impostava il container minio-buckets, tolto perché l'immagine minio/mc
# non esiste più su Docker Hub).
PDD_SCADENZA_GIORNI_SVILUPPO = 4


def _attendi_storage(s3: Any, *errori: type[BaseException]) -> bool:
    """Aspetta che lo storage di sviluppo risponda (MinIO senza healthcheck nel compose)."""
    attesa = float(os.environ.get("SEED_ATTESA_STORAGE_SECONDI", "90"))
    scadenza = time.monotonic() + attesa
    while True:
        try:
            s3.list_buckets()
            return True
        except errori as exc:
            if time.monotonic() >= scadenza:
                logger.warning("seed_storage_non_raggiungibile", attesa_secondi=attesa,
                               errore=str(exc)[:200])
                return False
            time.sleep(2)


def _scadenza_pdd(s3: Any, bucket: str) -> None:
    try:
        s3.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={"Rules": [{
                "ID": "scadenza-pdd", "Status": "Enabled", "Filter": {"Prefix": ""},
                "Expiration": {"Days": PDD_SCADENZA_GIORNI_SVILUPPO},
            }]},
        )
    except Exception as exc:  # noqa: BLE001 — in sviluppo la scadenza è una comodità
        logger.warning("seed_scadenza_pdd_non_impostata", bucket=bucket, errore=str(exc)[:200])


def _crea_bucket_sviluppo(s3: Any = None) -> dict[str, str]:
    """Bucket MinIO di sviluppo. In produzione i bucket (con Object Lock) li
    crea l'infrastruttura (Terraform), non l'applicazione.

    - bucket dei PdA (S3_BUCKET_AIP): creato CON Object Lock (MinIO lo consente
      solo alla creazione, come ``mc mb --with-lock``): lo scrittore WORM di
      aip-builder e hashchain rifiuta un bucket senza lock (VD-C-011). Un bucket
      esistente senza lock NON si ripara: errore nel log con l'istruzione
      (ricreare il volume di sviluppo).
    - bucket dei versamenti (S3_BUCKET_PDV): area di transito, senza lock
      (i file orfani si eliminano, VD-C-015).
    - bucket dei pacchetti di distribuzione (S3_BUCKET_PDD, predefinito
      ``verumdoc-pdd``, R03 b4/i1): ZIP temporanei dei PdD, senza lock (si
      eliminano alla scadenza).
    Restituisce, per bucket, l'esito: creato | presente | senza_object_lock | errore.
    """
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        logger.warning("seed_bucket_saltato", motivo="boto3 non installato")
        return {}
    s3 = s3 or _client_s3_sviluppo()
    if not _attendi_storage(s3, BotoCoreError, ClientError):
        return {}
    bucket_aip = os.environ.get("S3_BUCKET_AIP", "verumdoc-aip")
    bucket_pdv = os.environ.get("S3_BUCKET_PDV", "verumdoc-pdv")
    bucket_pdd = os.environ.get("S3_BUCKET_PDD", "verumdoc-pdd")
    esiti: dict[str, str] = {}
    for bucket, con_lock in ((bucket_pdv, False), (bucket_aip, True), (bucket_pdd, False)):
        try:
            try:
                s3.head_bucket(Bucket=bucket)
            except ClientError:
                kwargs: dict[str, Any] = {"Bucket": bucket}
                if con_lock:
                    kwargs["ObjectLockEnabledForBucket"] = True
                # Fuori da us-east-1 lo standard S3 vuole la regione esplicita
                regione = getattr(getattr(s3, "meta", None), "region_name", None)
                if regione and regione != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {"LocationConstraint": regione}
                s3.create_bucket(**kwargs)
                esiti[bucket] = "creato"
                logger.info("seed_bucket_creato", bucket=bucket, object_lock=con_lock)
                if bucket == bucket_pdd:
                    _scadenza_pdd(s3, bucket)
                continue
            esiti[bucket] = "presente"
            if con_lock:
                try:
                    conf = s3.get_object_lock_configuration(Bucket=bucket)
                    attivo = conf["ObjectLockConfiguration"].get("ObjectLockEnabled") == "Enabled"
                except ClientError:
                    attivo = False
                if not attivo:
                    esiti[bucket] = "senza_object_lock"
                    logger.error(
                        "seed_bucket_senza_object_lock", bucket=bucket,
                        azione="il bucket dei PdA esiste senza Object Lock e non si può "
                               "abilitare dopo: ricreare il volume MinIO di sviluppo "
                               "(docker compose down -v) e riavviare",
                    )
        except (BotoCoreError, ClientError) as exc:
            esiti[bucket] = "errore"
            logger.warning("seed_bucket_non_creato", bucket=bucket, errore=str(exc))
    return esiti


async def run_seed(dsn: str | None = None, env: str | None = None) -> dict[str, Any]:
    import asyncpg

    conn = await asyncpg.connect(dsn=dsn or dsn_da_ambiente())
    try:
        async with conn.transaction():
            # Le classi di base (tenant_id NULL) e il cliente dimostrativo sono dati
            # del conservatore: con il ruolo dei servizi la Row Level Security li
            # accetta solo con l'ambito «conservatore», valido per questa transazione.
            await conn.execute(
                "SELECT set_config('app.ente', '', true), "
                "set_config('app.ambito', 'conservatore', true)"
            )
            classi = await seed_classi_base(conn)
            demo = demo_abilitato(env)
            tenant_demo = await seed_demo(conn) if demo else False
    finally:
        await conn.close()
    logger.info("seed_ok", classi_create=classi, demo=demo, tenant_demo_creato=tenant_demo)
    if demo:
        _crea_bucket_sviluppo()
    return {"classi_create": classi, "demo": demo, "tenant_demo_creato": tenant_demo}


if __name__ == "__main__":
    asyncio.run(run_seed())
