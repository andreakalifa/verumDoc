# CHANGELOG — R04 — Scala e catena (23/10/2026)

Seconda tappa del piano R03–R05 (`docs/architettura/PIANO_R03_R05.md`). Prerequisito: R03 applicato, con le correzioni per l'avvio su Windows.

Contenuto:
- la catena delle impronte a due livelli, con checkpoint firmati e testimone indipendente;
- il verificatore pubblico;
- la verifica continua dell'archivio;
- il database e la pubblicazione degli eventi per 1 milione di documenti al giorno;
- la console delle code di scarto;
- l'infrastruttura con permessi minimi per servizio;
- la prova di carico.

Esito sul registro (`docs/registro/criticita.md`):
- chiuse: VD-C-075, 076, 084, 085, 086, 097, 101–106, 108;
- parzialmente chiuse: 080, 081, 096;
- nuove: 109–118.

Fonti: rapporti degli agenti (catena, testimone, verifiche, dati, servizi, infra, integrazione) e revisione indipendente in quattro giri. Il primo giro ha dato «non approvabile»; tutti i rilievi sono stati corretti, ognuno con un test che fallisce senza la correzione (100 prove di mutazione su 100). La verifica finale, chiuso F1, dice «approvabile».

---

## Funzionalità

### Catena delle impronte (VD-P-39) — `libs/catena`, hashchain, aip-builder

**Due livelli RFC 6962**
- Le foglie di ogni PdA sono le impronte dei file e dei metadati e l'IPdA; la radice del PdA è una foglia del registro globale append-only.
- Un solo sequenziatore con lease. Le prove d'inclusione e di consistenza leggono O(log n) nodi salvati.
- Su 100.000 foglie: 3.395 foglie/s, prova d'inclusione 1 ms, prova di consistenza 6 ms.

**Checkpoint**
- Formato C2SP tlog-checkpoint dentro una nota firmata Ed25519, verificato contro l'implementazione Go `golang.org/x/mod/sumdb` e i valori di prova di Certificate Transparency.
- Emessi circa ogni 60 s, scritti nel WORM prima della pubblicazione.
- Sigillo qualificato e marca tramite signature quando c'è il fornitore; altrimenti `NON_FIRMATO`, dichiarato.
- Evento `catena.checkpoint` unico per tutti gli enti.

**Testimone indipendente** (`services/testimone`)
- DB, chiave e account propri.
- Controfirma solo dopo aver verificato la firma del registro e la consistenza con il checkpoint precedente.
- Una biforcazione lo mette in stato «compromesso» persistente, sbloccabile solo in locale.
- In produzione è obbligatorio, e nessun PdV diventa CONSERVATO senza la sua controfirma (VD-P-40).

**Conservazione e RdV**
- «Conservato» (VD-C-097) solo con il PdA nel registro e coperto da un checkpoint controfirmato.
- RdV definitivo v2 con le prove documento→PdA→registro e il checkpoint, verificato prima della scrittura in WORM.

**Verificatore pubblico** (`tools/verificatore`)
- CLI senza dipendenze da VerumDoc e pagina web in un solo file che non usa la rete (stile «Partenze»).
- Controlla l'impronta del file, le inclusioni, le firme distinte del registro e del testimone, l'origine e la consistenza fra checkpoint salvati.
- Guida per clienti e ispettori: `docs/integrazione/verifica_indipendente.md`.

**Robustezza del registro**
- Controllo di coerenza all'avvio e ogni minuto con ciò che è già pubblicato in WORM e controfirmato. Se il DB è indietro, il registro si ferma con allarme e non riordina mai le foglie.
- Comandi operatore per rifiuti, blocchi e sospensione.
- Runbook `docs/runbooks/ripristino_registro_da_backup.md`.

### Verifica continua dell'integrità — `hashchain-verifica`
- Ogni PdA viene riletto da storage a rotazione, almeno una volta ogni 365 giorni, con budget di letture e byte.
- Confronto con le foglie della catena, con il DB e con la retention Object Lock.
- Esiti in un registro append-only partizionato; allarme ed evento `integrita.anomalia` su ogni differenza.
- API di verifica su richiesta e riepilogo della copertura.

