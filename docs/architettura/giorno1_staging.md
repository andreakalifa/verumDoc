# R12 — Checklist «giorno 1 di staging»

Riferimenti: `ROADMAP_R07_R13.md` §R12; `infrastruttura.md` §11 (ordine di rilascio), `infra/k8s/secrets/README.md`; runbook `docs/runbooks/disaster_recovery.md` e `ripristino_registro_da_backup.md`; rapporto R12a (`changelog/CHANGELOG-2026-11-05-rilascio-R12a.md`).

Scritta in R12a, quando l'account cloud di staging non c'era ancora. Ogni passo ha il comando esatto, il controllo che dice «fatto» e la criticità che chiude o prova. Si eseguono in ordine; un passo non superato ferma i successivi che ne dipendono. Gli esiti (tempi, costi, valori misurati) si annotano in fondo, nella tabella «Esiti».

## 0. Prerequisiti (prima del giorno 1)

| # | Cosa | Chi | Controllo |
|---|---|---|---|
| 0.1 | Account AWS di staging (registro) e account AWS SEPARATO del testimone, con amministratori diversi | committente | due ID di account diversi; nessun utente amministratore in comune |
| 0.2 | Zona DNS `staging.verumdoc.it` (o equivalente) e host del testimone su una zona amministrata da chi non amministra il registro | committente | `dig NS` |
| 0.3 | Repository su GitHub con le Actions abilitate; ambiente `staging` con i segreti `VD_CARICO_TOKEN` (dopo il passo 8) | sviluppo | prima esecuzione della CI (passo 12) |
| 0.4 | Strumenti sulla macchina dell'operatore: `tofu` 1.8.5, `helm` 3.16.4, `kubectl` 1.31, `k6` 0.54.0, `aws` v2, `go` 1.23 | operatore | `tofu version`, `helm version`, … |

In R12a sono già stati verificati senza account: `tofu validate` di tutti i moduli e di tutti gli ambienti (compreso `environments/testimone`), rendering del chart per produzione, staging e testimone con kubeconform (CRD comprese), kustomize di base, overlay e testimone, oracolo Go dei checkpoint, script k6 contro un'API finta, ricostruzione del registro dal WORM sul database di test. `helm lint` vero non è stato eseguito (helm non scaricabile nell'ambiente di sviluppo): è il passo 6.2.

## 1. Stato Terraform e account principale

