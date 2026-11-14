# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/config.py
Sprint 0 FIX-001: aggiunto jwt_public_key e jwt_private_key per verifica firma RS256.
"""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False,
    )
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "dashboard-api"
    service_version: str = "2.0.0"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    )
    gateway_url: str = "http://gateway:8000"
    onboarding_url: str = "http://onboarding:8000"
    hashchain_url: str = "http://hashchain:8000"
    distribution_url: str = "http://distribution:8000"  # HIGH-002 FIX
    # R16 (VD-C-166): firma personale con OTP inoltrata con il token della persona
    signature_url: str = "http://signature:8000"
    signature_timeout_seconds: float = 60.0
    # g4: la verifica completa è asincrona in hashchain (202 subito): il proxy
    # usa il timeout normale; il limite di durata è HASHCHAIN_VERIFICA_TIMEOUT_SECONDS
    # di hashchain.
    # MinIO — usato dal download proxy
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_bucket_aip: str = "verumdoc-aip"
    cors_origins: list[str] = ["http://localhost:5173"]
    # JWT RS256 (REGOLE §19.1, §19.3, VD-C-004): chiave pubblica del gateway SOLO da
    # JWT_PUBLIC_KEY (senza, il servizio non parte, tranne ENV=test); iss e aud
    # devono coincidere con quelli del gateway. La privata non serve in esercizio.
    jwt_public_key: str = ""
    jwt_private_key: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    jwt_expiry_seconds: int = 3600
    # R03 f2 (REV-03): Redis dei limiti di richieste al minuto per piano (stesso
    # nome del gateway). Obbligatorio in produzione: senza, il servizio non parte.
    redis_url: str = ""

    # ── R04 servizi (VD-C-075): code di scarto ────────────────────────────
    # Il processo ``python -m app.services.consumer_scarti`` consuma le DLQ e le
    # materializza in public.scarti; qui le DLQ lette, con il servizio che le scrive.
    kafka_bootstrap: str = "kafka:9092"
    scarti_kafka_group_id: str = "dashboard-scarti"
    scarti_code_kafka: dict[str, str] = Field(default_factory=lambda: {
        "pdv.received.dlq": "validation",
        "pdv.validated.dlq": "aip-builder",
        "notification.dlq": "notification",
    })
    #: Topic su cui «Rielabora» può ripubblicare un evento scartato da notification.
    #: R04 correzioni (rev4 rilievo 7): ogni topic qui deve avere dashboard-api fra i
    #: «producers» di infra/kafka/topics.yaml (permessi Kafka minimi). NON ci sono
    #: ``aip.conserved`` e ``catena.checkpoint``: li scrive SOLO la hashchain (un
    #: servizio compromesso non deve poter annunciare conservazioni o checkpoint).
    scarti_topic_ripubblicabili: list[str] = Field(default_factory=lambda: [
        "pdv.received", "pdv.validated", "pdv.invalid", "aip.error",
        "notification.send", "caricamento.completato", "caricamento.errore", "pdd.pronto",
        "versamento.staging_scaduto",
    ])
    scarti_lotto: int = Field(200, ge=1, le=5_000)
    scarti_intervallo_tabelle_secondi: float = Field(60.0, gt=0, le=3_600)
    #: Massimo di messaggi in un piano di «Rielabora» in blocco (REGOLE §13)
    scarti_rielabora_max: int = Field(500, ge=1, le=10_000)
    scarti_limite_massimo: int = Field(100, ge=1, le=500)
    # R07 (D15): STAGING_SCARTATI_GIORNI tolta: impostazione della console per ente
    # (libs.shared.parametri.SQL_GIORNI_STAGING, la stessa del gateway)


settings = Settings()