### Dati per 1 milione di documenti al giorno
- **Outbox**: relay per servizio, ordine per chiave, blocco della sola chiave dopo 10 tentativi, sospensione del topic sugli errori di permesso, partizioni giornaliere staccate, metriche e allarmi distinti.
- **Partizioni e retention**:
  - eventi per i clienti e consegne webhook mensili, 90 giorni;
  - audit mensile senza retention, protetto anche dal proprietario (VD-P-41);
  - creazione anticipata con lease e connessione diretta.
- **Contatori per ente** al posto di `COUNT(*)`; replica di lettura facoltativa (`DATABASE_URL_LETTURA`).
- **Prova su 9,6 milioni di documenti** (7,5 GB):
  - conteggi per stato da 127 ms a 0,46 ms;
  - liste di coerenza da 8,7 minuti a 31 ms;
  - inserimento del versamento invariato a 0,6 ms.

### Servizi
- Console «Code di scarto» (VD-C-075): Rielabora in anteprima/applica con un solo evento di ripartenza per PdV, Archivia con motivo, eventi outbox bloccati.
- Scadenza dello staging dei PdV rifiutati a 90 giorni, con preavviso al cliente (VD-C-104).
- Elenchi a cursore (VD-C-102); `Idempotency-Key` su PdD e webhook (VD-C-106); SDK 3.1.0.
- Voucher PDND monouso e fruitori registrabili da API (VD-C-084).
- Rotazione delle chiavi JWT con `kid` (VD-C-085, runbook).

### Infrastruttura
- Un ruolo IAM per servizio con permessi minimi (VD-C-101), elenco del bucket solo dove serve e limitato al prefisso; ACL Kafka per topic; tutto controllato da `verifica_manifest` e `tofu test`.
- KEDA sul lag di Kafka; PgBouncer in modalità transaction (lock e `SET` di sessione solo su connessione diretta).
- Kafka su MSK con autenticazione IAM (`KAFKA_AUTENTICAZIONE=iam`).
- Testimone su host separato.
- Ruoli database separati: servizi `verumdoc_applicazione`, manutenzione, migrazioni, proprietario dei registri.
- CI completa (non eseguita qui) e scenario k6.

### Prova di carico locale (2 core, gateway reale, PostgreSQL, Redis)

| Richiesti | Accettati/s | p95 | Errori |
|---|---|---|---|
| 10/s | 10,0 | 0,049 s | 0 % |
| 25/s | 25,0 | 0,055 s | 0 % |
| 50/s | 47,7 | 0,88 s | 4,6 % (503 di contropressione voluti) |

Il miglioramento viene dal client S3 condiviso: CPU per versamento quasi dimezzata. Per 50/s in produzione bastano 2–3 istanze del gateway; da confermare con k6 in staging.

## Correzioni
- **Revisione indipendente, primo giro.** Due rilievi gravi:
  - conservazione dopo 600 s anche con un checkpoint rifiutato;
  - outbox bloccata da un solo evento.

  E poi:
  - verifica continua cieca su un documento cancellato;
  - Rielabora doppia;
  - audit cancellabile staccando una partizione;
  - lock di sessione con PgBouncer;
  - permessi Kafka su ogni topic;
  - testimone facoltativo in produzione;
  - verificatore senza controllo d'origine;
  - checkpoint copiato per ogni ente;
  - consegne staccate dalla retention;
  - RdV con un checkpoint diverso dal riferimento.
- **Secondo giro:**
  - audit aggirabile dal proprietario;
  - errori di topic trattati come errori di chiave (recupero in 300 s invece di 12 s);
  - rifiuti del testimone da gestire uno a uno;
  - ritentativi dentro la richiesta HTTP;
  - lease di manutenzione perso senza effetto.
- **Terzo giro:**
  - ripristino da backup che avrebbe riordinato foglie già pubblicate;
  - chiusura in blocco di una biforcazione;
  - sospensione del registro senza allarme;
  - vincoli e partizioni manuali sull'audit.
- **Quarto giro (F1):** controllo di coerenza che su S3 vero, senza `s3:ListBucket`, avrebbe fermato in silenzio tutta la conservazione. Corretto anche il readiness S3 dei servizi in sola lettura.
- **Integrazione:**
  - tre errori nelle migrazioni, nascosti dalle teste multiple;
  - sessione di lettura che dava 503 sull'elenco PdA;
  - valanga di log con Kafka giù;
  - client S3 creato a ogni versamento.

