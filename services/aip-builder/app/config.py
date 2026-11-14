# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/aip-builder/app/config.py
Riferimento piano: F1.3 — AIP Builder SInCRO 2020
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "aip-builder"
    service_version: str = "1.0.0"
    database_url: str = "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    kafka_bootstrap: str = "localhost:9092"
    kafka_group_id: str = "aip-builder-service"
    kafka_topic_in: str = "pdv.validated"
    # R04 (VD-C-097): l'aip-builder non pubblica più «aip.conserved»: lo pubblica
    # la hashchain quando il PdA è nel registro e coperto da un checkpoint.
    kafka_topic_error: str = "aip.error"
    # VD-C-009: ritentativi limitati (attese in secondi), poi DLQ "<topic_in><suffisso>"
    ritardi_ritentativi_secondi: list[float] = [2.0, 10.0, 60.0]
    suffisso_dlq: str = ".dlq"
    # Conservazione permanente: orizzonte (anni) della retention Object Lock
    # COMPLIANCE, oltre il quale resta il legal hold (s3_uploader.calcola_retention).
    # R09 (D04, VD-C-070): è il parametro RETENTION_PERMANENTE_ANNI del motore
    # (predefinito 30, rinnovo automatico); questo valore d'ambiente vale solo senza
    # la riga del conservatore (compatibilità)
    retention_permanente_orizzonte_anni: int | None = None
    # S3 / MinIO
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_bucket_aip: str = "verumdoc-aip"
    s3_region: str = "eu-south-1"
    # R03 (b5) — file fino a 5 GiB senza tenerli in memoria:
    # blocco di lettura per ricalcolare le impronte in streaming;
    # oltre la soglia la copia server-side nello storage WORM è multipart
    # (CopyObject singolo accetta al più 5 GiB); dimensione delle parti copiate.
    lettura_blocco_kb: int = Field(default=1024, ge=64, le=65536)
    copia_multipart_soglia_mb: int = Field(default=1024, ge=5, le=5120)
    copia_parte_mb: int = Field(default=256, ge=5, le=5120)
    # URN prefix AGID
    # F2-03: dati conservatore per URN AGID
    conservatore_cf: str = "00000000000"   # CF conservatore (da impostare in produzione)
    conservatore_nome: str = "VerumDoc S.r.l."
    urn_prefix: str = "urn:verumdoc:aip"
    log_level: str = "INFO"

settings = Settings()
