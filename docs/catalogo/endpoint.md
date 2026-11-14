# Catalogo degli endpoint pubblici

<!-- File GENERATO da tools/docs/genera_openapi.py importando le app FastAPI:
     non modificarlo a mano. -->

OpenAPI unica: [`openapi.yaml`](openapi.yaml) (OpenAPI 3.1.0, versione 3.0.0). Host: `api.verumdoc.it`. Errori: [`errori.md`](errori.md).

| Servizio | Operazioni | Instradate dall'ingress |
|---|---:|---:|
| gateway | 43 | 43 |
| distribution | 18 | 18 |
| notification | 22 | 22 |
| onboarding | 68 | 68 |
| dashboard-api | 59 | 59 |
| hashchain | 14 | 14 |
| ciclo-vita | 15 | 15 |
| **Totale** | **239** | **239** |

Webhook documentati: 18.

## gateway

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/pdnd/v1/aip/{aip_id}` | E-service 2 PDND — Consultazione AIP | voucher PDND | sì |
| GET | `/api/pdnd/v1/hashchain/verify/{aip_id}` | E-service 3 PDND — Registrazione dell'AIP nella catena delle impronte | voucher PDND | sì |
| POST | `/api/pdnd/v1/versamento` | E-service 1 PDND — Versamento PA | voucher PDND | sì |
| POST | `/api/v1/auth/conferme-decisione` | Conferma firmata di una decisione sensibile del ciclo di vita | JWT, chiave API | sì |
| GET | `/api/v1/auth/credenziali` | Le mie credenziali (senza segreti) | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/codici-recupero` | Nuovi codici di recupero (mostrati una sola volta) | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/passkey` | Registra una nuova passkey | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/passkey/opzioni` | Opzioni per registrare una nuova passkey | JWT, chiave API | sì |
| DELETE | `/api/v1/auth/credenziali/passkey/{passkey_id}` | Revoca una passkey (mai l'ultima utilizzabile) | JWT, chiave API | sì |
| PATCH | `/api/v1/auth/credenziali/passkey/{passkey_id}` | Rinomina una passkey | JWT, chiave API | sì |
| PUT | `/api/v1/auth/credenziali/password` | Cambia la password (verifica della vecchia) | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/riautenticazione` | Conferma dell'identità per modificare le credenziali | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/riautenticazione/passkey/opzioni` | Sfida per confermare con una passkey | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/totp` | Nuovo segreto del codice a tempo (mostrato una sola volta) | JWT, chiave API | sì |
| POST | `/api/v1/auth/credenziali/totp/conferma` | Attiva il nuovo codice a tempo | JWT, chiave API | sì |
| POST | `/api/v1/auth/inviti/completa` | Completa l'attivazione: credenziali e utente attivo | nessuna | sì |
| POST | `/api/v1/auth/inviti/passkey/opzioni` | Opzioni di registrazione della passkey | nessuna | sì |
| POST | `/api/v1/auth/inviti/totp` | Segreto del codice a tempo da registrare nell'app | nessuna | sì |
| POST | `/api/v1/auth/inviti/verifica` | Apre l'attivazione guidata di un invito | nessuna | sì |
| POST | `/api/v1/auth/locale/accesso` | Accesso con e-mail e password (primo passo) | nessuna | sì |
| POST | `/api/v1/auth/locale/secondo-fattore` | Codice a tempo o di recupero → sessione | nessuna | sì |
| GET | `/api/v1/auth/metodi` | Metodi d'accesso abilitati dal conservatore | nessuna | sì |
| POST | `/api/v1/auth/passkey/accesso` | Accesso con passkey | nessuna | sì |
| POST | `/api/v1/auth/passkey/opzioni` | Sfida per l'accesso con passkey | nessuna | sì |
| GET | `/api/v1/auth/sessione` | Utente della sessione corrente (senza token) | JWT, chiave API | sì |
| DELETE | `/api/v1/auth/sessione` | Uscita: chiude la sessione lato server e cancella i cookie | nessuna | sì |
| GET | `/api/v1/auth/spid/callback` | Callback SPID/CIE — verifica l'identità e torna al frontend con un codice monouso | nessuna | sì |
| GET | `/api/v1/auth/spid/login` | Avvia flusso SPID/CIE | nessuna | sì |
| GET | `/api/v1/auth/spid/providers` | Lista provider SPID disponibili | nessuna | sì |
| POST | `/api/v1/auth/spid/scambio` | Scambia il codice monouso SPID/CIE con il token di sessione | nessuna | sì |
| POST | `/api/v1/caricamenti` | Apre una sessione di caricamento | JWT, chiave API | sì |
| GET | `/api/v1/caricamenti/{caricamento_id}` | Stato della sessione di caricamento | JWT, chiave API | sì |
| DELETE | `/api/v1/caricamenti/{caricamento_id}` | Annulla la sessione ed elimina i file | JWT, chiave API | sì |
| POST | `/api/v1/caricamenti/{caricamento_id}/completa` | Chiede la verifica del file (fuori dalla richiesta) | JWT, chiave API | sì |
| PUT | `/api/v1/caricamenti/{caricamento_id}/parti/{numero}` | Invia una parte (corpo binario) | JWT, chiave API | sì |
| GET | `/api/v1/health/ready` | Readiness | nessuna | sì |
| GET | `/api/v1/info` | Info | nessuna | sì |
| GET | `/api/v1/lifecycle/scadenze` | AIP con scadenza imminente | JWT, chiave API | sì |
| POST | `/api/v1/lifecycle/{aip_id}/avvia` | Avvio di uno scenario del ciclo di vita (dismesso: vedi /api/v1/ciclo-vita) | JWT, chiave API | sì |
| GET | `/api/v1/me` | Identità della richiesta: ente, piano, chiave API | JWT, chiave API | sì |
| POST | `/api/v1/versamento/ud` | Versamento Unità Documentaria — LG AGID 2022 | JWT, chiave API | sì |
| GET | `/api/v1/versamento/{pdv_id}` | Stato PdV | JWT, chiave API | sì |
| GET | `/api/v1/versamento/{pdv_id}/rdv` | Download RdV definitivo | JWT, chiave API | sì |

## distribution

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/v2/distribution/aip/{aip_id}` | Info AIP distribuibile | JWT, chiave API | sì |
| GET | `/api/v2/distribution/pdd/{pdd_id}` | Stato e collegamenti di un PdD | JWT, chiave API | sì |
| POST | `/api/v2/distribution/pdd/{pdd_id}/rigenera` | Rigenera un PdD scaduto (R07, D18) | JWT, chiave API | sì |
| POST | `/api/v2/distribution/richiedi` | Richiedi Pacchetto di Distribuzione | JWT, chiave API | sì |
| POST | `/api/v2/distribution/search` | Ricerca AIP per distribuzione | JWT, chiave API | sì |
| GET | `/api/v2/distribution/storico` | Storico richieste distribuzione del tenant | JWT, chiave API | sì |
| POST | `/api/v2/distribution/{pdd_id}/approva` | Approva richiesta PdD in attesa | JWT, chiave API | sì |
| POST | `/api/v2/distribution/{pdd_id}/rifiuta` | Rifiuta richiesta PdD in attesa | JWT, chiave API | sì |
| GET | `/api/v2/fascicoli` | Elenco dei fascicoli dell'ente | JWT | sì |
| POST | `/api/v2/fascicoli` | Crea fascicolo informatico | JWT | sì |
| GET | `/api/v2/fascicoli/{fascicolo_id}` | Dettaglio fascicolo | JWT | sì |
| GET | `/api/v2/fascicoli/{fascicolo_id}/aip` | Lista AIP nel fascicolo | JWT | sì |
| POST | `/api/v2/fascicoli/{fascicolo_id}/aip` | Aggiungi AIP al fascicolo | JWT | sì |
| POST | `/api/v2/fascicoli/{fascicolo_id}/chiudi` | Chiudi il fascicolo (data di chiusura per il termine di scarto) | JWT | sì |
| GET | `/api/v2/info` | Info | nessuna | sì |
| POST | `/api/v2/migration/export-batch` | Prepara la migrazione di più AIP | JWT | sì |
| POST | `/api/v2/migration/export/{aip_id}` | Prepara il pacchetto di migrazione di un AIP | JWT | sì |
| GET | `/api/v2/migration/status/{job_id}` | Stato del pacchetto di migrazione | JWT | sì |

## notification

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/v1/eventi` | Eventi dell'ente dopo un cursore | JWT, chiave API | sì |
| GET | `/api/v1/notifiche/campanella` | Da fare per te e avvisi recenti | JWT | sì |
| POST | `/api/v1/notifiche/campanella/letti` | Segna come letti | JWT | sì |
| GET | `/api/v1/notifiche/pec/canali` | Canali PEC: registro, stato e tipi | JWT (ruolo RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| PUT | `/api/v1/notifiche/pec/canali/{codice}` | Registra o modifica un canale PEC | JWT (ruolo RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| POST | `/api/v1/notifiche/pec/canali/{codice}/prova` | Prova d'invio: accoda un messaggio di prova sul canale | JWT (ruolo RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| GET | `/api/v1/notifiche/pec/messaggi` | Messaggi PEC, dal più recente, a cursore | JWT (ruolo ADMIN_TENANT, RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| GET | `/api/v1/notifiche/pec/messaggi/{messaggio_id}` | Messaggio PEC con le sue ricevute | JWT (ruolo ADMIN_TENANT, RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| GET | `/api/v1/notifiche/pec/ricevute` | Messaggi della casella non associati o non autentici | JWT (ruolo RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| GET | `/api/v1/notifiche/pec/ricevute/{ricevuta_id}/eml` | Messaggio grezzo di una ricevuta (evidenza) | JWT (ruolo ADMIN_TENANT, RSC_CONSERVATORE, SUPER_ADMIN) | sì |
| GET | `/api/v1/notifiche/preferenze` | Cosa ricevere e come | JWT | sì |
| PUT | `/api/v1/notifiche/preferenze` | Salva cosa ricevere e come | JWT | sì |
| GET | `/api/v1/webhook` | Webhook dell'ente | JWT (ruolo ADMIN_TENANT) | sì |
| POST | `/api/v1/webhook` | Crea un webhook (il segreto si vede solo ora) | JWT (ruolo ADMIN_TENANT) | sì |
| GET | `/api/v1/webhook/tipi` | Tipi di evento sottoscrivibili | JWT (ruolo ADMIN_TENANT) | sì |
| GET | `/api/v1/webhook/{webhook_id}` | Dettaglio di un webhook | JWT (ruolo ADMIN_TENANT) | sì |
| DELETE | `/api/v1/webhook/{webhook_id}` | Elimina un webhook | JWT (ruolo ADMIN_TENANT) | sì |
| PATCH | `/api/v1/webhook/{webhook_id}` | Modifica un webhook | JWT (ruolo ADMIN_TENANT) | sì |
| GET | `/api/v1/webhook/{webhook_id}/consegne` | Consegne di un webhook (stato=FALLITA: coda di scarto) | JWT (ruolo ADMIN_TENANT) | sì |
| POST | `/api/v1/webhook/{webhook_id}/consegne/{consegna_id}/riprova` | Ripete una consegna FALLITA | JWT (ruolo ADMIN_TENANT) | sì |
| POST | `/api/v1/webhook/{webhook_id}/segreto/chiudi-transizione` | Ritira subito il segreto precedente | JWT (ruolo ADMIN_TENANT) | sì |
| POST | `/api/v1/webhook/{webhook_id}/segreto/ruota` | Ruota il segreto HMAC (il nuovo si vede solo ora) | JWT (ruolo ADMIN_TENANT) | sì |

## onboarding

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/v1/configurazione/accordi` | Accordi di versamento (ultima versione, a cursore) | JWT | sì |
| GET | `/api/v1/configurazione/bozze` | Bozze (a cursore) | JWT | sì |
| POST | `/api/v1/configurazione/bozze` | Crea una bozza di modifica | JWT | sì |
| GET | `/api/v1/configurazione/bozze/{bozza_id}` | Dettaglio di una bozza | JWT | sì |
| DELETE | `/api/v1/configurazione/bozze/{bozza_id}` | Annulla una bozza | JWT | sì |
| PATCH | `/api/v1/configurazione/bozze/{bozza_id}` | Aggiorna una bozza (con la versione letta) | JWT | sì |
| POST | `/api/v1/configurazione/bozze/{bozza_id}/anteprima` | Anteprima: piano e impatto (non scrive) | JWT | sì |
| POST | `/api/v1/configurazione/bozze/{bozza_id}/applica` | Applica la bozza (impronta del piano) | JWT | sì |
| GET | `/api/v1/configurazione/formati` | Catalogo base dei formati (PUID, MIME, esito predefinito) | JWT | sì |
| GET | `/api/v1/configurazione/impostazioni` | Impostazioni effettive con origine | JWT | sì |
| GET | `/api/v1/configurazione/profili` | Profili di metadati per tipologia e per classe | JWT | sì |
| GET | `/api/v1/configurazione/profili/{profilo_id}/versioni` | Versioni di un profilo | JWT | sì |
| GET | `/api/v1/configurazione/profilo-validatore` | Profilo esportato per il validatore senza rete | JWT, chiave API | sì |
| GET | `/api/v1/configurazione/proposte` | Proposte del conservatore agli enti | JWT | sì |
| POST | `/api/v1/configurazione/proposte/{proposta_id}/accetta` | Accetta una proposta | JWT | sì |
| POST | `/api/v1/configurazione/proposte/{proposta_id}/rifiuta` | Rifiuta una proposta | JWT | sì |
| GET | `/api/v1/configurazione/regole` | Catalogo delle regole con l'effetto effettivo | JWT | sì |
| POST | `/api/v1/configurazione/valida` | Validatore preventivo (stessi rilievi del versamento) | JWT, chiave API | sì |
| GET | `/api/v1/onboarding/accesso/fornitori` | Fornitori d'identità | JWT | sì |
| PUT | `/api/v1/onboarding/accesso/fornitori/{tipo}` | Registra o modifica | JWT | sì |
| GET | `/api/v1/onboarding/classi-documentali` | Lista classi documentali | JWT | sì |
| POST | `/api/v1/onboarding/classi-documentali` | Crea classe documentale | JWT | sì |
| GET | `/api/v1/onboarding/classi-documentali/{classe_id}` | Dettaglio classe documentale | JWT | sì |
| PUT | `/api/v1/onboarding/classi-documentali/{classe_id}` | Aggiorna classe documentale | JWT | sì |
| POST | `/api/v1/onboarding/classi-documentali/{classe_id}/attiva` | Attiva/disattiva classe | JWT | sì |
| GET | `/api/v1/onboarding/firma/credenziali-personali` | Credenziali di firma personali (R16) | JWT | sì |
| PUT | `/api/v1/onboarding/firma/credenziali-personali/{user_id}` | Registra, cambia o disattiva la credenziale personale di una persona | JWT | sì |
| GET | `/api/v1/onboarding/firma/fornitori` | Registro dei fornitori di firma e marca | JWT | sì |
| GET | `/api/v1/onboarding/firma/fornitori/stato` | Stato, certificati e scadenze dei fornitori | JWT | sì |
| PUT | `/api/v1/onboarding/firma/fornitori/{codice}` | Registra o modifica un fornitore di firma o marca | JWT | sì |
| POST | `/api/v1/onboarding/firma/fornitori/{codice}/prova` | Prova di connessione con firma (o marca) di prova verificata | JWT | sì |
| GET | `/api/v1/onboarding/preferenze` | Preferenze personali | JWT | sì |
| PUT | `/api/v1/onboarding/preferenze` | Salva le preferenze personali | JWT | sì |
| GET | `/api/v1/onboarding/tenant` | Lista tenant | JWT | sì |
| POST | `/api/v1/onboarding/tenant` | Registra nuovo tenant | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}` | Dettaglio tenant | JWT | sì |
| DELETE | `/api/v1/onboarding/tenant/{tenant_id}` | Disattiva tenant | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/accesso` | Metodi d'accesso abilitati e ammessi per l'ente | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/api-keys` | Lista API keys del tenant | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/api-keys` | Genera nuova API key per il tenant | JWT | sì |
| DELETE | `/api/v1/onboarding/tenant/{tenant_id}/api-keys/{key_id}` | Revoca API key | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/attiva` | Attiva tenant (TRIAL -> ATTIVO) | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/classi` | Classi documentali abilitate per il tenant | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/classi` | Aggiorna classi documentali abilitate per il tenant | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/inviti` | Inviti dell'ente | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/inviti` | Invita una persona (utente non attivo + e-mail) | JWT | sì |
| DELETE | `/api/v1/onboarding/tenant/{tenant_id}/inviti/{invito_id}` | Revoca un invito in attesa | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/inviti/{invito_id}/reinvia` | Nuovo collegamento d'invito (il vecchio decade) | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/natura` | Dichiara la natura dell'ente (PA o PRIVATO) | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/ruoli` | Permessi, modelli e ruoli personalizzati dell'ente | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/ruoli` | Crea un ruolo personalizzato | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/ruoli/{ruolo_id}` | Modifica un ruolo personalizzato | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/ruoli/{ruolo_id}/disattiva` | Disattiva un ruolo non assegnato | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account` | Account SFTP dell'ente | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account` | Crea un account SFTP | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}` | Dettaglio di un account SFTP | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}` | Modifica descrizione o responsabile | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}/chiavi` | Aggiunge una chiave pubblica SSH | JWT | sì |
| DELETE | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}/chiavi/{chiave_id}` | Revoca una chiave pubblica SSH | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}/ip-ammessi` | Indirizzi da cui l'account si collega | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}/riattiva` | Riattiva un account sospeso | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/sftp/account/{account_id}/sospendi` | Sospende l'account (accesso e lavorazione) | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/stato` | Cambia stato tenant | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/stato-info` | Info stato e transizioni ammesse | JWT | sì |
| GET | `/api/v1/onboarding/tenant/{tenant_id}/utenti` | Lista utenti del tenant | JWT | sì |
| POST | `/api/v1/onboarding/tenant/{tenant_id}/utenti` | Crea utente nel tenant | JWT | sì |
| PUT | `/api/v1/onboarding/tenant/{tenant_id}/utenti/{user_id}` | Modifica utente | JWT | sì |
| DELETE | `/api/v1/onboarding/tenant/{tenant_id}/utenti/{user_id}` | Disattiva utente (soft delete) | JWT | sì |

