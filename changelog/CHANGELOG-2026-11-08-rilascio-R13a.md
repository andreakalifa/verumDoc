# CHANGELOG — R13a — Firma e marca qualificate predisposte (31/10/2026)

Decisione del committente: tutto ciò che dipende da risorse esterne (contratto con un
prestatore qualificato, credenziali, HSM reale) si fa alla fine; ora il codice è completo,
provato e configurabile, così che dopo bastino parametri e credenziali.

- **Base**: R12c (`changelog/CHANGELOG-2026-11-07-rilascio-R12c.md`), cioè R12b con le
  correzioni R12b.1/R12b.2 (`seed.py`, `versamento_service.py`, `docker-compose.yml`, già
  incluse identiche in questo rilascio) più la revisione grafica della console. Migrazioni
  fino a `20261106_053`; migrazione nuova `20261107_054`.
- **Criticità**: VD-C-068 e VD-C-124 chiuse con riserve; nuove VD-C-165…168 (168
  registrate: 20 aperte, 12 parzialmente chiuse, 39 chiuse con riserve, 97 chiuse).
- **Scelte da confermare**: VD-P-130…144.
- **Documentazione**: `docs/architettura/firma_qualificata.md` (con la checklist
  «attivazione fornitore», §7).
- **Console**: la pagina nuova «Fornitori di firma e marca» segue il sistema grafico R12c
  (testa di pagina chiusa dal binario, colonna unica, tabellone impilato su telefono, stati
  vuoti con `Vuoto`); la voce sta nell'area «Configurazione» del menu R12c.

> **Avviso operativo**
> - **Servizio di firma con database**: `signature` legge il registro dei fornitori e i
>   parametri con un login di SOLA LETTURA (in sviluppo `vd_firma`). In produzione senza
>   `DATABASE_URL` il servizio **non parte**.
> - **Gruppi del database (migrazione 054)**: senza `verumdoc_firme_documenti`,
>   aip-builder non costruisce i PdA (la riga dell'IPdA da sigillare nasce nella stessa
>   transazione).
> - Comportamento invariato finché non si registra un fornitore: senza fornitore
>   qualificato i documenti si dichiarano `NON_FIRMATO` (allarme `DocumentiNonFirmati`) e
>   la conservazione procede (`FIRMA_SENZA_FORNITORE=NON_FIRMATO`, VD-P-132).

## Revisioni di sicurezza

Due revisioni indipendenti dopo lo sviluppo; **tutti i rilievi sono corretti** prima del
rilascio, con test nel repository e mutazioni uccise.

| Revisione | Gravità | Rilievo | Correzione | Esito |
|---|---|---|---|---|
| Prima | Grave | Stallo dei sigilli dopo un crash o un lease perso fra la scrittura WORM e l'aggiornamento della riga | Chiave WORM con l'impronta della busta (scrittura idempotente); errore per documento rinviato con attesa crescente, il lotto prosegue | Corretta |
| Prima | Media | Segreti spediti a un host esterno configurato in console | `host` e `pin_spki_sha256` nella voce di `FIRMA_SEGRETI_JSON`; fornitore non costruito e richiesta rifiutata se l'indirizzo esce dagli host (VD-P-142) | Corretta |
| Prima | Media | https non obbligatorio | In produzione obbligatorio per ogni fornitore con credenziali, TSA comprese | Corretta |
| Prima | Media | Identità del certificato di firma non controllata | `certificati_attesi_sha256` / `organization_identifier` obbligatori in produzione, controllati su materiale e firma prodotta; `credential_id` obbligatorio (VD-P-143) | Corretta |
| Prima | Media | GRANT della migrazione 054 troppo ampi | UPDATE di `firme_documenti` solo alla hashchain (`verumdoc_firme_documenti_sigillo`), trigger d'immutabilità; GRANT ai login documentati (§7.2) | Corretta |
| Prima | Bassa | Failover TSA: token malformato non gestito | Errore del fornitore e ripiego sulla TSA successiva | Corretta |
| Seconda | Media | INSERT di righe già FIRMATO da chi accoda | INSERT delle sole colonne d'origine e trigger BEFORE INSERT `firme_documenti_nuove` | Corretta |
| Seconda | Bassa | Firma pagata a ogni tentativo con errore persistente | Retention letta prima della firma; `ERRORE` dopo `FIRMA_TENTATIVI_MASSIMI` con allarme (VD-P-144) | Corretta |
| Seconda | Bassa | Storico delle buste non registrato alla rimarcatura | `storico_buste` a ogni rimarcatura, esatto dal trigger | Corretta |

