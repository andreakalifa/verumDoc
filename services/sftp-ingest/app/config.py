# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/sftp-ingest/app/config.py — impostazioni del canale SFTP.

Servizio: sftp-ingest (piano R03 §3.2). Nessun valore normativo: limiti, tempi
e politiche di sicurezza sono impostazioni con predefinito (REGOLE §8.1).

Segreti (REGOLE §19.3, §19.4), solo da variabili d'ambiente o secret manager:
  SFTP_CHIAVE_HOST              chiave privata host SSH (OpenSSH o PEM). In
                                produzione obbligatoria; in development, se manca,
                                si genera una ed25519 in SFTP_CHIAVE_HOST_FILE_SVILUPPO
                                (file ignorato da git); in test resta in memoria.
  SFTP_TOKEN_CHIAVE_PRIVATA     chiave RSA con cui il canale firma i token di servizio
                                (contratto C2) per l'utente responsabile dell'account.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from libs.shared.elenchi_ambiente import elenco_da_ambiente
from libs.shared.security.token_canale import DURATA_MASSIMA_TOKEN_CANALE

#: Ambienti in cui chiavi e indirizzi «di sviluppo» sono ammessi.
AMBIENTI_SVILUPPO = frozenset({"development", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore",
    )
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed).
    env: str = "production"
    service_name: str = "sftp-ingest"
    service_version: str = "1.0.0"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    )
    #: Componenti attivi in questo processo: «server», «lavoratore» o entrambi.
    #: Si possono scalare separatamente (repliche del solo lavoratore).
    sftp_componenti: Annotated[list[str], NoDecode] = ["server", "lavoratore"]

    # ── Server SFTP ────────────────────────────────────────────────────
    sftp_host: str = "0.0.0.0"  # noqa: S104 — il servizio ascolta nel pod/container
    sftp_porta: int = 2222
    sftp_chiave_host: SecretStr = SecretStr("")
    sftp_chiave_host_file_sviluppo: str = ".chiave_host_sviluppo"
    #: Radice del volume: una cartella per account (<radice>/<account_id>/).
    #: In esercizio è un volume condiviso fra server e lavoratori (RWX).
    sftp_radice: str = "/var/lib/verumdoc/sftp"
    #: Spazio massimo occupato da un account sul volume (byte), tutte le cartelle.
    sftp_quota_account_byte: int = 50 * 1024**3
    #: Ogni quanto si ricalcola l'occupazione reale dal disco (secondi).
    sftp_quota_ricalcolo_secondi: int = 60
    #: Tipi di chiave pubblica ammessi per l'accesso (nomi OpenSSH).
    sftp_tipi_chiave_ammessi: Annotated[list[str], NoDecode] = [
        "ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521", "ssh-rsa",
    ]
    #: Lunghezza minima delle chiavi RSA (bit).
    sftp_rsa_bit_minimi: int = 3072
    #: Algoritmi di firma accettati per l'autenticazione: niente ssh-rsa (SHA-1).
    sftp_algoritmi_firma: Annotated[list[str], NoDecode] = [
        "ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521", "rsa-sha2-512", "rsa-sha2-256",
    ]
    #: Tempo massimo per completare l'autenticazione (secondi).
    sftp_login_timeout_secondi: int = 60
    # R10 (D12, VD-C-107): PROXY protocol del bilanciatore. Reti dei bilanciatori
    # (CIDR, separate da virgole): solo da lì l'intestazione PROXY si legge ed è
    # obbligatoria; dagli altri indirizzi la connessione è diretta (IP del socket).
    # Vuoto = nessun bilanciatore con PROXY protocol (comportamento di prima).
    sftp_proxy_sorgenti_fidate: Annotated[list[str], NoDecode] = []
    sftp_proxy_attesa_secondi: float = 5.0

    # ── Lavoratore ─────────────────────────────────────────────────────
    lavoratore_intervallo_secondi: float = 5.0
    #: Cartelle lavorate in parallelo da una replica.
    lavoratore_parallelismo: int = 4
    #: Parti di un file caricate in parallelo.
    lavoratore_parti_parallele: int = 4
    #: Durata del lease su una cartella (secondi); rinnovato a un terzo.
    lavoratore_lease_secondi: int = 300
    #: Una cartella è presa in carico solo se nulla è cambiato da tanti secondi.
    lavoratore_stabilita_secondi: int = 30
    #: Una cartella mai completata (indice assente, file «.tmp-» rimasti) ferma da
    #: tante ore si lavora comunque e si scarta con il motivo: il cliente ha un esito.
    lavoratore_abbandono_ore: int = 24
    #: Attese fra i tentativi dopo un errore temporaneo (§12.4); poi scarto.
    lavoratore_ritardi_secondi: list[int] = [60, 300, 1500, 7200]
    #: Attesa massima della verifica di un caricamento prima di riprovare.
    lavoratore_attesa_verifica_secondi: int = 600
    #: Dimensione massima di indice.json (byte).
    lavoratore_indice_max_byte: int = 10 * 1024 * 1024
    #: Giorni di permanenza di fatti/, scarti/ e out/ prima della pulizia.
    lavoratore_conserva_fatti_giorni: int = 7
    lavoratore_conserva_scarti_giorni: int = 30
    lavoratore_conserva_esiti_giorni: int = 30
    #: Cartelle considerate per giro di scansione e account (limite di lavoro).
    lavoratore_cartelle_per_giro: int = 200
    #: R03 f2 (REV-15): account segnalati presi in lease per giro da una replica.
    lavoratore_account_per_giro: int = 100
    #: Ogni quanto (e all'avvio) tutti gli account si guardano una volta, divisi
    #: fra le repliche: rete di sicurezza per segnalazioni perse e pulizia.
    lavoratore_controllo_completo_secondi: int = 3600
    #: Attesa massima di un pacchetto fermato dalla quota del piano (QTA-001/002)
    #: dalla prima presa in carico; i rinvii per quota non consumano i tentativi.
    lavoratore_quota_attesa_massima_ore: int = 72
    #: Intervallo di scrittura a lotti delle segnalazioni del server SFTP.
    sftp_segnalazioni_intervallo_secondi: float = 0.5

    # ── Gateway (contratto C1) ─────────────────────────────────────────
    gateway_url: str = "http://gateway:8000"
    gateway_timeout_secondi: float = 60.0
    #: Attese fra i ritentativi di una singola chiamata (solo errori ritentabili).
    gateway_ritentativi_secondi: list[float] = [1.0, 3.0, 10.0]
    #: Tetto all'attesa di un Retry-After DENTRO una chiamata (c2, difetto 1):
    #: oltre, la cartella si rilascia e il nuovo tentativo si programma.
    gateway_retry_after_max_secondi: float = 60.0
    #: Base pubblica dei link in esito.json (vuota = percorsi relativi).
    url_pubblico_api: str = ""

    # ── Token di servizio (contratto C2) ───────────────────────────────
    sftp_token_chiave_privata: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    # Il pubblico (aud) del token di canale è la costante del contratto C2
    # «verumdoc-canale» (libs.shared.security.token_canale): JWT_AUDIENCE non si
    # legge più (R03 i1), così il canale non può emettere token col pubblico utente.
    token_canale_durata_secondi: int = 300

    @field_validator("sftp_componenti", "sftp_tipi_chiave_ammessi", "sftp_algoritmi_firma",
                     "sftp_proxy_sorgenti_fidate",
                     mode="before")
    @classmethod
    def _elenchi(cls, valore: object) -> object:
        # Formato unico con onboarding (R03 i1): elenco separato da virgole;
        # array JSON accettato per compatibilità (libs.shared.elenchi_ambiente).
        return elenco_da_ambiente(valore)

    @property
    def ambiente(self) -> str:
        return (self.env or "").strip().lower()

    @property
    def in_produzione(self) -> bool:
        return self.ambiente not in AMBIENTI_SVILUPPO


