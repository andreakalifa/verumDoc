# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/validation/app/config.py
Sprint 2B FIX-015: aggiunto S3 settings per download file e ri-verifica hash.
R08/R09 (VD-C-008, VD-C-009): verifica impronte sempre attiva, ritentativi e DLQ.
"""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False,
        extra="ignore",
    )
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "validation"
    service_version: str = "1.0.0"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    )
    redis_url: str = "redis://:redis_secret_dev_only@localhost:6379/0"
    kafka_bootstrap: str = "localhost:9092"
    kafka_group_id: str = "validation-service"
    kafka_topic_in: str = "pdv.received"
    kafka_topic_valid: str = "pdv.validated"
    kafka_topic_invalid: str = "pdv.invalid"
    log_level: str = "INFO"
    log_json: bool = False
    # R03 (b5): il limite fisso VLD-801 di 100 MB non esiste più. Il limite per
    # file è quello della classe fotografato nel PdV (`pdv.regole_applicate`,
    # impostazione CLASSE_DIMENSIONE_MAX_MB con l'effetto di RGL-104). Solo per i
    # PdV senza fotografia (versati prima di R03) vale questo predefinito del
    # servizio: 5120 MB = 5 GiB, il massimo di un file da sessione di caricamento.
    validazione_max_file_mb: int = Field(default=5120, ge=1)
    # Lettura in streaming dallo storage: blocco letto e passato agli algoritmi
    # d'impronta (in memoria al più un blocco per file in lettura).
    lettura_blocco_kb: int = Field(default=1024, ge=64, le=65536)
    # Concorrenza del consumer: PdV elaborati in parallelo da una replica e
    # messaggi letti ma non ancora confermati (oltre si smette di leggere).
    concorrenza_pdv: int = Field(default=8, ge=1, le=256)
    max_messaggi_in_sospeso: int = Field(default=256, ge=1)
    # Attesa prima di rielaborare un messaggio la cui DLQ non si è potuta scrivere
    attesa_dopo_errore_dlq_secondi: float = Field(default=30.0, ge=0)
    # Formati per i PdV SENZA fotografia delle regole (versati prima di R03);
    # con la fotografia vale FORMATI_AMMESSI della classe (effetto di RGL-105).
    formati_ammessi: list[str] = [
        "application/pdf",
        "application/pkcs7-mime",
        "application/xml",
        "text/xml",
        "image/tiff",
        "image/jpeg",
        "image/png",
    ]
    # Sprint 2B FIX-015: S3 per download file e ri-verifica hash
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_region: str = "eu-south-1"
    # VD-C-008: la verifica delle impronte sui file scaricati è SEMPRE attiva
    # (l'interruttore s3_verify_hash è stato rimosso: lo Step 5 è fail-closed).
    # VD-C-009: ritentativi limitati (attese in secondi), poi DLQ "<topic_in><suffisso>"
    ritardi_ritentativi_secondi: list[float] = [2.0, 10.0, 60.0]
    suffisso_dlq: str = ".dlq"
    # R08 (D01, D02): riconoscimento del formato (Siegfried/veraPDF) e verifica
    # delle firme lavorano su una COPIA LOCALE del file in /tmp (emptyDir con
    # limite). Oltre questa dimensione il file non si copia: formato dal gateway
    # (firma binaria) e firme «non verificate per dimensione» (rilievo RGL-109
    # se il livello lo richiede). Verificatore e strumenti: variabili FIRME_* e
    # FORMATI_*/SIEGFRIED_*/VERAPDF_BIN lette da libs/shared/firme e libs/shared/formati.
    verifica_contenuto_max_mb: int = Field(default=512, ge=1, le=5120)
    # R08 (D01): verificatore delle firme (libs/shared/firme): EU DSS nel servizio
    # verifica-firme con ripiego pyHanko per i PDF, o pyHanko con trust anchor locali
    firme_verificatore: str = "pyhanko"
    firme_dss_url: str = ""
    firme_dss_token: SecretStr = SecretStr("")
    firme_dss_timeout_s: float = Field(default=60.0, gt=0)
    firme_ripiego_pdf: bool = True
    firme_radici_pem: str = ""
    firme_revoche_rete: bool = False
    # Revisione R08 (X5, X7, X8): dimensione massima di un file inviato al
    # verificatore (allineata a VERIFICA_DIMENSIONE_MASSIMA di verifica-firme;
    # oltre: «firme non verificate», rifiuto solo da CRITTOGRAFICA), verifiche
    # pesanti contemporanee e tempo massimo di una verifica pyHanko
    firme_dimensione_max_mb: int = Field(default=60, ge=1, le=5120)
    firme_concorrenza: int = Field(default=2, ge=1, le=64)
    firme_tempo_max_s: float = Field(default=120.0, gt=0)
    # R08 (D02): riconoscimento dei formati (libs/shared/formati)
    formati_riconoscitore: str = "siegfried"
    siegfried_bin: str = "sf"
    siegfried_home: str = ""
    siegfried_firme: str = "default.sig"
    verapdf_bin: str = "verapdf"

    def ambiente_firme(self) -> dict[str, str]:
        """R08: configurazione del verificatore per ``libs.shared.firme``."""
        return {"FIRME_VERIFICATORE": self.firme_verificatore,
                "FIRME_DSS_URL": self.firme_dss_url,
                "FIRME_DSS_TOKEN": self.firme_dss_token.get_secret_value(),
                "FIRME_DSS_TIMEOUT_S": str(self.firme_dss_timeout_s),
                "FIRME_RIPIEGO_PDF": str(self.firme_ripiego_pdf).lower(),
                "FIRME_RADICI_PEM": self.firme_radici_pem,
                "FIRME_REVOCHE_RETE": str(self.firme_revoche_rete).lower(),
                "FIRME_DIMENSIONE_MAX_MB": str(self.firme_dimensione_max_mb),
                "FIRME_TEMPO_MAX_S": str(self.firme_tempo_max_s)}

    def ambiente_formati(self) -> dict[str, str]:
        return {"FORMATI_RICONOSCITORE": self.formati_riconoscitore,
                "SIEGFRIED_BIN": self.siegfried_bin, "SIEGFRIED_HOME": self.siegfried_home,
                "SIEGFRIED_FIRME": self.siegfried_firme, "VERAPDF_BIN": self.verapdf_bin}


settings = Settings()