Mutazioni: 23/23 sui punti di sicurezza dello sviluppo, 19/19 della prima revisione,
7/7 della seconda. Dettaglio dei rilievi più sotto.

## Cosa servirà dal prestatore
Da chiedere al prestatore qualificato (checklist completa in `firma_qualificata.md` §7.1):
- endpoint **CSC API v2** e token OAuth2 (client_id e segreto `client_credentials`), oppure
  HSM con libreria PKCS#11;
- credenziale di **sigillo elettronico qualificato** del conservatore con firma automatica:
  `authMode`, SCAL, PIN e/o OTP statico, firme per autorizzazione, `credential_id`;
- eventuale certificato client per mTLS e impronte delle chiavi dei server (pinning);
- certificati radice e intermedi PEM, presenza nella Trusted List UE (QcCompliance);
- **TSA qualificata RFC 3161**: almeno due indirizzi, politica (OID), credenziali,
  certificati;
- raggiungibilità di OCSP/CRL e IP d'uscita da autorizzare;
- per la firma del responsabile: credenziale personale con OTP (VD-C-166).

## Prima del deploy
1. **Login e gruppi del database**: login del servizio di firma membro del SOLO gruppo
   `verumdoc_firma` (in sviluppo `vd_firma`, creato da `infra/postgres/ruoli_sviluppo.sql`
   al `docker compose up`), con `idle_in_transaction_session_timeout` come gli altri e in
   PgBouncer `userlist.txt`.
2. **GRANT ai login di produzione** (dopo la 054, esatti in `firma_qualificata.md` §7.2):
   `verumdoc_firme_documenti` ad aip-builder e dashboard-api (SELECT e INSERT delle
   colonne d'origine); `verumdoc_firme_documenti_sigillo` SOLO alla hashchain (anche
   UPDATE); `verumdoc_firma_fornitori` a onboarding. Controllo con la query di §7.2.
3. **Secret** `signature-secrets`: `DATABASE_URL` (login della firma) e
   `FIRMA_SEGRETI_JSON` (`{}` finché non c'è il prestatore; poi, per ogni fornitore con
   segreti, la voce con `host` e, se usato, `pin_spki_sha256`: un indirizzo fuori dagli
   host non riceve mai il segreto).
4. **IAM della hashchain**: scrittura, retention e legal hold anche su
   `<ente>/<pda>/Sigillo_*`; Terraform `iam_servizi` prima del Chart.
5. **Migrazione `20261107_054`** con il Job `gateway-migrazioni` (usa
   `registri_modificabili` per `catena_checkpoint`: serve il superutente come per la 034).
   Controllo: `alembic current` = `20261107_054`. La 054 è stata corretta durante le due
   revisioni prima del rilascio e non era mai stata consegnata: nessun database del
   committente (di sviluppo o di staging) può essere già a 054, quindi non serve alcun
   downgrade; si applica una volta sola sopra la 053.
6. **Rete**: onboarding → signature; signature → PgBouncer. **Chart**: signature,
   onboarding, hashchain, aip-builder, dashboard-api, frontend.
7. **Controlli**: `signature` pronto (log `registro_fornitori_caricato`); la pagina
   «Fornitori di firma e marca» mostra l'origine «variabili d'ambiente» e nessun fornitore;
   un versamento di prova va a CONSERVATO con IPdA e RdV `NON_FIRMATO`.
