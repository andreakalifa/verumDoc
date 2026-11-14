# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/config.py
Sprint 0 FIX-002: aggiunto jwt_public_key/jwt_private_key.
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
    service_name: str = "distribution"
    service_version: str = "2.0.0"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    )
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_lifecycle: str = "lifecycle.event"
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_bucket_aip: str = "verumdoc-aip"
    s3_region: str = "eu-south-1"
    signed_url_expiry: int = 3600
    opensearch_url: str = "http://opensearch:9200"
    opensearch_index: str = "verumdoc-aip"
    log_level: str = "INFO"
    # JWT RS256 (REGOLE §19.1, §19.3, VD-C-004): chiave pubblica del gateway SOLO da
    # JWT_PUBLIC_KEY (senza, il servizio non parte, tranne ENV=test); iss e aud
    # devono coincidere con quelli del gateway. La privata non serve in esercizio.
    jwt_public_key: str = ""
    jwt_private_key: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # R05 (contratto Q2): token di SERVIZIO del ciclo di vita verso l'API di
    # migrazione (riversamento in uscita, restituzione). Firmati come gli altri
    # token di servizio (emetti_token_servizio), verificati con JWT_PUBLIC_KEY.
    migrazione_pubblico_servizio: str = "verumdoc-distribution"
    migrazione_scope_servizio: str = "migrazione:servizio"
    # Chiavi API (R03 b4, VD-C-099): stesso pepper di gateway e onboarding.
    # Obbligatorio fuori da development/test (senza, il servizio non parte).
    api_key_pepper: SecretStr = SecretStr("")
    # R03 f2 (REV-03): Redis dei limiti di richieste al minuto per piano (stesso
    # nome del gateway). Obbligatorio in produzione: senza, il servizio non parte.
    redis_url: str = ""

    # ── Pacchetti di distribuzione (R03 b4, VD-C-077) ────────────────────
    # Durata predefinita e massima dei collegamenti prefirmati (secondi).
    pdd_durata_link_secondi: int = Field(3600, ge=60, le=604_800)
    pdd_durata_link_max_secondi: int = Field(86_400, ge=60, le=604_800)
    # Sopra questa dimensione complessiva (o per MIGRAZIONE, o se richiesto) il
    # PdD è uno ZIP preparato fuori dalla richiesta HTTP; sotto, collegamenti
    # ai file con impronte verificate durante la richiesta.
    pdd_sincrono_max_mb: int = Field(50, ge=0, le=10_000)
    # Bucket temporaneo dei PdD ZIP (NON conservativo: nessun Object Lock;
    # gli oggetti si eliminano alla scadenza dal lavoratore).
    s3_bucket_pdd: str = "verumdoc-pdd"
    pdd_zip_parte_mb: int = Field(8, ge=5, le=512)
    # R07 (D18): la validità è l'impostazione della console PDD_VALIDITA_ORE (72 h,
    # fino a 30 giorni, per ente); questa variabile è il TETTO tecnico, allineato
    # alla scadenza del bucket temporaneo (Terraform: tetto/24 + 1 giorni).
    pdd_zip_validita_ore: int = Field(720, ge=1, le=24 * 30)
    pdd_lease_secondi: int = Field(1800, ge=60, le=86_400)
    pdd_ritardi_secondi: list[int] = Field(default_factory=lambda: [60, 300, 1500, 7200])
    kafka_topic_pdd_pronto: str = "pdd.pronto"
    # Conservatore d'origine nel manifesto di migrazione (prima valore fisso nel codice)
    conservatore_denominazione: str = ""
    conservatore_codice_fiscale: str = ""

    # ── Ricerca (R03 b4, VD-C-093) ─────────────────────────────────────
    opensearch_utente: str = ""
    opensearch_password: SecretStr = SecretStr("")
    opensearch_timeout_secondi: float = Field(10.0, gt=0, le=120)
    # R03 f2 (REV-06): i risultati si contano fino a questo numero (oltre:
    # «più di N», total_esatto=false), mai tutti: il conteggio esatto su milioni
    # di PdA costa quanto la ricerca intera.
    ricerca_conteggio_massimo: int = Field(1000, ge=1, le=100_000)
    indicizzazione_lotto: int = Field(200, ge=1, le=5000)
    indicizzazione_intervallo_secondi: float = Field(2.0, gt=0, le=600)
    indicizzazione_ritardi_secondi: list[int] = Field(
        default_factory=lambda: [60, 300, 1500, 7200])
    # Lavoratori (indicizzazione e PdD ZIP) come task del processo; false se
    # girano come processi dedicati (python -m app.services.lavoratori).
    lavoratori_attivi: bool = True


settings = Settings()