## dashboard-api

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/dashboard/admin/tenants-stats` | Stats aggregate tenant (admin) | JWT | sì |
| GET | `/api/dashboard/aip` | Lista Aip | JWT, chiave API | sì |
| GET | `/api/dashboard/aip/{aip_id}/download/completo` | Scarica AIP completo (zip con tutti i file) — solo il proprio ente | JWT | sì |
| GET | `/api/dashboard/aip/{aip_id}/download/manifest` | Scarica manifest.json (metadati) | JWT | sì |
| GET | `/api/dashboard/aip/{aip_id}/download/rdv` | Scarica il rapporto di versamento (metadati) | JWT | sì |
| GET | `/api/dashboard/aip/{aip_id}/download/sincro` | Scarica IndiceAIP SInCRO XML (metadati) | JWT | sì |
| GET | `/api/dashboard/aip/{aip_id}/files` | Lista file dell'AIP (metadati) | JWT | sì |
| GET | `/api/dashboard/audit` | Storico audit trail — SUPER_ADMIN, RSC, ADMIN_TENANT | JWT | sì |
| GET | `/api/dashboard/classi-documentali` | Lista classi documentali | JWT | sì |
| POST | `/api/dashboard/classi-documentali` | Crea classe documentale | JWT | sì |
| PUT | `/api/dashboard/classi-documentali/{classe_id}` | Aggiorna classe documentale | JWT | sì |
| GET | `/api/dashboard/contratti` | Lista contratti di servizio del cliente | JWT | sì |
| POST | `/api/dashboard/contratti` | Registra un contratto di servizio | JWT | sì |
| POST | `/api/dashboard/distribution` | Distribution | JWT | sì |
| GET | `/api/dashboard/documentazione-servizio` | Documenti del servizio: ultima versione e dati da compilare | JWT, chiave API | sì |
| POST | `/api/dashboard/documentazione-servizio/generazioni` | Genera la documentazione del servizio (asincrona) | JWT, chiave API | sì |
| GET | `/api/dashboard/documentazione-servizio/generazioni/{generazione_id}` | Stato di una richiesta di generazione | JWT, chiave API | sì |
| GET | `/api/dashboard/documentazione-servizio/versioni` | Storico delle versioni di un documento | JWT, chiave API | sì |
| GET | `/api/dashboard/documentazione-servizio/versioni/{versione_id}` | Dettaglio di una versione | JWT, chiave API | sì |
| GET | `/api/dashboard/documentazione-servizio/versioni/{versione_id}/{formato}` | Scarica Markdown o HTML stampabile | JWT, chiave API | sì |
| GET | `/api/dashboard/documenti-conservatore` | Versioni pubblicate di manuale e piano e stato del sigillo | JWT, chiave API | sì |
| GET | `/api/dashboard/documenti-conservatore/{documento_id}/busta` | Busta CAdES (.p7m) del documento sigillato | JWT, chiave API | sì |
| POST | `/api/dashboard/documenti-conservatore/{tipo}/pubblica` | Pubblica una versione da sigillare | JWT, chiave API | sì |
| GET | `/api/dashboard/firme-responsabile` | Documenti da firmare e firmati dal responsabile | JWT, chiave API | sì |
| POST | `/api/dashboard/firme-responsabile` | Accoda un documento da firmare dal responsabile | JWT, chiave API | sì |
| POST | `/api/dashboard/firme-responsabile/documentazione/{versione_id}` | Mette in firma la versione stampabile (HTML) di un documento generato | JWT, chiave API | sì |
| POST | `/api/dashboard/firme-responsabile/{documento_id}/annulla` | Annulla un documento ancora da firmare | JWT, chiave API | sì |
| GET | `/api/dashboard/firme-responsabile/{documento_id}/busta` | Busta CAdES (.p7m) firmata dal responsabile | JWT, chiave API | sì |
| GET | `/api/dashboard/firme-responsabile/{documento_id}/contenuto` | Documento da leggere prima di firmarlo | JWT, chiave API | sì |
| POST | `/api/dashboard/firme-responsabile/{documento_id}/firma` | Firma personale con l'OTP | JWT, chiave API | sì |
| POST | `/api/dashboard/firme-responsabile/{documento_id}/otp` | Chiede l'invio dell'OTP al prestatore | JWT, chiave API | sì |
| GET | `/api/dashboard/hashchain` | Hashchain Status | JWT | sì |
| POST | `/api/dashboard/hashchain/verifica-completa` | Hashchain Verifica Completa | JWT | sì |
| GET | `/api/dashboard/hashchain/verifica-completa/{verifica_id}` | Hashchain Stato Verifica | JWT | sì |
| GET | `/api/dashboard/manuale-conservazione` | Genera il Manuale di conservazione dai dati reali (bozza non firmata) | JWT | sì |
| GET | `/api/dashboard/nomine-rsc` | Nomine del responsabile della conservazione | JWT | sì |
| POST | `/api/dashboard/nomine-rsc` | Registra la nomina del responsabile della conservazione | JWT | sì |
| GET | `/api/dashboard/notifiche` | Storico notifiche del tenant | JWT | sì |
| GET | `/api/dashboard/overview` | Overview | JWT | sì |
| GET | `/api/dashboard/pdnd/fruitori` | Fruitori PDND registrati | JWT, chiave API | sì |
| POST | `/api/dashboard/pdnd/fruitori` | Registra un fruitore PDND | JWT, chiave API | sì |
| PATCH | `/api/dashboard/pdnd/fruitori/{fruitore_id}` | Modifica un fruitore PDND | JWT, chiave API | sì |
| GET | `/api/dashboard/piano-cessazione` | Piano di Cessazione — Allegato B Det. 455/2021 | JWT | sì |
| GET | `/api/dashboard/scarti` | Messaggi delle code di scarto | JWT, chiave API | sì |
| GET | `/api/dashboard/scarti/code` | Code di scarto lette | JWT, chiave API | sì |
| POST | `/api/dashboard/scarti/rielabora/anteprima` | Anteprima di «Rielabora» (non scrive) | JWT, chiave API | sì |
| POST | `/api/dashboard/scarti/rielabora/applica` | Applica «Rielabora» (piano confermato) | JWT, chiave API | sì |
| GET | `/api/dashboard/scarti/{scarto_id}` | Dettaglio di un messaggio | JWT, chiave API | sì |
| POST | `/api/dashboard/scarti/{scarto_id}/archivia` | Archivia un messaggio scartato con motivo | JWT, chiave API | sì |
| POST | `/api/dashboard/search` | Search | JWT, chiave API | sì |
| GET | `/api/dashboard/sla/report` | Report SLA mensile | JWT | sì |
| GET | `/api/dashboard/sla/throughput` | Throughput versamenti | JWT | sì |
| GET | `/api/dashboard/tenants` | Lista Tenants | JWT | sì |
| POST | `/api/dashboard/tenants` | Crea Tenant | JWT | sì |
| POST | `/api/dashboard/tenants/{tenant_id}/attiva` | Attiva Tenant | JWT | sì |
| GET | `/api/dashboard/versamenti` | Versamenti | JWT, chiave API | sì |
| POST | `/api/dashboard/versamenti/{pdv_id}/rielabora/anteprima` | Anteprima di «Rielabora» di un proprio versamento rifiutato | JWT, chiave API | sì |
| POST | `/api/dashboard/versamenti/{pdv_id}/rielabora/applica` | Applica «Rielabora» di un proprio versamento rifiutato | JWT, chiave API | sì |
| GET | `/api/info` | Info | nessuna | sì |

## hashchain

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/v1/hashchain/checkpoint` | Ultimo checkpoint del registro | JWT | sì |
| GET | `/api/v1/hashchain/checkpoint/{dimensione}` | Checkpoint a una dimensione | JWT | sì |
| GET | `/api/v1/hashchain/prova/consistenza` | Prova di consistenza fra due checkpoint | JWT | sì |
| GET | `/api/v1/hashchain/prova/inclusione` | Prova d'inclusione documento → PdA → registro | JWT | sì |
| GET | `/api/v1/hashchain/registro/allarmi` | Allarmi del registro e del testimone (conservatore) | JWT | sì |
| GET | `/api/v1/hashchain/registro/foglie` | Hash delle foglie del registro | JWT | sì |
| GET | `/api/v1/hashchain/status` | Stato Catena | JWT | sì |
| POST | `/api/v1/hashchain/verifica-completa` | Verifica Completa | JWT | sì |
| GET | `/api/v1/hashchain/verifica-completa/{verifica_id}` | Stato Verifica Completa | JWT | sì |
| POST | `/api/v1/hashchain/verifiche` | Chiede la verifica d'integrità di PdA o di un ente | JWT | sì |
| POST | `/api/v1/hashchain/verifiche/campione` | Verifica a campione dopo un incidente di storage (conservatore) | JWT | sì |
| GET | `/api/v1/hashchain/verifiche/registro` | Registro delle verifiche d'integrità (a cursore) | JWT | sì |
| GET | `/api/v1/hashchain/verifiche/riepilogo` | Copertura del ciclo e anomalie aperte (manuale e cruscotto) | JWT | sì |
| GET | `/api/v1/hashchain/verifiche/{richiesta_id}` | Stato di una verifica su richiesta | JWT | sì |

