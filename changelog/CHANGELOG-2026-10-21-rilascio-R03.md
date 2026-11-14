# CHANGELOG — R03 — Canali e contratto API (21/10/2026)

Prima tappa del piano R03–R05 (`docs/architettura/PIANO_R03_R05.md`, decisione VD-P-29). Prerequisito: R02 applicato.
Contenuto: un solo caso d'uso di versamento per tutti i canali, in streaming; motore delle regole v1; catalogo errori unico; sessioni di caricamento; SFTP; PDND rifatto; adattatori di compatibilità (tolti in R18.2, VD-P-191) (niente SOAP); eventi per i clienti e webhook firmati; chiave API e quote su tutti i servizi; ricerca reale; PdD completi; SDK 3.0; OpenAPI 3.1 unica; infrastruttura dei canali.
Esito sul registro (`docs/registro/criticita.md`): chiuse VD-C-077, 083, 091, 092, 093, 094, 095, 098, 099, 100; parzialmente chiuse 078 (resta la PEC) e 088; nuove 101–108.
Fonti: rapporti degli agenti a1, a2, b1–b5, c1, c2, i1–i3, f1, f2 e revisione indipendente (giudizio iniziale «approvabile con correzioni»: 1 Alta, 7 Media, 7 Bassa, tutte corrette e riverificate, vedi «Revisione indipendente»).

---

## Funzionalità

**Versamento**
- **Caso d'uso unico** `versa(RichiestaVersamento)` (`services/gateway/app/services/versamento_service.py`): fonti `FonteFlusso` (streaming su staging S3 a parti da 8 MB, impronte SHA-256/SHA-512/SHA3-256 e testa del file calcolate durante il trasferimento) e `FileGiaCaricato` (sessioni). Ogni canale lo chiama; quote, idempotenza, chiave UD e regole valgono per tutti.
- **Motore delle regole v1** `libs/verumdoc_rules/` (VD-P-01): impostazioni con catena classe → ente → conservatore → predefinito e origine; catalogo regole RGL-101…106 con effetto blocca/avvisa/ignora e `locked`; profili di metadati per classe versionati (`valido_dal`/`valido_al`, tipi, obbligatorietà, sensibilità); registro dei formati versionato; quote per ente; fotografia `pdv.regole_applicate`, avvisi in `pdv.avvisi`.
- **Sessioni di caricamento** `/api/v1/caricamenti` (fino a 5 GiB, parti parallele e fuori ordine, ripresa, verifica fuori richiesta, scadenza e pulizia) e **versamento per riferimento** (`POST /api/v1/versamento/ud` in JSON).
- **Chiave UD univoca** per ente (VRS-124), liberata da un rifiuto o da un ERRORE; `Idempotency-Key` obbligatoria con transizione fino al 31/03/2027; ripetizione di un PdV rifiutato → 409 VRS-127 su ogni canale.
- **Pulizia dello staging** dopo la copia verificata nel PdA (CronJob `gateway-pulizia-staging`).