## Conformità normativa
- RdV definitivo con un namespace VerumDoc versionato (v2), mai AgID. L'IPdA SInCRO resta da verificare contro lo XSD UNI (VD-C-071, VD-C-096 parziale).
- Prove di integrità verificabili da terzi senza il conservatore; verifica periodica di tutto l'archivio.
- La firma e la marca dei checkpoint restano non qualificate finché manca il fornitore (VD-C-068).

## Dismissioni
- Topic `hashchain.seal` (da togliere a mano dal cluster).
- La hashchain non consuma più Kafka.
- `aip.conserved` non è più prodotto da aip-builder.
- Variabili `KAFKA_TOPIC_SEAL`, `BATCH_SIZE`, `BATCH_TIMEOUT_SECONDS`, `POLL_TIMEOUT_MS`, `RITARDI_SIGILLO_SECONDI` (hashchain) e `KAFKA_TOPIC_CONSERVED` (aip-builder).
- HPA sostituiti da ScaledObject KEDA per 5 carichi.
- Codice morto (VD-C-086, VD-C-105).

## File eliminati
Tolti dallo script `tools/pulizia/elimina_R04.sh` (`.ps1` per Windows), idempotente, con prova senza `--applica`:

| File | Motivo |
|---|---|
| `infra/k8s/base/hpa/{aip-builder,distribution-lavoratori,notification,notification-webhook,validation}-hpa.yaml` | sostituiti da ScaledObject KEDA |
| `libs/shared/connectors/` (7 file) | codice senza chiamanti (VD-C-086) |
| `libs/shared/observability/health.py`, `libs/shared/security/headers.py` | codice senza chiamanti (VD-C-086) |
| `services/gateway/app/auth/quote.py` | sostituito da `limiti_ente` (VD-C-105) |
| `services/hashchain/app/services/consumer.py` | la hashchain non consuma più Kafka (VD-P-39) |
| `tools/pulizia/elimina_R03.sh`, `.ps1` | già applicati con R03 |

## Test eseguiti (23/10/2026, numeri copiati dall'esecuzione)

PostgreSQL 16 reale, template creato da zero con `alembic upgrade head` (testa `20261023_035`, `alembic check` senza differenze); S3 simulato (moto); ogni servizio nel suo interprete.

| Suite | Passati (0 falliti) |
|---|---|
| gateway (2 esecuzioni) | 658 · 658 |
| validation | 153 |
| aip-builder | 182 |
| hashchain | 255 |
| notification | 258 |
| distribution | 151 |
| dashboard-api | 235 |
| signature | 91 |
| sftp-ingest | 147 |
| onboarding | 234 |
| testimone | 61 |
| libs/shared | 289 |
| libs/verumdoc_rules | 116 |
| libs/catena | 64 (+1 saltato: oracolo Go facoltativo) |
| tools/docs, tools/dev, tools/verificatore (con Playwright) | 30, 17, 26 |
| infra/kafka, infra/k8s (con `tofu test` del modulo IAM) | 5, 90 |
| sdk | 91 |
| **Totale** | **3.153** (R03: 2.712) |

Inoltre:
- `make docs-api` e `make verifiche` con uscita 0;
- frontend typecheck, lint e build con uscita 0;
- `tofu validate` su 14 configurazioni.

Test end-to-end `gateway/tests/test_r04_catena_e2e.py`: versamento → CONSERVATO solo dopo la controfirma del testimone reale → RdV v2 verificato dalla CLI (uscita 0; 1 con documento alterato) → verifica continua che trova il file alterato.

Prove di mutazione sulle correzioni della revisione: 100 su 100 rilevate.

**Non provati qui:**
- Kafka e MSK reali, OpenSearch reale;
- MinIO e S3 con permessi IAM veri (un test che li richiede si salta senza `VD_TEST_S3_URL`);
- immagini Docker, deploy, k6 e CI su un ambiente reale;
- PowerShell (`elimina_R04.ps1`).


