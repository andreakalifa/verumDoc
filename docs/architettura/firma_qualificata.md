# Firma, sigillo e marca qualificati (R13a)

> Stato al 01/11/2026 (R16: firma personale del responsabile, §9): codice completo e configurabile; manca solo la parte esterna
> (contratto con un prestatore qualificato, credenziali, eventuale HSM). Criticità
> VD-C-068 e VD-C-124 chiuse con riserve; decisione aperta VD-P-05 (scelta del
> prestatore). Scelte di dettaglio da confermare: VD-P-130…144.

## 1. Cosa fa il sistema

| Oggetto | Cosa si appone | Quando | Dove finisce |
|---|---|---|---|
| Indice del PdA (IPdA SInCRO) | sigillo qualificato CAdES (`.p7m` con il contenuto), livello `FIRMA_LIVELLO_DOCUMENTI` | dopo la costruzione del PdA, prima della conservazione | `<ente>/<pda>/Sigillo_IndiceAIP_SInCRO.xml.<sha>.p7m` (WORM, retention del PdA) |
| Rapporto di versamento definitivo (RdV v2) | come sopra | dopo l'emissione del RdV | `<ente>/<pda>/Sigillo_RdV_Definitivo_v2.xml.<sha>.p7m` |
| Manuale di conservazione, piano di cessazione | come sopra, sul contenuto canonico (JSON senza la data di generazione) | «Pubblica» nella pagina del manuale (API `POST /api/dashboard/documenti-conservatore/{manuale|piano}/pubblica`) | `registro/documenti/<tipo>/<impronta>.p7m` (WORM, permanente) e copia nel database per lo scaricamento |
| Checkpoint del registro | sigillo CAdES staccato B-T (come da R02) e, da R13a, marca RFC 3161 dell'ULTIMO checkpoint almeno ogni `CATENA_MARCA_INTERVALLO_MINUTI` (ancoraggio esterno) | ciclo del registro | `registro/checkpoint/<n>.p7m`, `registro/marche/<n>.<sha>.tsr` |
| Attestazione del ciclo di vita (prova d'inclusione) | sigillo CAdES staccato B-T, se `ATTESTAZIONE_SIGILLO` lo prevede | a richiesta della prova | campo `attestazione_sigillo` della risposta |

Ogni firma viene costruita localmente (pyHanko): al prestatore arriva SOLO l'impronta
degli attributi firmati, mai il documento. Prima di restituirla il servizio di firma la
verifica con il verificatore di R08 (pyHanko, `libs/shared/firme`): firma integra,
certificato uguale a quello del fornitore, catena verso le radici del prestatore
configurate (sempre in produzione), marca valida, revoche incluse per B-LT, marca
d'archivio ricalcolata per B-LTA. Una firma che non passa non si usa e si prova il
fornitore successivo.

## 2. Architettura

```
console (onboarding) ── registro dei fornitori (fornitori_firma, nessun segreto) ──┐
console (impostazioni) ── parametri «firma_qualificata» (impostazioni_valori) ─────┤
                                                                                    ▼
aip-builder ── firme_documenti (IPDA) ──┐                    signature (login di sola lettura)
hashchain  ── firme_documenti (RDV)  ───┼─► hashchain:       ├─ CATALOGO: CSC v2 · PKCS#11 · sviluppo
dashboard  ── firme_documenti (MAN.) ───┘   sigilla/rinnova  ├─ TSA RFC 3161 in ripiego
                                            marca checkpoint ├─ CAdES B-T/LT/LTA, PAdES B-LT/LTA
                                               │  token di   └─ verifica della firma prodotta (R08)
                                               └─ servizio ──►  /firma /marca /rimarca
```

- **Servizio `signature`** (`services/signature`): interfaccia unica `FornitoreFirma`
  (firma dell'impronta) e `FornitoreMarca` (token RFC 3161); `registro_fornitori.CATALOGO`
  costruisce l'adattatore per (ruolo, tipo); `GestoreConfigurazione` rilegge il registro
  ogni `FIRMA_CONFIGURAZIONE_CACHE_SECONDI` e ricostruisce solo se cambia; con il
  database non raggiungibile tiene l'ultima configurazione buona.
- **Hashchain** (`app/registro/firme_documenti.py`): sigillo dei documenti, recupero dei
  `NON_FIRMATO`, rimarcatura, marca dei checkpoint, cancello della conservazione. Ogni
  scrittura avviene con il lease del registro verificato nella stessa transazione
  (fencing, come R12a).
- **Stati** di `firme_documenti`: `DA_FIRMARE` → `FIRMATO` | `NON_FIRMATO`.
  `NON_FIRMATO` non è definitivo: si riprova ogni ora e si firma appena c'è un fornitore.

### 2.1 Senza fornitore qualificato

`FIRMA_SENZA_FORNITORE`:
- `NON_FIRMATO` (predefinito, comportamento di prima, VD-P-26): i documenti si dichiarano
  non firmati con il motivo, allarme `DocumentiNonFirmati`, la conservazione procede;
- `BLOCCA` (da usare in esercizio dopo l'attivazione): i documenti restano `DA_FIRMARE`
  e un PdA il cui IPdA non è `FIRMATO` resta `IN_CATENA` (allarme
  `ConservazioneFermaPerFirma`). I PdA in lavorazione alla migrazione 054 sono
  nell'elenco `firma_transizione_r13` e non si bloccano per la riga mancante (VD-P-131).

### 2.2 Errori persistenti (stato `ERRORE`)

Un errore non previsto su un documento (storage, retention, busta già presente con
contenuto diverso…) lo rinvia con attesa crescente; dopo `FIRMA_TENTATIVI_MASSIMI`
(predefinito 10) errori il documento va in `ERRORE`: non si riprova più (nessuna firma
pagata a vuoto ogni ora), allarme `DocumentiDaSigillareInErrore`, e con `BLOCCA` il PdA
resta fermo. La retention del PdA si legge PRIMA di chiedere la firma. Corretta la
causa (log `firma_documento_in_errore`), l'operatore rimette il documento in coda con
il login della hashchain:
`UPDATE firme_documenti SET stato = 'DA_FIRMARE', errori = 0, prossimo_tentativo_il = NULL
WHERE id = '<id>' AND stato = 'ERRORE'` (con audit nel ticket).

In produzione il servizio di firma NON parte con un fornitore di SVILUPPO attivo, senza
`DATABASE_URL` o con il registro illeggibile; la console non salva un fornitore di
SVILUPPO in produzione; anche se una riga arrivasse nel database, l'adattatore non si
costruisce.

## 3. Adattatori

### 3.1 CSC API v2 (firma remota)

- OAuth2 `client_credentials` sull'indirizzo del token; `credentials/list` se
  `credential_id` è vuoto e la credenziale è una (solo fuori produzione: in produzione
  `credential_id` è obbligatorio; con più credenziali e nessun identificativo, errore);
  `credentials/info` (certificato, catena, `authMode`, `SCAL`, presenza di PIN e OTP).
- **Identità attesa** (revisione R13a (4)): `certificati_attesi_sha256` (impronte del
  certificato DER) e/o `organization_identifier` (OID 2.5.4.97, es. `VATIT-…`),
  obbligatoria in produzione per CSC e PKCS#11; controllata quando si legge il
  materiale (`credentials/info`, token) e di nuovo sulla firma prodotta: una
  credenziale con un altro certificato (anche valido e qualificato) non firma.
- **Sigillo automatico** (firma massiva senza interazione): PIN e, se il prestatore lo
  prevede, OTP «statico» dal Secret. Con `SCAL 1` e `firme_per_autorizzazione` > 1 un SAD
  vale per N firme fino a `expiresIn`; con `SCAL 2` un'autorizzazione per firma (SAD legato
  alle impronte). Una credenziale con OTP online senza OTP nel Secret è rifiutata (non
  si chiede mai un OTP a una persona per il sigillo automatico); `oauth2code` non è
  ammesso per il sigillo.
- **Firma di una persona con OTP** (responsabile della conservazione):
  `invia_otp` (`credentials/sendOTP`) e `firma_impronta_con_otp` nell'adattatore; dal
  rilascio R16 il flusso in console c'è (§9, VD-C-166).
- HTTP: tempo massimo, ritentativi solo se la richiesta non è partita o per operazioni
  idempotenti (mai un'autorizzazione con OTP), mTLS facoltativo (certificato e chiave
  dal Secret), pinning facoltativo della chiave del server (SHA-256 della
  SubjectPublicKeyInfo, verificato su ogni risposta e con una connessione di prova
  prima di spedire il segreto OAuth2).
- **Segreti legati agli host** (revisione R13a (2), (3)): un fornitore con segreti si
  costruisce solo se ogni indirizzo (`url_base`, `url_token`) ha un host elencato in
  `host` della sua voce di `FIRMA_SEGRETI_JSON`, fissata al deploy; il client HTTP lo
  ricontrolla a ogni richiesta prima che parta. Chi cambia gli indirizzi in console non
  può spedire `client_secret` o la password altrove. In produzione, con segreti, solo
  https. Il pinning del Secret (`pin_spki_sha256`) prevale su quello della console.
- ECDSA `r||s` convertito in DER; algoritmi RSA PKCS#1 v1.5 e ECDSA su SHA-256/384/512.

### 3.2 PKCS#11 (HSM, smart card)

`python-pkcs11`; libreria SOLO fra quelle di `PKCS11_LIBRERIE_AMMESSE` (fissate al
deploy: caricare una libreria è eseguire codice); token ed etichetta della chiave dalla
console, PIN dal Secret; certificato dalla console (PEM pubblico) o dal token. Firma
dell'impronta (RSA con DigestInfo, ECDSA). Provato con SoftHSM2 reale
(`services/signature/tests/test_r13a_fornitori.py`).

### 3.3 Sviluppo

CA di sviluppo generata (anche la CRL, per B-LT senza rete); vietato in produzione.

### 3.4 TSA RFC 3161

Più TSA in ordine di priorità (`MarcaConRipiego`): una TSA non raggiungibile o con una
risposta che non supera la verifica (stato, nonce, impronta, politica, firma, catena
verso le radici, istante entro `tolleranza_secondi`, impronta attesa del certificato)
si scarta e si passa alla successiva; anche un token malformato (non decodificabile)
è un errore del fornitore e porta alla TSA successiva. Credenziali Basic dal Secret,
legate agli host come per CSC; in produzione una TSA con credenziali usa solo https.

## 4. Formati

| Formato | Livelli | Note |
|---|---|---|
| CAdES (`.p7m` con contenuto, o staccata) | B-B, B-T, B-LT, B-LTA | B-LT: certificati e revoche (OCSP prima, poi CRL) in `certificates`/`crls`; B-LTA: `archive-time-stamp-v3` con `ats-hash-index-v3` (ETSI EN 319 122-1 §5.5.3) |
| PAdES | B-T, B-LT, B-LTA | pyHanko: DSS con le revoche, timestamp di documento per B-LTA |
| XAdES | — | non prodotto: nell'architettura c'è solo la verifica (EU DSS, R08) |

Rimarcatura (`/rimarca`, lavoro `rinnova` della hashchain): nuova marca d'archivio
prima della scadenza del certificato o della TSA (`FIRMA_RINNOVO_PREAVVISO_GIORNI`;
dopo una marca d'archivio conta solo la marca) o quando un algoritmo esce da
`FIRMA_ALGORITMI_AMMESSI`. La busta nuova è un oggetto nuovo (`….r<n>.<sha>.p7m`).

Chiavi nel WORM (revisione R13a (1)): `<ente>/<pda>/Sigillo_<nome>.<sha256[:24]>.p7m`,
`registro/documenti/<tipo>/<impronta>.<sha>.p7m`, `registro/marche/<n>.<sha>.tsr`, con
l'impronta della busta o del token nella chiave. Una firma CAdES e una marca non sono
deterministiche: se il processo cade (o perde il lease) dopo la scrittura nel WORM e
prima dell'aggiornamento della riga, al giro successivo la nuova busta va su una
chiave nuova; quella orfana resta nel WORM (non cancellabile) ma non è mai registrata
e quindi non vale per il registro. Un errore su un documento lo rinvia con attesa
crescente senza fermare gli altri.

## 5. Configurazione

### 5.1 Console (conservatore, permesso mai delegabile `firma_fornitori:gestisci`)

Pagina **Configurazione → Fornitori di firma e marca** (`/configurazione/firma-qualificata`):
- parametri del gruppo `firma_qualificata` (bozza, anteprima, applicazione, storico);
- stato: fornitori costruiti, problemi, segreti presenti (mai i valori), certificato e
  giorni alla scadenza, **Prova** (firma di prova verificata, o marca di prova; limite
  `FIRMA_PROVE_PER_ORA` per processo, audit `firma.fornitore_provato`);
- registro: codice, ruolo, tipo, nome, priorità (1 = primo; il successivo è il ripiego),
  campi del tipo; ogni modifica con audit `firma.fornitore_modificato` (prima/dopo).

Campi per tipo (`libs/shared/firma_qualificata/configurazione.py`):
- **CSC**: `url_base` (con `/csc/v2`), `url_token`, `client_id`, `credential_id`,
  `modo_autorizzazione` (auto/explicit/implicit), `firme_per_autorizzazione`,
  `timeout_secondi`, `ritentativi`, `sign_algo_oid`, `mtls`, `pin_spki_sha256`, `radici_pem`,
  `certificati_attesi_sha256`, `organization_identifier`;
- **PKCS11**: `libreria`, `token_label`, `chiave_label`, `certificato_pem`, `radici_pem`,
  `certificati_attesi_sha256`, `organization_identifier`;
- **RFC3161**: `url`, `politica_oid`, `timeout_secondi`, `ritentativi`, `tolleranza_secondi`,
  `certificato_sha256`, `radici_pem`.
In produzione: indirizzi CSC solo https (e TSA con credenziali), `radici_pem`
obbligatorie, `credential_id` e identità attesa obbligatori per la firma.

### 5.2 Segreti (Secret `signature-secrets`)

`FIRMA_SEGRETI_JSON`, un oggetto per codice di fornitore:

```json
{"csc-principale": {"host": ["csc.prestatore.it", "auth.prestatore.it"],
                    "pin_spki_sha256": ["<sha256 spki>"],
                    "client_secret": "…", "pin": "…", "otp": "…",
                    "mtls_certificato_pem": "…", "mtls_chiave_pem": "…"},
 "hsm-locale": {"pin": "…"},
 "tsa-principale": {"host": ["tsa.prestatore.it"], "utente": "…", "password": "…"}}
```

`host` (obbligatorio per CSC e per una TSA con credenziali) e `pin_spki_sha256` non
sono segreti ma vincoli fissati al deploy: la console non li vede né li cambia.

Ruoli del database (migrazione 054, revisione R13a (5)):

| Gruppo | Diritti | Login di produzione |
|---|---|---|
| `verumdoc_firma` | SELECT `fornitori_firma`, `impostazioni_valori` | servizio di firma (SOLO questo gruppo) |
| `verumdoc_firma_fornitori` | SELECT, INSERT, UPDATE `fornitori_firma` | onboarding |
| `verumdoc_firme_documenti` | SELECT `firme_documenti`; INSERT delle sole colonne d'origine (accoda) | aip-builder, dashboard-api |
| `verumdoc_firme_documenti_sigillo` | SELECT, INSERT, UPDATE `firme_documenti`; SELECT `firma_transizione_r13` | hashchain (SOLO questa) |

Nessun DELETE a nessuno; il trigger `firme_documenti_protette` rende immutabili
origine e impronta e, dopo FIRMATO, tutto tranne le colonne della rimarcatura (con
`rinnovi` crescente e la busta precedente aggiunta a `storico_buste`: chiave, versione,
impronta, livello). Il trigger `firme_documenti_nuove` ammette da chi non è nel gruppo
della hashchain solo righe `DA_FIRMARE` senza dati del sigillo, anche se per errore
avesse un INSERT su tutta la tabella.

## 6. Simulatore locale (collaudo)

`tools/simulatore_firma`: un prestatore CSC v2 e una TSA RFC 3161 finti (FastAPI), con
CA propria, CRL e OCSP; guasti iniettabili (503, TSA lenta, marca su un'impronta
diversa). Usato dai test end-to-end via ASGI e avviabile per il collaudo:

```bash
python -m tools.simulatore_firma --porta 8099            # stampa la configurazione da inserire
```

La configurazione stampata (`url_base`, `url_token`, `client_id`, `credential_id`,
`radici_pem`, TSA) va registrata in console; i segreti stampati vanno in
`FIRMA_SEGRETI_JSON` del servizio di firma di sviluppo. Il simulatore dichiara
«SIMULATORE» nei certificati: non è mai qualificato (la sua CA non è fra le radici di
un prestatore vero).

Nello stack locale (pacchetto R15–R17) il servizio di firma raggiunge il simulatore avviato sul
computer: `python -m tools.simulatore_firma --host 0.0.0.0 --url-pubblico
http://host.docker.internal:8099 --personale responsabile-1:1234`; il blocco «segreti» stampato
va in `VD_FIRMA_SEGRETI_JSON` prima di `docker compose up -d` (il compose lo passa come
`FIRMA_SEGRETI_JSON`). Con `--personale CREDENTIAL_ID[:PIN]` (ripetibile, R16) il simulatore
crea credenziali personali del responsabile, ne stampa credentialID e impronta del certificato
(da registrare con `PUT /api/v1/onboarding/firma/credenziali-personali/{user_id}`, VD-C-179) e
stampa nel terminale l'OTP monouso a ogni «Chiedi l'OTP» della console.

## 7. Checklist «attivazione fornitore»

### 7.1 Cosa chiedere al prestatore qualificato

1. Servizio di **firma remota CSC API v2** (versione e indirizzi: base `/csc/v2` e token
   OAuth2), oppure HSM con libreria PKCS#11 e modalità di consegna.
2. Credenziale di **sigillo elettronico qualificato** intestata al conservatore, con
   **firma automatica/massiva** (nessuna interazione per firma): `authMode`, `SCAL`,
   presenza di PIN e OTP, eventuale OTP statico; numero di firme per autorizzazione.
3. Credenziali OAuth2 `client_credentials` (client_id, segreto) e, se richiesto, certificato
   client per mTLS; impronte della chiave dei server per il pinning (facoltativo).
4. Certificati radice e intermedi della catena del sigillo (PEM) e conferma della
   presenza nella Trusted List UE (QcCompliance nel certificato).
5. **TSA qualificata RFC 3161**: indirizzi (almeno due per il ripiego), politica (OID),
   credenziali Basic, certificati della TSA, volumi e costi.
6. Raggiungibilità di OCSP/CRL dagli indirizzi dei certificati (B-LT) e liste degli IP
   d'uscita da autorizzare.
7. Per la firma del responsabile: credenziale personale con OTP (sendOTP).

### 7.2 Parametri da inserire

1. Database, da amministratore (dopo la migrazione 054, che crea i gruppi se ha
   CREATEROLE; altrimenti crearli `NOLOGIN` e rieseguire i GRANT della migrazione):
   ```sql
   CREATE ROLE <login firma> LOGIN PASSWORD '…' IN ROLE verumdoc_firma;   -- solo questo
   GRANT verumdoc_firma_fornitori         TO <login onboarding>;
   GRANT verumdoc_firme_documenti         TO <login aip-builder>, <login dashboard-api>;
   GRANT verumdoc_firme_documenti_sigillo TO <login hashchain>;            -- solo questo
   ALTER ROLE <login firma> SET idle_in_transaction_session_timeout = '15min';
   ```
   Controllo: `SELECT r.rolname, m.rolname FROM pg_auth_members a JOIN pg_roles r ON
   r.oid = a.member JOIN pg_roles m ON m.oid = a.roleid WHERE m.rolname LIKE
   'verumdoc_firm%'` mostra esattamente le righe sopra.
2. Secret `signature-secrets`: `FIRMA_SEGRETI_JSON` (§5.2, con `host` per ogni
   fornitore con segreti), `DATABASE_URL` (login della firma); per un HSM
   `PKCS11_LIBRERIE_AMMESSE` nei values. IAM della hashchain con i prefissi `Sigillo_`
   (`infra/helm/values.yaml` → modulo Terraform `iam_servizi`).
3. Console → Fornitori di firma e marca: un fornitore FIRMA (CSC o PKCS11, priorità 1),
   eventuale secondo fornitore di ripiego (priorità 2), le TSA (priorità 1, 2…), con
   `radici_pem`, `credential_id` e l'identità attesa del certificato.
4. Parametri: `FIRMA_LIVELLO_DOCUMENTI` (B-LT o B-LTA), `FIRMA_ALGORITMO_IMPRONTA`,
   `CATENA_MARCA_INTERVALLO_MINUTI`, `ATTESTAZIONE_SIGILLO`; per ultimo, dopo le prove,
   `FIRMA_SENZA_FORNITORE=BLOCCA`.

### 7.3 Prove da eseguire

1. «Prova» su ogni fornitore: esito riuscito, verificata, `qualificata` vera, certificato
   con QcCompliance e giorni alla scadenza attesi.
2. Stato: `production_ready` vero, nessun problema, segreti presenti.
3. Un versamento di prova fino a CONSERVATO: `firme_documenti` dell'IPdA e del RdV in
   `FIRMATO`, buste `Sigillo_*.p7m` nel WORM, verificate con EU DSS (`verifica-firme`,
   VD-C-143) e con un verificatore esterno (es. quello del prestatore).
4. Checkpoint `MARCATO` entro l'intervallo; token in `registro/marche/`.
5. Ripiego: disattivare (o rendere irraggiungibile) il primo fornitore e la prima TSA,
   verificare che si usi il secondo.
6. Rimarcatura: con un documento di prova e `FIRMA_RINNOVO_PREAVVISO_GIORNI` alto,
   verificare la busta `….r1.<sha>.p7m` B-LTA.
7. Recupero: documenti `NON_FIRMATO` precedenti all'attivazione firmati entro un'ora.
8. Infine `FIRMA_SENZA_FORNITORE=BLOCCA` e allarmi `verumdoc-r13.yml` attivi.

## 8. Riserve

- Nessun prestatore reale provato (VD-P-05): adattatori provati con il simulatore e,
  per PKCS#11, con SoftHSM2.
- CAdES B-LTA (`archive-time-stamp-v3`) verificato dal nostro verificatore e da pyHanko
  per la firma di base, non ancora da EU DSS (VD-C-143, VD-C-167).
- IPdA dei PdA già conservati prima di R13a non sigillati (VD-C-165).
- Firma personale con OTP del responsabile provata solo con il simulatore; credenziali
  personali registrabili solo con l'API di onboarding (VD-C-179).
- Pinning provato sulla logica e su trasporto senza TLS, non con un server TLS reale.

## 9. Firma personale del responsabile (R16, VD-C-166)

Documenti che il responsabile del servizio firma PERSONALMENTE con la propria firma
qualificata e l'OTP del prestatore (manuale, piano di cessazione, nomine, qualunque altro
`tipo`): il sigillo del conservatore (§1) resta, la firma personale si aggiunge.

```
servizio che produce il documento ──accoda_documento──► firme_responsabile (DA_FIRMARE)
console «Firme del responsabile» ──► dashboard-api ──token della persona──► signature
   1. «Leggi il documento»            /otp   → credentials/sendOTP (credenziale della persona)
   2. «Chiedi l'OTP»                  /firma → authorize con OTP e PIN → signHash → busta
   3. OTP (e PIN) → «Firma»                    CAdES verificata, certificato = quello atteso
dashboard-api: busta coerente → FIRMATO (+ audit; il trigger ricontrolla credenziale e certificato)
hashchain (giro del registro): riverifica → WORM registro/documenti/responsabile/<tipo>/
   <impronta>.<sha_busta[:24]>.p7m → ARCHIVIATO   (oppure RESPINTO, allarme FirmeResponsabileRespinte)
```

- **Punto d'aggancio generico**: `libs/shared/firme_responsabile.accoda_documento(session,
  tipo=…, titolo=…, nome=…, contenuto=…, richiesto_da=…, firmatario_atteso=None)` nella
  transazione del servizio che produce il documento; idempotente per (tipo, impronta)
  finché il documento non è annullato o respinto. Manuale e piano si accodano anche dalla
  pagina (stesso contenuto canonico del sigillo, `POST
  /api/dashboard/firme-responsabile/conservatore/{manuale|piano}`). R15 userà questa
  funzione per i documenti generati.
- **Credenziale personale** (VD-P-140): `credenziali_firma_personali` (persona,
  fornitore CSC del registro, `credentialID`, impronta SHA-256 ATTESA del certificato),
  una attiva per persona, nessun segreto; si registra con `PUT
  /api/v1/onboarding/firma/credenziali-personali/{user_id}` (permesso mai delegabile
  `firma_fornitori:gestisci`, versione attesa, audit con prima e dopo; una credenziale
  cambiata si disattiva e se ne crea una nuova, mai riscritta).
- **Servizio di firma** (`/api/v1/signature/personale/otp`, `/firma`): solo token utente
  di un ruolo fisso del conservatore, con accesso al portale (mai chiave API, token di
  servizio o ruolo personalizzato); la credenziale si legge per il `user_id` del token;
  il cliente OAuth2 è quello del fornitore (segreti legati agli host come per il sigillo);
  l'adattatore personale firma UNA volta e non ritenta l'autorizzazione con l'OTP; la
  busta è verificata (R08) e il suo certificato confrontato con quello atteso due volte;
  OTP e PIN mai nei log. Limiti per persona: `FIRMA_PERSONALE_TENTATIVI` (5 ogni 15
  minuti per l'OTP e per la firma, riserva atomica in Redis, VD-P-166).
- **Database** (migrazione `20261110_057_residui`): trigger `firme_responsabile_protette`
  su INSERT, UPDATE, DELETE e TRUNCATE (una riga nuova entra DA_FIRMARE senza firma;
  richiesta e contenuto immutabili; passaggi ammessi DA_FIRMARE → FIRMATO | ANNULLATO,
  FIRMATO → ARCHIVIATO | RESPINTO; FIRMATO pretende busta con impronta giusta e
  credenziale ATTIVA della persona con il certificato dichiarato; ARCHIVIATO pretende la
  chiave con le due impronte; archiviazione e rifiuto solo dalla hashchain); diritti per
  colonna: `verumdoc_firme_responsabile` (dashboard-api) richiesta e firma,
  `verumdoc_firme_documenti_sigillo` (hashchain) archiviazione.
- **Riverifica della hashchain** prima del WORM: impronta della busta, contenuto incluso
  uguale al documento con un solo firmatario, credenziale attiva della persona con il
  certificato dichiarato, `/api/v1/signature/verifica` «valida» con lo stesso certificato;
  servizio di firma irraggiungibile = nuovo tentativo con attesa crescente. Scrittura nel
  WORM idempotente (chiave con l'impronta della busta, §31 R13a revisione (1)) e UPDATE con
  il lease del registro (fencing).
- **Collaudo** con il simulatore: `Simulatore.aggiungi_personale(credentialID, nome)`
  crea una credenziale personale con OTP online monouso (`otp_inviati` dopo
  `credentials/sendOTP`).