def problemi_avvio(s: Settings) -> list[str]:
    """Impostazioni che impediscono l'avvio (REGOLE §19.4). Funzione pura."""
    problemi: list[str] = []
    ammessi = {"server", "lavoratore"}
    sconosciuti = [c for c in s.sftp_componenti if c not in ammessi]
    if sconosciuti:
        problemi.append(f"SFTP_COMPONENTI non ammessi: {sconosciuti} (ammessi: server, lavoratore)")
    if not s.sftp_componenti:
        problemi.append("SFTP_COMPONENTI vuoto: nessun componente da avviare")
    for rete in s.sftp_proxy_sorgenti_fidate:
        import ipaddress

        try:
            r = ipaddress.ip_network(rete, strict=True)
        except ValueError:
            problemi.append(f"SFTP_PROXY_SORGENTI_FIDATE: «{rete}» non è una rete valida")
            continue
        if r.prefixlen < (20 if r.version == 4 else 48):
            # revisione R10 (V4): solo le subnet dell'NLB, mai la VPC intera
            problemi.append(f"SFTP_PROXY_SORGENTI_FIDATE: «{rete}» più ampia di /20 (IPv4) o "
                            "/48 (IPv6): indicare solo le subnet del bilanciatore")
    if s.sftp_rsa_bit_minimi < 2048:
        problemi.append("SFTP_RSA_BIT_MINIMI sotto 2048 non è ammesso")
    if "ssh-rsa" in s.sftp_algoritmi_firma:
        problemi.append("SFTP_ALGORITMI_FIRMA non può contenere ssh-rsa (firma SHA-1)")
    if s.lavoratore_lease_secondi < 30:
        problemi.append("LAVORATORE_LEASE_SECONDI deve essere almeno 30")
    if not s.lavoratore_ritardi_secondi:
        problemi.append("LAVORATORE_RITARDI_SECONDI vuoto")
    if not 60 < s.token_canale_durata_secondi <= DURATA_MASSIMA_TOKEN_CANALE:
        # oltre 60 s per il rinnovo anticipato; il massimo è quello del contratto C2
        problemi.append("TOKEN_CANALE_DURATA_SECONDI deve essere fra 61 e "
                        f"{DURATA_MASSIMA_TOKEN_CANALE}")
    if s.in_produzione:
        if "server" in s.sftp_componenti and not s.sftp_chiave_host.get_secret_value().strip():
            problemi.append("SFTP_CHIAVE_HOST mancante: in produzione la chiave host viene "
                            "dal secret manager")
        if ("lavoratore" in s.sftp_componenti
                and not s.sftp_token_chiave_privata.get_secret_value().strip()):
            problemi.append("SFTP_TOKEN_CHIAVE_PRIVATA mancante: il lavoratore non può "
                            "autenticarsi verso il gateway")
        if ("lavoratore" not in s.sftp_componenti
                and s.sftp_token_chiave_privata.get_secret_value().strip()):
            # R06 (VD-C-126): il server (esposto con l'NLB) non firma token di
            # canale; la chiave sta solo nel Secret del lavoratore
            problemi.append("SFTP_TOKEN_CHIAVE_PRIVATA nel processo del solo server: va solo "
                            "nel Secret sftp-ingest-lavoratore-secrets")
        if not s.gateway_url.startswith("https://") and "localhost" in s.gateway_url:
            problemi.append("GATEWAY_URL punta a localhost in produzione")
    return problemi


settings = Settings()
