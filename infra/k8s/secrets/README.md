# Secret dei servizi VerumDoc

I file `<servizio>-secrets.template.yaml` sono **modelli generati** da
`infra/k8s/genera_manifest.py` a partire da `services.<servizio>.secretKeys` di
`infra/helm/values.yaml`: elencano le chiavi che il servizio legge, con valori
**vuoti**. Nel repository non si scrivono mai valori veri.

Non sono inclusi in `base/kustomization.yaml`: un `kubectl apply -k` non deve
sovrascrivere i Secret reali con valori vuoti. I Deployment li richiedono
(`envFrom.secretRef`), quindi senza Secret i pod restano in
`CreateContainerConfigError` (errore esplicito, mai avvio con credenziali di
sviluppo).

## Come crearli (per ambiente)

1. Copiare il modello fuori dal repository e riempire i valori (da AWS Secrets
   Manager: `rds_secret_arn`, `redis_secret_arn`; chiavi JWT; credenziali dei
   fornitori di firma/TSA; SMTP).
2. Cifrarlo con Sealed Secrets e salvarlo in `infra/k8s/sealed-secrets/<ambiente>/`:

       kubeseal --format yaml < gateway-secrets.yaml > infra/k8s/sealed-secrets/staging/gateway-secrets.yaml

3. Aggiungere i file cifrati alle `resources` dell'overlay dell'ambiente.

In alternativa (consigliata su EKS) External Secrets Operator con un
`ExternalSecret` per servizio che produce un Secret con lo stesso nome e le
stesse chiavi. Con Helm: `secrets.create=true` e valori passati solo a riga di
comando (`--set-string secrets.values.<servizio>.<CHIAVE>=...`).

Con IRSA (R04, VD-C-101: un ruolo per servizio, modulo Terraform `iam_servizi`)
le chiavi `S3_ACCESS_KEY`/`S3_SECRET_KEY` devono essere presenti e VUOTE: un
valore assente farebbe valere il predefinito di sviluppo delle Settings
(`minioadmin`). Verificato nel codice (23/10/2026): aip-builder, hashchain,
validation e il controllo di prontezza passano a boto3 `None` quando sono vuote
(catena di credenziali di default → IRSA); **gateway**
(`app/services/versamento_service.py`, `_s3_client`), **distribution**
(`app/services/s3_client.py`) e **dashboard-api** (`app/routers/download.py`)
passano la stringa vuota, che boto3 usa come credenziale: con IRSA l'accesso a S3
di questi tre servizi FALLISCE finché il codice non usa `... or None` (richiesta
al coordinatore nel rapporto R04 infra).

## Chiavi introdotte nel rilascio del 20/10/2026

- `API_KEY_PEPPER` (gateway **e** onboarding, stesso valore, diverso per
  ambiente; almeno 32 byte casuali, es. `openssl rand -hex 32`): HMAC delle
  chiavi API. Fuori da development/test i due servizi non partono senza;
  cambiarlo invalida tutte le chiavi API emesse.
- `JWT_PUBLIC_KEY` anche per hashchain (le API della catena verificano il
  token): stessa chiave pubblica degli altri servizi.
- `PDND_PUBLIC_KEY` (gateway): chiave pubblica PEM con cui PDND firma i voucher;
  non è segreta, ma si gestisce qui come le altre chiavi. Senza, le API PDND
  rispondono 503.
  Con la chiave configurata servono anche `PDND_AUDIENCE` e `PDND_ISSUER`
  reali (configmap): altrimenti il gateway parte ma le sole API PDND rispondono
  503 PDND-503 «PDND non configurato» e il log d'avvio indica le variabili
  mancanti (revisione di sicurezza M1).
- `FIRMA_SEGRETI_JSON` (signature, R13a): segreti dei fornitori di firma e marca
  registrati in console, un oggetto JSON per codice di fornitore
  (`{"csc-principale": {"host": ["csc.prestatore.it"], "pin_spki_sha256": […],
  "client_secret": "…", "pin": "…", "otp": "…", "mtls_certificato_pem": "…",
  "mtls_chiave_pem": "…"}, "tsa-principale": {"host": ["tsa.prestatore.it"],
  "utente": "…", "password": "…"}}`; nomi in `libs/shared/firma_qualificata`).
  `host` lega i segreti agli host a cui si possono inviare (revisione R13a (2)):
  un indirizzo cambiato in console verso un altro host rende il fornitore non
  utilizzabile. `{}` finché non c'è il prestatore. Mai nella console né nei log.