## ciclo-vita

| Metodo | Percorso | Sintesi | Autenticazione | Ingress |
|---|---|---|---|---|
| GET | `/api/v1/ciclo-vita/conferme` | Conferma firmata delle decisioni sensibili: stato | JWT, chiave API | sì |
| GET | `/api/v1/ciclo-vita/pratiche` | Elenco delle pratiche | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/annullamento` | Annullamento logico di un versamento | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/restituzione` | Restituzione di fine contratto (export completo dell'ente) | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/rettifica` | Rettifica dei metadati (nuovo PdV collegato all'originale) | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/riversamento-ingresso` | Riversamento in ingresso da un altro conservatore | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/riversamento-uscita` | Riversamento in uscita: PdD MIGRAZIONE con manifesto | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/scarto/proposta` | Proposta di scarto: anteprima, poi applica con l'impronta del piano | JWT, chiave API | sì |
| GET | `/api/v1/ciclo-vita/pratiche/{pratica_id}` | Dettaglio di una pratica | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/{pratica_id}/allegati` | Carica un allegato della pratica (archiviato nel WORM) | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/{pratica_id}/approvazione` | Seconda approvazione di un annullamento (quattro occhi) | JWT, chiave API | sì |
| GET | `/api/v1/ciclo-vita/pratiche/{pratica_id}/oggetti` | Oggetti coinvolti in una pratica | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/{pratica_id}/riprendi` | Riprende una pratica sospesa dopo i tentativi esauriti | JWT, chiave API | sì |
| POST | `/api/v1/ciclo-vita/pratiche/{pratica_id}/transizione` | Transizione di stato con lo stato atteso | JWT, chiave API | sì |
| GET | `/api/v1/ciclo-vita/stato-automatico` | Lavoro automatico del ciclo di vita e blocchi WORM | JWT, chiave API | sì |

