# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/config.py
Riferimento piano: F1.5 — Onboarding Service
"""
from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False)
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "onboarding"
    service_version: str = "1.0.0"
    database_url: str = "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    redis_url: str = "redis://:redis_secret_dev_only@localhost:6379/0"
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_lifecycle: str = "lifecycle.event"
    # R07 (D28, D17): lavoro periodico della configurazione (modifiche programmate,
    # anteprima del giorno prima, avviso dei versamenti senza Idempotency-Key)
    configurazione_lavoratore_attivo: bool = True
    configurazione_intervallo_secondi: float = 300.0
    # R13a: servizio di firma (stato e prova dei fornitori, inoltrati con il token
    # della persona; nessun token di servizio)
    signature_url: str = "http://signature:8000"
    signature_timeout_seconds: float = 60.0
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_bucket_contracts: str = "verumdoc-contracts"
    # Email — placeholder, SMTP reale in Fase 2
    smtp_host: str = "localhost"
    smtp_port: int = 587
    admin_email: str = "admin@verumdoc.it"
    log_level: str = "INFO"
    # JWT RS256 (REGOLE §19.1, §19.3): chiave pubblica del gateway SOLO da
    # variabile d'ambiente/secret JWT_PUBLIC_KEY; senza, il servizio non parte
    # (eccetto ENV=test). La privata non serve a onboarding (solo test).
    jwt_public_key: str = ""
    jwt_private_key: SecretStr = SecretStr("")
    jwt_expiry_seconds: int = 3600
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # Chiavi API (REGOLE §19.8, libs.shared.security.chiavi_api): pepper dell'HMAC,
    # uguale nel gateway. Obbligatorio fuori da development/test (il servizio non parte).
    api_key_pepper: SecretStr = SecretStr("")
    # R10 (D08): indirizzo della console nei collegamenti d'invito
    base_url_console: str = "https://app.verumdoc.it"
    # R12a (VD-C-152): chiave PUBBLICA X25519 (base64url) del servizio di posta
    # (notification): il collegamento d'invito si sigilla prima dell'outbox e lo
    # apre solo la posta all'invio. Senza, gli inviti non si creano (503).
    posta_chiave_pubblica: str = ""
    posta_chiave_pubblica_id: str = "p1"

settings = Settings()
