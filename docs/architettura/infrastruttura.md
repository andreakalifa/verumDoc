# Infrastruttura di VerumDoc (R04 «Scala e catena»)

> Aggiornato il 23/10/2026 (r04-infra). Riferimenti: piano R03–R05 §2 e §4,
> contratti K1–K7 di R04, REGOLE_SVILUPPO §23.3, §27. Criticità: VD-C-101
> (ruoli IAM), VD-C-081 (infrastruttura vera, parte carico), VD-C-076/103 (dati).

## 1. Una sola fonte

`infra/helm/values.yaml` descrive la topologia (servizi, lavoratori, job,
rete, ruoli IAM, scalatura, PgBouncer, rotte). Da lì derivano:

| Derivato | Come | Controllo |
|---|---|---|
| Chart Helm (`infra/helm/templates`) | legge i values | `helm lint` + `helm template` + kubeconform (CI, job `manifest`) |
| Manifest kustomize (`infra/k8s/base`, `overlays/`, `testimone/`) | `python infra/k8s/genera_manifest.py` | `make verifiche` (manifest rigenerati identici), kubeconform; confronto Helm ↔ kustomize senza differenze |
| Terraform/OpenTofu | `yamldecode(file(".../helm/values.yaml"))` negli ambienti | `tofu validate`, `tofu test` (modulo `iam_servizi`) |
| Coerenza con il codice | `python infra/k8s/verifica_manifest.py` | variabili lette dalle Settings, rete, IAM, KEDA, quota, connessioni |

## 2. Account e rilasci

| Rilascio | Comando Helm | Kustomize | Contenuto |
|---|---|---|---|
| Principale, produzione | `-f values.yaml -f values.prod.yaml` | `infra/k8s/overlays/prod` | registro e tutti i servizi tranne il testimone |
| Principale, staging | `-f values.yaml -f values.staging.yaml` | `infra/k8s/overlays/staging` | come sopra, repliche ridotte |
| Testimone | `-f values.yaml -f values.testimone.yaml` | `infra/k8s/testimone` | SOLO il testimone (namespace `verumdoc-testimone`) |

`global.account` («principale» o «testimone») e `services.<s>.account`
decidono cosa entra in un rilascio (`topologia.servizi_attivi`, helper Helm
`verumdoc.attivo`).

### 2.1 Il testimone in un account separato (contratto K3, piano §5.3)

Il testimone controfirma i checkpoint del registro: ha valore solo se chi
amministra il registro non può manomettere il testimone. In produzione:

- **account AWS separato**, con amministratori (IAM Identity Center) diversi da
  quelli dell'account principale; nessuna relazione di fiducia IAM fra i due;
- **cluster e database propri** (RDS PostgreSQL dell'account del testimone,
  schema `testimone`, migrazioni del servizio con il Job `testimone-migrazioni`);
- **chiave Ed25519 propria** (`TESTIMONE_CHIAVE_PRIVATA`, formato note firmate
  C2SP), generata nell'account e scritta direttamente nel suo secret manager;
- **nessun ruolo IAM** (`irsa: false`: il testimone non usa servizi AWS);
- **ingress su un host proprio**: `https://testimone.verumdoc.it/api/v1/testimone`.
  Motivo dell'host separato invece di `/api/v1/testimone` sull'host `api`: il
  percorso sotto l'host del registro passerebbe per l'ALB, il certificato e il
  WAF amministrati da chi il testimone deve controllare, e i due servizi
  starebbero nello stesso cluster. Con l'host separato il registro chiama il
  testimone come un servizio esterno (`TESTIMONE_URL`, uscita 443 della hashchain).
- Terraform dell'account del testimone: **da realizzare** (VPC, EKS o ECS,
  RDS, ALB, WAF, ACM). Le reti in `values.testimone.yaml` (10.20.0.0/16) sono una
  proposta non sovrapposta a produzione (10.0) e staging (10.1).

In sviluppo il testimone gira nello stesso docker-compose con un database
separato (`verumdoc_testimone`, utente `vd_testimone`, creato dal contenitore
`testimone-db` anche su volumi esistenti).

## 3. Carichi

