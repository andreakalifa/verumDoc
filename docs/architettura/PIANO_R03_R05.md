# Piano R03–R05 — VerumDoc unico: canali, scala, catena, configurazione

> Aggiornato il 21/10/2026 dopo R03 (chiave Kafka, VD-P-34). Stato: approvato dal committente il 21/10/2026 nelle linee (tappe con piano unico; catena interna rafforzata, nessun registro pubblico; canali REST e SFTP propri più adattatori di compatibilità, **niente SOAP**; obiettivo 1 milione di documenti al giorno). Le scelte di dettaglio marcate «da confermare» finiscono nel changelog di ogni tappa (REGOLE §3.3).
> Fonti: requisiti normativi (`docs/audit/allegati/ricerca_normativa.md`), registro criticità (VD-C-068…100), REGOLE_SVILUPPO §8 e §12–13.

## 1. Principio: i servizi attesi, con garanzie verificabili

I produttori devono trovare in VerumDoc i servizi attesi da un sistema di conservazione: API di versamento, stato e recupero, SFTP, PDND, notifiche, ricerca, esibizione, scarto, cruscotti. Ogni servizio aggiunge una garanzia verificabile.

| Servizio atteso | VerumDoc |
|---|---|
| Ricevuta di versamento | Attestazione con **prova d'inclusione Merkle** del documento nella catena, verificabile offline con un programma pubblico |
| Idempotenza | `Idempotency-Key` su **tutti** i canali (REST, SFTP, PDND, adattatori), replay identico e 409 sul conflitto |
| Esiti | un solo catalogo `problem+json`, tre esiti distinti (non valido / rifiutato / errore temporaneo), severità per regola |
| Notifiche | webhook firmati HMAC con anti-replay, ritentativi, coda di scarto e **elenco eventi equivalente** per chi non può ricevere chiamate |
| Configurazione | profili normativi **versionati con validità temporale**, anteprima dell'impatto prima di applicare, schema per classe interrogabile via API |
| Integrità | verifica **continua a rotazione** su tutto l'archivio, catena con testimone indipendente e checkpoint consegnati ai clienti |
| Prima del versamento | **validatore preventivo** via API e CLI che applica lo stesso motore di regole del server |

## 2. Obiettivo di carico e numeri di progetto

| Grandezza | Valore di progetto |
|---|---|
| Documenti al giorno | 1.000.000 (media ~12/s; **picco 50/s** per 1 ora) |
| Dimensione media / massima di un file | 300 KB / 5 GB (sopra 100 MB solo con sessione di caricamento) |
| Ingresso giornaliero | ~300 GB/giorno, ~110 TB/anno prima della replica |
| Enti / utenti del portale | 5.000 enti, 50.000 utenti, 2.000 contemporanei |
| Tempi (p95) | presa in carico ≤ 2 s (escluso trasferimento); stato CONSERVATO ≤ 15 min; risposta del portale ≤ 500 ms |
| Disponibilità | 99,9% su versamento e stato; nessuna perdita di eventi (outbox + Kafka almeno una volta + consumer idempotenti) |

Oltre 10 milioni al giorno si passa allo sharding del database (§4.3): il disegno lo prevede, ma non si realizza ora.

## 3. Canali d'ingresso (R03)

Tutti i canali chiamano **lo stesso caso d'uso di versamento** (`libs`/gateway, un'unica funzione: REGOLE §9). Un canale traduce soltanto il formato d'ingresso e d'uscita.

