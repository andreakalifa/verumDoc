# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/config.py — impostazioni del testimone indipendente.

Riferimenti: PIANO_R03_R05 §5.3 (testimone in un account e con amministratori
diversi), contratto K3 di R04, REGOLE_SVILUPPO §19.3 (segreti solo da
variabili d'ambiente / secret manager) e §19.4 (impostazioni pericolose
fermano l'avvio).

Il testimone NON condivide nulla con il gateway: database proprio, chiave
propria, nessun JWT del gateway. Riceve dal registro soltanto:
  - la chiave PUBBLICA del registro (verifier key signed note, K2);
  - un token opaco di servizio, di cui conserva solo l'impronta SHA-256.

Gli elenchi si scrivono separati da virgole (es. due chiavi del registro
durante una rotazione).
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _elenco(valore: str) -> list[str]:
    return [v.strip() for v in valore.split(",") if v.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    # Senza ENV si assume produzione (fail-closed), come negli altri servizi.
    env: str = "production"
    service_name: str = "testimone"
    service_version: str = "1.0.0"
    log_level: str = "INFO"
    # Database PROPRIO del testimone (mai quello del registro): in produzione
    # un'istanza in un account diverso; in sviluppo un database separato nello
    # stesso cluster. Le tabelle stanno nello schema «testimone».
    database_url: str = (
        "postgresql+asyncpg://vd_testimone:vd_testimone_dev_only@localhost:5434/verumdoc_testimone"
    )
    # Chiave privata Ed25519 del testimone nel formato signed note
    # «PRIVATE+KEY+verumdoc-testimone+<keyhash>+<base64>» (lo stesso del
    # registro, libs.catena.nota). Solo da secret manager: mai nel repository,
    # mai nei log.
    testimone_chiave_privata: SecretStr = SecretStr("")
    # Nome della chiave nella signed note (K2).
    testimone_nome_chiave: str = "verumdoc-testimone"
    # Chiavi pubbliche del registro come verifier key signed note
    # («verumdoc-registro+<keyhash>+<base64>»), separate da virgole.
    testimone_chiavi_registro: str = ""
    # Origini dei checkpoint che questo testimone osserva (riga 1 della nota).
    testimone_origini_ammesse: str = "verumdoc.it/registro/v1"
    # Impronte SHA-256 (hex) dei token di servizio del registro accettati.
    testimone_impronte_token_registro: str = ""
    # Limiti del corpo della richiesta di controfirma: una prova di
    # consistenza RFC 6962 ha al più ~2·log2(n) righe (126 per 2^63 foglie).
    testimone_max_corpo_byte: int = 65536
    testimone_max_righe_prova: int = 128
    # Ogni quanto rileggere dal database lo stato per le metriche (secondi).
    testimone_intervallo_stato_secondi: float = 30.0

    @property
    def chiavi_registro(self) -> list[str]:
        return _elenco(self.testimone_chiavi_registro)

    @property
    def origini_ammesse(self) -> list[str]:
        return _elenco(self.testimone_origini_ammesse)

    @property
    def impronte_token_registro(self) -> list[str]:
        return [v.lower() for v in _elenco(self.testimone_impronte_token_registro)]


settings = Settings()
