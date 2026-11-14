# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/hashchain/app/config.py
Sprint 2A FIX-011: aggiunto S3 settings per persistere RdV Definitiva.
R08/R09 (VD-C-009, VD-C-019): sigillo a tempo, ritentativi, servizio di firma.
R04 (K1-K3): registro a due livelli, checkpoint firmati, testimone.
"""
from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False,
        extra="ignore",
    )
    # Revisione di sicurezza M5: senza ENV si assume produzione (fail-closed);
    # sviluppo e test lo impostano esplicitamente (docker-compose, conftest).
    env: str = "production"
    service_name: str = "hashchain"
    service_version: str = "1.0.0"
    database_url: str = (
        "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
    )
    # Kafka: solo per il relay dell'outbox (R04, VD-C-076).
    kafka_bootstrap: str = "localhost:9092"
    # R04: la hashchain non consuma più «aip.conserved» a lotti né pubblica
    # «hashchain.seal»: il registro legge catena_coda e pubblica via outbox.
    # Le vecchie impostazioni (KAFKA_*, BATCH_*, POLL_TIMEOUT_MS,
    # RITARDI_SIGILLO_SECONDI) sono ignorate se presenti.
    # Ogni quanto riprendere firme e RdV dei blocchi della vecchia catena (secondi)
    intervallo_ripresa_secondi: float = 60.0
    log_level: str = "INFO"
    # Servizio signature (VD-C-019): senza URL o con servizio non qualificato
    # il blocco registra esplicitamente «non firmato».
    signature_url: str = "http://signature:8000"
    # Token di SERVIZIO verso signature (audience «verumdoc-signature», scope
    # «firma:servizio»; revisione M2): mai un token utente. Vedi firma_blocchi.py.
    signature_service_token: SecretStr = SecretStr("")
    signature_timeout_seconds: float = 15.0
    conservatore_nome: str = "VerumDoc S.r.l."
    # Sprint 2A FIX-011: S3 per persistere RdV Definitiva
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev_only"
    s3_bucket_aip: str = "verumdoc-aip"
    s3_region: str = "eu-south-1"
    # RdV definitivo in WORM con la retention del PdA (classe documentale).
    # Per le classi a conservazione permanente (e per gli oggetti del registro)
    # l'orizzonte è il parametro RETENTION_PERMANENTE_ANNI del motore (R09, D04:
    # predefinito 30, stesso valore dell'aip-builder); questo valore d'ambiente
    # vale solo senza la riga del conservatore (compatibilità)
    retention_permanente_orizzonte_anni: int | None = None
    # Autenticazione delle API (g3-backend, §19.1): token RS256 del gateway,
    # verificati con la sola chiave pubblica (hashchain non firma token).
    # Senza JWT_PUBLIC_KEY il servizio non parte (tranne ENV=test).
    jwt_public_key: SecretStr = SecretStr("")
    jwt_issuer: str = "verumdoc.it"
    jwt_audience: str = "verumdoc-api"
    # Verifica completa asincrona (g4-chiusura): tempo massimo dell'esecuzione
    # di fondo; oltre, la verifica si chiude con stato INTERROTTA.
    hashchain_verifica_timeout_seconds: float = 300.0

    # ── R04: registro delle impronte a due livelli (contratti K1, K2, K3) ──
    # Chiave Ed25519 del registro nel formato «PRIVATE+KEY+verumdoc-registro+…»
    # (tools/dev/genera_segreti_dev.py in sviluppo; Secret in esercizio). È una
    # chiave di SERVIZIO, non un sigillo qualificato. Senza, il servizio non parte
    # (tranne ENV=test, con una chiave effimera).
    catena_chiave_privata: SecretStr = SecretStr("")
    # Origine dei checkpoint: vuota = «verumdoc.it/registro/v1» in produzione,
    # «verumdoc.it/registro/v1/<ENV>» negli altri ambienti.
    catena_origine: str = ""
    # Un checkpoint ogni N secondi se il registro è cresciuto (K2)
    catena_checkpoint_secondi: float = 60.0
    # Checkpoint senza controfirma del testimone oltre N secondi = allarme (K3).
    # R04 correzioni (rev4 rilievo 1): un checkpoint NON controfirmato non porta
    # mai a CONSERVATO se il testimone è configurato (si attende, con l'allarme);
    # uno RIFIUTATO non si usa mai e sospende il completamento finché un
    # operatore non registra la gestione del rifiuto (app.registro.operatore).
    catena_controfirma_max_secondi: float = 600.0
    # Sequenziatore: ogni quanto legge la coda, quante foglie per transazione,
    # durata del lease (REGOLE §12.9: un solo sequenziatore attivo).
    catena_sequenziatore_intervallo_secondi: float = 1.0
    catena_sequenziatore_lotto: int = 1000
    catena_lease_secondi: float = 30.0
    # PdA completati (RdV definitivo + CONSERVATO) per giro
    catena_completamento_lotto: int = 200
    # Foglie per segmento esportato nel WORM (registro/foglie/<da>-<a>.jsonl)
    catena_worm_segmento_foglie: int = 10000
    # Registro SOSPESO (R04 secondo giro, runbook ripristino_registro_da_backup):
    # dopo un ripristino del database il ciclo non sequenzia, non emette
    # checkpoint e non contatta il testimone finché i controlli non sono fatti.
    catena_registro_sospeso: bool = False
    # Testimone (K3): URL facoltativo fuori da produzione; senza, stato
    # «NON_CONFIGURATO» dichiarato. Con ENV=production è OBBLIGATORIO: senza,
    # il servizio non parte (rev4 rilievo 8). Con l'URL servono la verifier key
    # del testimone (per verificare la
    # controfirma) e il token di servizio con cui il registro si presenta.
    testimone_url: str = ""
    testimone_verificatore: str = ""
    testimone_service_token: SecretStr = SecretStr("")
    testimone_timeout_seconds: float = 10.0
    # Se non c'è nulla da inviare, ogni N secondi una lettura di prova del
    # testimone (GET dell'ultimo checkpoint controfirmato): un testimone che non
    # risponde è un allarme anche quando il registro non cresce.
    testimone_sonda_secondi: float = 60.0
    # Token di servizio accettati su GET /registro/foglie (testimone, verificatori):
    # audience dedicata e scope richiesto.
    catena_pubblico_servizio: str = "verumdoc-registro"
    catena_scope_lettura_foglie: str = "registro:leggi"

    # ── R05: foglie interne (contratto Q3) ed export dell'audit (VD-C-112) ──
    # Token di servizio ammessi su POST/GET /interno/foglie (ciclo-vita): stessa
    # audience del registro, scope dedicato.
    catena_scope_aggiunta_foglie: str = "registro:aggiungi"
    # Foglie del ciclo di vita restituite al più con una prova d'inclusione
    catena_ciclo_vita_max_foglie: int = 50
    # Export di audit_global nel WORM: durata di un periodo (un segmento) e
    # margine dopo la fine del periodo prima di esportarlo (le transazioni
    # ancora aperte che scriverebbero righe in quel periodo devono chiudersi:
    # vedi docs/architettura/catena.md §11).
    audit_export_attivo: bool = True
    # R12a (VD-C-148): rinnovo del blocco WORM degli oggetti registro/ (segmenti,
    # checkpoint, controfirme, sigilli, audit) quando mancano meno di
    # RETENTION_RINNOVO_SOGLIA_ANNI, fino ad adesso + RETENTION_PERMANENTE_ANNI,
    # mai in riduzione; censimento degli oggetti scritti prima di R12a
    rinnovo_worm_attivo: bool = True
    rinnovo_worm_secondi: float = 86400.0
    rinnovo_worm_lotto: int = 500
    # R13a (VD-C-068): documenti del conservatore da sigillare (IPdA, RdV, manuale,
    # piano) a ogni giro; rimarcatura prima delle scadenze ogni N secondi
    firma_documenti_lotto: int = 20
    firma_rinnovo_attivo: bool = True
    firma_rinnovo_secondi: float = 3600.0
    firma_rinnovo_lotto: int = 50
    # R14 (VD-C-165): censimento degli IPdA dei PdA CONSERVATI prima di R13a (senza riga
    # in firme_documenti), solo con un fornitore qualificato attivo; PdA al più per giro
    # e attesa fra due passaggi completi (quelli conservati dopo rientrano al successivo)
    firma_arretrato_ipda_attivo: bool = True
    firma_arretrato_ipda_lotto: int = 100
    firma_arretrato_ipda_ripasso_secondi: float = 86400.0
    censimento_worm_giorni: int = 30
    # R07 (D19): AUDIT_EXPORT_PERIODO_SECONDI tolta: il periodo è l'impostazione del
    # conservatore AUDIT_EXPORT_PERIODO_MINUTI (console, 15 minuti, 5-60)
    audit_export_margine_secondi: int = 600
    # Righe al più in un segmento: un periodo più fitto si divide a metà
    audit_export_righe_massime: int = 200000
    # Inizio della prima esportazione (ISO 8601); vuoto = inizio dell'ora della
    # riga d'audit più vecchia
    audit_export_inizio: str = ""
    # Periodi esportati al più per giro del ciclo (recupero di un arretrato)
    audit_export_periodi_per_giro: int = 24


settings = Settings()