- `DATABASE_URL` (signature, R13a): login di SOLA LETTURA membro solo di
  `verumdoc_firma` (fornitori e parametri); senza, in produzione il servizio
  di firma non parte. Gli altri GRANT dei gruppi R13a ai login di produzione
  (onboarding, aip-builder, dashboard-api, hashchain) sono in
  `docs/architettura/firma_qualificata.md` §7.2.
- `SIGNATURE_SERVICE_TOKEN` (hashchain): **token di servizio**, non un token
  utente (revisione di sicurezza M2). Audience `verumdoc-signature`, scope
  `firma:servizio`, nessun ente né ruolo, durata massima 90 giorni. Si emette
  in un ambiente che ha la chiave privata del gateway (`JWT_PRIVATE_KEY`):

      python -m libs.shared.security.emetti_token_servizio --servizio hashchain --giorni 30

  L'output (solo il token) va scritto direttamente nel Secret di hashchain.
  Ruotarlo prima della scadenza (es. Job mensile); scaduto o rifiutato, i
  blocchi della catena restano `DA_FIRMARE` e si firmano dopo il rinnovo
  (log `firma_blocco_credenziali_rifiutate`). Il servizio signature accetta
  `/firma` e `/marca` SOLO con questo token: un token utente riceve 403.

## Chiavi introdotte nel rilascio R03 (21/10/2026)

- `API_KEY_PEPPER` anche per **distribution**, **dashboard-api** e
  **notification** (chiave API su ricerca, PdD, elenchi ed eventi): STESSO
  valore di gateway e onboarding. Fuori da development/test i servizi non
  partono senza.
- `JWT_PUBLIC_KEY` anche per **notification** (API eventi e webhook): stessa
  chiave pubblica degli altri servizi.
- `WEBHOOK_CHIAVE_CIFRATURA` (notification): 32 byte casuali in base64
  (`openssl rand -base64 32`), cifra con AES-256-GCM i segreti dei webhook. In
  produzione il servizio non parte senza. Rotazione: la chiave nuova va qui con
  un id nuovo in `WEBHOOK_CHIAVE_CIFRATURA_ID` (ConfigMap) e la vecchia in
  `WEBHOOK_CHIAVI_CIFRATURA_PRECEDENTI` (stesso Secret, solo per decifrare,
  JSON `{"k1": "<base64>"}`: `docs/integrazione/webhook.md`). Persa la chiave, i
  segreti dei webhook non si decifrano più (consegne WH-106): si ricreano i
  webhook.