## Scelte da confermare (VD-P-40, VD-P-42 e rapporti)
1. **Conservazione sospesa** se il testimone non risponde o rifiuta. Più severo del revisore, ma sicuro: riparte da sola quando il testimone torna.
2. **Registro fermo con allarme invece di ricostruzione automatica** dal WORM, quando il DB è indietro. Il comando di ricostruzione è in R05 (VD-C-111).
3. **Foglia di riversamento** per i PdA conservati con la catena di R02.
4. **Checkpoint come evento unico per tutti gli enti**; RdV v2 non firmato a sé (il sigillo sta sul checkpoint).
5. **Outbox**: 10 tentativi prima del blocco di una chiave; «Archivia» = scartare l'evento; topic sospeso senza limite di durata, con allarme di pagina.
6. **Audit**: proprietario senza membri, superutente fuori perimetro (VD-C-112).
7. **Verifica continua**: ciclo di 365 giorni (da firmare come VD-N, VD-C-116), richiesta di verifica aperta ad ADMIN_TENANT, una riga di registro per oggetto.
8. **Voucher PDND monouso di default.** Le Linee guida PDND ammettono il riuso; si disattiva con `PDND_VOUCHER_MONOUSO=false`.
9. **Fruitori PDND e permessi**: fruitori registrabili solo da SUPER_ADMIN; console delle code di scarto per SUPER_ADMIN e RSC.
10. **Readiness S3**: un 403 sull'HEAD del bucket conta come «presente» (VD-C-114).
11. **Infrastruttura**: soglie KEDA, quota del namespace, tetto multipart 16, ruolo dei nodi senza S3/KMS.
12. **`integrita.anomalia`** come evento dell'ente del PdA; il conservatore riceve l'allarme da Alertmanager.

## Limiti noti
- **Esercizio mai provato davvero** (VD-C-081 parziale, 109, 110): Kafka/MSK reale, deploy, k6 e CI su un ambiente reale, Terraform dell'account del testimone.
- **Firma e marca** non qualificate (VD-C-068). L'IPdA non è verificato contro lo XSD UNI (VD-C-071).
- **Row Level Security per ente** sulle tabelle dei dati da fare in R05 (VD-C-080 parziale).
- **Storage di sviluppo** su Chainguard MinIO senza digest fissato (VD-C-115).
- **Comandi di gestione delle migrazioni**: `alembic heads/history` fuori dal container richiedono `PYTHONPATH` (VD-C-117).

---

## CAMBI INCOMPATIBILI

1. **Momento di CONSERVATO**: solo con il PdA nel registro, coperto da un checkpoint **controfirmato dal testimone**. In produzione la hashchain non parte senza `TESTIMONE_URL`; un testimone giù sospende i «conservato» (con allarme).
2. **RdV definitivo formato 2**, con le intestazioni `X-Formato-RdV` e `X-Checkpoint-Dimensione`; i v1 restano scaricabili.
3. **Elenchi** `GET /api/dashboard/notifiche`, `/api/dashboard/audit`, `/api/v2/distribution/storico`: forma `{items, limit, cursore, altri}`; `skip > 0` → 422.
4. **Token e PDND**: i token portano `kid` e `JWT_PUBLIC_KEY` accetta più chiavi; un voucher PDND riusato → 401 `PDND-401-RIUSATO`; Redis guasto → 503.
5. **Outbox, eventi, consegne e audit** con PK che comprende la data; ordine dell'outbox per chiave, non totale. Con Kafka giù non si registra più un tentativo per versamento.
6. **Infrastruttura**:
   - il gateway non migra più all'avvio in Kubernetes (Job `gateway-migrazioni`);
   - il ruolo dei nodi non dà più S3/KMS (serve IRSA);
   - serve KEDA;
   - Kafka in esercizio solo con IAM.
7. **Database**:
   - i servizi si connettono con un login membro di `verumdoc_applicazione`;
   - le migrazioni 033 e 034 richiedono il master (`rds_superuser`);
   - rinominare o modificare i registri d'audit è rifiutato fuori da `registri_modificabili`.
8. **Eventi per i clienti nuovi**: `versamento.staging_scaduto`, `catena.checkpoint` (globale), `integrita.anomalia`. Una riprova di consegna webhook oltre la retention → 422 `WH-012`.
9. **Audit**: l'evento di gestione dei rifiuti del testimone si chiama `catena.rifiuti_testimone_gestiti`. La console delle code di scarto non può ripubblicare `aip.conserved` né `catena.checkpoint`.

## Variabili d'ambiente nuove e tolte

Fonti: rapporti degli agenti (colonna «Fonte»); le aggiunte dei giri di correzione sono nella tabella successiva. C = ConfigMap, S = Secret.

