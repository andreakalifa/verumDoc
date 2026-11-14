# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/config.py
Configurazione del servizio notifiche — VD-C-024.

Posta: SMTP con sicurezza esplicita.
  SMTP_SICUREZZA = auto | starttls | tls | nessuna
  auto → porta 465 = TLS implicito, ogni altra porta = STARTTLS obbligatorio.
  «nessuna» (testo in chiaro) è ammessa solo fuori produzione.
Senza SMTP_HOST e SMTP_MITTENTE la posta è «non configurata»: i messaggi
restano registrati come «non partita» con il motivo, mai finti come inviati.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "notification"
    service_version: str = "3.0.0"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    )
    kafka_bootstrap: str = "localhost:9092"
    kafka_group_id: str = "notification-service"
    kafka_topic_in: str = "notification.send"
    kafka_topic_dlq: str = "notification.dlq"
    # Ritentativi dell'elaborazione di un evento Kafka (attese in secondi), poi DLQ
    kafka_ritardi_ritentativi_secondi: list[float] = [1.0, 5.0, 30.0]

    # ── SMTP ──────────────────────────────────────────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_sicurezza: Literal["auto", "starttls", "tls", "nessuna"] = "auto"
    smtp_mittente: str = ""
    smtp_nome_mittente: str = ""
    smtp_utente: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_timeout_secondi: float = 30.0
    # PEM di una CA privata per il server SMTP (vuoto = CA di sistema)
    smtp_ca_pem: str = ""

    # ── Coda di uscita (public.notifications) ─────────────────────────
    invio_intervallo_secondi: float = 10.0
    invio_lotto: int = 20
    # Attese tra un tentativo e il successivo; tentativi massimi = len + 1
    invio_ritardi_secondi: list[int] = [60, 300, 900, 3600]
    # Un messaggio «in invio» da più di così torna in coda (crash a metà)
    invio_recupero_dopo_secondi: int = 600

    log_level: str = "INFO"

    # ── Autenticazione delle API pubbliche (eventi, webhook) ──────────
    # Stessa chiave pubblica, emittente e pubblico dei token del gateway.
    jwt_public_key: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # Pepper delle chiavi API (uguale a onboarding e gateway). Obbligatorio
    # fuori da development/test (libs.shared.security.chiavi_api.carica_pepper).
    api_key_pepper: SecretStr = SecretStr("")
    # R03 f2 (REV-03): Redis dei limiti di richieste al minuto per piano (stesso
    # nome del gateway). Obbligatorio in produzione: senza, il servizio non parte.
    redis_url: str = ""

    # ── Elenco degli eventi per i clienti (contratto C3) ──────────────
    eventi_consumer_attivo: bool = True
    eventi_kafka_group_id: str = "notification-eventi-cliente"
    # Topic Kafka → tipo di evento per i clienti. I topic dei servizi esistenti
    # hanno un nome proprio; quelli nuovi si chiamano come il tipo.
    eventi_topic: dict[str, str] = {
        "pdv.received": "versamento.ricevuto",
        "pdv.validated": "versamento.validato",
        "pdv.invalid": "versamento.rifiutato",
        # VD-C-097 (R04): aip.conserved lo pubblica la hashchain quando il PdA
        # è nel registro ed è coperto da un checkpoint.
        "aip.conserved": "versamento.conservato",
        "caricamento.completato": "caricamento.completato",
        "caricamento.errore": "caricamento.errore",
        # Anche le esibizioni: un'esibizione è un PdD di tipo ESIBIZIONE; quando
        # arriva come ZIP, distribution pubblica pdd.pronto con tipo «ESIBIZIONE»
        # (una consegna LINK è pronta già nella risposta). Nessun servizio
        # pubblica un topic «esibizione.pronta»: tolto (R03 i1).
        "pdd.pronto": "pdd.pronto",
        # R04 (contratto K1): checkpoint del registro delle impronte, per tutti gli enti
        "catena.checkpoint": "catena.checkpoint",
        # R04 (VD-C-104): preavviso di scadenza dello staging dei PdV rifiutati (gateway)
        "versamento.staging_scaduto": "versamento.staging_scaduto",
        # R04 (verifica continua, hashchain): anomalia d'integrità di un PdA
        # dell'ente. L'allarme al conservatore è Alertmanager (metriche
        # verumdoc_verifica_anomalie_*), non una e-mail: vedi rapporto R04 int.
        "integrita.anomalia": "integrita.anomalia",
        # R05 (contratto Q2): ciclo di vita (ciclo-vita, topic = tipo)
        "pratica.cambio_stato": "pratica.cambio_stato",
        "versamento.annullato": "versamento.annullato",
        "versamento.rettificato": "versamento.rettificato",
        "scarto.eseguito": "scarto.eseguito",
        "riversamento.pronto": "riversamento.pronto",
        # R07 (D13, D17): avvisi automatici all'ente (gateway, onboarding)
        "quota.soglia_raggiunta": "quota.soglia_raggiunta",
        "idempotenza.chiave_omessa": "idempotenza.chiave_omessa",
        # R09 (D20, D21, D23): avvisi del ciclo di vita (ciclo-vita)
        "ciclo_vita.avviso": "ciclo_vita.avviso",
    }
    eventi_limite_predefinito: int = 100
    eventi_limite_massimo: int = 500
    # R03 f2 (REV-13): un evento compare nell'elenco quando il passo di
    # pubblicazione (seriale, una replica alla volta) gli assegna la posizione,
    # dopo il suo commit: il cursore sulla posizione non salta mai un evento.
    # Ritardo ≈ un intervallo; lotto massimo per giro (a lotto pieno si ripete
    # subito). Prima: cursore sull'id con 2 s di «visibilità» (perdeva eventi).
    eventi_pubblicazione_attiva: bool = True
    eventi_pubblicazione_intervallo_secondi: float = 0.2
    eventi_pubblicazione_lotto: int = 5000
    # Consumer degli eventi: messaggi letti per volta (partizioni in parallelo,
    # in ordine dentro la partizione) e un solo commit degli offset per lotto.
    eventi_consumer_lotto: int = 500
    # EVENTI_RITARDO_VISIBILITA_SECONDI è TOLTA dalla R03 (f2, i3): il cursore è la
    # posizione di pubblicazione. Se un ambiente la imposta ancora è ignorata
    # (extra="ignore"), come ogni variabile non prevista.

    # ── Webhook firmati (contratto C3) ────────────────────────────────
    webhook_lavoratore_attivo: bool = True
    # Chiave AES-256-GCM dei segreti a riposo: 32 byte in base64 (es.
    # `openssl rand -base64 32`). Obbligatoria in produzione; senza, fuori
    # produzione, le API dei webhook rispondono 503 WH-030.
    webhook_chiave_cifratura: SecretStr = SecretStr("")
    webhook_chiave_cifratura_id: str = "k1"
    # Chiavi precedenti per la rotazione, JSON {"k0": "<base64>"}: si usano
    # solo per decifrare i segreti scritti prima della rotazione.
    webhook_chiavi_cifratura_precedenti: SecretStr = SecretStr("")
    # R12a (VD-C-152): chiave PRIVATA X25519 (base64url) della posta: apre al
    # momento dell'invio i segreti sigillati (collegamento d'invito) che
    # onboarding sigilla con la pubblica corrispondente. Obbligatoria in
    # produzione; senza, i messaggi con segreti NON partono (mai in chiaro).
    posta_chiave_privata: SecretStr = SecretStr("")
    posta_chiave_privata_id: str = "p1"
    posta_chiavi_private_precedenti: SecretStr = SecretStr("")
    # R12b (VD-C-161): l'invio della posta (SMTP e apertura dei segreti sigillati)
    # gira nel processo con POSTA_INVIO_ATTIVO=true. In esercizio è il lavoratore
    # dedicato ``notification-posta`` (``python -m app.lavoratore_posta``, nessun
    # HTTP, non esposto), l'UNICO a ricevere POSTA_CHIAVE_PRIVATA e le credenziali
    # SMTP; le repliche API (esposte all'ingress) hanno false e in produzione non
    # partono se ricevono quelle chiavi. Predefinito true: in sviluppo un solo
    # processo fa tutto, come prima.
    posta_invio_attivo: bool = True
    # ── R13b (VD-C-078): canale PEC ────────────────────────────────────
    # Segreti dei canali PEC per codice, legati agli host (libs.shared.pec): SOLO
    # nel Secret del lavoratore notification-posta, mai nelle repliche API.
    pec_segreti_json: SecretStr = SecretStr("")
    pec_attivo: bool = True
    pec_intervallo_secondi: float = 15.0
    pec_lotto: int = 20
    # attese fra i tentativi d'invio; tentativi massimi = len + 1, poi NON_PARTITO
    pec_ritardi_secondi: list[int] = [60, 300, 900, 3600, 14400]
    # un messaggio IN_INVIO da più di così torna in coda (stesso Message-ID)
    pec_recupero_dopo_secondi: int = 900
    pec_lettura_massimo: int = 200
    # prove d'invio dalla console al più per ora (tutti i canali)
    pec_prove_per_ora: int = 10
    # ── R14 (VD-C-170, VD-C-172) ───────────────────────────────────────
    # metriche del lavoratore della posta calcolate dal database dalle repliche API
    metriche_posta_attive: bool = True
    metriche_posta_intervallo_secondi: float = Field(60.0, ge=5.0, le=3600.0)
    # pulizia delle notifiche personali oltre NOTIFICHE_PERSONALI_GIORNI (parametro del
    # motore) nel lavoratore della posta, con lease e fencing (lavori_periodici)
    pulizia_personali_attiva: bool = True
    pulizia_personali_intervallo_secondi: float = Field(3600.0, ge=60.0, le=86400.0)
    pulizia_personali_lotto: int = Field(1000, ge=1, le=10_000)
    pulizia_personali_giri_massimi: int = Field(20, ge=1, le=1000)
    pulizia_personali_lease_secondi: int = Field(600, ge=30, le=3600)
    # Solo sviluppo/test: ammette indirizzi privati e di loopback (vietato in
    # produzione: il servizio non parte).
    webhook_consenti_indirizzi_privati: bool = False
    # PEM di una CA in più per i certificati dei destinatari (vuoto = CA di sistema)
    webhook_ca_pem: str = ""
    # Attese fra un tentativo e il successivo (REGOLE §12.4); tentativi = len + 1,
    # poi la consegna è FALLITA (coda di scarto consultabile e ripetibile).
    webhook_ritardi_secondi: list[int] = [60, 300, 1500, 7200]
    webhook_timeout_connessione_secondi: float = 3.0
    webhook_timeout_secondi: float = 10.0
    webhook_intervallo_secondi: float = 2.0
    webhook_lotto: int = 50
    webhook_concorrenza: int = 20
    # Una consegna IN_CONSEGNA da più di così (crash del lavoratore) si riprende
    webhook_lease_secondi: int = 120
    webhook_max_sottoscrizioni_per_ente: int = 10
    # R03 f2 (REV-14): porte di destinazione ammesse per i webhook (vuoto = tutte,
    # come prima). Consigliato in produzione: [443] (decisione da confermare).
    webhook_porte_ammesse: list[int] = []
    webhook_lunghezza_massima_url: int = 2048

    @property
    def in_produzione(self) -> bool:
        return self.env.lower() == "production"


settings = Settings()
