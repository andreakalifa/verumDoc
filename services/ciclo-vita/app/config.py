# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/ciclo-vita/app/config.py — impostazioni del servizio ciclo di vita.

Riferimenti: contratto Q2 di R05, VD-C-072, VD-P-06; REGOLE_SVILUPPO §8.1
(nessun valore normativo nel codice: soglie, ruoli e termini sono impostazioni
con predefinito), §19.4 (impostazioni pericolose fermano l'avvio).

Le regole del ciclo di vita che dipendono da scelte del committente (chi può
annullare dopo la conservazione, orizzonte di retention degli allegati) sono
impostazioni con predefinito dichiarato nel rapporto R05 come «da confermare».
Quando il motore delle regole avrà i livelli ENTE/ACCORDO per questi codici
(contratto Q1), la risoluzione passerà da lì senza cambiare i chiamanti
(``app.domain.permessi``).
"""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      case_sensitive=False, extra="ignore")

    # Senza ENV si assume produzione (fail-closed, revisione M5)
    env: str = "production"
    service_name: str = "ciclo-vita"
    service_version: str = "1.0.0"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev")
    # Kafka: solo per il relay dell'outbox (eventi per i clienti, VD-C-076)
    kafka_bootstrap: str = "localhost:9092"

    # ── Autenticazione (stessa chiave pubblica, emittente e pubblico del gateway) ──
    jwt_public_key: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # Pepper delle chiavi API (uguale a gateway e onboarding)
    api_key_pepper: SecretStr = SecretStr("")
    # Limite di richieste al minuto per piano (libs/shared/limiti_richieste)
    redis_url: str = ""

    # ── Storage ────────────────────────────────────────────────────────
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "eu-south-1"
    # Bucket conservativo (WORM): PdA da scartare, allegati, verbali, atti
    s3_bucket_aip: str = "verumdoc-aip"
    # Bucket di transito dei versamenti: PdD ricevuti con le sessioni di caricamento
    s3_bucket_pdv: str = "verumdoc-pdv"

    # ── Servizi chiamati ──────────────────────────────────────────────
    # Registro delle impronte (contratto Q3): foglie di scarto, annullamento,
    # rettifica con un token di servizio (aud «verumdoc-registro», scope
    # «registro:aggiungi»). Senza token il lavoro resta in attesa con l'errore.
    hashchain_url: str = "http://hashchain:8000"
    hashchain_token_registro: SecretStr = SecretStr("")
    hashchain_timeout_secondi: float = 15.0
    # Gateway: sessioni di caricamento e versamento per riferimento, con un
    # token di canale CICLO_VITA firmato con la chiave PROPRIA del servizio.
    gateway_url: str = "http://gateway:8000"
    gateway_timeout_secondi: float = 60.0
    ciclo_vita_token_chiave_privata: SecretStr = SecretStr("")
    # Distribution: PdD MIGRAZIONE (riversamento in uscita e restituzione) con un
    # token di servizio (aud «verumdoc-distribution», scope «migrazione:servizio»).
    distribution_url: str = "http://distribution:8000"
    distribution_token_servizio: SecretStr = SecretStr("")
    distribution_timeout_secondi: float = 30.0

    # ── Lavoratore (REGOLE §12.9) ─────────────────────────────────────
    lavoratore_attivo: bool = True
    lavoratore_intervallo_secondi: float = 2.0
    lavoratore_lease_secondi: int = Field(300, ge=30, le=3600)
    # Attese fra i tentativi di un passo fallito per un errore temporaneo
    lavoratore_ritardi_secondi: list[int] = [60, 300, 1500, 7200]
    # Elementi trattati per giro di una pratica (lease rinnovato a ogni lotto)
    lavoratore_lotto: int = Field(200, ge=1, le=5000)
    # Attesa fra due controlli dello stato di un PdV di rettifica o di un PdD
    lavoratore_attesa_esterna_secondi: int = Field(30, ge=1, le=3600)

    # ── Regole del ciclo di vita (predefiniti da confermare, rapporto R05) ──
    # R09 (D23): ruoli e doppia approvazione dell'annullamento sono parametri del
    # motore (ANNULLAMENTO_*), per ente, in console
    rettifica_ruoli: list[str] = ["ADMIN_TENANT", "OPERATORE", "API_SYSTEM"]
    # Scarto: PdA al massimo per pratica (oltre: restringere i criteri)
    scarto_max_pda_per_pratica: int = Field(100_000, ge=1, le=5_000_000)
    # Revisione 2 di R05 (S3): tentativi al più per la cancellazione di UN PdA
    # già iniziata, con errori dello storage che non sono un blocco riconosciuto
    # (legal hold, retention: chiudono subito). Oltre: oggetto NON_COMPLETATO,
    # PdA ELIMINAZIONE_INCOMPLETA, la pratica si conclude con eccezioni. Mai un
    # ciclo infinito. R09 (D20): parametro SCARTO_TENTATIVI_PDA del motore; questo
    # valore d'ambiente vale solo senza la riga del conservatore
    scarto_tentativi_max_pda: int = Field(5, ge=1, le=100)
    # Allarme (metrica verumdoc_ciclo_vita_pratiche_lavoro_in_errore): pratiche con
    # lavoro e almeno tanti tentativi falliti
    lavoratore_soglia_allarme_tentativi: int = Field(5, ge=1, le=1000)
    # R14 (VD-C-133): dopo tanti tentativi falliti il lavoro si SOSPENDE (stato di
    # errore esplicito ``LC-409-TENTATIVI-ESAURITI``: lavoro tolto, allarme
    # PraticaSospesa, ripresa manuale documentata in docs/runbooks/pratica_sospesa.md).
    # 0 = tentativi illimitati (comportamento prima di R14). Con i ritardi
    # predefiniti 30 tentativi sono circa due giorni e mezzo.
    lavoratore_tentativi_massimi: int = Field(30, ge=0, le=10_000)
    # Intervallo di aggiornamento delle metriche dello scarto (processo API)
    metriche_intervallo_secondi: float = Field(60.0, ge=5.0, le=3600.0)
    # Riversamento in uscita / restituzione: PdA al massimo per pratica
    riversamento_max_pda_per_pratica: int = Field(1_000_000, ge=1, le=50_000_000)
    # PdA per richiesta a distribution (la sua API ne accetta al più 100)
    riversamento_lotto_distribution: int = Field(100, ge=1, le=100)
    # Elementi d'esempio restituiti dall'anteprima (il piano completo è nell'impronta)
    anteprima_esempi: int = Field(50, ge=0, le=500)
    # Allegati caricati dagli utenti (autorizzazione, nulla osta)
    allegati_max_mb: int = Field(50, ge=1, le=1024)
    # R09 (D04): retention di allegati, verbali e atti = RETENTION_PERMANENTE_ANNI
    # (parametro del motore) con rinnovo automatico del lavoratore

    # ── Lavoro periodico del lavoratore (R09: D04, D06, D20, D21) ─────────
    # Ogni passo ha un lease in ciclo_vita_periodico (una replica alla volta)
    periodico_attivo: bool = True
    periodico_controllo_secondi: float = Field(60.0, ge=1.0, le=3600.0)
    # Intervalli dei passi (la frequenza della proposta di scarto è un parametro
    # dell'ente: questo è solo l'intervallo dei controlli)
    periodico_proposte_secondi: int = Field(6 * 3600, ge=60)
    periodico_riprese_secondi: int = Field(900, ge=60)
    periodico_minimizzazione_secondi: int = Field(6 * 3600, ge=60)
    periodico_rinnovo_secondi: int = Field(24 * 3600, ge=60)
    periodico_lease_secondi: int = Field(1800, ge=60, le=86400)
    # Elementi per giro (PdA da minimizzare, PdA e allegati da rinnovare)
    periodico_lotto: int = Field(500, ge=1, le=100_000)
    # PdD ricevuti per il riversamento in ingresso: dimensione massima (MB) e
    # massima dell'indice letto in memoria (MB)
    riversamento_ingresso_max_mb: int = Field(5120, ge=1)
    riversamento_ingresso_indice_max_mb: int = Field(20, ge=1, le=200)
    # Denominazione del conservatore nei verbali e negli atti (vuota = «non
    # indicata», mai inventata: stesso valore di dashboard-api)
    conservatore_denominazione: str = ""

    # ── Elenchi a cursore ─────────────────────────────────────────────
    elenco_limite_predefinito: int = 50
    elenco_limite_massimo: int = 200

    # R08 (D01): verificatore delle firme (libs/shared/firme): EU DSS nel servizio
    # verifica-firme con ripiego pyHanko per i PDF, o pyHanko con trust anchor locali
    firme_verificatore: str = "pyhanko"
    firme_dss_url: str = ""
    firme_dss_token: SecretStr = SecretStr("")
    firme_dss_timeout_s: float = Field(default=60.0, gt=0)
    firme_ripiego_pdf: bool = True
    firme_radici_pem: str = ""
    firme_revoche_rete: bool = False
    # Revisione R08 (X7, X8): dimensione massima inviata al verificatore e tempo
    # massimo di una verifica pyHanko (thread)
    firme_dimensione_max_mb: int = Field(default=60, ge=1, le=5120)
    firme_tempo_max_s: float = Field(default=120.0, gt=0)

    # ── R17 (VD-P-164): conferma firmata dal gateway delle decisioni sensibili ──
    # ATTIVO | DISATTIVO; vuoto = ATTIVO in produzione, DISATTIVO altrove (sviluppo e
    # test). Le chiavi PUBBLICHE del gateway (JSON {kid: chiave Ed25519 in base64}) e i kid
    # revocati vengono dalla configurazione (ConfigMap), mai dal database.
    conferma_decisioni: str = ""
    conferme_chiavi_pubbliche: str = ""
    conferme_kid_revocati: str = ""

    @property
    def in_produzione(self) -> bool:
        return self.env.lower() == "production"

    @property
    def conferma_decisioni_attiva(self) -> bool:
        """R17: in produzione è ATTIVA salvo DISATTIVO esplicito (che l'avvio rifiuta)."""
        valore = self.conferma_decisioni.strip().upper()
        if not valore:
            return self.in_produzione
        return valore != "DISATTIVO"

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


settings = Settings()