## Webhook

| Evento | Risorsa | Quando |
|---|---|---|
| `caricamento.completato` | caricamento | Il file della sessione di caricamento è verificato e utilizzabile nel versamento. |
| `caricamento.errore` | caricamento | La verifica del file della sessione di caricamento è fallita. |
| `catena.checkpoint` | checkpoint | Nuovo checkpoint firmato del registro delle impronte (dimensione, radice, testo della nota firmata): conservarlo permette di chiedere in seguito una prova di consistenza. |
| `ciclo_vita.avviso` | avviso | Avviso del ciclo di vita (`sottotipo`): PROPOSTA_SCARTO (proposta di scarto automatica in bozza da controllare), RIPRESA_SCARTO (scarto incompleto ripreso da solo dopo la rimozione del blocco), ANNULLAMENTO_DA_APPROVARE (serve una seconda persona dell'ente), ANNULLAMENTO_RESPINTO (annullamento non applicato perché qualcosa è cambiato prima dell'applicazione), PRATICA_RIPRESA (pratica ferma dopo ripetuti errori ripresa dal conservatore). `pratica_id` per i dettagli. |
| `idempotenza.chiave_omessa` | avviso | Nell'ultima settimana l'ente ha versato senza intestazione Idempotency-Key (`versamenti`): dopo `fino_al` quelle richieste saranno rifiutate (428 VRS-125). |
| `integrita.anomalia` | verifica_integrita | La verifica periodica dell'integrità ha trovato un'anomalia su un PdA dell'ente (esito DIVERSO, MANCANTE, RETENTION_ASSENTE o ERRORE_LETTURA ripetuto). Il conservatore è già allertato e gestisce il ripristino. |
| `pdd.pronto` | pdd | Il pacchetto di distribuzione richiesto (anche un'esibizione: tipo ESIBIZIONE) è pronto per lo scaricamento. |
| `pratica.cambio_stato` | pratica | Una pratica del ciclo di vita (rettifica, annullamento, scarto, riversamento, restituzione) è passata a un nuovo stato. Un evento per ogni passo (`versione`); dettaglio e azioni consentite dall'API delle pratiche. |
| `quota.soglia_raggiunta` | avviso | I versamenti del giorno hanno raggiunto l'80% o il 100% della quota giornaliera dell'ente (`soglia`); al 100% i successivi sono rifiutati o rallentati (`effetto`). `piano_suggerito`: piano con un limite adeguato. |
| `riversamento.pronto` | pratica | I pacchetti del riversamento in uscita o della restituzione sono pronti: manifesto e collegamenti dalla pratica. |
| `scarto.eseguito` | pratica | Lo scarto autorizzato è stato eseguito: conteggi dei PdA scartati e bloccati, impronta del verbale di scarto conservato. |
| `versamento.annullato` | versamento | Il PdV è stato annullato (annullamento logico: nessuna cancellazione prima dello scarto; un PdA conservato resta esibibile). |
| `versamento.conservato` | versamento | Il PdA del PdV è nel registro delle impronte ed è coperto da un checkpoint firmato (R04, `fase` = CONSERVATO; gli eventi dei produttori R03 hanno `fase` = PDA_SU_STORAGE). |
| `versamento.rettificato` | versamento | Una rettifica dei metadati del PdV è conservata come nuovo PdV collegato (`pdv_rettifica_id`); l'originale resta immutato. |
| `versamento.ricevuto` | versamento | Il PdV è stato preso in carico (stato RICEVUTO). |
| `versamento.rifiutato` | versamento | La verifica del PdV ha trovato errori bloccanti: il PdV non sarà conservato. |
| `versamento.staging_scaduto` | versamento | Le copie di transito del PdV rifiutato saranno eliminate alla data `scade_il`: dopo, il versamento non si potrà più rielaborare. |
| `versamento.validato` | versamento | La verifica del PdV è terminata senza errori bloccanti. |

## Instradamento da correggere

Nessuna: ogni operazione è instradata al suo servizio.

## Operazioni oscurate

Stesso metodo e percorso in più servizi: l'ingress ne raggiunge uno solo; le altre restano interne.

| Metodo | Percorso | Servizio | Raggiunto invece |
|---|---|---|---|
| GET | `/api/v1/info` | hashchain | gateway |
| GET | `/api/v1/info` | notification | gateway |
| GET | `/api/v1/info` | onboarding | gateway |

## Senza autenticazione dichiarata

Operazioni la cui rotta non dichiara intestazioni o schemi di autenticazione (pubbliche per scelta: salute, informazioni, accesso SPID).

- GET `/api/info` (dashboard-api)
- POST `/api/v1/auth/inviti/completa` (gateway)
- POST `/api/v1/auth/inviti/passkey/opzioni` (gateway)
- POST `/api/v1/auth/inviti/totp` (gateway)
- POST `/api/v1/auth/inviti/verifica` (gateway)
- POST `/api/v1/auth/locale/accesso` (gateway)
- POST `/api/v1/auth/locale/secondo-fattore` (gateway)
- GET `/api/v1/auth/metodi` (gateway)
- POST `/api/v1/auth/passkey/accesso` (gateway)
- POST `/api/v1/auth/passkey/opzioni` (gateway)
- DELETE `/api/v1/auth/sessione` (gateway)
- GET `/api/v1/auth/spid/callback` (gateway)
- GET `/api/v1/auth/spid/login` (gateway)
- GET `/api/v1/auth/spid/providers` (gateway)
- POST `/api/v1/auth/spid/scambio` (gateway)
- GET `/api/v1/health/ready` (gateway)
- GET `/api/v1/info` (gateway)
- GET `/api/v2/info` (distribution)