1. Backend dello stato (una volta, credenziali dell'account di staging):
   `AWS_REGION=eu-south-1 AWS_ACCOUNT_ID=<id staging> bash infra/scripts/bootstrap_terraform_backend.sh`
2. `cd infra/terraform/environments/staging && tofu init && tofu plan -out piano.tfplan`
   Controllo: il piano crea VPC 10.1.0.0/16, EKS, Aurora, ElastiCache, MSK, OpenSearch, S3 (Object Lock sul bucket dei PdA), KMS, ECR, WAF, ruoli IRSA per servizio (compreso `hashchain-verifica-audit`, VD-C-150).
3. `tofu apply piano.tfplan`; salvare gli output (`tofu output -json > output-staging.json`, fuori dal repository).

## 2. Account del testimone (VD-C-110)

1. Backend dello stato NELL'ACCOUNT DEL TESTIMONE (bucket e tabella di lock propri, es. `verumdoc-testimone-terraform-state`, `verumdoc-testimone-terraform-lock`).
2. ```bash
   cd infra/terraform/environments/testimone
   tofu init -backend-config="bucket=verumdoc-testimone-terraform-state"
   tofu plan -var account_id_testimone=<id testimone> -var account_id_principale=<id staging> -out piano.tfplan
   tofu apply piano.tfplan
   ```
   Controllo: il provider rifiuta credenziali di un altro account (`allowed_account_ids`); le precondizioni rifiutano account uguali e un testimone con ruoli IAM.
3. Record DNS di validazione del certificato (`tofu output acm_validazione_dns`), creato da chi amministra la zona del testimone.
4. Riportare in `infra/helm/values.testimone.yaml` gli `DA_IMPOSTARE` (`acm_certificate_arn_testimone`, `waf_web_acl_arn_testimone`) e verificare che `network.*` coincida con le sottoreti create (10.20.0.0/16).

## 3. Cluster: componenti di base

Nei due cluster (registro e testimone), con il kubeconfig di `aws eks update-kubeconfig`:
1. AWS Load Balancer Controller (ruolo IRSA dal modulo eks), metrics-server, kube-state-metrics (serve agli allarmi della verifica dell'audit).
2. Solo nel cluster del registro: KEDA ≥ 2.16 nel namespace `keda` con il ServiceAccount `keda-operator` annotato con il ruolo `keda-operatore`; Secret `keda-postgres`.
3. Prometheus con le regole `infra/observability/regole/*.yml` (comprese `verumdoc-r12.yml`) e Alertmanager verso la reperibilità.

## 4. Immagini

1. Build e push su ECR di ogni immagine (job `immagini` della CI con il push abilitato, o a mano con lo stesso Dockerfile): 13 immagini più `verifica-firme` (VD-C-143: prima build reale di EU DSS con Maven Central raggiungibile).
2. Controllo: `aws ecr describe-images` per ogni repository; tag = commit del rilascio.

## 5. Segreti

1. Seguire `infra/k8s/secrets/README.md` per ogni Secret (`*-secrets.template.yaml`), valori fuori dal repository, cifrati con sealed-secrets.
2. Novità R12a (VD-C-152): coppia X25519 della posta.
   ```bash
   openssl genpkey -algorithm X25519 -out posta.pem
   openssl pkey -in posta.pem -outform DER | tail -c 32 | base64 -w0 | tr '+/' '-_' | tr -d '='   # → POSTA_CHIAVE_PRIVATA (notification-posta-secrets, R12b)
   openssl pkey -in posta.pem -pubout -outform DER | tail -c 32 | base64 -w0 | tr '+/' '-_' | tr -d '='   # → POSTA_CHIAVE_PUBBLICA (onboarding-secrets)
   shred -u posta.pem
   ```
   Controllo: onboarding e il lavoratore `notification-posta` partono (in produzione/staging non partono senza).
   R12b (VD-C-161): la chiave privata della posta, le chiavi precedenti e le credenziali SMTP (`SMTP_UTENTE`, `SMTP_PASSWORD`) vanno SOLO in `notification-posta-secrets`, con una copia di `DATABASE_URL` (il lavoratore non riceve `notification-secrets`); se restano in `notification-secrets` le repliche API di notification in staging/produzione non partono (log `configurazione dei webhook non valida: … VD-C-161`).
3. Testimone: verifier key in `TESTIMONE_VERIFICATORE` della hashchain; impronta del token di servizio nel testimone.

## 6. Database e rilascio del chart

1. Ruoli di gruppo e login dei servizi sul cluster Aurora (come `infra/postgres/ruoli_sviluppo.sql`, password da Secrets Manager), compreso il login dell'auditor membro del solo `verumdoc_auditor` (VD-C-150) e la sua voce nel `userlist.txt` di PgBouncer.
2. `helm lint infra/helm -f infra/helm/values.yaml -f infra/helm/values.staging.yaml` e `helm template … | kubeconform -strict` (helm vero: in R12a solo il renderer minimo).
3. `helm upgrade --install verumdoc infra/helm -n verumdoc -f infra/helm/values.yaml -f infra/helm/values.staging.yaml`
   Controllo: il Job `gateway-migrazioni` arriva alla testa `20261106_053` (`alembic current`); tutti i Deployment pronti; partizioni del giorno create dalla manutenzione.
4. Nel cluster del testimone: `helm upgrade --install verumdoc-testimone infra/helm -n verumdoc-testimone -f infra/helm/values.yaml -f infra/helm/values.testimone.yaml`; `TESTIMONE_URL=https://<host testimone>` nei values della hashchain.

## 7. Prova di fumo

1. `curl https://api.<staging>/api/v1/health/ready` → 200; console raggiungibile.
2. Ente di prova, utente invitato: l'e-mail d'invito arriva con il collegamento (sigillato in coda, aperto solo all'invio: nessun `#invito=` in chiaro in `outbox_eventi` né in `notifications`). Gli eventi d'invito in chiaro scritti dai pod precedenti durante il deploy, una volta pubblicati, e le notifiche inviate li ripulisce il lavoratore di onboarding a ogni giro (`minimizza_inviti_in_chiaro`): controllo `SELECT count(*) FROM outbox_eventi WHERE pubblicato_il IS NOT NULL AND payload->>'testo' ~ '#invito=[A-Za-z0-9]'` = 0 dopo un giro.
   Ruolo `verumdoc_registro_worm`: `GRANT verumdoc_registro_worm TO <login della hashchain>` (solo quello; VD-C-148).
   Ruoli R13a (migrazione 054, `docs/architettura/firma_qualificata.md` §7.2): login del servizio di firma membro SOLO di `verumdoc_firma`; `GRANT verumdoc_firma_fornitori TO <login onboarding>`; `GRANT verumdoc_firme_documenti TO <login aip-builder>, <login dashboard-api>`; `GRANT verumdoc_firme_documenti_sigillo TO <login della hashchain>` (solo quello).
3. Un versamento → CONSERVATO con checkpoint controfirmato dal testimone (`GET /api/v1/hashchain/checkpoint`).
4. Hashchain: metrica `verumdoc_catena_worm_rinnovo_in_errore` = 0 dopo il primo passo di rinnovo; `registro_worm_censimento.completato_il` valorizzato (VD-C-148).

## 8. Kafka su MSK (VD-C-109)

1. Topic con `infra/scripts/kafka_create_topics.sh` (TLS + IAM).
2. Prova delle ACL: ogni servizio scrive solo dove è «producers» e legge solo dove è «consumers» in `infra/kafka/topics.yaml`; un tentativo fuori ACL (es. `validation` che scrive su `aip.conserved`) deve essere rifiutato.
3. Token di carico: utente di servizio dell'ente di prova con scope `versamento:ud` → segreto `VD_CARICO_TOKEN`.
4. R13b (revisione della configurazione, VD-C-109). Già provato in sviluppo: parametri di connessione (`KAFKA_AUTENTICAZIONE=iam`, SASL_SSL + OAUTHBEARER, contesto TLS con `KAFKA_CA_FILE` facoltativo), token sul filo nel formato RFC 7628, rinnovo 60 s prima della scadenza, rifiuto del broker e TLS non attendibile (`libs/shared/kafka/tests/test_r13b_kafka_sasl_tls.py`, broker di prova: un Kafka vero non era scaricabile dall'ambiente di sviluppo). Da provare qui:
   - porta 9098 (IAM) raggiungibile solo dai nodi; client MSK con `allow.everyone.if.no.acl.found=false`;
   - ogni ServiceAccount (IRSA) firma il token con il PROPRIO ruolo: `kafka-cluster:Connect` sul cluster, `WriteData`/`ReadData`/`DescribeTopic` SOLO sui topic di `topics.yaml`, `AlterGroup`/`DescribeGroup` sui soli gruppi dichiarati (`gruppi_kafka` dei values);
   - R13b: `notification` (gruppo `notification-service`) legge anche `pratica.cambio_stato` per le comunicazioni PEC: il topic ha già `consumers: [notification]`, controllare che la policy del gruppo `notification-service` lo comprenda;
   - prova negativa: un pod con il ruolo di `validation` che prova a scrivere su `aip.conserved` → `TopicAuthorizationFailed`; uno senza IRSA → autenticazione rifiutata;
   - durata del token (15 minuti) e riconnessione dopo la scadenza durante un carico di almeno 30 minuti senza errori nei consumer.

## 9. Disaster recovery: failover (scenario A)

`aws rds failover-db-cluster --db-cluster-identifier verumdoc-staging-aurora`; misurare il tempo di indisponibilità percepito (versamenti in errore, ripresa automatica). Controllo: nessun allarme del registro dopo la ripresa.

## 10. Disaster recovery: point-in-time (scenario B)

`disaster_recovery.md` §3.2 su un cluster NUOVO, istante = 30 minuti prima. Misurare: tempo di ripristino, tempo fino a servizio attivo (RTO), dati persi (RPO). Annotare nel runbook §1.

## 11. Disaster recovery: registro dal WORM

Sul cluster ripristinato al passo 10: `controlla-worm`, poi `ricostruisci-da-worm --prova` e reale (`disaster_recovery.md` §4). Controllo: stesse radici dei checkpoint pubblicati, controllo di coerenza superato, registro che riparte; `verifica-audit` completa coerente.

## 12. CI su GitHub (VD-C-131, VD-C-081)

1. Prima esecuzione di `.github/workflows/ci.yml` su un ramo: job `python` (con l'oracolo Go: clone di `golang/mod` v0.20.0 al commit fissato), `ruff`, `frontend`, `terraform` (compreso `environments/testimone`), `manifest` (helm lint vero), `immagini`.
2. Salti attesi da gestire nel controllo «nessun test saltato»: i test di conformità all'XSD ufficiale UNI 11386 (VD-C-071, finché lo schema non è acquistato) e quelli che richiedono il JDK se il runner non lo avesse. Il controllo `grep "[0-9]+ skipped"` va ristretto ai soli salti non dichiarati (oggi fallirebbe per i salti R11 voluti).
3. Pipeline di rilascio (immagini con push su tag, firma, promozione staging → produzione): da scrivere dopo la prima esecuzione verde.

## 13. Carico e costi

1. Picco: workflow «Carico», modalità `k6` (`tools/carico/k6/versamenti.js`), 50 versamenti/s per 1 ora. Soglie del piano §2 (presa in carico p95 ≤ 2 s).
2. Giornata: modalità `giornata` (`tools/carico/k6/giornata_milione.js`), 1.000.000 di documenti con la curva oraria, prima `ORE=2` (prova breve) poi `COMPRESSIONE=4`, `ORE=24` (giornata intera in 6 ore). Soglie: presa in carico p95 ≤ 2 s, campioni CONSERVATO p95 ≤ 1 ora, errori < 1 %.
3. Costi: Cost Explorer filtrato sui tag `Project=verumdoc`, `Environment=staging` per la giornata di carico; costo per 1.000 documenti.
4. Al termine: scarto dei PdA di prova secondo il runbook (ente di prova con classe a retention breve).

## 14. SFTP dietro l'NLB reale (VD-C-154)

Accesso SFTP con PROXY protocol dalle sottoreti dell'NLB: l'IP del cliente nell'audit è quello reale; un indirizzo fuori da `sftp_account.ip_ammessi` è rifiutato. Lo stesso per `X-Forwarded-For` dietro l'ALB (`PROXY_FIDATI`).

R13b (revisione, VD-C-154). Già provato in sviluppo: intestazioni v1/v2, LOCAL dei controlli di salute, TLV di AWS (tipo 0xEA, endpoint VPC) consumati senza toccare il banner SSH (`libs/shared/security/tests/test_r13b_proxy_aws.py`). Da controllare qui:
1. NLB con target group in modalità **IP** e attributo `proxy_protocol_v2.enabled=true` (annotazione `service.beta.kubernetes.io/aws-load-balancer-proxy-protocol: "*"` dei values); controlli di salute TCP: arrivano come LOCAL e non finiscono nell'audit come accessi.
2. `SFTP_PROXY_SORGENTI_FIDATE` = `nlbSourceCidrs` = sottoreti private dell'NLB (mai la VPC intera, al più /20) e NetworkPolicy coerente: una connessione diretta al pod da un'altra sottorete è rifiutata.
3. Con `preserve_client_ip` attivo l'NLB non cambia l'origine TCP: verificare che la sorgente vista dal pod sia comunque nelle reti fidate (altrimenti l'intestazione è rifiutata, fail-closed).
4. ALB: `PROXY_FIDATI` = sottoreti dell'ALB; verificare che l'IP registrato nell'audit sia quello del cliente reale anche quando il cliente invia un proprio `X-Forwarded-For` inventato (l'ALB lo estende: deve contare solo il valore aggiunto dall'ALB).

## 14-bis. Canali esterni predisposti (R13b)

1. **PEC** (VD-C-078): checklist `docs/integrazione/pec.md` §4 (casella, `PEC_SEGRETI_JSON` con gli host, radici dei gestori, prova d'invio con ricevute associate). Login del lavoratore della posta: raccomandato un login proprio membro di `verumdoc_pec` (invio e ricevute) e le repliche API membri di `verumdoc_pec_accoda` e `verumdoc_pec_canali` (VD-P-150).
2. **SPID/CIE** (VD-C-069): checklist `docs/architettura/accesso_spid_cie.md` §4 (`SPID_HOST_AMMESSI`, chiave del cliente se `private_key_jwt`).
3. **Webhook**: rotazione del segreto da un ente di prova; il destinatario di prova accetta con entrambi i segreti durante la transizione.
4. **GRANT della migrazione 055** ai login di produzione: `verumdoc_pec_canali`, `verumdoc_pec_accoda`, `verumdoc_notifiche_personali` al login di notification; `verumdoc_pec` al login del lavoratore della posta; il gateway ha già `verumdoc_credenziali_accesso` (INSERT delle voci personali).

## 14-ter. Debito interno (R14)

1. **GRANT della migrazione 056**: `verumdoc_notifiche_pulizia` al solo login del lavoratore della posta (pulizia delle notifiche personali oltre `NOTIFICHE_PERSONALI_GIORNI`); la hashchain ha già `verumdoc_firme_documenti_sigillo` (cursore del censimento degli IPdA storici, `firme_arretrato_ipda`).
2. **Allarmi R14** (`infra/observability/regole/verumdoc-r14.yml`) e cruscotto Grafana (`infra/observability/grafana/cruscotti/`): caricarli in Prometheus/Alertmanager e Grafana dello staging; provare `PraticaSospesa` con una pratica di prova e `PostaCodaFerma` fermando il lavoratore della posta.
3. **Arretrato dei sigilli** (VD-C-165): si attiva da solo quando un fornitore qualificato è attivo (`FIRMA_ARRETRATO_IPDA_ATTIVO=true`, predefinito); controllare il log `firma_arretrato_ipda` e la metrica `verumdoc_firme_arretrato_ipda_completato`.

## 15. Chiusura di R12

Criticità da chiudere con gli esiti: VD-C-109 (passo 8), VD-C-110 (passi 2, 6.4, 7.3), VD-C-131 (passo 12), VD-C-081 (passi 4, 6, 12, 13), VD-C-150 (passi 6.1 e verifica dell'audit riuscita con il login dell'auditor), VD-C-154 (passo 14), VD-C-143 (passo 4.1 e prova della Trusted List). Registro delle criticità, STATO_PROGETTO e changelog di R12 aggiornati con gli esiti qui sotto.

## Esiti

| Passo | Data | Esito | Misure (tempi, costi, valori) | Note |
|---|---|---|---|---|
| — | — | — | — | — |