| Carico | Tipo | Scalatura | Database | Note |
|---|---|---|---|---|
| gateway | Deployment HTTP | HPA CPU 2–20 | PgBouncer | `VD_SALTA_MIGRAZIONI=1` |
| gateway-migrazioni | Job (hook pre-install/pre-upgrade; Argo CD PreSync) | — | diretto (`gateway-migrazioni`) | `alembic upgrade head` prima dei servizi |
| gateway-verifica-caricamenti | Deployment | HPA CPU 2–10 | PgBouncer | |
| gateway-pulizia-orfani, gateway-pulizia-staging | CronJob | — | PgBouncer | |
| gateway-db-manutenzione | CronJob (ogni ora) | — | **diretto**, ruolo della manutenzione (`db-manutenzione`) | partizioni: creazione e distacco delle operative, solo creazione per i registri; traccia in `audit_global` (correzioni R04) |
| validation | Deployment | **KEDA** ritardo `validation-service` su `pdv.received` (100) + CPU, 2–16 | PgBouncer | |
| aip-builder | Deployment | **KEDA** ritardo `aip-builder-service` su `pdv.validated` (50) + CPU, 2–12 | PgBouncer | |
| signature | Deployment | HPA CPU 2–8 | — | nessun ruolo IAM |
| hashchain | Deployment, 1 replica, Recreate | — | **diretto** | sequenziatore e lock di sessione |
| hashchain-verifica | Deployment senza HTTP, metriche 9108 | 2 repliche fisse (budget) | PgBouncer (`hashchain-pgbouncer`) | ServiceAccount e ruolo propri, sola lettura del WORM |
| distribution | Deployment HTTP | HPA CPU 2–10 | PgBouncer | |
| distribution-lavoratori | Deployment | **KEDA** coda `indicizzazione_aip` (1.000) + CPU, 2–8 | PgBouncer | |
| notification | Deployment HTTP | **KEDA** ritardo `notification-eventi-cliente` (500) e `notification-service` (200) + CPU, 2–6 | PgBouncer | |
| notification-webhook | Deployment HTTP | **KEDA** coda `webhook_consegne` (500) + CPU, 2–8 | PgBouncer | uscita 443 verso i clienti |
| onboarding, dashboard-api, frontend | Deployment HTTP | HPA CPU | PgBouncer (frontend: nessuno) | |
| dashboard-api-scarti | Deployment senza HTTP, 1 replica | — | PgBouncer | consumer delle DLQ (gruppo `dashboard-scarti`) e relay dell'outbox «dashboard-api» (r04-servizi) |
| sftp-ingest, sftp-ingest-lavoratore | Deployment | HPA CPU 2–6 / 2–10 | PgBouncer | il lavoro dipende dal trasferimento e dal gateway: la CPU è il segnale giusto |
| pgbouncer | Deployment 2 repliche (anti-affinità obbligatoria), PDB | — | primario e reader | immagine `infra/pgbouncer/Dockerfile` |
| testimone | Deployment HTTP (account del testimone) | HPA CPU 2–4 | DB proprio | |
| testimone-migrazioni | Job (account del testimone) | — | DB proprio | |

Relay dell'outbox (VD-C-076, r04-dati): ogni servizio che scrive eventi avvia il
proprio relay nel processo principale (gateway, onboarding, distribution,
hashchain); più repliche sono sicure (gruppi e `pg_try_advisory_xact_lock`).
Nessun Deployment dedicato: si aggiunge come lavoratore solo se l'arretrato
(`verumdoc_outbox_eta_piu_vecchio_secondi`) lo chiede.

## 4. Ruoli IAM per servizio (VD-C-101)

Modulo `infra/terraform/modules/iam_servizi`: un ruolo
`<progetto>-<ambiente>-<ServiceAccount>` per ogni ServiceAccount annotato, fiducia
limitata a `system:serviceaccount:verumdoc:<nome>` dell'emittente OIDC del
cluster. Permessi dal blocco `iam` dei values; il ruolo dei NODI non ha più
permessi S3/KMS.

