# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/config.py — aggiornato con SPID/CIE F2.4
"""
from __future__ import annotations

from datetime import date

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Ambienti in cui il login di sviluppo PUÒ essere attivato (REGOLE §19.2).
AMBIENTI_LOGIN_SVILUPPO: frozenset[str] = frozenset({"development", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False)
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "gateway"
    service_version: str = "2.0.0"
    database_url: str = "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    redis_url: str = "redis://:redis_secret_dev_only@localhost:6379/0"
    kafka_bootstrap: str = "localhost:9092"
    vault_url: str = "http://localhost:8200"
    vault_token: str = "dev-root-token-local-only"
    # JWT RS256 (REGOLE §19.1, §19.3): chiavi SOLO da variabili d'ambiente/secret
    # (JWT_PRIVATE_KEY, JWT_PUBLIC_KEY). Il gateway firma: senza chiave privata
    # non parte (eccetto ENV=test, che usa una coppia effimera in memoria).
    jwt_private_key: SecretStr = SecretStr("")
    jwt_public_key: str = ""
    jwt_expiry_seconds: int = 3600
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # R06 (VD-C-127): scadenza della sessione del portale per inattività (minuti),
    # più breve per i ruoli privilegiati; fissata all'accesso per ogni sessione
    # (libs.shared.security.inattivita), applicata da tutti i servizi.
    # R07 (D14): SESSIONE_INATTIVITA_* tolte: il limite per ruolo è un'impostazione
    # della console (SESSIONE_INATTIVITA_MINUTI_<RUOLO>, libs.shared.parametri)
    # Login di sviluppo (dev-token, mock SPID): VERUMDOC_DEV_AUTH=1 E ENV development/test.
    verumdoc_dev_auth: bool = False
    cors_origins: list[str] = ["https://app.verumdoc.it"]
    log_level: str = "INFO"
    log_json: bool = False
    rate_limit_window_seconds: int = 60
    # F2.4 — SPID/CIE
    spid_aggregator_url: str = ""
    spid_client_id: str = ""
    spid_client_secret: SecretStr = SecretStr("")
    # VD-C-006: verifica dell'id_token dell'aggregatore (firma via JWKS, iss, aud=client_id)
    spid_issuer: str = ""
    spid_jwks_url: str = ""
    spid_jwks_cache_seconds: int = 3600
    # Revisione B3: livello minimo dell'autenticazione, letto dall'`acr`
    # dell'id_token VERIFICATO (non da quanto chiesto dal browser). Mai L1.
    spid_livello_minimo: int = Field(2, ge=2, le=3)
    # R13b (VD-C-069): chiave privata del cliente OIDC (PEM, RSA o EC P-256) per
    # private_key_jwt, request object firmato e decifratura dello UserInfo; kid
    # pubblicato all'aggregatore. Host a cui si possono inviare SPID_CLIENT_SECRET e le
    # asserzioni (fissati al deploy): obbligatori in produzione con un segreto.
    spid_chiave_privata_client: SecretStr = SecretStr("")
    spid_chiave_client_kid: str = ""
    spid_host_ammessi: list[str] = []
    base_url: str = "https://api.verumdoc.it"
    # SPID mock: attivo solo se anche il login di sviluppo è abilitato
    spid_mock_enabled: bool = True
    # Indirizzi di ritorno del frontend ammessi oltre a https://app.verumdoc.it
    # (es. staging): SPID_ALLOWED_REDIRECTS='["https://app.staging.verumdoc.it/login"]'
    spid_allowed_redirects: list[str] = []
    # ── R10 (D08): accesso locale (password con codice a tempo, passkey, inviti) ──
    # Chiave AES-256-GCM (32 byte in base64) dei segreti TOTP, con id per la
    # rotazione e le precedenti in JSON {"id": "chiave"}; senza chiave l'accesso
    # con password e codice a tempo risponde 503 ACC-503 (mai segreti in chiaro).
    credenziali_chiave_cifratura: SecretStr = SecretStr("")
    credenziali_chiave_cifratura_id: str = "c1"
    credenziali_chiavi_cifratura_precedenti: SecretStr = SecretStr("")
    # WebAuthn: identificativo della parte (dominio della console) e origini
    # ammesse; vuoti = host e origine di BASE_URL_CONSOLE
    webauthn_rp_id: str = ""
    webauthn_rp_nome: str = "VerumDoc"
    webauthn_origini: list[str] = []
    # Indirizzo della console (collegamenti d'invito, origine WebAuthn)
    base_url_console: str = "https://app.verumdoc.it"
    # Revisione R10 (V1): proxy fidati (CIDR delle sottoreti dell'ALB) da cui si legge
    # X-Forwarded-For per l'IP reale del cliente; mai 0.0.0.0/0. Vuoto fuori da
    # sviluppo/test = limite per IP disattivato (resta il blocco per account).
    proxy_fidati: list[str] = []
    # Tentativi SBAGLIATI per IP reale sulle rotte d'accesso locali (15 minuti) e
    # limite anonimo separato della sola sfida della passkey
    accesso_fallimenti_ip_15_minuti: int = Field(30, ge=5, le=10_000)
    accesso_opzioni_passkey_ip_15_minuti: int = Field(300, ge=10, le=100_000)

    @field_validator("proxy_fidati")
    @classmethod
    def _proxy_fidati(cls, valore: list[str]) -> list[str]:
        import ipaddress

        for rete in valore:
            r = ipaddress.ip_network(rete, strict=True)  # ValueError: l'avvio si ferma
            if r.prefixlen < 8:
                raise ValueError(f"PROXY_FIDATI: «{rete}» troppo ampia (renderebbe "
                                 "falsificabile l'IP del cliente)")
        return valore
    # Chiavi API (REGOLE §19.8, libs.shared.security.chiavi_api): pepper dell'HMAC,
    # uguale in onboarding. Obbligatorio fuori da development/test (app.auth.api_key).
    api_key_pepper: SecretStr = SecretStr("")

    # S3 / MinIO — per upload file fisici al versamento
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_bucket_pdv: str = "verumdoc-pdv"
    s3_region: str = "eu-south-1"

    # ── Versamento (R03: caso d'uso unico in streaming) ─────────────────
    # Limite per file del canale REST multipart (sopra si usano le sessioni di
    # caricamento): un file oltre il limite si rifiuta (VRS-010) senza leggerlo tutto.
    versamento_rest_max_file_mb: int = Field(100, ge=1, le=5000)
    # R03 f1 (REV-09): corpo multipart intero (tutti i file), letto SOLO dopo
    # l'autenticazione e fermato appena supera il limite (anche senza
    # Content-Length, cioè in chunked). Allineato all'ingress (maxBodyMB 110).
    versamento_rest_max_corpo_mb: int = Field(110, ge=1, le=100_000)
    # Numero massimo di file per PdV (protezione del servizio, VRS-113).
    versamento_max_file_per_pdv: int = Field(500, ge=1, le=100_000)
    # Parte del caricamento multipart sullo staging (S3: minimo 5 MB). In memoria
    # resta al più una parte per file in trasferimento.
    staging_parte_mb: int = Field(8, ge=5, le=512)
    # Idempotency-Key obbligatoria (VD-P-27) — valori del livello «conservatore»
    # nel motore delle regole; vuoti = predefinito del registro (vero / 31/03/2027).
    idempotenza_obbligatoria: bool | None = None
    idempotenza_transizione_fino_al: date | None = None
    # Data da cui il versamento senza Idempotency-Key è deprecato (intestazione
    # Deprecation, RFC 9745): rilascio R03.
    idempotenza_deprecata_dal: date = date(2026, 10, 21)

    # ── Sessioni di caricamento (R03 b1, contratto C1) ──────────────────
    # Limiti tecnici del servizio (non normativi: il limite per file della
    # classe resta nel motore delle regole, CLASSE_DIMENSIONE_MAX_MB).
    caricamenti_max_dimensione_mb: int = Field(5120, ge=1, le=1_048_576)
    # Dimensione delle parti (S3: minimo 5 MB tranne l'ultima, al più 10.000 parti)
    caricamenti_dimensione_parte_mb: int = Field(8, ge=5, le=512)
    # Una sessione non usata da un versamento scade (e i suoi file si eliminano)
    caricamenti_scadenza_ore: int = Field(24, ge=1, le=24 * 30)
    # Verificatore e pulizia nel processo del gateway (come il relay dell'outbox);
    # disattivabili per eseguirli come processi dedicati (python -m app.services.caricamenti)
    caricamenti_lavoratore_attivo: bool = True
    caricamenti_intervallo_secondi: float = Field(5.0, gt=0, le=3600)
    caricamenti_pulizia_intervallo_secondi: float = Field(300.0, gt=0, le=86_400)
    caricamenti_lease_verifica_secondi: int = Field(900, ge=30, le=86_400)
    caricamenti_verifica_tentativi_max: int = Field(5, ge=1, le=100)
    # Corpo JSON del versamento per riferimento (indice + riferimenti ai caricamenti)
    versamento_json_max_mb: int = Field(16, ge=1, le=256)
    # R03 f1 (REV-07): una parte prenotata (riga senza ETag) più vecchia di così,
    # con un contenuto diverso, si considera abbandonata (processo morto fra la
    # prenotazione e la scrittura) e il reinvio la sostituisce invece del 409.
    caricamenti_prenotazione_parte_secondi: int = Field(300, ge=30, le=86_400)

    # ── Pulizia dello staging dopo la conservazione (R03 f1, REV-08) ─────
    # Le copie di transito (versamenti/…, caricamenti/…) di un PdV si eliminano
    # dopo che il PdA è su storage (copia verificata) da almeno questo tempo.
    staging_pulizia_attivo: bool = True
    staging_pulizia_attesa_ore: int = Field(24, ge=1, le=24 * 365)
    staging_pulizia_intervallo_secondi: float = Field(300.0, gt=0, le=86_400)
    # PdV per lotto e lotti al più per giro (1M documenti/giorno ≈ 12/s: 500 × 20
    # lotti ogni 5 minuti = 10.000 PdV per giro, oltre 30 volte il fabbisogno)
    staging_pulizia_lotto: int = Field(500, ge=1, le=5_000)
    staging_pulizia_lotti_per_giro: int = Field(20, ge=1, le=1_000)
    # Verifiche (HEAD) contemporanee sullo storage del PdA
    staging_pulizia_verifiche_parallele: int = Field(16, ge=1, le=256)
    # R03 f3 (REV-N1): rinvio PER PdV quando la verifica non riesce. Attesa
    # crescente (RINVIO_MINUTI × 2^(tentativi-1), al più RINVIO_MAX_ORE); dopo
    # TENTATIVI_MAX verifiche non riuscite il PdV è «bloccato» (escluso dai giri,
    # log di errore e metrica verumdoc_staging_pulizia_pdv_bloccati) finché un
    # operatore non lo rimette in coda. I motivi permanenti (dati di transito
    # assenti, chiave fuori dallo staging) bloccano subito.
    staging_pulizia_rinvio_minuti: int = Field(10, ge=1, le=24 * 60)
    staging_pulizia_rinvio_max_ore: int = Field(24, ge=1, le=24 * 30)
    staging_pulizia_tentativi_max: int = Field(8, ge=1, le=100)
    # Metrica dei PdV bloccati nel /metrics delle repliche API: conteggio al più
    # ogni METRICHE_SECONDI e fermato a METRICHE_TETTO righe (indice parziale)
    staging_pulizia_metriche_secondi: float = Field(300.0, ge=10, le=86_400)
    staging_pulizia_metriche_tetto: int = Field(100_000, ge=1, le=10_000_000)

    # ── Canale PDND (R03 b1, VD-C-092) ──────────────────────────────────
    # Il file viaggia in base64 nel JSON dell'e-service: limite sul file decodificato.
    pdnd_max_file_mb: int = Field(50, ge=1, le=1024)
    # R03 f1 (REV-05): richieste di versamento PDND contemporanee per replica (oltre:
    # 503 PDND-503-OCCUPATO ritentabile) e limite dei campi diversi dal file.
    pdnd_richieste_contemporanee_max: int = Field(8, ge=1, le=1000)
    pdnd_max_metadati_mb: int = Field(4, ge=1, le=64)

    # ── Disco temporaneo della replica (R03 f3, REV-N2) ─────────────────
    # Il multipart REST (Starlette scrive su /tmp le parti oltre 1 MB) e il
    # file decodificato PDND (FileDecodificato) usano /tmp, un emptyDir con
    # sizeLimit (Helm: services.gateway.tmpSizeLimit): oltre, kubelet sfratta il
    # pod. Due tetti per replica, controllati PRIMA di leggere il corpo:
    #   - multipart contemporanei (oltre: 503 UPL-503-OCCUPATO ritentabile);
    #   - budget unico di byte prenotati su /tmp, condiviso da multipart e PDND
    #     (ogni richiesta prenota Content-Length o, se manca, il suo massimo;
    #     oltre: 503 ritentabile del canale). verifica_manifest.py controlla che
    #     sizeLimit copra il budget (o i tetti di concorrenza) più un margine.
    versamento_multipart_richieste_contemporanee_max: int = Field(8, ge=1, le=1000)
    disco_temporaneo_budget_mb: int = Field(1536, ge=1, le=1_048_576)

    # ── R17 (VD-P-164): conferma firmata delle decisioni sensibili ──────
    # Chiave privata Ed25519 SOLO del gateway (Secret gateway-secrets: PEM PKCS#8 o 32 byte
    # in base64) e suo identificativo; le chiavi PUBBLICHE vanno a ciclo-vita
    # (CONFERME_CHIAVI_PUBBLICHE). Senza chiave nessuna conferma si firma (503, fail-closed).
    conferme_chiave_privata: SecretStr = SecretStr("")
    conferme_kid: str = ""
    # Validità della conferma firmata (deve essere registrata dall'API entro questo tempo)
    conferme_durata_secondi: int = Field(300, ge=30, le=900)

    def login_sviluppo_abilitato(self) -> bool:
        """True solo con flag esplicito VERUMDOC_DEV_AUTH=1 E ENV development/test."""
        return bool(self.verumdoc_dev_auth) and login_sviluppo_consentito(self.env)


def login_sviluppo_consentito(env: str) -> bool:
    """L'ambiente ammette il login di sviluppo? (mai staging/produzione)."""
    return (env or "").strip().lower() in AMBIENTI_LOGIN_SVILUPPO


settings = Settings()