- `POSTA_CHIAVE_PRIVATA` (`notification-posta-secrets`, R12b VD-C-161: solo il
  lavoratore della posta, con `DATABASE_URL`, `POSTA_CHIAVI_PRIVATE_PRECEDENTI`, `SMTP_UTENTE` e
  `SMTP_PASSWORD`; in `notification-secrets` fermano l'avvio delle repliche API) e
  `POSTA_CHIAVE_PUBBLICA` (onboarding),
  R12a (VD-C-152): coppia X25519, 32 byte grezzi in base64url ciascuna
  (`bash infra/scripts/genera_segreti_dev_r03.sh` mostra come estrarli con
  openssl). Onboarding sigilla il collegamento d'invito con la pubblica; solo la
  posta lo apre all'invio. Senza, in produzione nessuno dei due servizi parte.
  Rotazione: coppia nuova con id nuovo (`POSTA_CHIAVE_PRIVATA_ID` e
  `POSTA_CHIAVE_PUBBLICA_ID`, predefinito `p1`), la privata vecchia in
  `POSTA_CHIAVI_PRIVATE_PRECEDENTI` (JSON `{"p1": "<base64url>"}`) finché la coda
  non ha più messaggi sigillati con quella.
- `OPENSEARCH_PASSWORD` (distribution): solo con autenticazione di base su
  OpenSearch (`OPENSEARCH_UTENTE` nella ConfigMap); vuota altrimenti.
- **sftp-ingest** (Secret nuovo `sftp-ingest-secrets`):
  - `DATABASE_URL`;
  - `SFTP_CHIAVE_HOST`: chiave privata host SSH (OpenSSH o PEM, es.
    `ssh-keygen -t ed25519 -N "" -f host`). Deve restare la STESSA fra i
    rilasci: i clienti ne fissano l'impronta e un cambio fa rifiutare la
    connessione. Senza, il server SFTP non parte;
  - `SFTP_TOKEN_CHIAVE_PRIVATA`: chiave RSA ≥ 2048 bit PROPRIA del canale
    (`openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072`), con cui il
    lavoratore firma i token di canale (contratto C2). MAI la chiave privata del
    gateway: il canale non deve poter firmare token utente.
- `SFTP_TOKEN_CHIAVE_PUBBLICA` (gateway): la chiave pubblica corrispondente
  (`openssl pkey -in privata.pem -pubout`). Non è segreta; senza, in produzione
  il gateway esclude il canale SFTP (avviso nel log) e i versamenti SFTP
  ricevono 401.

R05 — ciclo di vita. Revisione 2 di R05 (S2): i tre token sotto stanno SOLO nel
Secret del lavoratore `ciclo-vita-lavoratore-secrets` (modello
`ciclo-vita-lavoratore-secrets.template.yaml`); `ciclo-vita-secrets`, montato
anche dal pod API esposto all'ingress, ha solo database, Redis, S3, chiave
pubblica JWT e pepper. In produzione il lavoratore senza i tre token NON parte;
l'API che li riceve (Secret sbagliato) NON parte:
  - `CICLO_VITA_TOKEN_CHIAVE_PRIVATA`: chiave RSA ≥ 2048 bit PROPRIA del servizio
    (come `SFTP_TOKEN_CHIAVE_PRIVATA`), per i token di canale `CICLO_VITA` dei
    versamenti di rettifica e di riversamento in ingresso. MAI la chiave del
    gateway;
  - `HASHCHAIN_TOKEN_REGISTRO`: token di servizio verso l'API interna del
    registro (contratto Q3), emesso con la chiave privata del gateway:
    `python -m libs.shared.security.emetti_token_servizio --servizio ciclo-vita
    --pubblico verumdoc-registro --scope registro:aggiungi --giorni 30`;
  - `DATABASE_URL` (revisione 3 di R05, T5): login del database PROPRIO del
    lavoratore, DIVERSO da quello dell'API di `ciclo-vita-secrets`: membro di
    `verumdoc_applicazione` e di `verumdoc_ciclo_vita_lavoratore` (unico ruolo
    che scrive `esiti_esecuzione`, migrazione 043), con
    `idle_in_transaction_session_timeout = '15min'`. Il Deployment lo prende
    da qui (`envSegreti`), non dal Secret del servizio. Il login dell'API NON
    deve essere membro di `verumdoc_ciclo_vita_lavoratore`: in produzione l'API
    con quel login non parte, e nemmeno il lavoratore senza;
  - `DISTRIBUTION_TOKEN_SERVIZIO`: token di servizio verso l'API di migrazione
    di distribution (riversamento in uscita, restituzione):
    `python -m libs.shared.security.emetti_token_servizio --servizio ciclo-vita
    --pubblico verumdoc-distribution --scope migrazione:servizio --giorni 30`.
  I due token scadono (massimo 90 giorni): ruotarli prima, come
  `SIGNATURE_SERVICE_TOKEN`. Alla scadenza le pratiche restano in attesa con
  l'errore visibile e riprendono appena il token nuovo è in servizio.
- `CICLO_VITA_TOKEN_CHIAVE_PUBBLICA` (gateway): pubblica di
  `CICLO_VITA_TOKEN_CHIAVE_PRIVATA`. Non è segreta; senza, in produzione il
  gateway esclude il canale `CICLO_VITA` e i versamenti del ciclo di vita
  ricevono 401.

Chiave tolta in R03: `GATEWAY_INTERNAL_URL` (ConfigMap del gateway) non è più
letta. Se un `.env` locale la contiene ancora, le impostazioni del gateway la
rifiutano come chiave non prevista: va tolta.

In sviluppo: `bash infra/scripts/genera_segreti_dev_r03.sh` crea
`infra/dev_sftp.env`, `infra/dev_canali_pub.env` e `infra/dev_webhook.env`
(letti da docker-compose.yml, ignorati da git). R05: `python
tools/dev/genera_segreti_dev.py` crea anche `infra/dev_ciclo_vita.env` e
`infra/dev_ciclo_vita_pub.env` (coppia del canale CICLO_VITA); i due token di
servizio di ciclo-vita si aggiungono a mano a `infra/dev_ciclo_vita.env` con i
comandi sopra.

## Chiavi e Secret introdotti in R04 (23/10/2026)

Account principale:

- `CATENA_CHIAVE_PRIVATA` (hashchain): chiave Ed25519 del registro nel formato
  delle note firmate C2SP `PRIVATE+KEY+verumdoc-registro+<keyhash>+<base64>`
  (`libs.catena.nota.genera_chiave`), chiave di SERVIZIO, non un sigillo
  qualificato. Senza, la hashchain non parte (tranne `ENV=test`).
- `TESTIMONE_SERVICE_TOKEN` (hashchain): token opaco con cui il registro si
  presenta al testimone (casuale, almeno 32 byte). Sta SOLO qui: il testimone ne
  conosce l'impronta SHA-256 (`printf %s "$TOKEN" | sha256sum`).
- Secret `gateway-migrazioni`, chiave `DATABASE_URL`: URL DIRETTO dell'istanza
  primaria (non PgBouncer) per il Job `gateway-migrazioni` (CREATE INDEX
  CONCURRENTLY, lock), con l'utente AMMINISTRATORE (master di RDS, membro di
  `rds_superuser`: crea ruoli di gruppo ed event trigger dei registri). Solo
  qui: mai nei Secret dei servizi né della manutenzione.