**Canali**
- **SFTP** (servizio nuovo `services/sftp-ingest`): accesso solo a chiave pubblica, chroot per account, quota; cartella per PdV con `indice.json`, rinomina atomica, `esito.json`; lavoratori multipli con lease; versa tramite le sessioni con un token di canale RS256 (`aud=verumdoc-canale`) accettato solo per il responsabile di un account SFTP attivo. Account e chiavi gestiti in onboarding (`/api/v1/onboarding/sftp/...`). Guida `docs/integrazione/sftp.md`.
- **PDND** sopra il caso d'uso unico: identificativo e chiave UD dal fruitore, idempotenza, formato dal contenuto, base64 decodificato a blocchi su file temporaneo, limite di richieste contemporanee.
- **Adattatori di compatibilità** sotto `/api/compat/` (strati sottili sopra il caso d'uso unico) (tolti in R18.2, VD-P-191): rotte, guide e test non sono più nel repository.
- **Eventi per i clienti** `GET /api/v1/eventi` (cursore = posizione assegnata dopo il commit, nessun evento saltato) e **webhook firmati** `/api/v1/webhook` (HMAC-SHA256 con timestamp, segreto cifrato AES-256-GCM, ritentativi 60/300/1500/7200 s, consegne fallite consultabili e riprovabili, SSRF ad allow-list di indirizzi globali, porta 443 in produzione). Guida `docs/integrazione/webhook.md`.

**Accesso, quote, ricerca, distribuzione**
- Verifica unica delle chiavi API (`libs/shared/security/verifica_chiavi_api.py`) su gateway, distribution, dashboard-api, notification; `/api/v1/me` anche con chiave API.
- Quote per piano e per ente (`limiti_ente`): richieste al minuto su tutti i servizi (`libs/shared/limiti_richieste`) e PdV al giorno atomici (script Lua su Redis).
- Ricerca: aip-builder popola `metadati_allegato5` e accoda l'indicizzazione; lavoratore a lotti su OpenSearch senza metadati sensibili; ricerca per ente a cursore; elenchi del portale a cursore senza `COUNT(*)` né `OFFSET` (migrazioni 020, 022).
- PdD completi con i file reali e impronte verificate; ZIP in streaming oltre 50 MB con evento `pdd.pronto`.

**Scalabilità**
- Nessun file intero in memoria in gateway, validation (lettura a blocchi, consumer con 8 PdV in parallelo e offset in ordine) e aip-builder (impronte a blocchi, copia WORM lato server con retention COMPLIANCE verificata).
- Kafka: topic di pipeline a 48 partizioni, chiave = id della risorsa (VD-P-34); lavoratori dedicati (`gateway-verifica-caricamenti`, `notification-webhook`, `distribution-lavoratori`) e CronJob di pulizia.

**Contratto e strumenti**
- Catalogo errori unico `libs/shared/errori/catalogo.yaml` in problem+json (VD-P-30), generato in `docs/catalogo/errori.md`.
- OpenAPI 3.1 unica `docs/catalogo/openapi.yaml` ed `endpoint.md` (112 operazioni, autenticazione ricavata dalle dipendenze reali, webhook nella sezione `webhooks:`), `make docs-api`; `make verifiche` controlla freschezza, instradamenti e codici.
- SDK 3.0.0 (`sdk/`), sincrono e asincrono; guida per i produttori `docs/integrazione/README.md`.

**Infrastruttura**
- Helm e manifest: sftp-ingest (server e lavoratore, NLB su porta 22, EFS condiviso), instradamenti nuovi, limiti del corpo per percorso nel WAF, egress dei webhook solo verso IP pubblici in 443, `REDIS_URL` dove serve il limitatore. Terraform/OpenTofu: bucket di staging con scadenza dei multipart incompleti, bucket PdD temporaneo, EFS, ruolo del gateway in lettura sui PdA.

## Correzioni

Le criticità chiuse sono nel registro con il motivo e i test. Oltre a quelle, dalla revisione indipendente:
- REV-01 (Alta): negli adattatori un'UD corretta dopo un rifiuto non si poteva riversare e un PdV in ERRORE tornava «POSITIVO» → chiave derivata con l'impronta del contenuto, VRS-127.
- REV-02 quota aggirabile in parallelo → riserva atomica; REV-03 limiti solo sul gateway → su tutti i servizi; REV-04 token di canale per qualunque utente → solo responsabile di un account SFTP attivo; REV-05 PDND 140 MB di memoria per 50 MB → 3 MB; REV-07 parte bloccata per sempre dopo un errore di storage → ripresa; REV-08 staging mai ripulito → pulizia verificata; REV-09 corpo letto prima dell'autenticazione; REV-10 catalogo che dichiarava autenticazioni false; REV-11 metadati sensibili nell'indice; REV-12 adattatore che sovrascriveva una richiesta; REV-13 cursore degli eventi che poteva saltare; REV-14 SSRF verso `fec0::/10`; REV-15 scansione SFTP di tutte le aree a ogni giro.
- Difetti trovati durante l'integrazione: canale letto da un JWT utente qualsiasi; evento senza produttore; due formati per il token di canale e per `SFTP_TIPI_CHIAVE_AMMESSI`; `allegati` annidati ignorati (ora VRS-114); metadati minimi definiti in due modi; estensioni fisse; regole infrastrutturali preesistenti (reti di staging, controllo di salute dell'ALB, quota del namespace).

## Conformità normativa
- Il RdV preliminare non dichiara più il namespace AgID inesistente: usa `https://schemi.verumdoc.it/rdv/preliminare/1` e riporta gli avvisi. Il formato definitivo e la firma restano in R04 (VD-C-096, VD-C-068).
- I metadati non previsti non si perdono più e finiscono nell'IPdA; `tipologia_xml` della classe decide il tipo dell'IPdA (DAI/DI/AGG) invece della deduzione dal nome.
- Le regole e le impostazioni applicate sono fotografate sul PdV (REGOLE §8.3).