1. **REST v1** — `POST /api/v1/versamento/ud` (multipart, file fino a 100 MB) e **sessioni di caricamento** per file grandi: `POST /api/v1/caricamenti` → upload a parti riprendibile, direttamente su storage (S3 multipart), impronta calcolata durante il trasferimento → versamento che cita i `caricamento_id`. Nessun file intero in memoria.
2. **SFTP** — servizio `sftp-ingest`: account per ente con chiave SSH, una cartella per PdV con `indice.json` (schema pubblicato), file con prefisso temporaneo e rinomina a fine invio, esito `esito.json` scritto accanto (con id del PdV e link al rapporto). Idempotenza sul nome della cartella.
3. **PDND** — e-service esistenti con idempotenza obbligatoria e formato rilevato dal contenuto.
4. **Adattatori di compatibilità** (strato sottile sopra il caso d'uso, nessuna logica propria). Realizzati in R03 e **tolti in R18.2** (VD-C-187, VD-P-191): niente adattatori verso interfacce di terzi senza una specifica pubblica verificata.
5. **Portale** — versamento manuale e massivo dal cruscotto (stesso caso d'uso).

Fuori perimetro per scelta del committente: SOAP/WSDL.

## 4. Architettura per 1 milione di documenti al giorno (R04)

### 4.1 Flusso
```
canale → gateway (stateless, HPA) ──file──▶ S3 staging (multipart, impronta in streaming)
            │ indice + riferimenti
            ▼
        PostgreSQL (PdV RICEVUTO + outbox, una transazione)
            ▼ relay outbox (N repliche, SKIP LOCKED, lotti)
        Kafka (48 partizioni, chiave = id della risorsa)
            ▼
validation (KEDA sul lag) → aip-builder (PdA a lotti per ente/classe) → signature (sigillo per PdA)
            ▼                                               ▼
        hashchain (sequencer unico con lease, lotti di radici PdA) → WORM + DR
            ▼
        indicizzazione (bulk) → OpenSearch      notification → e-mail / webhook / elenco eventi
```

### 4.2 Scelte
- **Nessun file in memoria**: lettura a blocchi, impronte calcolate durante il trasferimento, verifica del formato sui primi KB. Il gateway scala orizzontalmente perché non tiene stato.
- **PdA a lotti**: un PdA raccoglie i documenti di un ente e di una classe fino a N documenti o T minuti (configurabili per classe). Con PdA da 1.000 documenti, 1 milione di documenti = 1.000 sigilli e 1.000 marche al giorno: costi e latenza del fornitore qualificato restano sostenibili.
- **Kafka**: chiave di partizione = id della risorsa (PdV, caricamento, PdD), così un ente grande non resta su una sola partizione; l'equità fra enti la danno le quote (VD-P-34); consumer idempotenti; scalatura automatica sul lag (KEDA); code di scarto con interfaccia e «Rielabora».
- **Outbox**: relay in più repliche con `FOR UPDATE SKIP LOCKED` a lotti, tabella partizionata per giorno e svuotata dopo la pubblicazione; un relay per servizio (chiude VD-C-076).
- **PostgreSQL**: partizionamento per mese su `pdv`, `documenti`, `aip`, `audit`, `outbox`, `eventi`; indici BRIN sulle date; PgBouncer; repliche di sola lettura per cruscotto e ricerca; contatori per ente aggiornati dagli eventi (mai `COUNT(*)` su tabelle grandi). Row Level Security come seconda difesa (VD-C-080).
- **Storage**: Object Lock COMPLIANCE; replica su un secondo sito (DR) con lo stesso blocco; passaggio automatico a classi d'archivio più economiche dopo N giorni, senza toccare la retention.
- **Ricerca**: OpenSearch con indici mensili dietro un alias, indicizzazione a lotti dal flusso eventi, instradamento per ente.
- **Quote**: limiti di frequenza e di volume per piano e per ente, sulle chiavi API come sui token (chiude VD-C-099).
- **Prova di carico**: scenario k6 a 50 versamenti/s per un'ora con file da 300 KB, su ambiente con MinIO, Kafka e PostgreSQL veri; i numeri del §2 diventano soglie di CI.

### 4.3 Oltre 10 milioni al giorno (predisposto, non realizzato)
Sharding per ente (Citus o database per gruppo di enti) dietro il repository dati; più istanze della catena per regione con radice globale; storage per ente in bucket dedicati.

## 5. Catena delle impronte rafforzata (R04) — «la blockchain al centro»

Scelta del committente: **catena interna**, nessun registro pubblico. La forza non viene da un registro esterno ma da quattro garanzie verificabili da chiunque riceva una ricevuta.

1. **Due livelli di Merkle (RFC 6962)**: foglie = impronte dei documenti e dei loro metadati → radice del PdA; le radici dei PdA entrano nel registro globale append-only. Ogni ricevuta porta la **prova d'inclusione** documento → PdA → blocco.
2. **Checkpoint firmati** (radice + dimensione dell'albero + tempo), con sigillo qualificato e marca RFC 3161, emessi a intervalli regolari e consegnati ai clienti (webhook, rapporto, API). Il cliente che conserva i checkpoint può chiedere in qualsiasi momento una **prova di consistenza** fra due checkpoint: se il registro fosse riscritto, la prova non tornerebbe.
3. **Testimone indipendente**: un secondo servizio, in un account e con amministratori diversi, riceve i checkpoint, verifica la consistenza con il precedente e li **controfirma**. Un checkpoint senza controfirma è un allarme.
4. **Verifica continua**: ogni oggetto dell'archivio viene riletto da storage e confrontato con la sua foglia almeno una volta l'anno (rotazione), più verifiche su richiesta; gli esiti stanno in un registro delle verifiche consultabile.

Il registro è ricostruibile dal WORM (blocchi e checkpoint scritti anche lì). Un **verificatore pubblico** (CLI e pagina web statica) controlla ricevute, prove e checkpoint senza bisogno di VerumDoc. Chiude VD-C-096, VD-C-097, VD-C-098; la firma dei blocchi resta legata al fornitore qualificato (VD-C-068).

## 6. Configurazione semplice ma completa e cruscotti (R05)

### 6.1 Motore delle regole (`libs/verumdoc_rules`, VD-P-01) — base di tutto, avviato in R03
- Catena di risoluzione unica (REGOLE §8.2): override motivato → accordo → classe → ente → profilo del conservatore → predefinito normativo, con **origine mostrata**.
- Catalogo delle regole con effetto **blocca / avvisa / ignora**, `locked` quando la norma impone il blocco.
- Profili di metadati per tipologia come **dati versionati** con validità temporale; il PdV fotografa la versione applicata.
- Lo stesso motore gira nel server, nel validatore preventivo e nella CLI.

### 6.2 Console del conservatore (personale VerumDoc)
Enti e piani (quote, limiti), profili normativi e loro versioni (con anteprima dell'impatto sui dati reali e applicazione con impronta del piano, REGOLE §13), classi di base, retention, fornitori di firma e marca, stato dei canali (SFTP, webhook, PDND), code di scarto con «Rielabora», monitor della catena e del testimone, verifiche d'integrità, audit.

### 6.3 Console del cliente
Classi documentali da modelli pronti, metadati propri tipizzati (anche «sensibili»: cifrati e mascherati), severità delle regole entro i limiti consentiti, politica di formazione dei PdA, canali (chiavi API, account SFTP, webhook con prova di consegna), utenti e ruoli, deleghe di firma, validatore preventivo dal browser, richieste di esibizione e di scarto con il loro iter.
Ogni modifica passa da una bozza versionata con anteprima («da domani 12 versamenti al giorno di questa classe sarebbero rifiutati»).

### 6.4 Scalare con gli utenti
Cruscotti su repliche di lettura e contatori precalcolati; paginazione a cursore ovunque; esportazioni grandi asincrone; cache per ente; sessione in cookie HttpOnly (VD-C-079); viste che restano veloci con milioni di documenti per ente.

## 7. Tappe

| Tappa | Contenuto | Criticità chiuse (obiettivo) |
|---|---|---|
| **R03 — Canali e contratto API** | catalogo errori `problem+json`; caso d'uso di versamento unico con caricamento in streaming e sessioni di caricamento; chiave UD univoca; metadati per classe applicati dal motore delle regole (prima versione); `Idempotency-Key` obbligatoria con transizione; PDND idempotente; SFTP; adattatori di compatibilità (tolti in R18.2); webhook firmati ed elenco eventi; indicizzazione e ricerca; chiave API su tutti i servizi e quote per piano; PdD completi; SDK riscritto; OpenAPI 3.1 unica | 077, 078, 083, 091, 092, 093, 094, 095, 099, 100 |
| **R04 — Scala e catena** | partizionamento, relay multipli, KEDA, prova di carico 50/s; catena a due livelli con prove d'inclusione e consistenza, checkpoint, testimone, verifica continua, verificatore pubblico; RdV e IPdA nel formato corretto; evento «conservato» al momento giusto; hashchain raggiungibile | 075, 076, 081, 096, 097, 098 |
| **R05 — Configurazione e cruscotti** | motore delle regole completo con profili versionati e anteprima; console del conservatore e del cliente; ciclo di vita (rettifica, annullamento, scarto con iter, riversamento); fascicoli; RLS; sessione in cookie | 072, 079, 080, 087, 088 |
| Dipendono da terzi | firma e marca qualificate (068), aggregatore SPID (069), XSD UNI 11386 (071), retention permanente (070, decisione), verifica firme e formati con Trusted List, veraPDF, DROID (073, 074) | — |

Ogni tappa chiude con la checklist §0.4, i test verdi su PostgreSQL vero e i documenti allineati.
