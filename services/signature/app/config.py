# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/config.py
Configurazione del servizio di firma e marca — VD-C-010.

Fornitori (decisione VD-P-05 ancora aperta):
  FIRMA_FORNITORE = non_configurato | sviluppo | csc
  MARCA_FORNITORE = non_configurato | sviluppo | rfc3161

R13a: i fornitori si registrano dalla console nella tabella ``fornitori_firma``
(CSC, PKCS#11, TSA RFC 3161, sviluppo, con priorità e ripiego) che il servizio
legge con un login di sola lettura (``DATABASE_URL``, gruppo ``verumdoc_firma``)
insieme ai parametri del gruppo «firma_qualificata»; i segreti dei fornitori
stanno in ``FIRMA_SEGRETI_JSON`` (Secret). Senza righe attive nel registro
vale la configurazione d'ambiente qui sotto (comportamento precedente, §8.14).

«sviluppo» produce firme e marche crittograficamente vere ma NON qualificate
(chiave autogenerata): in ENV=production è rifiutato (REGOLE §19.4, §20.2).
Nessuna credenziale ha un valore di default: i segreti arrivano solo da
ambiente/secret manager e sono SecretStr (mai nei log).
"""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "signature"
    service_version: str = "3.0.0"
    log_level: str = "INFO"

    # JWT RS256 (REGOLE §19.1, §19.3, VD-C-004): chiave pubblica del gateway SOLO da
    # JWT_PUBLIC_KEY (senza, il servizio non parte, tranne ENV=test); iss e aud
    # devono coincidere con quelli del gateway. La privata non serve in esercizio.
    jwt_public_key: str = ""
    jwt_private_key: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # Revisione di sicurezza M2: /firma e /marca accettano SOLO il token di
    # servizio (audience dedicata, scope «firma:servizio»), emesso con
    # `python -m libs.shared.security.emetti_token_servizio --servizio hashchain`.
    jwt_audience_servizio: str = "verumdoc-signature"

    # ── Scelta dei fornitori ────────────────────────────────────────
    firma_fornitore: Literal["non_configurato", "sviluppo", "csc"] = "non_configurato"
    marca_fornitore: Literal["non_configurato", "sviluppo", "rfc3161"] = "non_configurato"

    # ── Firma remota: Cloud Signature Consortium API v2 ─────────────
    # URL base comprensivo del prefisso di versione, es. https://rssp.example.it/csc/v2
    csc_url_base: str = ""
    # Endpoint OAuth2 (client_credentials) che rilascia il token di servizio
    csc_url_token: str = ""
    csc_client_id: str = ""
    csc_client_secret: SecretStr = SecretStr("")
    csc_credential_id: str = ""
    csc_pin: SecretStr = SecretStr("")
    # OID dell'algoritmo di firma; vuoto = dedotto dalla chiave del certificato
    csc_sign_algo_oid: str = ""
    csc_timeout_secondi: float = 30.0

    # ── Marca temporale: TSA RFC 3161 su HTTP ───────────────────────
    tsa_url: str = ""
    tsa_utente: str = ""
    tsa_password: SecretStr = SecretStr("")
    # Politica della TSA attesa nel TSTInfo (vuoto = nessun vincolo)
    tsa_policy_oid: str = ""
    # PEM con le radici attendibili della TSA (obbligatorio in produzione)
    tsa_radici_pem: str = ""
    tsa_timeout_secondi: float = 30.0

    # ── Verifica firme ───────────────────────────────────────────────
    # PEM con le radici attendibili per le firme (in attesa della Trusted List)
    firma_radici_pem: str = ""

    # ── Adattatore di sviluppo ───────────────────────────────────────
    # Cartella in cui conservare chiavi e certificati di sviluppo tra un avvio
    # e l'altro; vuota = generati in memoria a ogni avvio.
    sviluppo_dir_chiavi: str = ""

    # ── R13a: registro dei fornitori ────────────────────────────────
    # Login di SOLA LETTURA di fornitori_firma e impostazioni_valori (gruppo
    # verumdoc_firma, migrazione 20261107_054); obbligatorio in produzione.
    database_url: str = ""
    # Segreti dei fornitori registrati: {"<codice>": {"client_secret": …, "pin": …,
    # "otp": …, "mtls_certificato_pem": …, "mtls_chiave_pem": …, "utente": …,
    # "password": …}} (libs.shared.firma_qualificata.SEGRETI_PER_TIPO)
    firma_segreti_json: SecretStr = SecretStr("")
    # Librerie PKCS#11 caricabili (percorsi assoluti separati da virgola): fissate
    # al deploy, mai dalla console (caricare una libreria è eseguire codice)
    pkcs11_librerie_ammesse: str = ""
    # Secondi fra due letture del registro e dei parametri (modifiche in console)
    firma_configurazione_cache_secondi: float = 30.0
    # Prove di connessione con firma di prova per ora, per TUTTE le repliche (R16,
    # VD-C-168: riserva atomica in Redis; senza REDIS_URL, fuori produzione, per processo)
    firma_prove_per_ora: int = 20
    # R16 (VD-C-166, VD-C-168): Redis dei limiti condivisi; obbligatorio in produzione
    redis_url: str = ""
    # R16 (VD-C-166): richieste di OTP e tentativi di firma personale per persona ogni
    # 15 minuti (ciascuna operazione ha il suo contatore)
    firma_personale_tentativi: int = 5

    # Dimensione massima del contenuto da firmare (byte)
    max_dimensione_byte: int = 50 * 1024 * 1024

    @property
    def in_produzione(self) -> bool:
        return self.env.lower() == "production"


settings = Settings()