- Secret `db-manutenzione`, chiave `DATABASE_URL` (R04 secondo giro): URL
  DIRETTO con il login della manutenzione delle partizioni, creato
  dall'amministratore `IN ROLE verumdoc_manutenzione, verumdoc_applicazione`;
  lo usa il CronJob `gateway-db-manutenzione` (`DATABASE_URL_MANUTENZIONE`).
  Non è proprietario dei registri (`docs/architettura/infrastruttura.md`,
  «Ruoli del database»).
- **Login dei servizi** (rilievo 5): il `DATABASE_URL` di ogni servizio e le
  righe di `pgbouncer-secrets` usano login creati `IN ROLE
  verumdoc_applicazione` (solo DML, nessuna proprietà), mai l'amministratore
  né il login della manutenzione. Prerequisito del rilascio.
- Secret `hashchain-pgbouncer`, chiave `DATABASE_URL`: URL della hashchain
  attraverso PgBouncer (`postgresql+asyncpg://...@pgbouncer:6432/verumdoc`), usato
  dal lavoratore `hashchain-verifica`; la API della hashchain resta diretta.
- Secret `pgbouncer-secrets`, chiave `userlist.txt`: una riga per utente
  applicativo, `"utente" "SCRAM-SHA-256$..."` (il segreto SCRAM di
  `pg_authid.rolpassword`) o la password; sincronizzato dal secret manager
  quando la password di RDS ruota (External Secrets).
- ConfigMap `pgbouncer-ca`, chiave `global-bundle.pem`: bundle delle CA di RDS
  (https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem) per
  `server_tls_sslmode = verify-full`. Non è un segreto; la pipeline lo crea.
- Secret `keda-postgres`, chiave `connection`: stringa libpq verso il reader
  (`host=<rds_reader_endpoint> port=5432 dbname=verumdoc user=keda_lettura
  sslmode=verify-full password=...`); utente con `SELECT` sulle sole tabelle
  di coda (`indicizzazione_aip`, `webhook_consegne`).
- **URL dei database dei servizi dietro PgBouncer**: `DATABASE_URL` di gateway,
  validation, aip-builder, distribution, notification, onboarding,
  dashboard-api, sftp-ingest punta a `pgbouncer:6432/verumdoc`; la replica di
  sola lettura (r04-dati) a `pgbouncer:6432/verumdoc_lettura`
  (`DATABASE_URL_LETTURA`, facoltativa: senza, le letture vanno sul primario).
  hashchain resta diretta all'istanza primaria.

Account del testimone (Secret `testimone-secrets`, `values.testimone.yaml`):

- `DATABASE_URL`: database del testimone (RDS del suo account; in produzione
  rifiutati localhost e credenziali `*_dev_only`).
- `TESTIMONE_CHIAVE_PRIVATA`: Ed25519 nel formato
  `PRIVATE+KEY+verumdoc-testimone+<keyhash>+<base64>` (NON PEM), DIVERSA da quella
  del registro. Generarla nell'account del testimone e scriverla direttamente
  nel secret manager; la verifier key pubblica (`python -m app.operatore
  chiave-pubblica`) va in `TESTIMONE_VERIFICATORE` della hashchain (ConfigMap).
- Configurazione non segreta del testimone (ConfigMap): `TESTIMONE_CHIAVI_REGISTRO`
  (verifier key del registro), `TESTIMONE_IMPRONTE_TOKEN_REGISTRO` (SHA-256 del
  token del registro), `TESTIMONE_ORIGINI_AMMESSE`.