| ServiceAccount | staging | aip (WORM) | pdd | Kafka |
|---|---|---|---|---|
| gateway | lettura, scrittura, eliminazione, elenco, multipart | lettura | — | produzione |
| validation | lettura | — | — | produzione, consumo (gruppo `validation-service`) |
| aip-builder | lettura | lettura, scrittura, worm, multipart | — | produzione, consumo (gruppo `aip-builder-service`) |
| hashchain | — | lettura, scrittura, worm, elenco **solo sotto `registro/`** | — | produzione (relay dell'outbox) |
| hashchain-verifica | — | lettura, lettura_retention | — | — |
| distribution | — | lettura | lettura, scrittura, multipart | produzione |
| dashboard-api | — | lettura | — | produzione («Rielabora»), consumo (gruppo `dashboard-scarti`) |
| notification | — | — | — | produzione (`notification.dlq`), consumo (gruppi `notification-service`, `notification-eventi-cliente`) |
| onboarding | — | — | — | produzione |
| keda-operatore (namespace keda) | — | — | — | descrizione di topic e gruppi |
| signature, sftp-ingest, frontend, pgbouncer, testimone | nessun ruolo | | | |

**Elenco limitato a un prefisso** (R04 quarto giro, F1): `elenco_prefissi: {aip: ["registro/"]}` nel blocco `iam` della hashchain. Il modulo genera una dichiarazione `s3:ListBucket` a parte, con la condizione `StringLike s3:prefix = registro/*`. Serve al controllo di coerenza del registro con il WORM (`app/registro/coerenza.py`).

`make verifiche` controlla due cose (`verifica_manifest.errori_elenco_s3`):
- ogni servizio il cui codice chiama `list_objects` ha `elenco`;
- ogni `elenco` corrisponde a un uso nel codice.

Oggi elencano solo il gateway (staging, pulizia degli orfani) e la hashchain (`registro/`). La prontezza S3 (`HeadBucket`, che richiede `s3:ListBucket`) conta un 403 come «bucket presente»: S3 risponde 404 per un bucket inesistente. Per questo i servizi con sola lettura dei PdA non hanno bisogno di elencarli.

Significato: `lettura` = GetObject/GetObjectVersion; `scrittura` = PutObject;
`eliminazione` = DeleteObject; `elenco` = ListBucket; `multipart` =
Abort/ListParts/ListBucketMultipartUploads; `worm` = Put/Get retention e legal
hold + GetBucketObjectLockConfiguration; `lettura_retention` = Get retention e
legal hold. KMS: Decrypt/DescribeKey a chi legge, in più Encrypt/GenerateDataKey
a chi scrive. Nessun ruolo può eliminare oggetti del bucket dei PdA.

**Kafka per topic (correzioni R04, rev4 rilievo 7).** «produzione» e «consumo»
non valgono più su `topic/*`: il ruolo ha `WriteData` SOLO sui topic di cui il
servizio è fra i `producers` di `infra/kafka/topics.yaml`, `ReadData` solo su
quelli di cui è fra i `consumers`, e `AlterGroup`/`DescribeGroup` solo sui suoi
gruppi (`gruppi_kafka` nel blocco `iam`). Gli ambienti Terraform leggono
`topics.yaml` (unica fonte dei topic) e passano gli elenchi al modulo; il
modulo rifiuta «produzione» senza topic e «consumo» senza topic o gruppi.
`aip.conserved` e `catena.checkpoint` li scrive solo la hashchain (un servizio
compromesso non può annunciare una conservazione o un checkpoint);
«Rielabora» di dashboard-api ripubblica solo sui topic dove dashboard-api è
producer. `verifica_manifest.py` controlla la coerenza values ↔ `topics.yaml`,
`tofu test` i permessi risultanti. L'operatore KEDA resta in sola descrizione
su tutti i topic e gruppi.

Le annotazioni `eks.amazonaws.com/role-arn` (chart e kustomize) usano lo stesso
nome; `verifica_manifest.py` rifiuta un ServiceAccount annotato senza blocco
`iam`, e `tofu test` controlla nomi, fiducia e permessi sui values reali.

Kafka su MSK richiede TLS e autenticazione IAM (`modules/kafka`): i client
dei servizi si collegano con `libs/shared/kafka/connessione.py`
(`KAFKA_AUTENTICAZIONE: iam`, controllato da `verifica_manifest.py`).

## 5. PostgreSQL

- **PgBouncer** in modalità `transaction` davanti all'istanza primaria
  (database logico `verumdoc`) e ai reader (`verumdoc_lettura`). Parametri in
  `values.yaml` (`pgbouncer.impostazioni`), `max_db_connections` 45 per
  database e per istanza.
- **asyncpg** (`libs/shared/db`) usa prepared statement di protocollo: con
  PgBouncer ≥ 1.21 e `max_prepared_statements` > 0 funzionano in modalità
  transaction (l'immagine fallisce la build sotto la 1.21). Le connessioni alla
  replica impostano `default_transaction_read_only` (r04-dati): è fra i
  `track_extra_parameters`. Non passano da PgBouncer: la hashchain (lock
  consultivo di SESSIONE in `app/services/verifica_asincrona.py`, che usa
  `DATABASE_URL_DIRETTA` se impostata, altrimenti l'URL della API, diretto),
  le migrazioni e la manutenzione delle partizioni.
- **Nessuno stato di sessione attraverso PgBouncer** (correzioni R04, rev4
  rilievo 6). Ricerca sistematica (`pg_advisory_lock`, `pg_try_advisory_lock`,
  `SET`/`RESET` senza `LOCAL`) su servizi, librerie e strumenti: restano solo
  lock di TRANSAZIONE (`pg_*advisory_xact_lock`) e `SET LOCAL`, tranne il lock
  di sessione della verifica completa asincrona (connessione diretta, sopra).
  La manutenzione delle partizioni (`libs/shared/db/partizioni.py`) non usa più
  il lock consultivo di sessione né `SET lock_timeout`: una replica alla volta
  con un **lease in tabella** (`manutenzione_lease`), `SET LOCAL lock_timeout`
  nella transazione di creazione e aggancio, `DETACH ... CONCURRENTLY` senza
  timeout (attende senza bloccare le scritture).
- **Ruoli del database** (correzioni R04, rilievo 5, migrazioni `20261023_031` e `20261023_033`). Ruoli separati DAVVERO, in esercizio e in sviluppo:

  | Ruolo | Tipo | Chi lo usa | Cosa può |
  |---|---|---|---|
  | amministratore (RDS: utente master, `rds_superuser`; compose: `vd_user`) | login | solo il Job `gateway-migrazioni` (Secret `gateway-migrazioni`; compose: `DATABASE_URL_MIGRAZIONI` del gateway) | migrazioni, creazione dei ruoli di gruppo e degli event trigger; proprietario delle tabelle non partizionate e delle funzioni degli event trigger. Le difese **dentro** il database non lo fermano; da R05 quelle **fuori** sì: WORM e catena per registro e RdV, e l'export dell'audit nel WORM (VD-C-112) rende dimostrabile ogni sua modifica di `audit_global` già esportato (catena.md §11) |
  | `verumdoc_applicazione` | gruppo NOLOGIN | login dei servizi (`DATABASE_URL` dei Secret `<servizio>-secrets` e di PgBouncer; compose: `vd_app`) | SELECT/INSERT/UPDATE/DELETE sulle tabelle di `public` (anche future), niente UPDATE/DELETE sui registri, niente DELETE su `catena_checkpoint`; nessuna proprietà |
  | `verumdoc_manutenzione` | gruppo NOLOGIN | login della manutenzione (Secret `db-manutenzione`, `DATABASE_URL_MANUTENZIONE`; compose: `vd_manutenzione`), membro anche di `verumdoc_applicazione` | proprietario delle tabelle partizionate operative (`outbox_eventi`, `webhook_consegne`, `eventi_cliente`) e delle loro partizioni, CREATE nello schema `partizioni`, EXECUTE di `vd_crea_partizione_registro` |
  | `verumdoc_audit_proprietario` | gruppo NOLOGIN, **nessun membro** | nessuno | proprietario di `audit_global`, `verifiche_registro`, delle loro partizioni e di `vd_crea_partizione_registro` (SECURITY DEFINER) |

  Il proprietario dei registri non è né il ruolo delle migrazioni né quello della manutenzione: la manutenzione dei registri **crea** soltanto (con la funzione, che decide nome, limiti e difese: RLS con FORCE, politiche, trigger contro TRUNCATE) e non stacca mai. I gruppi e la cessione delle proprietà li crea la migrazione 033 se gira con un superutente (su RDS `rds_superuser`); senza, un WARNING e restano le difese che non richiedono privilegi (FORCE RLS, funzione di creazione). I **login** dei servizi e della manutenzione li crea l'amministratore (in sviluppo il servizio compose `db-ruoli`, `infra/postgres/ruoli_sviluppo.sql`): `CREATE ROLE <servizio> LOGIN … IN ROLE verumdoc_applicazione`, `CREATE ROLE <manutenzione> LOGIN … IN ROLE verumdoc_manutenzione, verumdoc_applicazione`. **Prerequisito del rilascio in esercizio**: i Secret dei servizi e `pgbouncer-secrets` con i login dei servizi, `db-manutenzione` con quello della manutenzione, l'utente master solo in `gateway-migrazioni`.
- **Manutenzione delle partizioni con il ruolo dedicato**: CronJob `gateway-db-manutenzione` (ogni ora, `python -m libs.shared.db.manutenzione`, `DATABASE_URL_MANUTENZIONE` dal Secret `db-manutenzione`, connessione diretta). Nei servizi la manutenzione in processo mantiene le partizioni solo con `DATABASE_URL_MANUTENZIONE` (in compose: il gateway) o nella suite di prova (`ENV=test`, `DB_MANUTENZIONE_PARTIZIONI=auto`); altrimenti consolida solo i contatori. Ogni distacco lascia una voce in `audit_global` (`db.partizione_staccata`, con il ruolo del database); `audit_global` e `verifiche_registro` non si staccano mai. Lease perso durante il giro (rinnovo fallito): il giro si ferma (`manutenzione_lease_perso`).
- **Event trigger dei registri** (031 e 033; servono i diritti di superutente, su RDS `rds_superuser`; senza, la migrazione lo segnala con un WARNING). Valgono per tutti, proprietario compreso:
  - `sql_drop` rifiuta il DROP di registri, partizioni dei registri, tabelle `catena_*`, funzioni di protezione, e di trigger, regole e politiche dei registri;
  - `ddl_command_start` rifiuta un ALTER TABLE che nomina i registri con DETACH, DISABLE o NO FORCE;
  - `ddl_command_end` (ALTER TABLE) controlla lo stato: registri presenti col loro nome, partizioni agganciate con nome conforme (niente RENAME) e nessun distacco in corso, tabelle dei registri non agganciate solo se vuote, RLS e FORCE su `audit_global` e partizioni, trigger delle difese attivi (non disattivati né solo in replica). Ferma anche ciò che è nascosto in SQL dinamico;
  - `ddl_command_end` (funzioni, regole, trigger, politiche) rifiuta CREATE/ALTER/REPLACE delle funzioni di protezione e ogni regola, trigger o politica sui registri diversi da quelli delle difese.

  - dalla migrazione `20261023_034` (terzo giro):
    - lo stato scatta anche su CREATE/ALTER INDEX e confronta la struttura dei registri con l'impronta in `protezione.impronte_registri` (T1: niente vincoli, NOT NULL o indici unici aggiunti fuori da una migrazione, nemmeno su una partizione);
    - «oggetti» rifiuta `CREATE TABLE … PARTITION OF` un registro (T2).

  Le funzioni degli event trigger appartengono all'amministratore: il proprietario dei registri non può sostituirle. **Le migrazioni che cambiano un registro** (funzione di protezione, politica, trigger, vincolo, colonna, indice unico) sospendono e riattivano gli event trigger nello stesso file con `libs.shared.db.migrazioni_registri.registri_modificabili`: riattivazione in `finally` e impronte aggiornate. La procedura e un esempio sono in `docs/runbooks/migrazioni_audit.md`. Solo superutente. Un superutente può sospenderli: contro di lui valgono il WORM e i checkpoint (catena.md §11).
- **Il superutente e l'audit** (R05, VD-C-112). Fino a R04 le righe di `audit_global` erano protette contro il superutente solo dall'organizzazione. Ora la hashchain esporta ogni periodo chiuso (un'ora, dopo 10 minuti di margine) in un segmento WORM `registro/audit/<da>-<a>.jsonl` (Object Lock COMPLIANCE, conservazione permanente come il resto del registro), con l'impronta del segmento precedente e la radice di Merkle delle righe, e ne registra l'impronta nella catena (foglia `audit`, checkpoint firmati e controfirmati). Un superutente che cancella, modifica o aggiunge righe in un periodo esportato è scoperto da `python -m app.registro.operatore verifica-audit` (runbook `docs/runbooks/audit_worm.md`, da eseguire almeno a ogni controllo periodico e dopo ogni intervento con le credenziali del master). Resta scoperto il periodo non ancora esportato (al più 70 minuti con i predefiniti) e il superutente può fermare l'export: lo segnalano la metrica `verumdoc_catena_audit_ritardo_secondi` (allarme da aggiungere, vedi rapporto R05) e il buco nella catena dei segmenti. Permessi: la hashchain scrive già nel bucket dei PdA sotto `registro/` (stesso ruolo IAM, nessun permesso nuovo) e legge `audit_global` (SELECT del gruppo `verumdoc_applicazione`).
- **Replica di lettura**: Aurora ha già 1 writer e 2 reader
  (`modules/rds`); `DATABASE_URL_LETTURA` punta a `pgbouncer:6432/verumdoc_lettura`
  (output Terraform `rds_reader_endpoint`).
- **Connessioni**: `postgres.maxConnessioni` (200) è anche il parametro
  `max_connections` di Aurora (Terraform lo legge dai values).
  `verifica_manifest.py` controlla: riservate (20) + PgBouncer (2 × 45) + client
  diretti al massimo delle repliche × 30 (hashchain 30, Job migrazioni 30) ≤ 200.

## 6. Scalatura e quota

KEDA (≥ 2.16, installato a parte nel namespace `keda` con il ruolo IRSA
`keda-operatore`): ScaledObject al posto dell'HPA dove il segnale giusto è il
ritardo di un gruppo Kafka o una coda in PostgreSQL; la CPU resta come
attivatore in più. `verifica_manifest.py` controlla che gruppo e topic dello
ScaledObject siano quelli che il servizio legge davvero, che le repliche
massime non superino le partizioni e che le query siano `SELECT` limitate.

Quota del namespace (`infra/k8s/base/namespace.yaml`): limiti 200 CPU / 256 GiB,
richieste 48 CPU / 80 GiB; la somma al massimo delle repliche (calcolata da
`verifica_manifest.py`) è 171,5 CPU / 209 GiB di limiti e 37,5 CPU / 54 GiB di
richieste (23/10/2026).

## 7. Storage di sviluppo

Lo scrittore WORM (`libs/shared/storage/worm.py`) richiede e verifica Object
Lock COMPLIANCE, retention e legal hold per oggetto, `If-None-Match` e checksum
SHA-256: uno storage di sviluppo è ammesso solo se passa
`infra/tests/test_storage_reale.py`.

**R05 (VD-C-115): versitygw al posto di Chainguard MinIO.** Il compose usa
versitygw (Versity S3 Gateway, Apache-2.0) v1.9.0 con backend POSIX e
versioning. Prove del 23/10/2026, con i binari ufficiali scaricati dalla
release e verificati con lo SHA-256 pubblicato:

| Prova | v1.8.0 | v1.9.0 | v1.10.0 |
|---|---|---|---|
| `infra/tests/test_storage_reale.py` (Object Lock, COMPLIANCE, legal hold, nessuna sovrascrittura, eliminazione della versione rifiutata anche con bypass, retention non accorciabile) | 6/6 | 6/6 | 6/6 |
| controlli extra: checksum SHA-256 sbagliato rifiutato, `If-None-Match: *`, `ListObjectsV2` con `StartAfter` e con prefisso esatto, versione che resta dopo un DELETE | 5/5 | 5/5 | 5/5 |
| prove del registro della hashchain sullo storage reale invece di moto (registro, coerenza, ricostruzione dal WORM, export dell'audit) | — | 45/45 | — |

Scelta la v1.9.0 (13/10/2026): la v1.10.0 era uscita il giorno prima.

| Alternativa | Licenza | Esito |
|---|---|---|
| versitygw | Apache-2.0 | **adottato** (prove sopra) |
| Chainguard MinIO | AGPL-3.0 (MinIO) | sostituito: pubblica solo `latest`, digest non ricavabile da qui, MinIO community senza correzioni |
| SeaweedFS, RustFS | Apache-2.0 | non provati: release non scaricabili da questo ambiente (rete limitata); da provare con la stessa prova se servisse un'alternativa |
| Garage | AGPL-3.0 | escluso: Object Lock assente |
| Ceph RGW | LGPL | troppo pesante per lo sviluppo locale |
| moto (test) | Apache-2.0 | resta per i test unitari; non è un oracolo per Object Lock |

**Immagine.** Nessun registro di immagini era raggiungibile da qui
(`ghcr.io`, Docker Hub, `cgr.dev`): un digest non si poteva ricavare con
certezza. Il compose costruisce quindi l'immagine da sé (`build.dockerfile_inline`):
scarica il binario statico della release v1.9.0 per l'architettura (amd64 o
arm64), ne verifica lo SHA-256 (fissato nel compose, uguale a quello del file
`checksums.txt` della release) e lo copia in un'immagine `scratch` che contiene
solo il binario: l'immagine finale non dipende da immagini di terzi per tag
(`alpine:3.20` serve solo a estrarre). Il servizio si chiama ancora `minio`
(gli altri servizi usano `http://minio:9000` e le stesse credenziali di
sviluppo); il volume è nuovo (`storage_data`): un vecchio volume MinIO non si
legge (`docker compose down -v`). Il filesystem del volume deve avere gli
attributi estesi (retention e legal hold di versitygw stanno lì). Il
contenitore `minio-pronto` attende `/health` (l'immagine non ha shell né curl) e
i servizi che usano S3 dipendono da lui. Non c'è più la console web di MinIO
(porta 9001).

**Da allineare** (fuori dal perimetro di questa modifica, richiesto al
coordinatore): il passo MinIO della CI (`.github/workflows/ci.yml`) e la
tabella dei servizi di `GUIDA_TEST_COMPLETA.md`.

## 8. Osservabilità

Allarmi in `infra/observability/regole/verumdoc-r04.yml` (testimone, verifica
continua, outbox): un test controlla che citino solo metriche registrate dal
codice. Target nuovi: `testimone:8000`, `hashchain-verifica:9108`.

### 8.1 Tracce distribuite (R16, VD-C-176, VD-P-170)

`libs/shared/observability/tracciamento.py`, installato da `installa_metriche` in ogni
servizio Python (nessuna riga nei `main.py`). **Spento per impostazione**; variabili
d'ambiente del processo:

| Variabile | Predefinito | Significato |
|---|---|---|
| `OTEL_TRACCE_ATTIVE` | `false` | attiva le tracce |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://tempo:4318` | collettore OTLP/HTTP (le tracce vanno a `/v1/traces`, codifica JSON) |
| `OTEL_CAMPIONAMENTO` | `0.1` | quota delle tracce nuove registrate (la decisione del chiamante prevale) |
| `OTEL_HOST_INTERNI` | i servizi del cluster, `localhost` | host verso cui si propaga `traceparent` |
| `OTEL_TIMEOUT_SECONDI` | `5` | tempo massimo di un invio al collettore |

- HTTP in ingresso: span del server con il MODELLO della rotta (mai il percorso effettivo
  né la query), figlio del `traceparent` ricevuto;
- HTTP in uscita (httpx): span del client; `traceparent` SOLO verso gli host interni,
  mai verso prestatori esterni (CSC, TSA, aggregatore, gestori PEC);
- Kafka: `traceparent` fra le intestazioni dei messaggi prodotti (`kafka_produce` e i
  produttori propri di validation, aip-builder e notification) e span del consumatore
  (`ciclo_consumer`, consumo parallelo di validation, eventi di notification);
- nessun dato personale o segreto: l'esportatore pubblica solo gli attributi di
  `ATTRIBUTI_AMMESSI` (metodo, modello della rotta, stato, host di destinazione, topic,
  servizio, tipo d'errore); un collettore irraggiungibile non blocca nulla.

Stack locale: `infra/observability/tempo.yaml` riceve OTLP gRPC (4317) e HTTP (4318);
`docker compose` passa `OTEL_TRACCE_ATTIVE=${VD_TRACCE:-false}` ai servizi (`VD_TRACCE=true`
per attivarle). Nel cluster le tracce restano spente: collettore, variabili e NetworkPolicy
d'uscita sono da predisporre (VD-C-178), come il contesto attraverso l'outbox.

## 9. Integrazione continua

`.github/workflows/ci.yml`: `python` (PostgreSQL e Redis come servizi, MinIO
avviato in un passo perché i servizi di GitHub non accettano il comando;
OpenTofu per `tofu test`; tutte le suite; `make verifiche`), `ruff`, `frontend`,
`terraform` (fmt, validate, `tofu test`), `manifest` (helm lint/template,
kustomize, kubeconform con gli schemi delle CRD KEDA), `immagini` (build di ogni
servizio, PgBouncer e frontend, prova d'avvio). `.github/workflows/carico.yml`:
prova di carico manuale (k6 su staging o locale sul runner).

## 10. Prova di carico

Strumenti: `tools/carico/prova_locale.py` (gateway reale con uvicorn, PostgreSQL e
Redis reali, S3 moto, Kafka assente; `make carico-locale`) e
`tools/carico/k6/versamenti.js` (multipart, sessioni di caricamento, stato;
soglie del piano §2; `make carico-k6`, workflow manuale `carico.yml`).

Misure del 23/10/2026 su una macchina di prova da 2 core condivisa con altri
processi (dettaglio nel rapporto R04 infra §5):

- codice R03, 2 processi uvicorn: 10/s → p95 0,17 s; 25/s → 23,6/s accettati,
  p95 0,92 s, 5 % di 503 (tetto di multipart contemporanei per processo); 50/s →
  macchina satura (96 % CPU), ~22/s accettati. Circa 40–47 ms di CPU del gateway
  per versamento da 300 KB;
- con il client S3 riusato (esperimento, non nel codice): 28 ms di CPU per
  versamento, 25/s senza errori con p95 0,55 s sulla stessa macchina carica.

Per 50 versamenti/s in esercizio: con 1 CPU per pod servono almeno 3–4 pod del
gateway (l'HPA arriva a 20) dopo la correzione del client S3; la soglia p95 ≤ 2 s
va confermata con k6 in staging.

## 11. Ordine di rilascio R04

1. Terraform (`tofu plan`/`apply`): ruoli `iam_servizi` (il ruolo del gateway
   si sposta con il blocco `moved`), parametro `max_connections`, repository
   ECR nuovi (sftp-ingest, testimone, pgbouncer). Il ruolo dei nodi perde i
   permessi S3/KMS: applicare insieme alle annotazioni dei ServiceAccount.
2. Account del testimone: infrastruttura, Secret, rilascio con
   `values.testimone.yaml`; verifier key del testimone in
   `TESTIMONE_VERIFICATORE` della hashchain, impronta del token nel testimone.
3. KEDA nel cluster (namespace `keda`, ServiceAccount `keda-operator` con il
   ruolo `keda-operatore`), Secret `keda-postgres`.
4. Secret nuovi (`infra/k8s/secrets/README.md` §R04), ConfigMap `pgbouncer-ca`,
   `pgbouncer-secrets`; `DATABASE_URL` dei servizi su PgBouncer, con i ruoli di
   login dei servizi membri di `verumdoc_applicazione` (§5, correzioni R04).
5. Topic Kafka (`kafka_create_topics.sh`): `catena.checkpoint`,
   `integrita.anomalia`, `versamento.staging_scaduto`; `hashchain.seal` si toglie a mano.
6. Rilascio del chart: il Job `gateway-migrazioni` applica le migrazioni prima
   dei servizi.