| Servizio / processo | Variabile | Nuova / tolta | Predefinito del codice | Dove in esercizio | Fonte |
|---|---|---|---|---|---|
| **tutti con Kafka** (gateway, validation, aip-builder, notification, distribution, onboarding, hashchain, dashboard-api) | `KAFKA_AUTENTICAZIONE` | nuova | `nessuna` (PLAINTEXT) | C: `iam` | int |
| idem | `KAFKA_REGIONE` | nuova | vuota → `AWS_REGION` | C: `eu-south-1` | int |
| idem | `KAFKA_CA_FILE` | nuova | vuota (CA di sistema) | non impostata | int |
| gateway, onboarding, distribution, hashchain, dashboard-api (scarti) | `OUTBOX_LOTTO`, `OUTBOX_GRUPPI_PER_GIRO`, `OUTBOX_MISURA_OGNI_SECONDI`, `OUTBOX_ORIGINE` | nuove | 500, 16, 15, nome del servizio | non impostate | dati |
| idem | `OUTBOX_PAUSA_KAFKA_SECONDI`, `OUTBOX_PAUSA_KAFKA_MASSIMA_SECONDI` | nuove | 2, 60 | non impostate | int |
| idem | `DB_MANUTENZIONE_ATTIVA`, `DB_MANUTENZIONE_PARTIZIONI_SECONDI`, `DB_MANUTENZIONE_CONTATORI_SECONDI`, `DB_MANUTENZIONE_CONTATORI_LOTTO` | nuove | true, 3600, 30, 50000 | non impostate | dati |
| tutti (libs/shared/db) | `DATABASE_URL_LETTURA` | nuova | vuota (primario in sola lettura) | S facoltativa (notification, dashboard-api) | dati |
| tutti via PgBouncer | `DATABASE_URL` | valore cambiato | — | S: `…@pgbouncer:6432/verumdoc` | infra |
| gateway | `VD_SALTA_MIGRAZIONI` | nuova | 0 | entrypoint: 1 (Job `gateway-migrazioni`) | infra |
| gateway | `STAGING_SCARTATI_GIORNI`, `_PREAVVISO_GIORNI`, `_ATTIVO`, `_LOTTO`, `_LOTTI_PER_GIRO` | nuove | 90, 7, true, 500, 20 | non impostate | servizi |
| gateway | `PDND_VOUCHER_MONOUSO`, `PDND_TOLLERANZA_SECONDI` | nuove | true, 30 | non impostate | servizi |
| gateway | `VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX` | valore cambiato | 8 | C: 16 | infra |
| gateway (e tutti, `JWT_PUBLIC_KEY`) | `JWT_PUBLIC_KEY` | formato esteso | — | può contenere più PEM (rotazione) | servizi |
| dashboard-api | `KAFKA_BOOTSTRAP`, `SCARTI_KAFKA_GROUP_ID`, `SCARTI_CODE_KAFKA`, `SCARTI_TOPIC_RIPUBBLICABILI`, `SCARTI_LOTTO`, `SCARTI_INTERVALLO_TABELLE_SECONDI`, `SCARTI_RIELABORA_MAX`, `SCARTI_LIMITE_MASSIMO`, `STAGING_SCARTATI_GIORNI` | nuove | `kafka:9092`, `dashboard-scarti`, le 3 DLQ, …, 200, 60, 500, 100, 90 | C: `KAFKA_BOOTSTRAP` da Terraform | servizi |
| distribution, notification | `IDEMPOTENZA_DURATA_ORE`, `IDEMPOTENZA_IN_CORSO_MAX_SECONDI`, `IDEMPOTENZA_PULIZIA_LOTTO` | nuove | 24, 120, 50 | non impostate | servizi |
| hashchain | `CATENA_CHIAVE_PRIVATA` | nuova | — (**obbligatoria** tranne ENV=test; signed note, NON PEM) | S | catena |
| hashchain | `CATENA_ORIGINE`, `CATENA_CHECKPOINT_SECONDI`, `CATENA_CONTROFIRMA_MAX_SECONDI`, `CATENA_SEQUENZIATORE_INTERVALLO_SECONDI`, `CATENA_SEQUENZIATORE_LOTTO`, `CATENA_LEASE_SECONDI`, `CATENA_COMPLETAMENTO_LOTTO`, `CATENA_WORM_SEGMENTO_FOGLIE`, `CATENA_PUBBLICO_SERVIZIO`, `CATENA_SCOPE_LETTURA_FOGLIE` | nuove | "", 60, 600, 1, 1000, 30, 200, 10000, `verumdoc-registro`, `registro:leggi` | C (60, 600), resto predefinito | catena |
| hashchain | `TESTIMONE_URL`, `TESTIMONE_VERIFICATORE`, `TESTIMONE_TIMEOUT_SECONDS` | nuove | "" (NON_CONFIGURATO), "", 10 | C: URL e `DA_IMPOSTARE:verifier_key_testimone` | catena |
| hashchain | `TESTIMONE_SERVICE_TOKEN` | nuova | "" | S | catena |
| hashchain | `KAFKA_GROUP_ID`, `KAFKA_TOPIC_IN`, `KAFKA_TOPIC_SEAL`, `BATCH_SIZE`, `BATCH_TIMEOUT_SECONDS`, `POLL_TIMEOUT_MS`, `RITARDI_SIGILLO_SECONDI` | **tolte** (consumer a lotti eliminato) | — | togliere dalle ConfigMap se presenti | catena |
| aip-builder | `KAFKA_TOPIC_CONSERVED` | **tolta** | — | idem | catena |
| hashchain-verifica (processo nuovo) | `VERIFICA_CICLO_GIORNI`, `_PRIMA_ENTRO_GIORNI`, `_LOTTO`, `_LEASE_SECONDI`, `_CONCORRENZA`, `_LETTURE_AL_SECONDO`, `_BYTE_AL_SECONDO`, `_BLOCCO_LETTURA_BYTE`, `_RITARDI_ERRORE_LETTURA_SECONDI`, `_SOGLIA_ERRORE_LETTURA`, `_SEMINA_INTERVALLO_SECONDI`, `_SEMINA_SOVRAPPOSIZIONE_MINUTI`, `_SEMINA_LOTTO`, `_SEMINA_LOTTI_PER_GIRO`, `_RICONCILIAZIONE_GIORNI`, `_RICONCILIAZIONE_LOTTI_PER_GIRO`, `_PREPARAZIONE_LOTTO`, `_MANUTENZIONE_INTERVALLO_SECONDI`, `_PARTIZIONI_MESI_AVANTI`, `_COPERTURA_CONSERVA_GIORNI`, `_RIEPILOGO_INTERVALLO_SECONDI`, `_RETENTION_TOLLERANZA_SECONDI`, `_MANIFEST_MAX_BYTE`, `_RICHIESTA_MAX_PDA` (anche hashchain API), `_ATTESA_INATTIVO_SECONDI`, `_METRICHE_PORTA`, `_DETENTORE` | nuove | 365, 30, 20, 900, 4, 50, 104857600, 1048576, [60,300,1500,7200], 3, 300, 120, 5000, 20, 7, 20, 5000, 3600, 3, 730, 300, 86400, 67108864, 1000, 15, 9108, host-pid | non impostate; `DATABASE_URL` da S `hashchain-pgbouncer` | verifiche, infra |
| testimone (servizio nuovo) | `ENV`, `DATABASE_URL`, `TESTIMONE_CHIAVE_PRIVATA`, `TESTIMONE_NOME_CHIAVE`, `TESTIMONE_CHIAVI_REGISTRO`, `TESTIMONE_ORIGINI_AMMESSE`, `TESTIMONE_IMPRONTE_TOKEN_REGISTRO`, `TESTIMONE_MAX_CORPO_BYTE`, `TESTIMONE_MAX_RIGHE_PROVA`, `TESTIMONE_INTERVALLO_STATO_SECONDI`, `VD_SALTA_MIGRAZIONI` | nuove | production, URL di sviluppo, — (non parte), `verumdoc-testimone`, — (non parte), `verumdoc.it/registro/v1`, — (non parte), 65536, 128, 30, 0 | S: `DATABASE_URL`, `TESTIMONE_CHIAVE_PRIVATA`; C: il resto (`DA_IMPOSTARE:` blocca l'avvio) | testimone, infra |
| KEDA, PgBouncer | Secret `keda-postgres`, `pgbouncer-secrets`, ConfigMap `pgbouncer-ca` | nuovi | — | S/C | infra |
| registro delle impostazioni (CONSERVATORE) | `RETENZIONE_EVENTI_CLIENTE_GIORNI` (90, 7–3650), `RETENZIONE_OUTBOX_GIORNI` (3, 1–90) | nuove | — | DB | dati |

Componenti nuovi:
- processi `hashchain-verifica` (metriche su 9108), `dashboard-api-scarti`, `testimone` (8000, nel compose 8011), PgBouncer (6432);
- Job `gateway-migrazioni` e `testimone-migrazioni`;
- ScaledObject KEDA (5 carichi).

Topic: `catena.checkpoint`, `integrita.anomalia` e `versamento.staging_scaduto` nuovi; `hashchain.seal` **da togliere a mano dal cluster**.


**Aggiunte dai giri di correzione della revisione:**

| Servizio / processo | Variabile | Nuova / cambiata | Predefinito | Note |
|---|---|---|---|---|
| hashchain | `TESTIMONE_URL` | cambiata | — | **obbligatoria con `ENV=production`** |
| hashchain | `TESTIMONE_SONDA_SECONDI` | nuova | 60 | sonda del testimone |
| hashchain | `CATENA_REGISTRO_SOSPESO` | nuova | false | solo a mano per il ripristino, vietata nei values |
| tutti con relay | `OUTBOX_MAX_TENTATIVI`, `OUTBOX_RIPROVA_SECONDI`, `OUTBOX_RIPROVA_MASSIMA_SECONDI`, `OUTBOX_RIPROVE_PER_GIRO` | nuove | 10, 2, 300, 20 | blocco per chiave |
| tutti con relay | `OUTBOX_PAUSA_TOPIC_SECONDI`, `OUTBOX_PAUSA_TOPIC_MASSIMA_SECONDI` | nuove | 2, 30 | sospensione del topic |
| libs/shared/db | `DATABASE_URL_MANUTENZIONE`, `DATABASE_URL_DIRETTA`, `DATABASE_URL_MIGRAZIONI` | nuove | vuote (= `DATABASE_URL`) | ruoli separati; connessione diretta fuori da PgBouncer |
| libs/shared/db | `DB_MANUTENZIONE_PARTIZIONI` | nuova | `auto` | in processo solo con `DATABASE_URL_MANUTENZIONE` o `ENV=test` |
| dashboard-api | `SCARTI_TOPIC_RIPUBBLICABILI` | cambiata | senza `aip.conserved` e `catena.checkpoint` | permessi minimi |
| tutti | `DATABASE_URL` dei servizi | cambiata | — | login membro di `verumdoc_applicazione` (non il proprietario) |
| Secret nuovo | `db-manutenzione` | nuovo | — | URL del ruolo di manutenzione |
| values IAM | `elenco_prefissi` | nuova chiave | — | `s3:ListBucket` della hashchain limitato a `registro/` |
| tools/verificatore | `--origine` | nuova opzione | `verumdoc.it/registro/v1` | |

## Migrazioni (025 → 035)

| Revisione | down_revision | Contenuto | Note |
|---|---|---|---|
| `20261023_025` | 024 | registro a due livelli, checkpoint, coda dei PdA storici con foglia di riversamento | |
| `20261023_027` | 024 | verifica continua: programmazione, richieste, registro partizionato, copertura | |
| `20261023_028` | 024 | outbox, eventi, consegne e audit partizionati; contatori per ente; BRIN | **copia le tabelle: 165 s a 1 M PdV, serve una finestra** |
| `20261023_029` | 024 | scarti, richieste idempotenti, avviso di scadenza dello staging, indici a cursore | |
| `20261023_030` | (025, 027, 028, 029) | unione | |
| `20261023_031` | 030 | correzioni della revisione: rifiuti del testimone, chiavi outbox ferme, ripartenze uniche, lease di manutenzione, ruolo `verumdoc_applicazione`, event trigger dei registri | parti privilegiate in blocchi DO (WARNING senza superutente) |
| `20261023_032` | 031 | indice dei gruppi outbox per partizione | CONCURRENTLY |
| `20261023_033` | 032 | topic sospesi, allarmi del testimone raggruppati, proprietario dedicato dei registri, RLS forzata, funzione `vd_crea_partizione_registro` | **con il master (`rds_superuser`)** |
| `20261023_034` | 033 | blocchi di coerenza del registro, impronta della struttura dei registri, event trigger aggiornati | **con il master**; usa `registri_modificabili` |
| `20261023_035` | 034 | blocco `COERENZA_NON_VERIFICABILE` | |

Tutte provate con upgrade da zero, downgrade fino a 024/030 e nuovo upgrade, `alembic check` pulito; lo script offline (`--sql`) gira con `psql`.