In sviluppo: `python tools/dev/genera_segreti_dev.py` (lo lancia `make dev-up`)
crea in più `infra/dev_catena.env` (hashchain) e `infra/dev_testimone.env`
(testimone), letti da docker-compose.yml.

## R13b (canali esterni predisposti)
- `PEC_SEGRETI_JSON` (`notification-posta-secrets`, solo il lavoratore della posta): password delle caselle PEC per codice del canale, ciascuna con gli `host` del gestore a cui legarla (`docs/integrazione/pec.md`). In `notification-secrets` ferma l'avvio delle repliche API in produzione.
- `SPID_CHIAVE_PRIVATA_CLIENT` (`gateway-secrets`): chiave privata PEM del cliente OIDC per `private_key_jwt`, request object e UserInfo cifrato; con `SPID_CLIENT_SECRET` o questa chiave serve `SPID_HOST_AMMESSI` (ConfigMap), altrimenti il gateway in produzione non parte.
- GRANT della migrazione 055: `verumdoc_pec_canali`, `verumdoc_pec_accoda`, `verumdoc_notifiche_personali` al login di notification; `verumdoc_pec` al login del lavoratore della posta.

## R14 (debito interno)
- Nessun segreto nuovo. GRANT della migrazione 056: `verumdoc_notifiche_pulizia` al SOLO login del lavoratore della posta (pulizia delle notifiche personali, lease in `lavori_periodici`); senza, la pulizia registra l'errore a ogni giro e le voci restano (nessun altro effetto).

## R16 (chiusura dei residui interni)
- **`signature-secrets`: chiave nuova `REDIS_URL`** (stesso Redis degli altri servizi, VD-C-168): limiti condivisi fra le repliche delle prove di connessione e delle firme personali. In produzione il servizio di firma **non parte** senza; la NetworkPolicy generata apre l'uscita verso Redis.
- GRANT della migrazione `20261110_057_residui` (R16): `verumdoc_firme_responsabile` al SOLO login di **dashboard-api** (documenti firmati dal responsabile); le colonne dell'archiviazione vanno già al gruppo della hashchain (`verumdoc_firme_documenti_sigillo`), la lettura delle credenziali personali al servizio di firma (`verumdoc_firma`) e alla console (`verumdoc_firma_fornitori`). Se il ruolo che migra non ha CREATEROLE, creare `verumdoc_firme_responsabile` da amministratore e rieseguire i GRANT della migrazione.
- Nessun segreto per le credenziali personali di firma: PIN e OTP li dà la persona a ogni firma; il cliente OAuth2 resta quello del fornitore in `FIRMA_SEGRETI_JSON`.

## R15 (documentazione di conformità generata; unita a R16 in R17)
- Variabili `CONSERVATORE_*` (anagrafica e responsabili del conservatore) nella configurazione di **dashboard-api**: senza, i documenti generati riportano «DA COMPILARE».
- GRANT della migrazione `20261111_058_documentazione` (nata 057 in R15, rinumerata all'unione): `verumdoc_documentazione` al SOLO login di **dashboard-api**, `verumdoc_documentazione_archivio` al SOLO login della **hashchain**, `verumdoc_auditor` al login dell'auditor (sola lettura, `tools/conformita/genera_documenti.py`).

## R17 (collaudo finale: conferma firmata delle decisioni sensibili, VD-P-164)
- **`gateway-secrets`: chiave nuova `CONFERME_CHIAVE_PRIVATA`** (Ed25519: PEM PKCS#8 o 32 byte in base64), SOLO nel Secret del gateway; il suo identificativo è `CONFERME_KID` nella configurazione del gateway. In produzione il gateway **non parte** senza una chiave valida.
- **ciclo-vita (API e lavoratore)**: `CONFERME_CHIAVI_PUBBLICHE` (JSON `{kid: chiave pubblica in base64}`, non segreto: ConfigMap) e `CONFERME_KID_REVOCATI`; `CONFERMA_DECISIONI=ATTIVO`. In produzione ciclo-vita non parte senza le chiavi pubbliche. Rotazione e revoca: `docs/runbooks/conferme_decisione.md`.
- GRANT della migrazione `20261112_059_conferme_decisione`: `verumdoc_conferme_registra` al SOLO login dell'**API di ciclo-vita** (registrazione delle conferme); i fatti del lavoratore (`conferme_lavoratore`) vanno già a `verumdoc_ciclo_vita_lavoratore`.