## Dismissioni
- Codici d'errore VRS-103, VRS-112 (→ VRS-130 con RGL-101/105), PDND-RIFIUTO, PDND-502, RATE-001 (→ QTA-001): marcati `dismesso` nel catalogo.
- Formato d'errore di R02 `{detail:{type,title}}` e intestazione `X-Correlation-ID` (→ problem+json, `X-Request-ID`).
- Evento `esibizione.pronta` (→ `pdd.pronto` con tipo ESIBIZIONE); `GATEWAY_INTERNAL_URL`, `EVENTI_RITARDO_VISIBILITA_SECONDI`, `MAX_FILE_SIZE` di validation.
- SDK 2.x.

## File eliminati
Tolti dallo script `tools/pulizia/elimina_R03.sh` (`.ps1` per Windows), idempotente, con prova senza `--applica`:

| File | Sostituito da |
|---|---|
| `sdk/verumdoc_sdk/exceptions.py` | `sdk/verumdoc_sdk/errori.py` (eccezioni per esito del catalogo) |
| `sdk/verumdoc_sdk/models.py` | `sdk/verumdoc_sdk/modelli.py` (modelli allineati all'API reale) |
| `tools/pulizia/elimina_R01_pulizia.sh`, `.ps1` | già applicati con R01 (REGOLE §3.3) |
| `tools/pulizia/elimina_R02.sh`, `.ps1` | già applicati con R02 |

## Test eseguiti (21/10/2026, numeri copiati dall'esecuzione)

PostgreSQL 16 reale, template creato da zero con `alembic upgrade head` (head `20261021_024`, `alembic check` senza differenze); S3 moto; ogni servizio nel suo interprete.

| Suite | Passati (0 falliti, 0 saltati) |
|---|---|
| gateway (3 esecuzioni) | 581 · 581 · 581 |
| validation | 152 |
| aip-builder | 180 |
| hashchain | 155 |
| signature | 91 |
| distribution | 144 |
| notification | 240 |
| dashboard-api | 201 |
| onboarding | 234 |
| sftp-ingest | 147 |
| libs/shared | 274 |
| libs/verumdoc_rules | 116 |
| tools/docs | 30 |
| tools/dev | 11 |
| infra/kafka | 5 |
| infra/k8s | 62 |
| sdk/tests | 89 |
| **Totale** | **2.712** (R02: 1.569) |

Inoltre: `make docs-api` e `make verifiche` (topic, manifest, OpenAPI e instradamenti, catalogo errori `--verifica --codici`, 10 Dockerfile, `docker compose config`) con uscita 0; frontend `npm run typecheck && npm run lint && npm run build` con uscita 0; `tofu validate` su 14 configurazioni, kubeconform e confronto Helm/kustomize (rapporti i2, i3).
Prove di mutazione sulle correzioni della revisione: con le correzioni tolte i test nuovi falliscono (gateway 24, validation 6, sftp-ingest 3 su 4, aip-builder 1, revisione N1–N3 9 su 12).
Non provati: Kafka e OpenSearch reali, MinIO con Object Lock, PowerShell (`elimina_R03.ps1`), deploy su cluster, carico.

## Revisione indipendente
Revisore senza parte nel codice. Primo giro: «approvabile con correzioni», 15 rilievi (REV-01 Alta; REV-02…08 Media; REV-09…15 Bassa), tutti corretti con test. Riverifica: 13 chiusi con prove rilanciate, REV-08 parziale, 3 rilievi nuovi (REV-N1 pulizia dello staging bloccabile, REV-N2 disco temporaneo del gateway, REV-N3 ricarica in un adattatore) corretti dopo la riverifica e coperti da `gateway/tests/test_f3_correzioni_revisione.py`. Nessun problema trovato su isolamento fra enti, TOCTOU dello staging, parser XML, copia WORM, chroot SFTP, consumo parallelo.

## Decisioni realizzate
VD-P-29 (piano), VD-P-30 (errori), VD-P-31 (versamento unico), VD-P-01 (motore delle regole v1, da ratificare), VD-P-27 (Idempotency-Key con transizione).

## Scelte di sviluppo da confermare
Registrate come decisioni in attesa in `docs/registro/decisioni.md`:
- VD-P-32 licenza asyncssh;
- VD-P-33 quote per piano;
- VD-P-34 chiave Kafka per risorsa;
- VD-P-35 cambi incompatibili;
- VD-P-36 scadenza dello staging dei PdV rifiutati;
- VD-P-37 webhook solo su 443;
- VD-P-38 permessi dei canali nuovi.

Inoltre:
- **Transizione e retention.** Data di fine transizione dell'Idempotency-Key (31/03/2027). Una sessione di caricamento si consuma anche se il PdV viene poi rifiutato; file vuoti non ammessi.
- **Pulizia dello staging.** 8 tentativi con attesa crescente fino a 24 h, poi PdV bloccato e segnalato.
- **Disco temporaneo del gateway.** 2 GiB, budget 1.536 MB, 8 multipart e 8 PDND contemporanei per replica.
- **PdD.** Soglia di 50 MB per il passaggio da link a ZIP; ZIP conservati 72 h fuori dal WORM.
- **Quote.** Fail-open se Redis non risponde.
- **Ricerca ed eventi.** Ricerca testuale a parole intere; eventi letti da ogni ruolo dell'ente.
- **Adattatori** (tolti in R18.2, VD-P-191). Elementi non supportati rifiutati, mai ignorati.
- **SFTP.** Nome utente unico globale; cartelle incomplete scartate dopo 24 h; pulizia automatica di `fatti/` e `scarti/`.
- **Errori.** URI dei tipi `https://docs.verumdoc.it/errori/<CODICE>` (dominio da confermare e pagina da pubblicare).

## Limiti noti
- **Firma e retention.** La firma e la marca restano non qualificate (VD-C-068). RdV e IPdA nel formato definitivo, evento «conservato» nel momento giusto e catena rafforzata sono di R04 (VD-C-096, 097).
- **PEC.** Non disponibile (VD-C-078 parziale).
- **Esercizio.** Ruoli IRSA mancanti per 8 servizi (VD-C-101). WAF non collegato all'ALB. Allarmi Prometheus per le metriche nuove non scritti.
- **Crescita delle tabelle.** `eventi_cliente` e consegne senza partizionamento (VD-C-103); elenchi residui con OFFSET (VD-C-102).
- **Codice morto.** `gateway/app/auth/quote.py` (VD-C-105).
- **Idempotenza mancante** su `/api/v2/distribution/richiedi` e `POST /api/v1/webhook` (VD-C-106).
- **IP del cliente.** Non arriva al server SFTP (VD-C-107).
- **PdV versati prima di R03.** Senza impronte nello staging restano bloccati nella pulizia finché non si decide (VD-P-36).
- **Prove su infrastruttura reale e di carico.** Rinviate a R04 (VD-C-081).

---

## CAMBI INCOMPATIBILI

**Client API:**
1. `Idempotency-Key` obbligatoria con transizione fino al 31/03/2027, poi 428 VRS-125. VRS-103 e VRS-112 sono dismessi e diventano VRS-130 (RGL-101 e RGL-105). VRS-124 vale per la chiave UD. RATE-001 diventa QTA-001.
2. **VRS-127** su ogni canale per la ripetizione di un PdV rifiutato, in errore o annullato. Prima la risposta era 200 con lo stato del PdV.
3. Adattatori (tolti in R18.2, VD-P-191): stessa UD con contenuto diverso → VRS-124 (prima VRS-121).
4. VRS-114 per `allegati` annidati; SEC-006 prima del trasferimento; minimi in `metadati_extra` non più validi; UPL-413 sul multipart oltre 110 MB, anche chunked; UPL-503 per le parti concorrenti.
5. PDND: QTA-001 e PDND-503-OCCUPATO. QTA-002 su ogni canale. Multipart REST: 503 UPL-503-OCCUPATO (ritentabile) oltre il limite di concorrenza o il budget del disco temporaneo.
   Errori: formato problem+json (`body.codice`, `type` è un URL), `X-Request-ID` al posto di `X-Correlation-ID` (VD-P-30). PDND: `id_documento` e `numero` obbligatori, risposte come il REST.
6. Portale e lettura a cursore, senza `total` né `skip`: `/api/dashboard/versamenti`, `/aip`, `/search`. distribution `/search`: `skip` rifiutato, `total` limitato.
7. Eventi: il cursore è `posizione`. Il tipo `esibizione.pronta` non esiste più.
8. Webhook: solo IP unicast globali e, in produzione, solo la porta 443 (`WEBHOOK_PORTE_AMMESSE=[443]`).
9. Token di canale: solo `aud=verumdoc-canale`, solo con un account SFTP ATTIVO del responsabile. 403 AUTH-010 sugli adattatori.
10. SFTP: un reinvio identico di una cartella il cui PdV è stato rifiutato è SCARTATO con VRS-127 (i3).
11. SDK 3.0.0, non compatibile con la 2.x. Nella 3.x `Evento.posizione` e `RisultatoRicerca.total_esatto`/`campi_testo` sono facoltativi e compatibili.

**Esercizio:**
1. Fuori da dev/test distribution, dashboard-api e notification non partono senza `API_KEY_PEPPER`, notification nemmeno senza `JWT_PUBLIC_KEY`. In produzione notification richiede anche `WEBHOOK_CHIAVE_CIFRATURA` e i tre servizi richiedono **`REDIS_URL`**.
2. Servono i Deployment dei lavoratori (verifica delle sessioni, indicizzazione e ZIP, webhook) e i CronJob `gateway-pulizia-orfani` e **`gateway-pulizia-staging`**.
3. Da rilasciare insieme: gateway con sftp-ingest (pubblico del token); notification, sftp-ingest e migrazione 022.
4. Da togliere dai `.env` locali: `GATEWAY_INTERNAL_URL`, `EVENTI_RITARDO_VISIBILITA_SECONDI`, `JWT_AUDIENCE` di sftp-ingest.
5. Prima del rilascio, ogni ente che usa SFTP deve avere un account ATTIVO con il responsabile giusto.
6. Quote: i predefiniti sono più alti. Gli enti oltre il piano si configurano in `impostazioni_valori` (livello ENTE).
7. Helm: l'annotazione IRSA ora è `verumdoc-<ambienteIam>-<servizio>` (`prod`/`staging`, prima `production`).


## Variabili d'ambiente nuove o tolte

C = ConfigMap, S = Secret, «env» = variabile del solo lavoratore. Il valore è quello di `values.yaml`; tra parentesi il predefinito del codice, se diverso. a1, c1, c2 e i1 non hanno variabili nuove. Origine = agente del rilascio (rapporti di lavoro). i1 ha solo smesso di leggere `JWT_AUDIENCE` in sftp-ingest.

### gateway

| Variabile | Dove | Valore | Origine |
|---|---|---|---|
| `GATEWAY_INTERNAL_URL` | **tolta** | — | b1 |
| `NOTIFICATION_INTERNAL_URL` | C | `http://notification:8000` | b1 |
| `VERSAMENTO_REST_MAX_FILE_MB`, `VERSAMENTO_MAX_FILE_PER_PDV`, `STAGING_PARTE_MB` | C | 100, 500, 8 | a2 |
| `IDEMPOTENZA_OBBLIGATORIA`, `IDEMPOTENZA_TRANSIZIONE_FINO_AL` | non impostate | registro: vero, 2027-03-31 | a2 |
| `IDEMPOTENZA_DEPRECATA_DAL` | C | 2026-10-21 | a2 |
| `CARICAMENTI_MAX_DIMENSIONE_MB`, `CARICAMENTI_DIMENSIONE_PARTE_MB`, `CARICAMENTI_SCADENZA_ORE` | C | 5120, 8, 24 | b1 |
| `CARICAMENTI_LAVORATORE_ATTIVO` | C | **false** (true); true nel Deployment `gateway-verifica-caricamenti` | b1 |
| `CARICAMENTI_INTERVALLO_SECONDI`, `CARICAMENTI_PULIZIA_INTERVALLO_SECONDI`, `CARICAMENTI_LEASE_VERIFICA_SECONDI`, `CARICAMENTI_VERIFICA_TENTATIVI_MAX` | C | 5, 300, 900, 5 | b1 |
| `VERSAMENTO_JSON_MAX_MB`, `PDND_MAX_FILE_MB` (più un limite degli adattatori, tolto in R18.2) | C | 16, 50 | b1 |
| `QUOTE_RICHIESTE_MINUTO` | C | `{"starter":120,"business":600,"enterprise":6000}` (b4: 60/300/1000) | b4 → **i3** |
| `QUOTE_PDV_GIORNO` | C | `{"starter":1000,"business":20000,"enterprise":200000}` (b4: 100/500/2000) | b4 → **i3** |
| `QUOTE_PIANO_PREDEFINITO`, `QUOTE_FINESTRA_SECONDI`, `QUOTE_PIANO_CACHE_SECONDI`, `QUOTE_FUSO_GIORNATA` | C | starter, 60, 60, Europe/Rome | b4 |
| `CANALE_SERVIZI_AMMESSI` | C | `{"sftp-ingest":["SFTP"]}` | b4 |
| `CANALE_CHIAVI_PUBBLICHE` | non impostata | `{}` | b4 |
| `SFTP_TOKEN_CHIAVE_PUBBLICA` | **S** | obbligatoria in produzione per il canale SFTP | b4/b2 |
| `S3_BUCKET_PDV` | C | `DA_TERRAFORM:s3_staging_bucket` | i2 |
| `VERSAMENTO_REST_MAX_CORPO_MB` | C | 110 | f1 |
| `CARICAMENTI_PRENOTAZIONE_PARTE_SECONDI` | C | 300 | f1 |
| `STAGING_PULIZIA_ATTIVO` | C | **false** (true): la pulizia gira nel CronJob `gateway-pulizia-staging` | f1, i3 |
| `STAGING_PULIZIA_ATTESA_ORE`, `STAGING_PULIZIA_LOTTO`, `STAGING_PULIZIA_LOTTI_PER_GIRO`, `STAGING_PULIZIA_VERIFICHE_PARALLELE` | C | 24, 500, 20, 16 | f1 |
| `STAGING_PULIZIA_INTERVALLO_SECONDI` | non impostata | 300 (serve solo se `ATTIVO=true`) | f1 |
| `PDND_RICHIESTE_CONTEMPORANEE_MAX`, `PDND_MAX_METADATI_MB` (più un limite degli adattatori, tolto in R18.2) | C | 8, 4 | f1 |
| `STAGING_PULIZIA_RINVIO_MINUTI`, `STAGING_PULIZIA_RINVIO_MAX_ORE`, `STAGING_PULIZIA_TENTATIVI_MAX`, `STAGING_PULIZIA_METRICHE_SECONDI`, `STAGING_PULIZIA_METRICHE_TETTO` | C | 10, 24, 8, 300, 100000 | f3 |
| `VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX`, `DISCO_TEMPORANEO_BUDGET_MB` | C | 8, 1536 (`tmpSizeLimit` del gateway 2Gi) | f3 |
| quota per ente | **non è una variabile** | righe `impostazioni_valori` ENTE: `QUOTA_PDV_GIORNO`, `QUOTA_RICHIESTE_MINUTO` (0 = piano) | i3 |

### validation e aip-builder

| Servizio | Variabile | Dove | Valore | Origine |
|---|---|---|---|---|
| validation | `VALIDAZIONE_MAX_FILE_MB`, `LETTURA_BLOCCO_KB`, `CONCORRENZA_PDV`, `MAX_MESSAGGI_IN_SOSPESO`, `ATTESA_DOPO_ERRORE_DLQ_SECONDI` | C | 5120, 1024, 8, 256, 30 | b5 |
| validation | `MAX_FILE_SIZE` | **tolta** (ignorata) | — | b5 |
| aip-builder | `LETTURA_BLOCCO_KB`, `COPIA_MULTIPART_SOGLIA_MB`, `COPIA_PARTE_MB` | C | 1024, 1024, 256 | b5 |

### distribution

| Variabile | Dove | Valore | Origine |
|---|---|---|---|
| `API_KEY_PEPPER` | **S** | obbligatoria fuori da dev/test | b4 |
| `OPENSEARCH_PASSWORD` | S | facoltativa | b4 |
| `OPENSEARCH_UTENTE`, `OPENSEARCH_TIMEOUT_SECONDI` | C | "", 10 | b4 |
| `PDD_DURATA_LINK_SECONDI`, `PDD_DURATA_LINK_MAX_SECONDI`, `PDD_SINCRONO_MAX_MB` | C | 3600, 86400, 50 | b4 |
| `S3_BUCKET_PDD` | C | `DA_TERRAFORM:s3_pdd_bucket` | b4 |
| `PDD_ZIP_PARTE_MB`, `PDD_ZIP_VALIDITA_ORE`, `PDD_LEASE_SECONDI`, `PDD_RITARDI_SECONDI` | C | 8, 72, 1800, `[60,300,1500,7200]` | b4 |
| `KAFKA_TOPIC_PDD_PRONTO` | C | pdd.pronto | b4 |
| `CONSERVATORE_DENOMINAZIONE`, `CONSERVATORE_CODICE_FISCALE` | C | "" (àncora con dashboard-api, **da impostare**) | b4 |
| `INDICIZZAZIONE_LOTTO`, `INDICIZZAZIONE_INTERVALLO_SECONDI`, `INDICIZZAZIONE_RITARDI_SECONDI` | C | 200, 2, `[60,300,1500,7200]` | b4 |
| `LAVORATORI_ATTIVI` | C | **false** (true); lavoro in `distribution-lavoratori` | b4 |
| `REDIS_URL` | **S** | **obbligatoria in produzione** | f2 |
| `LIMITI_RICHIESTE_ATTIVI`, `LIMITI_RICHIESTE_AMBITO`, `LIMITI_RICHIESTE_CATEGORIE` | non impostate | true, servizio, ricerca 30/120/600 e pdd 10/60/300 | f2 |
| `RICERCA_CONTEGGIO_MASSIMO` | C | 1000 | f2 |
| `QUOTE_RICHIESTE_MINUTO`, `QUOTE_PIANO_PREDEFINITO`, `QUOTE_FINESTRA_SECONDI`, `QUOTE_PIANO_CACHE_SECONDI` | C | uguali al gateway (àncore) | i3 |

### dashboard-api

| Variabile | Dove | Valore | Origine |
|---|---|---|---|
| `API_KEY_PEPPER` | **S** | obbligatoria fuori da dev/test | b4 |
| `REDIS_URL` | **S** | obbligatoria in produzione | f2 |
| `LIMITI_RICHIESTE_*` | non impostate | come distribution | f2 |
| `QUOTE_RICHIESTE_MINUTO`, `QUOTE_PIANO_PREDEFINITO`, `QUOTE_FINESTRA_SECONDI`, `QUOTE_PIANO_CACHE_SECONDI` | C | uguali al gateway | i3 |

### notification

| Variabile | Dove | Valore | Origine |
|---|---|---|---|
| `JWT_PUBLIC_KEY`, `API_KEY_PEPPER` | **S** | obbligatorie fuori da dev/test | b3 |
| `WEBHOOK_CHIAVE_CIFRATURA` | **S** | obbligatoria in produzione | b3 |
| `WEBHOOK_CHIAVI_CIFRATURA_PRECEDENTI` | S | facoltativa | b3 |
| `JWT_ISSUER`, `JWT_AUDIENCE` | C | verumdoc.it, verumdoc-api | b3 |
| `EVENTI_CONSUMER_ATTIVO`, `EVENTI_KAFKA_GROUP_ID` | C | true (false nel lavoratore webhook), notification-eventi-cliente | b3 |
| `EVENTI_TOPIC` | non impostata | mappa del codice | b3 |
| `EVENTI_LIMITE_PREDEFINITO`, `EVENTI_LIMITE_MASSIMO` | C | 100, 500 | b3 |
| `EVENTI_RITARDO_VISIBILITA_SECONDI` | **tolta** (b3 → f2/i3), anche dalle Settings | — | b3, f2, i3 |
| `WEBHOOK_LAVORATORE_ATTIVO` | C | **false** (true); true in `notification-webhook` | b3 |
| `WEBHOOK_CHIAVE_CIFRATURA_ID`, `WEBHOOK_CONSENTI_INDIRIZZI_PRIVATI` | C | k1, false (true solo in compose) | b3 |
| `WEBHOOK_CA_PEM` | non impostata | "" | b3 |
| `WEBHOOK_RITARDI_SECONDI`, `WEBHOOK_TIMEOUT_SECONDI`, `WEBHOOK_TIMEOUT_CONNESSIONE_SECONDI` | C | `[60,300,1500,7200]`, 10, 3 | b3 |
| `WEBHOOK_INTERVALLO_SECONDI`, `WEBHOOK_LOTTO`, `WEBHOOK_CONCORRENZA`, `WEBHOOK_LEASE_SECONDI` | C | 2, 50, 20, 120 | b3 |
| `WEBHOOK_MAX_SOTTOSCRIZIONI_PER_ENTE`, `WEBHOOK_LUNGHEZZA_MASSIMA_URL` | C | 10, 2048 | b3 |
| `REDIS_URL` | **S** | obbligatoria in produzione | f2 |
| `LIMITI_RICHIESTE_*` | non impostate | come distribution | f2 |
| `EVENTI_PUBBLICAZIONE_ATTIVA`, `EVENTI_PUBBLICAZIONE_INTERVALLO_SECONDI`, `EVENTI_PUBBLICAZIONE_LOTTO`, `EVENTI_CONSUMER_LOTTO` | C | true, 0.2, 5000, 500 | f2 |
| `WEBHOOK_PORTE_AMMESSE` | C | **`[443]`** (codice: `[]` = tutte) | f2, valore i3 |
| `QUOTE_RICHIESTE_MINUTO`, `QUOTE_PIANO_PREDEFINITO`, `QUOTE_FINESTRA_SECONDI`, `QUOTE_PIANO_CACHE_SECONDI` | C | uguali al gateway | i3 |

### onboarding

| Variabile | Dove | Valore | Origine |
|---|---|---|---|
| `SFTP_HOST_PUBBLICO`, `SFTP_PORTA_PUBBLICA` | C | sftp.verumdoc.it, 22 | b2 |
| `SFTP_RSA_BIT_MINIMI` | C | 3072 (àncora con sftp-ingest) | b2 |
| `SFTP_TIPI_CHIAVE_AMMESSI` | non impostata | separata da virgole, predefinito uguale a sftp-ingest | b2, i1 |

### sftp-ingest (servizio nuovo: tutte le variabili sono nuove)

| Variabile | Dove | Valore |
|---|---|---|
| `DATABASE_URL` | S | — |
| `SFTP_CHIAVE_HOST`, `SFTP_TOKEN_CHIAVE_PRIVATA` | **S** | obbligatorie in produzione |
| `ENV`, `LOG_LEVEL` | C | production, INFO |
| `SFTP_COMPONENTI` | C/env | server / lavoratore |
| `SFTP_PORTA`, `SFTP_RADICE` | C | 2222, /var/lib/verumdoc/sftp |
| `SFTP_QUOTA_ACCOUNT_BYTE`, `SFTP_RSA_BIT_MINIMI`, `SFTP_LOGIN_TIMEOUT_SECONDI` | C | 53687091200, 3072, 60 |
| `JWT_ISSUER`, `URL_PUBBLICO_API` | C | verumdoc.it, https://api.verumdoc.it |
| `SFTP_SEGNALAZIONI_INTERVALLO_SECONDI` | C | 0.5 (f2) |
| `GATEWAY_URL`, `GATEWAY_TIMEOUT_SECONDI` | env | http://gateway:8000, 60 |
| `LAVORATORE_PARALLELISMO`, `LAVORATORE_PARTI_PARALLELE`, `LAVORATORE_LEASE_SECONDI` | env | 4, 4, 300 |
| `LAVORATORE_ACCOUNT_PER_GIRO`, `LAVORATORE_CONTROLLO_COMPLETO_SECONDI`, `LAVORATORE_QUOTA_ATTESA_MASSIMA_ORE`, `GATEWAY_RETRY_AFTER_MAX_SECONDI` | env | 100, 3600, 72, 60 (f2) |

Non impostate, valgono i predefiniti del codice:
- `SFTP_HOST`, `SFTP_QUOTA_RICALCOLO_SECONDI`, `SFTP_TIPI_CHIAVE_AMMESSI`, `SFTP_ALGORITMI_FIRMA`, `TOKEN_CANALE_DURATA_SECONDI` (300, tra 61 e 3600);
- `GATEWAY_RITENTATIVI_SECONDI`;
- `LAVORATORE_INTERVALLO_SECONDI`, `_STABILITA_SECONDI`, `_ABBANDONO_ORE`, `_RITARDI_SECONDI`, `_ATTESA_VERIFICA_SECONDI`, `_INDICE_MAX_BYTE`, `_CONSERVA_{FATTI,SCARTI,ESITI}_GIORNI`, `_CARTELLE_PER_GIRO`.

`JWT_AUDIENCE` non è più letta (i1).

### Segreti di sviluppo e docker-compose

`infra/dev_sftp.env`, `infra/dev_canali_pub.env` e `infra/dev_webhook.env`, creati da `genera_segreti_dev_r03.sh` (i2). Compose: `REDIS_URL` per distribution, dashboard-api e notification (i3).


## Migrazioni (014 → 024)

| Revisione | down_revision | Contenuto | Autore | Note di rilascio |
|---|---|---|---|---|
| `20261021_014` | `20261020_013` | versamento unico, motore delle regole v1 (`impostazioni_valori`, profili), chiave UD univoca, fotografia delle regole | a2 | in transazione |
| `20261021_015` | 014 | sessioni di caricamento (C1) e richieste degli adattatori (tabella eliminata da `20261114_060`) | b1 | |
| `20261021_016` | 014 | canale SFTP: account, chiavi, lavorazioni con lease | b2 | |
| `20261021_017` | 014 | eventi per i clienti e webhook firmati (C3) | b3 | |
| `20261021_018` | 014 | indice delle chiavi API, coda d'indicizzazione, PdD | b4 | |
| `20261021_019` | (015, 016, 017, 018) | unione, nessuna operazione | i1 | |
| `20261021_020` | 019 | `idx_pdv_tenant_ricezione`, `idx_pdv_tenant_stato_ricezione` | i1 | CONCURRENTLY |
| `20261021_021` | 020 | `pdv.staging_eliminato_il`, `idx_pdv_staging_da_pulire`, impronta delle richieste degli adattatori (tabella eliminata da `20261114_060`) | f1 | CONCURRENTLY |
| `20261021_022` | 020 | `eventi_cliente.posizione` (= id per gli esistenti), `eventi_cliente_pubblicazione`, `sftp_segnalazioni`, indici PdA ed eventi | f2 | CONCURRENTLY; rilasciare insieme a notification e sftp-ingest |
| `20261021_023` | (021, 022) | unione, nessuna operazione | i3 | |
| `20261021_024` | 023 | pulizia dello staging: rinvio per riga, PdV bloccati; indici parziali, toglie l'indice della 021 | f3 | CONCURRENTLY |

La quota per ente non richiede migrazioni: usa la tabella `impostazioni_valori` della 014.