8. **Con il prestatore**: checklist `firma_qualificata.md` §7 (fornitori e radici in
   console, «Prova», versamento con sigilli `FIRMATO`, ripiego, rimarcatura) e, per
   ultimo, `FIRMA_SENZA_FORNITORE=BLOCCA`: senza fornitore qualificato la conservazione si
   ferma invece di produrre documenti non firmati.

Nota: helm, kubectl e terraform veri non eseguiti in sviluppo (manifest rigenerati e
verificati con `verifica_manifest.py`).

## Novità per Windows (sviluppo)
- `python tools/dev/genera_segreti_dev.py` genera ora anche la coppia X25519 della posta
  (`infra/dev_posta_priv.env`, `infra/dev_posta_pub.env`): un solo comando prima di
  `docker compose up -d --build`, niente OpenSSL.
- Il compose crea il login `vd_firma` e avvia `signature` con il registro dei fornitori;
  in sviluppo è ammesso il fornitore SVILUPPO e c'è il simulatore
  `python -m tools.simulatore_firma` (CSC v2, TSA, OCSP, CRL) per il collaudo.

## Per chi usa la console
- **Configurazione → Fornitori di firma e marca** (solo SUPER_ADMIN e
  RSC_CONSERVATORE, permesso mai delegabile `firma_fornitori:gestisci`): parametri del
  gruppo «firma_qualificata»; stato dei fornitori (certificato, giorni alla scadenza,
  segreti presenti, problemi) con **Prova** (firma di prova verificata); registro dei
  fornitori (CSC, PKCS#11, TSA RFC 3161; sviluppo solo fuori produzione) con priorità e
  ripiego. Ogni modifica e ogni prova vanno nell'audit.
- **Manuale e piano**: «Pubblica» crea la versione da sigillare con il sigillo del
  conservatore (API `POST /api/dashboard/documenti-conservatore/{manuale|piano}/pubblica`,
  elenco e scaricamento della busta `.p7m`).

## Per chi gestisce il servizio
- Parametri nuovi (gruppo `firma_qualificata`, solo conservatore): `FIRMA_SENZA_FORNITORE`
  (NON_FIRMATO | BLOCCA), `FIRMA_LIVELLO_DOCUMENTI` (B-LT), `FIRMA_ALGORITMO_IMPRONTA`
  (sha256), `FIRMA_ALGORITMI_AMMESSI`, `FIRMA_RINNOVO_PREAVVISO_GIORNI` (90),
  `CATENA_MARCA_INTERVALLO_MINUTI` (60), `ATTESTAZIONE_SIGILLO` (DISATTIVO); vincolo:
  l'algoritmo deve essere fra quelli ammessi.
- Permesso nuovo mai delegabile: `firma_fornitori:gestisci` (anche nel vincolo dei ruoli
  personalizzati).
- Impostazioni nuove: signature `DATABASE_URL`, `FIRMA_SEGRETI_JSON`,
  `PKCS11_LIBRERIE_AMMESSE`, `FIRMA_CONFIGURAZIONE_CACHE_SECONDI`, `FIRMA_PROVE_PER_ORA`;
  onboarding `SIGNATURE_URL`; hashchain `FIRMA_DOCUMENTI_LOTTO`, `FIRMA_RINNOVO_*`.
- Codici d'errore nuovi: `FIRMA-503-CONFIGURAZIONE`, `FIRMA-502-FIRMA-NON-VALIDA`,
  `FIRMA-404-FORNITORE`, `FIRMA-403-FORNITORI`, `FIRMA-422-CONFIGURAZIONE`, `FIRMA-429-PROVA`.
- Audit nuovi: `firma.fornitore_modificato`, `firma.fornitore_provato`,
  `conservatore.documento_pubblicato`.
- Metriche e allarmi (`infra/observability/regole/verumdoc-r13.yml`): documenti non
  firmati, conservazione ferma per firma, accumulo, origine diversa, rimarcatura in errore
  o in ritardo, checkpoint non marcato.
- Rotte nuove: signature `POST /rimarca`, `GET /fornitori`, `POST /fornitori/{codice}/prova`;
  hashchain `GET /prova/inclusione?sigillo=true`; onboarding
  `/api/v1/onboarding/firma/fornitori…`.
- Simulatore per il collaudo: `python -m tools.simulatore_firma`.

## Contenuto

### VD-C-068 — firma e marca qualificate (chiusa con riserve)
- `services/signature`: interfaccia unica (`firma_impronta`), registro `CATALOGO`
  (CSC v2, PKCS#11, sviluppo; TSA RFC 3161, sviluppo), `GestoreConfigurazione` dal
  database, ripiego fra fornitori e fra TSA, verifica della firma prodotta con il
  verificatore di R08 prima di restituirla, CAdES B-T/LT/LTA (ATSv3) e PAdES B-LT/LTA,
  revoche OCSP/CRL, rimarcatura, controllo d'avvio (niente sviluppo in produzione).
- CSC v2: OAuth2, `credentials/list`, `credentials/info`, `credentials/authorize` (PIN,
  OTP statico; SAD per N firme con SCAL 1), `signatures/signHash` (anche in lotto),
  `credentials/sendOTP` e firma con OTP per persone; client HTTP con tempo massimo,
  ritentativi idempotenti, mTLS e pinning facoltativi.
- PKCS#11 con allow-list delle librerie, provato con SoftHSM2.
- Hashchain `app/registro/firme_documenti.py`: sigillo di IPdA (accodato da aip-builder),
  RdV (accodato alla conservazione), manuale e piano; origine riletta alla versione
  registrata con l'impronta riverificata; `NON_FIRMATO` recuperabile; `BLOCCA` ferma la
  conservazione; rimarcatura con lease e fencing; marca dell'ultimo checkpoint.
- `libs/shared/firma_qualificata`: campi e segreti dei fornitori (una definizione per
  console e servizio); `libs/shared/firme/asn1_etsi.py`: attributi B-LTA leggibili.

### VD-C-124 — attestazione del ciclo di vita (chiusa con riserve)
- Sigillo qualificato facoltativo sull'attestazione nella prova d'inclusione
  (`ATTESTAZIONE_SIGILLO`).

### Altro (richieste del coordinatore durante il rilascio)
- `tools/dev/genera_segreti_dev.py` genera anche la coppia X25519 della posta
  (`infra/dev_posta_priv.env`, `infra/dev_posta_pub.env`, base64url senza «=»): su Windows
  basta `python tools/dev/genera_segreti_dev.py` (README).
- Test di regressione: seed con il login dei servizi sotto RLS
  (`gateway/tests/test_r13a_seed_ruolo_servizi.py`); bucket di sviluppo con
  `LocationConstraint` fuori da us-east-1 e regione dello storage del compose uguale a
  quella dei servizi (`gateway/tests/test_r13a_bucket_regione.py`).

### Revisione (rilievi 1–7)
1. **Grave — stallo dei sigilli dopo un crash** (`hashchain/app/registro/firme_documenti.py`):
   la busta si scriveva nel WORM prima dell'aggiornamento con il lease; dopo un crash o
   un lease perso la nuova firma (CAdES non deterministica) collideva con la busta
   orfana (`ContenutoWormDiverso` non gestito) e la riga in testa alla coda bloccava
   tutti i sigilli. Ora la chiave contiene l'impronta della busta
   (`Sigillo_<nome>.<sha256[:24]>.p7m`, anche rimarcature e `registro/marche/<n>.<sha>.tsr`)
   e un errore su un documento lo rinvia con attesa crescente senza fermare il lotto.
   Test portato nel repo (`test_nessuno_stallo_dopo_scrittura_worm_senza_update`, più
   lotto e marca del checkpoint).
2. **Segreti legati agli host** (`libs/shared/firma_qualificata/configurazione.py`,
   `http_sicuro.py`): `host` nella voce di `FIRMA_SEGRETI_JSON`; un fornitore con segreti
   e un indirizzo su un altro host non si costruisce e il client rifiuta la richiesta
   prima che parta; pinning del Secret che prevale sulla console.
3. **https in produzione** per ogni fornitore con credenziali, TSA comprese.
4. **Identità attesa del certificato** (`certificati_attesi_sha256`,
   `organization_identifier`), obbligatoria in produzione, controllata sul materiale
   (CSC, PKCS#11) e sulla firma prodotta; `credential_id` obbligatorio in produzione.
5. **Migrazione 054**: UPDATE su `firme_documenti` al solo gruppo
   `verumdoc_firme_documenti_sigillo` (hashchain); aip-builder e dashboard-api solo
   SELECT/INSERT; trigger `firme_documenti_protette` (origine e impronta immutabili,
   riga FIRMATO modificabile solo nelle colonne della rimarcatura con `rinnovi`
   crescente, nessun DELETE).
6. **TSA**: un token malformato è un errore del fornitore, si passa alla TSA successiva.
7. GRANT dei gruppi ai login di produzione nella checklist (§7.2), in
   `giorno1_staging.md` e in `infra/k8s/secrets/README.md`.
Mutazioni dei rilievi 1–5: 19 su 19 uccise (`r13a/mutazioni_rev.txt`).

### Seconda revisione (3 rilievi)
1. **Media**: chi accoda poteva inserire una riga già FIRMATO con busta inventata e
   `qualificata=true`. Ora INSERT delle sole colonne d'origine e trigger
   `firme_documenti_nuove` (solo `DA_FIRMARE` senza dati del sigillo per chi non è nel
   gruppo della hashchain).
2. **Bassa**: errore persistente dopo la firma = firma pagata ogni ora. Ora la retention
   si legge prima della firma (anche nella rimarcatura) e dopo `FIRMA_TENTATIVI_MASSIMI`
   errori il documento va in `ERRORE` con allarme `DocumentiDaSigillareInErrore`.
3. **Bassa**: ogni rimarcatura registra la busta precedente in `storico_buste` (il trigger
   lo esige).
Test di ripresa di sigillo e rimarcatura interrotti portato nel repo. Mutazioni: 7 su 7
uccise (`r13a/mutazioni_rev2.txt`; una prima versione equivalente sostituita).

### Migrazione `20261107_054`
`fornitori_firma`, `firme_documenti` (RLS per ente), `firma_transizione_r13`, colonne
della marca su `catena_checkpoint` (funzione di protezione aggiornata dentro
`registri_modificabili`), vincolo dei permessi mai delegabili; GRANT minimi ai gruppi
nuovi (UPDATE dei documenti solo alla hashchain), nessun DELETE, trigger
d'immutabilità di `firme_documenti`; `lock_timeout`; downgrade rifiutato con documenti
sigillati o checkpoint marcati.

## Test
- Corsa completa (PostgreSQL 16 reale, SoftHSM2 2.6.1, simulatore CSC/TSA/OCSP/CRL):
  dopo la seconda revisione **4.205 passati, 0 falliti** (3 saltati di conformità XSD in
  aip-builder); `make verifiche` 0. Migrazione 054 su/giù/su/giù/su e offline `--sql`.
- Dopo l'unione con R12c: frontend typecheck, lint, build verdi; e2e **87 passati**
  (85 di R12c + 2 di `r13a.spec.ts`, 2 lavoratori); pagina «Fornitori di firma e marca» a
  390 e 1280 px in chiaro e scuro senza scorrimento orizzontale né sovrapposizioni, axe-core
  WCAG AA senza violazioni; `make verifiche` 0; `tools/pulizia` 4 verdi.
