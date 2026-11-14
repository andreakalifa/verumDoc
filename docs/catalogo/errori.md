# Catalogo dei codici d'errore

<!-- File GENERATO da tools/docs/genera_catalogo_errori.py a partire da
     libs/shared/errori/catalogo.yaml: non modificarlo a mano. -->

Versione del catalogo: **2026.10.21** — 440 codici in 39 famiglie.

## Formato della risposta

Tutti i servizi rispondono agli errori con `application/problem+json` (RFC 9457):

```json
{
  "type": "https://docs.verumdoc.it/errori/VRS-102",
  "title": "Impronta del file errata",
  "status": 400,
  "detail": "Atteso: ab12… | Calcolato: cd34…",
  "instance": "/api/v1/versamento/ud",
  "codice": "VRS-102",
  "esito": "NON_VALIDO",
  "ritentabile": false,
  "dettagli": {},
  "campi": [],
  "id_richiesta": "5f0c2d…",
  "messaggio": "Impronta del file errata"
}
```

- `codice` è stabile e va usato dai programmi; `title` e `detail` sono per le persone.
- `campi` elenca gli errori per campo (`campo`, `posizione`, `tipo`, `messaggio`).
- `id_richiesta` è anche nell'intestazione `X-Request-ID`: citarlo nelle segnalazioni.
- `Retry-After` accompagna gli errori temporanei che prevedono un'attesa.
- `messaggio` ripete `title` per i client del formato precedente.

### Esiti

| Esito | Significato |
|---|---|
| `NON_VALIDO` | Colpa della richiesta o del pacchetto: correggere e reinviare. |
| `RIFIUTATO` | Regola o policy: ritentare la stessa richiesta non serve. |
| `ERRORE` | Problema del servizio o temporaneo: ritentare solo se `ritentabile`. |

I codici con contesto *asincrono* non sono risposte HTTP: compaiono in `errori[]`
dello stato del PdV; lo stato HTTP indicato è solo di classificazione.

I codici *dismessi* non sono più emessi: restano elencati perché un codice
pubblicato non cambia significato e non si riusa; la voce indica i sostituti.

## Famiglie

| Famiglia | Descrizione | Codici |
|---|---|---|
| [VRS](#vrs) | Versamento sincrono di un pacchetto di versamento (PdV) al gateway e consultazione del suo stato e del rapporto di versamento. | 32 |
| [VLD](#vld) | Verifica asincrona del PdV (passi della pipeline di validazione). I codici compaiono in `errori[]` dello stato del PdV, non come risposta HTTP. | 39 |
| [AIP](#aip) | Costruzione asincrona del pacchetto di archiviazione (PdA). I codici compaiono in `errori[]` dello stato del PdV. | 12 |
| [SEC](#sec) | Controlli di sicurezza sui dati in ingresso (nomi, testi, estensioni, indirizzi). | 6 |
| [AUTH](#auth) | Autenticazione e autorizzazione (token JWT, chiavi API, revoca, ruoli e scope). | 13 |
| [SPID](#spid) | Accesso al portale con SPID o CIE tramite aggregatore. | 12 |
| [PDND](#pdnd) | E-service esposti su PDND Interoperabilità (voucher, fruitori, versamento ed esibizione). | 13 |
| [RATE](#rate) | Limiti di frequenza delle richieste. | 1 |
| [LC](#lc) | Ciclo di vita dei pacchetti conservati (scarto, rettifica, migrazione). | 43 |
| [HC](#hc) | Catena delle impronte (hashchain) e sua verifica. | 4 |
| [DST](#dst) | Distribuzione ed esibizione (pacchetti di distribuzione, ricerca). | 12 |
| [FSC](#fsc) | Fascicoli e aggregazioni documentali. | 10 |
| [MIG](#mig) | Migrazione e riversamento dei pacchetti. | 6 |
| [DL](#dl) | Scaricamento dei pacchetti, dei file e dei rapporti dal portale. | 9 |
| [DSH](#dsh) | API del portale (dashboard) e inoltro verso i servizi interni. | 11 |
| [GOV](#gov) | Governo del servizio dal portale (clienti, contratti, nomine). | 9 |
| [SLA](#sla) | Rapporti sui livelli di servizio. | 2 |
| [SIG](#sig) | API del servizio di firma e marca (autenticazione e forma della richiesta). | 5 |
| [FIRMA](#firma) | Firma qualificata e marca temporale (adattatori verso i fornitori). | 16 |
| [ONB](#onb) | Attivazione e gestione dei clienti, degli utenti, delle chiavi API e delle classi documentali. | 11 |
| [S3](#s3) | Storage conservativo (Object Lock / WORM). | 7 |
| [VAL](#val) | Forma della richiesta non conforme allo schema dell'API (campi mancanti, tipi errati). | 1 |
| [HTTP](#http) | Errori HTTP generici senza codice di dominio (rotta inesistente, metodo non ammesso, errori dei servizi che non indicano un codice). | 16 |
| [INTERNAL](#internal) | Errori interni non previsti. Il dettaglio resta nei log, il client riceve solo l'identificativo della richiesta. | 1 |
| [UPL](#upl) | Sessioni di caricamento dei file (caricamento in streaming e a parti, ripresa). | 18 |
| [SFTP](#sftp) | Canale di versamento via SFTP (cartelle di deposito, esiti, ricevute). | 16 |
| [WH](#wh) | Webhook firmati verso i sistemi dei clienti (sottoscrizioni, consegne, firme). | 16 |
| [EVT](#evt) | Elenco degli eventi consultabile dai clienti (cursore, filtri). | 2 |
| [CMP](#cmp) | Adattatori di compatibilità, tolti in R18.2 (VD-P-191). Nessun codice della famiglia è più emesso; restano elencati perché un codice pubblicato non si riusa. | 12 |
| [RGL](#rgl) | Motore delle regole (metadati per classe, severità, versioni delle regole). | 14 |
| [CFG](#cfg) | Configurazione del servizio per conservatore ed enti (impostazioni, regole, profili di metadati, accordi di versamento, quote) con bozze, anteprima, applicazione e proposte; validatore preventivo (R05). | 11 |
| [QTA](#qta) | Quote e limiti di consumo per piano contrattuale. | 2 |
| [CAT](#cat) | Registro delle impronte a due livelli (checkpoint firmati, prove d'inclusione e di consistenza, contratto K1 di R04). | 8 |
| [TST](#tst) | Testimone indipendente della catena delle impronte (controfirma dei checkpoint del registro, protocollo ispirato a C2SP tlog-witness). | 11 |
| [IDEM](#idem) | Idempotency-Key delle richieste che creano una risorsa fuori dal versamento (richiesta di un PdD, creazione di un webhook). | 3 |
| [INT](#int) | Verifica continua dell'integrità dell'archivio (rilettura periodica e su richiesta degli oggetti conservati, registro delle verifiche). | 6 |
| [ACC](#acc) | R10 (D08): accesso delle persone con password e codice a tempo o con passkey, inviti per e-mail con attivazione guidata, fornitori d'identità e metodi d'accesso degli enti. | 19 |
| [RUO](#ruo) | R10 (D30): ruoli personalizzati dell'ente composti dai permessi concedibili. | 3 |
| [NTF](#ntf) | R10 (D32): campanella «da fare per te» e preferenze delle notifiche. | 8 |

## VRS

Versamento sincrono di un pacchetto di versamento (PdV) al gateway e consultazione del suo stato e del rapporto di versamento.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `VRS-001` | 400 | NON_VALIDO | no | Indice del versamento non valido | L'indice dell'unità documentaria non è un JSON valido o non rispetta lo schema. | Correggere l'indice secondo lo schema pubblicato e reinviare. |
| `VRS-010` | 413 | NON_VALIDO | no | File troppo grande | Un file supera la dimensione massima ammessa per un singolo file. | Ridurre il file o usare il canale previsto per i file grandi. |
| `VRS-101` | 400 | NON_VALIDO | no | File dichiarato ma non allegato | L'indice dichiara un file che non è presente fra gli allegati. | Allegare il file dichiarato o toglierlo dall'indice. |
| `VRS-102` | 400 | NON_VALIDO | no | Impronta del file errata | L'impronta calcolata sul file ricevuto non corrisponde a quella dichiarata nell'indice. | Ricalcolare l'impronta sul file effettivamente inviato e reinviare. |
| `VRS-103` | 400 | NON_VALIDO | no | Dismesso: vedi VRS-130 (rilievo RGL-101) | *Dismesso: non più emesso, vedi `VRS-130`, `RGL-101`.* Il documento principale non contiene i metadati obbligatori dell'Allegato 5. Da R03 i metadati minimi li verifica la regola RGL-101 e il rifiuto è VRS-130 (422) con l'elenco dei rilievi. | Gestire VRS-130 e leggere `dettagli.rilievi[]`. |
| `VRS-104` | 400 | NON_VALIDO | no | File allegati non dichiarati | Sono stati allegati file che l'indice non dichiara. | Dichiarare ogni file nell'indice o toglierlo dagli allegati. |
| `VRS-105` | 422 | RIFIUTATO | no | Classe documentale non censita | La classe documentale indicata non esiste per il cliente. | Usare una classe censita o chiederne la configurazione al conservatore. |
| `VRS-106` | 422 | RIFIUTATO | no | Classe documentale non attiva | La classe documentale esiste ma non è attiva per il versamento. | Chiedere l'attivazione della classe o usarne un'altra. |
| `VRS-107` | 400 | NON_VALIDO | no | Algoritmo d'impronta non ammesso | L'algoritmo d'impronta dichiarato non è fra quelli ammessi. | Usare uno degli algoritmi elencati in `dettagli.ammessi`. |
| `VRS-108` | 422 | RIFIUTATO | no | File eseguibile non ammesso | Il contenuto del file è un eseguibile; gli eseguibili non si conservano, qualunque sia il nome. | Togliere il file dal versamento. |
| `VRS-109` | 422 | NON_VALIDO | no | Formato del file non coerente | Il formato rilevato dal contenuto non è riconosciuto o non corrisponde a quello dichiarato. | Dichiarare il formato reale del file o convertirlo in un formato ammesso. |
| `VRS-110` | 400 | NON_VALIDO | no | Dato d'ingresso non valido | Un dato della richiesta non ha superato i controlli d'ingresso. | Correggere il dato indicato nel dettaglio e reinviare. |
| `VRS-111` | 400 | NON_VALIDO | no | Nome file ripetuto | Lo stesso nome file compare più volte nell'indice o fra gli allegati. | Dare a ogni file un nome univoco nel versamento. |
| `VRS-112` | 422 | RIFIUTATO | no | Dismesso: vedi VRS-130 (rilievo RGL-105) | *Dismesso: non più emesso, vedi `VRS-130`, `RGL-105`.* Il formato del file non è fra quelli ammessi dalla classe documentale. Da R03 lo verifica la regola RGL-105 e il rifiuto è VRS-130 (422) con il rilievo. | Gestire VRS-130 e leggere `dettagli.rilievi[]`. |
| `VRS-113` | 413 | NON_VALIDO | no | Troppi file nel versamento | Il versamento supera il numero massimo di file ammesso. | Dividere l'unità documentaria o usare una sessione di caricamento. |
| `VRS-114` | 400 | NON_VALIDO | no | Documenti annidati non ammessi | L'indice contiene documenti dentro il campo `allegati` di un documento. Il modello dell'indice è piatto (R03 f1); prima questi documenti erano accettati e ignorati senza avviso. | Elencare allegati e annessi direttamente in `documenti`, con `tipo` ALLEGATO, ANNESSO o ANNOTAZIONE. |
| `VRS-120` | 409 | RIFIUTATO | no | Versamento duplicato | Lo stesso indice è già stato versato e il PdV esistente non è stato rifiutato. | Consultare il PdV indicato in `dettagli.pdv_id_esistente`; non reinviare. |
| `VRS-121` | 409 | NON_VALIDO | no | Chiave di idempotenza già usata | La Idempotency-Key è già stata usata con un contenuto diverso. | Usare una nuova chiave per un versamento diverso. |
| `VRS-122` | 400 | NON_VALIDO | no | Chiave di idempotenza non valida | La Idempotency-Key non rispetta il formato ammesso. | Usare da 8 a 200 caratteri fra lettere, cifre e i segni . _ : - |
| `VRS-123` | 409 | ERRORE | sì | Versamento in conflitto | Un altro versamento con la stessa chiave è stato registrato nello stesso istante. | Ripetere la richiesta con la stessa chiave per leggerne l'esito. |
| `VRS-124` | 409 | RIFIUTATO | no | Chiave dell'unità documentaria già usata | Registro, anno e numero dell'indice identificano già un altro versamento attivo dello stesso cliente con un contenuto diverso (VD-C-095). Un PdV rifiutato libera la chiave. | Verificare il PdV indicato in `dettagli.pdv_id_esistente`; per un'unità documentaria diversa usare un'altra chiave. |
| `VRS-125` | 428 | NON_VALIDO | no | Idempotency-Key obbligatoria | Il versamento è arrivato senza l'intestazione Idempotency-Key dopo la fine del periodo di transizione (VD-P-27). | Inviare un identificativo univoco per ogni versamento nell'intestazione Idempotency-Key, riusandolo sui ritentativi dello stesso versamento. |
| `VRS-126` | 428 | NON_VALIDO | no | Versamento senza Idempotency-Key (avviso) | Avviso, non errore (compare fra gli `avvisi` della risposta 202; lo stato 428 è quello che riceverà la stessa richiesta dopo la transizione, VRS-125). Durante il periodo di transizione un versamento senza Idempotency-Key è accettato con una chiave derivata dal contenuto; la risposta porta le intestazioni Deprecation e Sunset e questo codice fra gli `avvisi`. | Inviare l'intestazione Idempotency-Key prima della data indicata nell'intestazione Sunset. |
| `VRS-127` | 409 | RIFIUTATO | no | Versamento già concluso con esito negativo | La richiesta ripete (stessa Idempotency-Key, o stesso contenuto con la chiave derivata dei canali di compatibilità) un versamento il cui PdV è stato rifiutato dalla verifica, è in errore o è annullato. Il PdV non è conservato; la risposta indica il PdV e il suo stato (R03 f1). | Leggere lo stato del PdV in `dettagli.link`; correggere e versare di nuovo con un contenuto corretto e, se la si usa, una nuova Idempotency-Key. Per un errore tecnico chiedere al conservatore la rielaborazione del PdV. |
| `VRS-130` | 422 | RIFIUTATO | no | Versamento non conforme alle regole di conservazione | Il motore delle regole ha trovato uno o più rilievi bloccanti (metadati minimi o obbligatori mancanti, tipi dei metadati del profilo, dimensione o formato non ammessi dalla classe). L'elenco completo è in `dettagli.rilievi` (codice della regola RGL-nnn, campo, documento, messaggio). | Correggere tutti i rilievi elencati e reinviare. |
| `VRS-403` | 403 | RIFIUTATO | no | Cliente o utente non identificabili | Il token non indica un cliente o un utente validi per il versamento. | Ottenere un token emesso per un utente o una chiave del cliente. |
| `VRS-404` | 404 | NON_VALIDO | no | PdV non trovato | Il PdV non esiste o non appartiene al cliente del token. | Verificare l'identificativo del PdV. |
| `VRS-409` | 409 | ERRORE | sì | Rapporto di versamento non ancora disponibile | Il rapporto di versamento definitivo si emette quando il PdV è conservato. | Riprovare quando lo stato del PdV è CONSERVATO. |
| `VRS-500` | 503 | ERRORE | sì (dopo 30 s) | Storage temporaneamente non disponibile | Lo storage dei file non ha accettato la scrittura; nessun dato è stato registrato. | Ripetere il versamento con la stessa Idempotency-Key. |
| `VRS-501` | 503 | ERRORE | sì (dopo 30 s) | Database temporaneamente non disponibile | Il database non ha risposto; nessun dato è stato registrato. | Ripetere il versamento con la stessa Idempotency-Key. |
| `VRS-502` | 502 | ERRORE | no | Rapporto di versamento non integro | Il rapporto letto dallo storage non corrisponde all'impronta registrata. | Segnalare al conservatore indicando l'identificativo della richiesta. |
| `VRS-503` | 503 | ERRORE | sì (dopo 30 s) | Storage temporaneamente non disponibile | Lo storage non ha restituito il rapporto di versamento. | Riprovare tra qualche minuto. |

## VLD

Verifica asincrona del PdV (passi della pipeline di validazione). I codici compaiono in `errori[]` dello stato del PdV, non come risposta HTTP.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `VLD-100` | 500 | ERRORE | sì | Errore interno nel passo 1 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista (codice VLD-<passo>00). | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-200` | 500 | ERRORE | sì | Errore interno nel passo 2 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-300` | 500 | ERRORE | sì | Errore interno nel passo 3 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-400` | 500 | ERRORE | sì | Errore interno nel passo 4 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-500` | 500 | ERRORE | sì | Errore interno nel passo 5 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-1000` | 500 | ERRORE | sì | Errore interno nel passo 10 della verifica | *Asincrono.* Il riconoscimento del formato è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-1001` | 422 | RIFIUTATO | no | Eseguibile riconosciuto dal contenuto | *Asincrono.* Un file del PdV è un eseguibile o uno script (firma binaria), qualunque sia il nome (R08, D02). | Togliere il file dal versamento. |
| `VLD-1002` | 422 | RIFIUTATO | no | Formato rifiutato dal catalogo dei formati | *Asincrono.* Il passo 10 della verifica ha identificato (PRONOM, veraPDF) un formato con esito RIFIUTATO nel catalogo fotografato al versamento; i rilievi RGL-107 sono in `dettagli.rilievi`. | Convertire il file in un formato ammesso e versare di nuovo. |
| `VLD-1100` | 500 | ERRORE | sì | Errore interno nel passo 11 della verifica | *Asincrono.* La verifica delle firme è terminata con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-1101` | 422 | RIFIUTATO | no | Firme insufficienti per il livello richiesto | *Asincrono.* Il passo 11 della verifica ha trovato firme che non soddisfano il livello della classe (RGL-109 bloccante); il rapporto di verifica con l'esito di ogni firma è in `dettagli.rapporto`. | Versare i documenti con firme valide al livello richiesto. |
| `VLD-600` | 500 | ERRORE | sì | Errore interno nel passo 6 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-700` | 500 | ERRORE | sì | Errore interno nel passo 7 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-800` | 500 | ERRORE | sì | Errore interno nel passo 8 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-900` | 500 | ERRORE | sì | Errore interno nel passo 9 della verifica | *Asincrono.* Il passo di verifica è terminato con un'eccezione non prevista. | Segnalare al conservatore; il PdV sarà riverificato. |
| `VLD-101` | 422 | NON_VALIDO | no | Classe documentale mancante nell'indice | *Asincrono.* L'indice non indica la classe documentale. | Indicare la classe documentale e versare di nuovo. |
| `VLD-102` | 422 | NON_VALIDO | no | Nessun documento nell'indice | *Asincrono.* L'indice non elenca alcun documento. | Elencare i documenti dell'unità documentaria e versare di nuovo. |
| `VLD-103` | 422 | NON_VALIDO | no | Campo obbligatorio mancante nel documento | *Asincrono.* Un documento dell'indice manca di un campo obbligatorio. | Completare il campo indicato e versare di nuovo. |
| `VLD-104` | 422 | NON_VALIDO | no | Impronta incompleta | *Asincrono.* L'impronta di un documento non indica valore e algoritmo. | Indicare impronta e algoritmo e versare di nuovo. |
| `VLD-201` | 422 | NON_VALIDO | no | Documenti duplicati | *Asincrono.* Lo stesso documento compare più volte nell'unità documentaria. | Togliere i duplicati e versare di nuovo. |
| `VLD-202` | 422 | NON_VALIDO | no | Nome file duplicato | *Asincrono.* Due documenti hanno lo stesso nome file. | Dare nomi univoci ai file e versare di nuovo. |
| `VLD-301` | 422 | NON_VALIDO | no | Documento principale mancante | *Asincrono.* L'unità documentaria non ha un documento principale. | Indicare il documento principale e versare di nuovo. |
| `VLD-302` | 422 | NON_VALIDO | no | Più documenti principali | *Asincrono.* L'unità documentaria ha più di un documento principale. | Lasciare un solo documento principale e versare di nuovo. |
| `VLD-401` | 422 | RIFIUTATO | no | Formato non ammesso | *Asincrono.* Uno o più documenti hanno un formato non ammesso per la conservazione. | Convertire i file in un formato ammesso e versare di nuovo. |
| `VLD-501` | 422 | NON_VALIDO | no | Verifica delle impronte fallita | *Asincrono.* L'impronta ricalcolata di uno o più file non corrisponde a quella dichiarata. | Verificare i file e le impronte e versare di nuovo. |
| `VLD-502` | 422 | NON_VALIDO | no | File nello storage diverso da quello versato | *Asincrono.* Il file letto dallo storage di transito non ha la dimensione o le impronte misurate dal gateway durante il versamento (file alterato o sostituito dopo la ricezione); l'elenco è in `dettagli.errori`. | Versare di nuovo l'unità documentaria; se si ripete, segnalare al conservatore. |
| `VLD-601` | 422 | NON_VALIDO | no | Metadati non conformi | *Asincrono.* I metadati dell'Allegato 5 non sono conformi; l'elenco per campo è in `dettagli.errori`. | Correggere i campi indicati e versare di nuovo. |
| `VLD-701` | 422 | NON_VALIDO | no | Classe documentale mancante | *Asincrono.* La classe documentale non è indicata. | Indicare la classe documentale e versare di nuovo. |
| `VLD-702` | 422 | RIFIUTATO | no | Classe documentale non attiva | *Asincrono.* La classe documentale non è attiva per il cliente. | Chiedere l'attivazione della classe al conservatore. |
| `VLD-703` | 422 | RIFIUTATO | no | Classe documentale non censita | *Asincrono.* La classe documentale non è censita per il cliente. | Chiedere la configurazione della classe al conservatore. |
| `VLD-705` | 422 | ERRORE | no | Identificativo del cliente non valido | *Asincrono.* Il PdV porta un identificativo del cliente non valido. | Segnalare al conservatore. |
| `VLD-801` | 422 | RIFIUTATO | no | File oltre il limite di dimensione | *Asincrono.* Uno o più file superano la dimensione massima ammessa. | Ridurre i file o concordare il limite col conservatore. |
| `VLD-802` | 500 | ERRORE | no | Regole applicate al PdV non leggibili | *Asincrono.* La fotografia delle regole salvata al versamento (`regole_applicate`) non è interpretabile; la verifica non sostituisce valori indovinati e si ferma. | Nessuna per il cliente; il conservatore corregge e rielabora il PdV. |
| `VLD-901` | 422 | NON_VALIDO | no | Anno non valido | *Asincrono.* L'anno della chiave dell'unità documentaria è fuori dall'intervallo ammesso. | Correggere l'anno e versare di nuovo. |
| `VLD-902` | 422 | NON_VALIDO | no | Anno non numerico | *Asincrono.* L'anno della chiave dell'unità documentaria non è un numero intero. | Correggere l'anno e versare di nuovo. |
| `VLD-903` | 422 | NON_VALIDO | no | Numero del documento vuoto | *Asincrono.* Il numero della chiave dell'unità documentaria è presente ma vuoto. | Indicare il numero o ometterlo e versare di nuovo. |
| `VLD-EVT-000` | 500 | ERRORE | no | Evento di verifica non leggibile | *Asincrono.* Il messaggio ricevuto dalla pipeline non è un oggetto JSON. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `VLD-EVT-001` | 500 | ERRORE | no | Evento di verifica con identificativi non validi | *Asincrono.* L'evento non porta identificativi validi di PdV o cliente. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `VLD-EVT-002` | 500 | ERRORE | no | Evento di verifica senza indice | *Asincrono.* L'evento non contiene l'indice del PdV. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `VLD-EVT-003` | 500 | ERRORE | no | PdV dell'evento inesistente | *Asincrono.* Il PdV indicato dall'evento non esiste per il cliente. | Nessuna per il cliente; il conservatore analizza l'evento. |

## AIP

Costruzione asincrona del pacchetto di archiviazione (PdA). I codici compaiono in `errori[]` dello stato del PdV.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `AIP-EVENTO-001` | 500 | ERRORE | no | Evento di costruzione non leggibile | *Asincrono.* Il messaggio ricevuto non è un oggetto JSON. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `AIP-EVENTO-002` | 500 | ERRORE | no | Evento di costruzione con identificativi non validi | *Asincrono.* L'evento non porta identificativi validi di PdV o cliente. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `AIP-EVENTO-003` | 500 | ERRORE | no | Indice senza documenti | *Asincrono.* L'indice del PdV da archiviare non contiene documenti. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `AIP-PDV-001` | 500 | ERRORE | no | PdV inesistente | *Asincrono.* Il PdV da archiviare non esiste per il cliente indicato. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `AIP-PDV-002` | 500 | ERRORE | no | PdV in uno stato che non ammette l'archiviazione | *Asincrono.* Il PdV non è nello stato richiesto per costruire il PdA. | Nessuna per il cliente; il conservatore analizza l'evento. |
| `AIP-INTEGRITA-001` | 500 | ERRORE | no | Algoritmo d'impronta non ammesso | *Asincrono.* L'algoritmo d'impronta del file non è fra quelli ammessi. | Segnalare al conservatore. |
| `AIP-INTEGRITA-002` | 500 | ERRORE | no | Impronta non corrispondente | *Asincrono.* L'impronta ricalcolata sul file in archiviazione non corrisponde a quella dichiarata. | Segnalare al conservatore. |
| `AIP-INTEGRITA-003` | 500 | ERRORE | no | Dimensione del file non corrispondente | *Asincrono.* Il file letto ha una dimensione diversa da quella versata. | Segnalare al conservatore. |
| `AIP-METADATI-001` | 500 | ERRORE | no | Metadati insufficienti per il PdA | *Asincrono.* Mancano metadati necessari a costruire il PdA (es. modalità di formazione). | Segnalare al conservatore. |
| `AIP-FILE-001` | 500 | ERRORE | no | File del documento non referenziato | *Asincrono.* Un documento dell'indice non ha il riferimento al file nello storage. | Segnalare al conservatore. |
| `AIP-STORAGE-001` | 500 | ERRORE | no | Oggetto già presente con contenuto diverso | *Asincrono.* Lo storage conservativo contiene già un oggetto con la stessa chiave ma contenuto diverso. | Segnalare al conservatore. |
| `AIP-STORAGE-002` | 500 | ERRORE | no | Copia nello storage conservativo non corrispondente | *Asincrono.* La copia del file nello storage conservativo non ha l'impronta o la dimensione del file versato (verifica dopo la copia lato storage). | Segnalare al conservatore. |

## SEC

Controlli di sicurezza sui dati in ingresso (nomi, testi, estensioni, indirizzi).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `SEC-001` | 400 | NON_VALIDO | no | Testo troppo lungo | Un testo supera la lunghezza massima ammessa. | Accorciare il testo e reinviare. |
| `SEC-002` | 400 | NON_VALIDO | no | Testo con sequenze non ammesse | Un testo contiene sequenze tipiche di un'iniezione SQL. | Togliere le sequenze non ammesse e reinviare. |
| `SEC-003` | 400 | NON_VALIDO | no | Testo con marcatori non ammessi | Un testo contiene marcatori HTML o script non ammessi. | Togliere i marcatori e reinviare. |
| `SEC-004` | 400 | NON_VALIDO | no | Percorso non ammesso | Un nome contiene sequenze di attraversamento di cartelle. | Usare un nome semplice, senza separatori di cartella. |
| `SEC-005` | 400 | RIFIUTATO | no | Indirizzo non ammesso | L'indirizzo punta a una rete interna o non ammessa. | Indicare un indirizzo pubblico raggiungibile. |
| `SEC-006` | 400 | NON_VALIDO | no | Estensione del file non ammessa | L'estensione del nome file non è fra quelle ammesse. | Usare un file con un'estensione ammessa. |

## AUTH

Autenticazione e autorizzazione (token JWT, chiavi API, revoca, ruoli e scope).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `AUTH-000` | 401 | NON_VALIDO | no | Autenticazione richiesta | La richiesta non porta credenziali. | Inviare un token Bearer o una chiave API. |
| `AUTH-001` | 401 | NON_VALIDO | no | Credenziali non valide | Il token o la chiave API non sono validi o sono stati revocati. | Ottenere un nuovo token o verificare la chiave API. |
| `AUTH-002` | 401 | NON_VALIDO | no | Credenziali scadute | Il token o la chiave API sono scaduti. | Rinnovare il token (nuovo accesso) o la chiave API. |
| `AUTH-003` | 401 | RIFIUTATO | no | Accesso revocato | L'utenza o l'ente non sono più attivi, oppure il ruolo è cambiato. | Accedere di nuovo o rivolgersi all'amministratore. |
| `AUTH-004` | 401 | RIFIUTATO | no | Sessione scaduta per inattività | La sessione del portale è rimasta inattiva oltre il limite (30 minuti, 15 per i ruoli privilegiati) oppure è stata chiusa. | Accedere di nuovo dal portale. |
| `AUTH-010` | 403 | RIFIUTATO | no | Permessi insufficienti | Il ruolo dell'utente non consente l'operazione. | Chiedere l'operazione a un utente con il ruolo adeguato. |
| `AUTH-011` | 403 | RIFIUTATO | no | Scope non autorizzato | Il token o la chiave non hanno lo scope richiesto dall'operazione. | Usare credenziali con lo scope richiesto. |
| `AUTH-020` | 403 | RIFIUTATO | no | Accesso negato a questo ente | L'utente non può operare sui dati dell'ente indicato. | Operare solo sui dati del proprio ente. |
| `AUTH-021` | 403 | RIFIUTATO | no | Cliente non determinabile dal token | Il token non indica un cliente valido. | Usare un token emesso per un utente di un ente. |
| `AUTH-030` | 503 | ERRORE | no | Chiavi API non disponibili | Le chiavi API non si possono verificare in questo ambiente (configurazione incompleta). | Usare l'accesso con token o segnalare al conservatore. |
| `AUTH-040` | 403 | RIFIUTATO | no | Richiesta non protetta contro la falsificazione | Richiesta che modifica dati con la sessione del portale (cookie) senza il token anti-CSRF corrispondente o da un'origine diversa da quella del portale. | Ripetere l'operazione dal portale (la pagina invia il token X-CSRF-Token); i sistemi versanti usano il token Bearer o la chiave API, non il cookie. |
| `AUTH-041` | 400 | RIFIUTATO | no | Credenziali duplicate o contrastanti | La richiesta presenta più di una credenziale dello stesso tipo (più intestazioni Authorization o X-Api-Key) oppure il cookie della sessione e un token Bearer diversi: non si sceglie quale usare. | Inviare una sola credenziale per richiesta. |
| `AUTH-503` | 503 | ERRORE | sì (dopo 30 s) | Verifica dell'accesso non disponibile | Il controllo di revoca non si è potuto eseguire. | Riprovare tra poco. |

## SPID

Accesso al portale con SPID o CIE tramite aggregatore.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `SPID-001` | 503 | ERRORE | no | Aggregatore SPID/CIE non configurato | L'accesso con SPID o CIE non è configurato in questo ambiente. | Usare un altro metodo d'accesso o segnalare al conservatore. |
| `SPID-002` | 400 | NON_VALIDO | no | Accesso scaduto o già concluso | Lo stato dell'accesso non è valido (scaduto o già usato). | Ripetere l'accesso. |
| `SPID-003` | 401 | RIFIUTATO | no | Accesso annullato o rifiutato | Il gestore dell'identità ha annullato o rifiutato l'accesso. | Ripetere l'accesso. |
| `SPID-004` | 502 | ERRORE | sì | Errore nello scambio con l'aggregatore | L'aggregatore non ha risposto correttamente. | Ripetere l'accesso tra poco. |
| `SPID-005` | 401 | RIFIUTATO | no | Identità digitale non verificata | L'asserzione dell'identità non ha superato le verifiche. | Ripetere l'accesso. |
| `SPID-006` | 400 | NON_VALIDO | no | Accesso iniziato in un altro browser o scaduto | L'accesso è stato avviato in un altro browser o è scaduto. | Ripetere l'accesso da questo browser. |
| `SPID-007` | 401 | RIFIUTATO | no | Codice fiscale assente o non valido | L'identità digitale non contiene un codice fiscale valido. | Rivolgersi al gestore dell'identità digitale. |
| `SPID-008` | 403 | RIFIUTATO | no | Nessun utente attivo per questo codice fiscale | Nessun ente attivo ha un utente attivo con questo codice fiscale. | Chiedere l'abilitazione all'amministratore dell'ente. |
| `SPID-009` | 403 | RIFIUTATO | no | Livello di autenticazione insufficiente | Il livello SPID usato è inferiore a quello richiesto. | Accedere con il livello richiesto (almeno SPID L2). |
| `SPID-010` | 400 | NON_VALIDO | no | Indirizzo di ritorno non ammesso | L'indirizzo di ritorno indicato non è fra quelli ammessi. | Avviare l'accesso dal portale. |
| `SPID-011` | 400 | NON_VALIDO | no | Provider non ammesso | Il provider indicato non è ammesso (valori spid, cie). | Indicare spid o cie. |
| `SPID-012` | 400 | NON_VALIDO | no | Codice di accesso non valido | Il codice di accesso non è valido, è già stato usato o è scaduto. | Ripetere l'accesso. |

## PDND

E-service esposti su PDND Interoperabilità (voucher, fruitori, versamento ed esibizione).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `PDND-401` | 401 | NON_VALIDO | no | Voucher PDND non valido | Il voucher manca, non è valido o non indica il client. Vecchi codici: `invalid_token`. | Richiedere un nuovo voucher a PDND e reinviare. |
| `PDND-401-SCADUTO` | 401 | NON_VALIDO | no | Voucher PDND scaduto | Il voucher è scaduto. Vecchi codici: `token_expired`. | Richiedere un nuovo voucher a PDND e reinviare. |
| `PDND-403` | 403 | RIFIUTATO | no | Fruitore non registrato | Il client PDND non è associato a un cliente del servizio. Vecchi codici: `fruitore_non_registrato`. | Chiedere al conservatore l'associazione del client al cliente. |
| `PDND-403-CLIENTE` | 403 | RIFIUTATO | no | Cliente non attivo | Il cliente associato al fruitore non è attivo. Vecchi codici: `cliente_non_attivo`. | Rivolgersi al conservatore. |
| `PDND-404` | 404 | NON_VALIDO | no | Pacchetto non trovato | Il pacchetto richiesto non esiste o non appartiene al cliente del fruitore. Vecchi codici: `aip_not_found`. | Verificare l'identificativo. |
| `PDND-422` | 422 | NON_VALIDO | no | Contenuto del file non valido | Il file inviato non è codificato correttamente in base64. Vecchi codici: `file_b64_non_valido`. | Codificare il file in base64 standard e reinviare. |
| `PDND-RIFIUTO` | 422 | NON_VALIDO | no | Dismesso: vedi i codici del versamento (VRS, RGL, VAL) | *Dismesso: non più emesso, vedi `VRS-130`.* Il versamento inoltrato tramite PDND è stato rifiutato dal servizio di versamento. Da R03 l'e-service PDND usa il caso d'uso unico del versamento e risponde con lo stesso codice del REST (es. VRS-130, VRS-121). Vecchi codici: `versamento_rifiutato`. | Gestire i codici del versamento; il vecchio `versamento_rifiutato` non è più emesso. |
| `PDND-502` | 502 | ERRORE | sì | Dismesso: vedi VRS-500 e VRS-501 | *Dismesso: non più emesso, vedi `VRS-500`, `VRS-501`.* Il servizio di versamento non è raggiungibile o ha risposto con un errore. Da R03 l'e-service PDND non chiama più il gateway via HTTP; i guasti di storage e database sono VRS-500 e VRS-501 (503). Vecchi codici: `versamento_failed`. | Gestire VRS-500 e VRS-501 (ritentabili). |
| `PDND-503` | 503 | ERRORE | no | PDND non configurato | Gli e-service PDND non sono disponibili per configurazione incompleta. Vecchi codici: `pdnd_not_configured`. | Segnalare al conservatore. |
| `PDND-503-OCCUPATO` | 503 | ERRORE | sì (dopo 5 s) | Troppi versamenti PDND contemporanei | La replica del servizio sta già elaborando il numero massimo di versamenti PDND contemporanei (impostazione PDND_RICHIESTE_CONTEMPORANEE_MAX), oppure lo spazio temporaneo su disco della replica (DISCO_TEMPORANEO_BUDGET_MB, condiviso con i versamenti multipart) è già prenotato da altre richieste in corso (R03 f3). Il file in base64 non è stato letto (R03 f1). | Ripetere la stessa richiesta (con la stessa Idempotency-Key) dopo i secondi indicati in Retry-After. |
| `PDND-413` | 413 | NON_VALIDO | no | File oltre il limite del canale PDND | Il file in base64 supera la dimensione massima ammessa dall'e-service di versamento. | Ridurre il file; per file grandi usare le API REST con le sessioni di caricamento. |
| `PDND-401-RIUSATO` | 401 | NON_VALIDO | no | Voucher PDND già usato | Il voucher (identificato dal suo jti) è già stato presentato; ogni voucher vale per una sola richiesta, oppure non ha il claim jti. | Richiedere un nuovo voucher a PDND per ogni richiesta. |
| `PDND-503-REPLAY` | 503 | ERRORE | sì (dopo 5 s) | Controllo dei voucher non disponibile | Il registro dei voucher già usati non risponde; per sicurezza il voucher non è accettato. | Ritentare con un nuovo voucher tra poco. |

## RATE

Limiti di frequenza delle richieste.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `RATE-001` | 429 | ERRORE | sì (dopo 60 s) | Dismesso: vedi QTA-001 | *Dismesso: non più emesso, vedi `QTA-001`.* Il cliente ha superato il numero di richieste ammesso nella finestra. | Attendere i secondi indicati in Retry-After e riprovare. |

## LC

Ciclo di vita dei pacchetti conservati (scarto, rettifica, migrazione).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `LC-501` | 501 | RIFIUTATO | no | Dismesso: vedi LC-410 (migrazione non disponibile dal gateway) | *Dismesso: non più emesso, vedi `LC-410`.* L'esportazione verso un nuovo conservatore non era realizzata; dal R05 è il riversamento in uscita del servizio ciclo-vita. | Usare POST /api/v1/ciclo-vita/pratiche/riversamento-uscita. |
| `LC-503` | 503 | RIFIUTATO | no | Dismesso: vedi LC-410 (ciclo di vita non disponibile dal gateway) | *Dismesso: non più emesso, vedi `LC-410`.* Lo scenario richiedeva un flusso autorizzato non realizzato; dal R05 le pratiche sono nel servizio ciclo-vita. | Usare le pratiche di /api/v1/ciclo-vita. |
| `LC-400-CURSORE` | 400 | NON_VALIDO | no | Cursore non valido | Il cursore non appartiene a questo elenco o è illeggibile. | Ripartire dalla prima pagina senza cursore. |
| `LC-403-RUOLO` | 403 | RIFIUTATO | no | Ruolo non abilitato all'operazione sulla pratica | Il ruolo non può avviare la pratica, chiedere la transizione o caricare l'allegato (matrice dei permessi del ciclo di vita). | Chiedere l'operazione a un utente con il ruolo indicato nel messaggio. |
| `LC-404` | 404 | NON_VALIDO | no | Pratica non trovata | La pratica non esiste o non appartiene all'ente. | Verificare l'identificativo. |
| `LC-404-ENTE` | 404 | NON_VALIDO | no | Ente non trovato | L'ente indicato non esiste o non è nel perimetro dell'utente. | Verificare l'identificativo dell'ente. |
| `LC-404-PDV` | 404 | NON_VALIDO | no | Versamento non trovato | Il PdV o il PdA non esiste o non appartiene all'ente. | Verificare l'identificativo. |
| `LC-404-ALLEGATO` | 404 | NON_VALIDO | no | Allegato non trovato | L'allegato citato non appartiene alla pratica. | Caricare l'allegato nella pratica e citarne l'identificativo. |
| `LC-409-STATO-CAMBIATO` | 409 | RIFIUTATO | no | Stato della pratica cambiato | Lo stato della pratica non è quello atteso dal client (STATO_CAMBIATO); un'altra richiesta l'ha modificata. Lo stato attuale è in `dettagli`. | Rileggere la pratica e ripetere la richiesta con lo stato attuale. |
| `LC-409-PIANO-CAMBIATO` | 409 | RIFIUTATO | no | Piano cambiato dall'anteprima | Il piano ricalcolato al momento di applicare ha un'impronta diversa da quella dell'anteprima (PIANO_CAMBIATO); nulla è stato scritto. Il piano nuovo è in `dettagli.piano`. | Rivedere il piano nuovo e confermare con la sua impronta. |
| `LC-409-TRANSIZIONE` | 409 | RIFIUTATO | no | Transizione non ammessa | La transizione richiesta non è prevista dalla macchina a stati della pratica in questo stato (o per questo tipo di ente). | Scegliere una delle transizioni indicate in `azioni_consentite` della pratica. |
| `LC-409-IN-ESECUZIONE` | 409 | RIFIUTATO | no | Esecuzione già richiesta | L'esecuzione della pratica è già stata chiesta ed è in corso. Finché il servizio non l'ha avviata si può solo respingere; una volta avviata (dettagli.esecuzione_avviata) nessuna transizione è più ammessa: il servizio la porta a termine con il verbale e gli esiti per elemento. | Seguire lo stato della pratica. |
| `LC-409-ANNULLATO` | 409 | RIFIUTATO | no | Versamento già annullato | Il versamento ha già una pratica di annullamento. | Nessuna azione: l'annullamento è registrato. |
| `LC-409-ANNULLAMENTO` | 409 | RIFIUTATO | no | Annullamento non ammesso | La configurazione del servizio non ammette l'annullamento dopo la conservazione. | Concordare con il conservatore una rettifica o uno scarto. |
| `LC-409-PDV-STATO` | 409 | RIFIUTATO | no | Stato del versamento incompatibile | Il versamento è in lavorazione o in uno stato che non ammette l'operazione (la rettifica richiede un PdV CONSERVATO). | Attendere la fine della lavorazione o scegliere un altro versamento. |
| `LC-409-PRATICA-APERTA` | 409 | RIFIUTATO | no | Pratica già aperta | Esiste già una pratica dello stesso tipo non conclusa per lo stesso versamento. | Seguire la pratica esistente. |
| `LC-409-IDEMPOTENZA` | 409 | RIFIUTATO | no | Idempotency-Key già usata con un'altra richiesta | La stessa chiave d'idempotenza è stata usata per una richiesta diversa. | Usare una chiave nuova per una richiesta nuova. |
| `LC-410` | 410 | RIFIUTATO | no | Operazione spostata nel servizio del ciclo di vita | L'avvio degli scenari del ciclo di vita dal gateway è dismesso: le pratiche si gestiscono con /api/v1/ciclo-vita/pratiche. Nessuna modifica è stata eseguita. | Usare l'endpoint indicato in `dettagli.sostituto` (intestazione Link). |
| `LC-413-ALLEGATO` | 413 | NON_VALIDO | no | Allegato troppo grande | L'allegato supera la dimensione massima ammessa (ALLEGATI_MAX_MB). | Ridurre il documento o concordare il limite con il conservatore. |
| `LC-422-ALLEGATI` | 422 | NON_VALIDO | no | Allegati obbligatori mancanti | La transizione richiede allegati caricati nella pratica e citati nella richiesta. | Caricare gli allegati indicati e citarli nella transizione. |
| `LC-422-MOTIVO` | 422 | NON_VALIDO | no | Motivo insufficiente | R09: la chiusura con eccezioni di uno scarto chiede un motivo di almeno 20 caratteri: resta nell'audit e nel registro delle impronte. | Spiegare perché i pacchetti rimasti non si cancelleranno (es. blocco legale definitivo). |
| `LC-403-QUATTRO-OCCHI` | 403 | RIFIUTATO | no | Approvazione non ammessa | R09 (D23): l'annullamento lo approva una persona diversa da chi l'ha chiesto, dell'ente, con un ruolo ammesso dall'ente (ANNULLAMENTO_RUOLI_APPROVAZIONE) e con un accesso personale (non con chiave API). | Chiedere l'approvazione a un'altra persona autorizzata dell'ente. |
| `LC-409-APPROVAZIONE` | 409 | RIFIUTATO | no | Nessuna approvazione in attesa | R09 (D23): la pratica non è un annullamento in attesa della seconda approvazione (già approvata, respinta o eseguita). | Ricaricare la pratica e controllarne lo stato. |
| `LC-422-ALLEGATO-TIPO` | 422 | NON_VALIDO | no | Allegato non ammesso | Tipo di allegato non caricabile dagli utenti, oppure file vuoto. | Usare AUTORIZZAZIONE_PRODUTTORE, NULLA_OSTA o ALTRO con un file non vuoto. |
| `LC-422-FIRMA` | 422 | NON_VALIDO | no | Firma del documento non accettabile | Il documento deve essere firmato (CAdES .p7m o PDF con firma PAdES) e, da R08, la firma è VERIFICATA al livello dell'ente (CICLO_VITA_FIRME_LIVELLO): firma alterata, certificato revocato, catena non riconducibile a un prestatore fidato o stato di revoca non disponibile (livello CON_REVOCHE) la rendono non accettabile. Il motivo in parole è in `dettaglio`, l'esito normalizzato in `dettagli.firma`. | Caricare il documento firmato digitalmente con un certificato valido e non revocato. |
| `LC-422-CRITERI` | 422 | NON_VALIDO | no | Criteri della pratica non validi | Criteri incoerenti (indicare anni oppure scadenza_entro), classe non disponibile, nessun PdA selezionabile o troppi PdA per una pratica. | Correggere o restringere i criteri. |
| `LC-422-METADATI` | 422 | NON_VALIDO | no | Metadati della rettifica non validi | Metadati vuoti, con nomi non ammessi o valori di tipo non previsto, oppure identificativo del pacchetto assente. | Indicare aip_id o pdv_id e metadati con nomi in minuscolo e valori semplici. |
| `LC-422-CARICAMENTO` | 422 | NON_VALIDO | no | Sessione di caricamento non utilizzabile | La sessione di caricamento del PdD ricevuto non esiste per l'ente, non è COMPLETA, è già stata usata o supera la dimensione ammessa. | Caricare di nuovo il PdD con una sessione di caricamento e usare il suo identificativo. |
| `LC-503-DB` | 503 | ERRORE | sì (dopo 30 s) | Ciclo di vita temporaneamente non disponibile | Il database non ha risposto. | Riprovare tra poco. |
| `LC-422-RICHIESTA-FIRMATA` | 422 | NON_VALIDO | no | Richiesta di annullamento firmata mancante o non pertinente | L'ente richiede la richiesta di annullamento firmata (ANNULLAMENTO_RICHIESTA_FIRMATA = OBBLIGATORIA) o il documento firmato non riporta l'identificativo del versamento (R08, D22). | Allegare in `documento_firmato` la richiesta firmata che cita l'identificativo del versamento. |
| `LC-503-FIRME` | 503 | ERRORE | sì (dopo 60 s) | Verifica delle firme non disponibile | Il verificatore delle firme (servizio verifica-firme o ripiego) non ha risposto; nulla è stato registrato. | Riprovare tra poco. |
| `LC-503-STORAGE` | 503 | ERRORE | sì (dopo 60 s) | Storage conservativo non disponibile | La scrittura nel WORM non è riuscita; nulla è stato registrato. | Riprovare tra poco. |
| `LC-422-IMPRONTA` | 422 | NON_VALIDO | no | Impronta del file ricevuto cambiata | *Asincrono.* Durante l'invio al gateway l'impronta di un file del PdD ricevuto non corrisponde a quella verificata. | Verificare il PdD ricevuto e ripetere il riversamento. |
| `LC-500-LAVORATORE` | 500 | ERRORE | sì | Errore interno del lavoratore del ciclo di vita | *Asincrono.* Il passo della pratica è terminato con un'eccezione non prevista; la pratica sarà ripresa. | Segnalare al conservatore se l'errore persiste. |
| `LC-403-CONFERMA` | 403 | RIFIUTATO | no | Conferma firmata della decisione richiesta | Questa decisione (approvazione o esecuzione di uno scarto, esecuzione di un riversamento in uscita, richiesta o approvazione di un annullamento, rettifica, ripresa di una pratica sospesa) richiede la conferma firmata dal gateway dopo una conferma dell'identità recente; manca, è scaduta, è già stata usata o non corrisponde alla pratica, alla versione o al contenuto deciso. Con una chiave API non si può ottenere. | Ripetere la decisione dalla console confermando la propria identità quando richiesto. |
| `LC-503-CONFERMA` | 503 | ERRORE | sì (dopo 60 s) | Verifica delle conferme non disponibile | Le chiavi pubbliche del gateway per verificare le conferme delle decisioni non sono configurate: nessuna decisione sensibile si registra (fail-closed). | Segnalare all'operatore dell'infrastruttura: CONFERME_CHIAVI_PUBBLICHE nella configurazione di ciclo-vita (runbook conferme_decisione). |
| `LC-409-AUTORIZZAZIONI` | 409 | RIFIUTATO | no | Autorizzazioni dello scarto non verificate | *Asincrono.* Prima di cancellare, il lavoratore ha ricontrollato da sé la catena delle autorizzazioni della pratica di scarto (cronologia completa e continua, nulla osta della Soprintendenza per le pubbliche amministrazioni, autorizzazione del produttore e nulla osta presenti nello storage conservativo con l'impronta registrata, piano approvato) e qualcosa non torna (elenco in `ultimo_errore.difetti`). Nessun documento è stato cancellato e la pratica non ha più lavoro. | Segnalare al conservatore: anomalia di sicurezza da verificare (runbook scarto_incompleto, § autorizzazioni non verificate). La pratica si può respingere. |
| `LC-409-TENTATIVI-ESAURITI` | 409 | ERRORE | no | Lavoro della pratica sospeso dopo troppi tentativi | *Asincrono.* Il lavoratore ha fallito il passo della pratica per `LAVORATORE_TENTATIVI_MASSIMI` volte di seguito (registro, gateway, distribution o storage che rifiutano sempre, oppure un difetto): il lavoro è stato tolto e la pratica è ferma in uno stato di errore esplicito, con l'ultimo errore in `ultimo_errore.ultimo` e il lavoro sospeso in `ultimo_errore.lavoro_sospeso`. Nessun nuovo tentativo automatico; ciò che era già stato fatto resta registrato. | Segnalare al conservatore: corretta la causa, la pratica si riprende con la procedura del runbook pratica_sospesa (allarme PraticaSospesa). Se l'esecuzione non era ancora iniziata, la pratica si può anche respingere. |
| `LC-409-NON-SOSPESA` | 409 | RIFIUTATO | no | Pratica non sospesa | Si è chiesto di riprendere una pratica che non è sospesa dopo i tentativi esauriti (`LC-409-TENTATIVI-ESAURITI`): ha ancora il suo lavoro, oppure non ne ha mai avuto uno sospeso. | Ricaricare la pratica e controllarne lo stato. |
| `LC-503-REGISTRO` | 503 | ERRORE | sì | Registro delle impronte non disponibile | *Asincrono.* La foglia del ciclo di vita non è stata accettata dal registro (servizio irraggiungibile o non configurato); il passo sarà ripetuto. | Nessuna azione: la pratica riprende da sola. |
| `LC-503-GATEWAY` | 503 | ERRORE | sì | Gateway non disponibile per il versamento | *Asincrono.* Il versamento della rettifica o del riversamento non è riuscito per un errore temporaneo o di configurazione; il passo sarà ripetuto. | Nessuna azione: la pratica riprende da sola. |
| `LC-503-DISTRIBUTION` | 503 | ERRORE | sì | Distribution non disponibile per i pacchetti di migrazione | *Asincrono.* La richiesta dei PdD MIGRAZIONE non è riuscita per un errore temporaneo o di configurazione; il passo sarà ripetuto. | Nessuna azione: la pratica riprende da sola. |
| `LC-503-LAVORATORE` | 503 | ERRORE | sì | Passo della pratica rinviato | *Asincrono.* Un sistema esterno o lo storage non ha risposto; il passo sarà ripetuto dopo l'attesa prevista. | Nessuna azione: la pratica riprende da sola. |

## HC

Catena delle impronte (hashchain) e sua verifica.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `HC-403` | 403 | RIFIUTATO | no | Ruolo non autorizzato alla verifica della catena | La verifica completa della catena è riservata ai ruoli del conservatore. | Chiedere la verifica a un responsabile del conservatore. |
| `HC-404` | 404 | NON_VALIDO | no | Verifica non trovata | La verifica della catena indicata non esiste. | Verificare l'identificativo. |
| `HC-409` | 409 | ERRORE | sì | Verifica già in corso | Una verifica completa della catena è già in esecuzione. | Attendere la fine della verifica in corso. |
| `HC-503` | 503 | ERRORE | sì (dopo 30 s) | Catena temporaneamente non disponibile | Il database della catena non ha risposto. | Riprovare tra poco. |

## DST

Distribuzione ed esibizione (pacchetti di distribuzione, ricerca).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `DST-101` | 422 | RIFIUTATO | no | Pacchetto non distribuibile | Lo stato del pacchetto non ne consente la distribuzione. | Attendere che il pacchetto sia conservato. |
| `DST-102` | 400 | NON_VALIDO | no | File richiesti mancanti | Una distribuzione di tipo ESTRATTO richiede l'elenco dei file. | Indicare `file_richiesti` e reinviare. |
| `DST-403` | 403 | RIFIUTATO | no | Richiesta non attribuibile | Il token non indica l'utente che richiede la distribuzione. | Usare un token emesso per un utente. |
| `DST-404` | 404 | NON_VALIDO | no | Pacchetto non trovato | Il pacchetto o la richiesta non esistono o non appartengono al cliente. | Verificare l'identificativo. |
| `DST-422` | 422 | NON_VALIDO | no | Richiesta di distribuzione non valida | Un identificativo o un campo della richiesta non è valido. | Correggere il campo indicato e reinviare. |
| `DST-503` | 503 | ERRORE | sì (dopo 30 s) | Distribuzione temporaneamente non disponibile | Il registro o l'indice di ricerca non hanno risposto. | Riprovare tra poco. |
| `DST-ENTE` | 403 | NON_VALIDO | no | Ente non valido | Il token non indica un ente valido oppure `tenant_id` non è valido. | Indicare un ente valido. |
| `DST-103` | 422 | NON_VALIDO | no | File richiesti non presenti nel pacchetto | Uno o più nomi di `file_richiesti` non corrispondono a documenti del pacchetto di archiviazione. | Indicare i nomi dei file come compaiono nel pacchetto (elenco in `dettagli.disponibili`). |
| `DST-104` | 422 | NON_VALIDO | no | Cursore di ricerca non valido | Il cursore indicato non è stato emesso da questa ricerca o è stato alterato. | Ripartire dalla prima pagina senza cursore e usare il `cursore` restituito. |
| `DST-410` | 410 | RIFIUTATO | no | Pacchetto di distribuzione scaduto | I collegamenti del pacchetto di distribuzione sono scaduti o il pacchetto è stato rimosso dallo storage temporaneo. | Richiedere un nuovo pacchetto di distribuzione. |
| `DST-409` | 409 | RIFIUTATO | no | Pacchetto di distribuzione non rigenerabile | R07 (D18): si rigenera solo un pacchetto scaduto (stato SCADUTO o PRONTO oltre la scadenza); lo stato attuale è in `dettagli.stato`. | Scaricare il pacchetto ancora valido o attendere che sia pronto. |
| `DST-502` | 502 | ERRORE | no | Impronta di un file del pacchetto non corrispondente | Un file letto dallo storage non corrisponde all'impronta registrata alla conservazione; il pacchetto non è stato consegnato e l'anomalia è registrata. | Segnalare al conservatore indicando l'identificativo della richiesta. |

## FSC

Fascicoli e aggregazioni documentali.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `FSC-401` | 401 | NON_VALIDO | no | Autenticazione richiesta | I fascicoli richiedono un token JWT. | Inviare un token Bearer. |
| `FSC-403` | 403 | RIFIUTATO | no | Cliente non indicato | Il token non indica un cliente. | Usare un token emesso per un utente di un ente. |
| `FSC-403-RUOLO` | 403 | RIFIUTATO | no | Profilo non abilitato alla modifica dei fascicoli | Il profilo può consultare i fascicoli ma non crearli o modificarli. | Chiedere l'operazione a un utente abilitato. |
| `FSC-404` | 404 | NON_VALIDO | no | Fascicolo non trovato | Il fascicolo non esiste o non appartiene al cliente. | Verificare l'identificativo. |
| `FSC-405` | 404 | NON_VALIDO | no | Pacchetto non trovato | Il pacchetto da aggiungere al fascicolo non esiste o non appartiene al cliente. | Verificare l'identificativo del pacchetto. |
| `FSC-409` | 409 | RIFIUTATO | no | Operazione in conflitto con lo stato del fascicolo | Codice già usato, fascicolo non aperto o pacchetto già presente. | Leggere il motivo nel titolo e correggere la richiesta. |
| `FSC-422-RESPONSABILE` | 422 | NON_VALIDO | no | Responsabile non valido | Il responsabile deve essere un utente attivo dello stesso ente. | Indicare un utente attivo dell'ente. |
| `FSC-400-CURSORE` | 400 | NON_VALIDO | no | Cursore non valido | Il cursore non appartiene all'elenco dei fascicoli o è illeggibile. | Ripartire dalla prima pagina senza cursore. |
| `FSC-422-CHIUSURA` | 422 | NON_VALIDO | no | Data di chiusura non valida | La data di chiusura è precedente all'apertura del fascicolo o futura. | Indicare una data fra l'apertura e oggi. |
| `FSC-503` | 503 | ERRORE | sì (dopo 30 s) | Fascicoli temporaneamente non disponibili | Il database non ha risposto. | Riprovare tra poco. |

## MIG

Migrazione e riversamento dei pacchetti.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `MIG-101` | 422 | RIFIUTATO | no | Pacchetto non migrabile | Lo stato del pacchetto non ne consente la migrazione. | Attendere che il pacchetto sia conservato. |
| `MIG-401` | 401 | NON_VALIDO | no | Autenticazione richiesta | La migrazione richiede un token JWT. | Inviare un token Bearer. |
| `MIG-403` | 403 | RIFIUTATO | no | Ruolo non autorizzato alla migrazione | Solo i ruoli del conservatore possono migrare pacchetti. | Chiedere l'operazione a un responsabile del conservatore. |
| `MIG-404` | 404 | NON_VALIDO | no | Pacchetto o job non trovato | Il pacchetto o il job di migrazione non esistono. | Verificare l'identificativo. |
| `MIG-422` | 422 | NON_VALIDO | no | Identificativo del pacchetto non valido | Uno o più identificativi non sono UUID; l'elenco è in `dettagli.non_validi`. | Correggere gli identificativi e reinviare. |
| `MIG-503` | 503 | ERRORE | sì (dopo 30 s) | Migrazione temporaneamente non disponibile | Il database non ha risposto. | Riprovare tra poco. |

## DL

Scaricamento dei pacchetti, dei file e dei rapporti dal portale.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `DL-400` | 400 | NON_VALIDO | no | Ente non valido | Il parametro `tenant_id` non è valido. | Indicare un identificativo d'ente valido. |
| `DL-401` | 401 | NON_VALIDO | no | Autenticazione richiesta | Lo scaricamento richiede un token JWT. | Inviare un token Bearer. |
| `DL-403` | 403 | RIFIUTATO | no | Cliente non indicato | Il token non indica un cliente. | Usare un token emesso per un utente di un ente. |
| `DL-403-CONTENUTO` | 403 | RIFIUTATO | no | Contenuto di un altro ente non accessibile | Anche i profili del conservatore leggono solo i metadati dei pacchetti di altri enti. | Scaricare solo i contenuti del proprio ente. |
| `DL-404` | 404 | NON_VALIDO | no | Pacchetto o file non trovato | Il pacchetto, il PdV o il file richiesto non esistono. | Verificare l'identificativo. |
| `DL-404-RDV` | 404 | ERRORE | sì | Rapporto di versamento non ancora disponibile | Il rapporto si emette dopo il sigillo nella catena delle impronte. | Riprovare quando il pacchetto è conservato. |
| `DL-502` | 502 | ERRORE | sì | Archivio non disponibile | Lo storage non ha restituito il contenuto richiesto. | Riprovare tra poco; se persiste segnalare l'identificativo della richiesta. |
| `DL-502-IMPRONTA` | 502 | ERRORE | no | Contenuto non integro | Il contenuto letto non corrisponde all'impronta registrata. | Segnalare al conservatore indicando l'identificativo della richiesta. |
| `DL-503` | 503 | ERRORE | sì (dopo 30 s) | Scaricamento temporaneamente non disponibile | Il database non ha risposto. | Riprovare tra poco. |

## DSH

API del portale (dashboard) e inoltro verso i servizi interni.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `DSH-400` | 400 | NON_VALIDO | no | Ente non valido | Il parametro `tenant_id` non è valido. | Indicare un identificativo d'ente valido. |
| `DSH-400-ENTE` | 400 | NON_VALIDO | no | Ente da indicare | Il profilo deve indicare l'ente su cui operare. | Indicare `tenant_id`. |
| `DSH-104` | 422 | NON_VALIDO | no | Cursore non valido | Il cursore indicato non è stato emesso da questo elenco o è stato alterato. | Ripartire dalla prima pagina senza cursore e usare il `cursore` restituito. |
| `DSH-422` | 422 | NON_VALIDO | no | Parametro non valido | Un parametro della richiesta non è valido. | Correggere il parametro indicato. |
| `DSH-502` | 502 | ERRORE | sì | Servizio interno non disponibile | Un servizio interno non è raggiungibile o ha risposto con un errore. | Riprovare tra poco; se persiste segnalare l'identificativo della richiesta. |
| `DSH-503` | 503 | ERRORE | sì (dopo 30 s) | Dati temporaneamente non disponibili | Il database non ha risposto. | Riprovare tra poco. |
| `DSH-UPSTREAM` | 400 | NON_VALIDO | no | Richiesta rifiutata da un servizio interno | Il servizio interno ha rifiutato la richiesta senza un codice leggibile. | Verificare la richiesta; se persiste segnalare l'identificativo della richiesta. |
| `DSH-404-SCARTO` | 404 | NON_VALIDO | no | Messaggio scartato non trovato | Nessun messaggio delle code di scarto ha questo identificativo. | Rileggere l'elenco delle code di scarto. |
| `DSH-409-SCARTO` | 409 | RIFIUTATO | no | Messaggio scartato già gestito | Il messaggio è già stato rielaborato o archiviato, anche da un'altra persona, e lo stato atteso non coincide. | Rileggere il dettaglio del messaggio. |
| `DSH-409-PIANO` | 409 | RIFIUTATO | no | Piano cambiato | Il piano di rielaborazione ricalcolato al momento dell'applicazione non coincide con l'anteprima confermata (impronta del piano diversa). Nulla è stato applicato; la risposta contiene il piano aggiornato. | Controllare il piano aggiornato e confermarlo di nuovo. |
| `DSH-422-PIANO` | 422 | NON_VALIDO | no | Selezione da rielaborare non valida | Indicare almeno un messaggio scartato o un filtro, entro il massimo di elementi per piano. | Correggere la selezione. |

## GOV

Governo del servizio dal portale (clienti, contratti, nomine).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `GOV-UUID` | 400 | NON_VALIDO | no | Identificativo non valido | L'identificativo indicato (ente, cliente) non è un UUID valido. | Correggere l'identificativo. |
| `GOV-404` | 404 | NON_VALIDO | no | Cliente non trovato | Il cliente indicato non esiste. | Verificare l'identificativo. |
| `GOV-409` | 409 | RIFIUTATO | no | Registrazione in conflitto | Il dato è già registrato (es. numero di contratto) o è in corso una registrazione concorrente. | Ricaricare i dati e verificare prima di ripetere. |
| `GOV-409-FIRMA` | 409 | RIFIUTATO | no | Documento non firmabile | Il documento non è più da firmare (firmato, annullato) o la firma spetta a un'altra persona. | Ricaricare l'elenco dei documenti da firmare. |
| `GOV-422-FIRMA` | 422 | NON_VALIDO | no | Documento da firmare non valido | Tipo, titolo, nome o contenuto del documento da firmare non sono validi (tipo in maiuscolo, contenuto non vuoto entro il limite). | Correggere i campi indicati. |
| `GOV-502-FIRMA` | 502 | ERRORE | sì | Firma restituita non coerente | Il servizio di firma ha restituito una busta che non corrisponde al documento o alla credenziale della persona: non si registra. | Riprovare; se persiste, segnalare al conservatore. |
| `GOV-503` | 503 | ERRORE | sì (dopo 30 s) | Dati di governo temporaneamente non disponibili | Il database non ha risposto. | Riprovare tra poco. |
| `GOV-404-FRUITORE` | 404 | NON_VALIDO | no | Fruitore PDND non trovato | Nessun fruitore PDND registrato con questo identificativo. | Verificare l'identificativo. |
| `GOV-409-FRUITORE` | 409 | RIFIUTATO | no | Client PDND già registrato | Il client_id PDND è già associato a un fruitore (di questo o di un altro ente). | Usare il fruitore esistente o disattivarlo prima di registrarne un altro. |

## SLA

Rapporti sui livelli di servizio.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `SLA-422` | 422 | NON_VALIDO | no | Mese non valido | Il mese deve essere nel formato AAAA-MM. | Correggere il parametro. |
| `SLA-503` | 503 | ERRORE | sì (dopo 30 s) | Rapporto non disponibile | Il database non ha risposto. | Riprovare tra poco. |

## SIG

API del servizio di firma e marca (autenticazione e forma della richiesta).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `SIG-401` | 401 | NON_VALIDO | no | Autenticazione richiesta | Il servizio di firma richiede un token JWT. | Inviare un token Bearer. |
| `SIG-403` | 403 | RIFIUTATO | no | Operazione riservata al token di servizio | Firma e marca sono riservate ai servizi interni con lo scope dedicato. | Usare il token di servizio. |
| `SIG-413` | 413 | NON_VALIDO | no | Contenuto troppo grande | Il contenuto supera la dimensione massima ammessa dal servizio di firma. | Firmare l'impronta invece del contenuto. |
| `SIG-422-BASE64` | 422 | NON_VALIDO | no | Contenuto non in base64 | Un campo non è codificato in base64 valido. | Codificare il campo in base64 standard. |
| `SIG-422-IMPRONTA` | 422 | NON_VALIDO | no | Impronta non valida | L'impronta manca, non è esadecimale o non è di 32 byte. | Indicare un'impronta SHA-256 esadecimale o il contenuto. |

## FIRMA

Firma qualificata e marca temporale (adattatori verso i fornitori).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `FIRMA-500` | 500 | ERRORE | no | Errore del servizio di firma | Errore non classificato del servizio di firma. | Segnalare al conservatore indicando l'identificativo della richiesta. |
| `FIRMA-503-CONFIGURAZIONE` | 503 | ERRORE | sì | Configurazione dei fornitori non disponibile | Il servizio di firma non riesce a leggere il registro dei fornitori o i parametri (database non raggiungibile o segreti non leggibili). | Ritentare più tardi; se persiste, controllare il database e il Secret del servizio di firma. |
| `FIRMA-502-FIRMA-NON-VALIDA` | 502 | ERRORE | sì | Firma prodotta non valida | La firma o la marca restituita dal fornitore non supera la verifica crittografica (impronta, firma, catena o certificato atteso) e non si usa. | Ritentare; se persiste, controllare il fornitore e le radici configurate in console. |
| `FIRMA-404-FORNITORE` | 404 | NON_VALIDO | no | Fornitore di firma non registrato | Nessun fornitore di firma o marca con il codice indicato. | Controllare il codice nella pagina «Fornitori di firma e marca». |
| `FIRMA-403-FORNITORI` | 403 | RIFIUTATO | no | Operazione riservata al conservatore | Registrare, modificare e provare i fornitori di firma e marca è un permesso mai delegabile dei ruoli del conservatore. | Chiedere a un responsabile del conservatore. |
| `FIRMA-422-CONFIGURAZIONE` | 422 | NON_VALIDO | no | Configurazione del fornitore non valida | La configurazione del fornitore di firma o marca non è utilizzabile (campi mancanti, indirizzi, radici, segreti nella console, fornitore di sviluppo in produzione). | Correggere i campi indicati nei motivi. |
| `FIRMA-429-PROVA` | 429 | ERRORE | sì | Troppe prove di connessione | Le prove con firma di prova consumano firme del prestatore e sono limitate. | Riprovare fra qualche minuto. |
| `FIRMA-503-NON-CONFIGURATA` | 503 | ERRORE | no | Firma non configurata | Nessun fornitore è configurato per l'operazione richiesta. | Segnalare al conservatore. |
| `FIRMA-409-SVILUPPO-IN-PRODUZIONE` | 409 | RIFIUTATO | no | Adattatore di sviluppo vietato in produzione | L'adattatore di sviluppo non può firmare né marcare in produzione. | Configurare un fornitore qualificato. |
| `FIRMA-502-FORNITORE` | 502 | ERRORE | sì | Errore del fornitore di firma o marca | Il fornitore remoto ha risposto con un errore o una risposta non valida. | Riprovare più tardi. |
| `FIRMA-503-LIMITE` | 503 | ERRORE | sì | Limite di frequenza non verificabile | Il contatore condiviso fra le repliche (Redis) non ha risposto; le operazioni che consumano firme del prestatore non partono senza il controllo del limite. | Riprovare tra poco; se persiste, controllare Redis. |
| `FIRMA-403-PERSONALE` | 403 | RIFIUTATO | no | Firma personale non consentita | La firma personale con OTP è riservata alle persone del conservatore con una credenziale personale, con un accesso al portale (mai una chiave API o un token di servizio). | Accedere con la propria utenza del conservatore. |
| `FIRMA-404-CREDENZIALE` | 404 | RIFIUTATO | no | Nessuna credenziale di firma personale | La persona non ha una credenziale di firma personale attiva, oppure il fornitore indicato non è un fornitore CSC attivo e costruito. | Chiedere al conservatore di registrare la credenziale personale (Console, Firma qualificata). |
| `FIRMA-422-OTP` | 422 | NON_VALIDO | no | OTP o PIN non accettato | Il prestatore ha rifiutato il codice monouso (OTP) o il PIN della firma personale. | Chiedere un nuovo OTP e riprovare. |
| `FIRMA-429-PERSONALE` | 429 | ERRORE | sì | Troppi tentativi di firma personale | Le richieste di OTP e i tentativi di firma personale sono limitati per persona. | Riprovare fra qualche minuto. |
| `FIRMA-422-RICHIESTA` | 422 | NON_VALIDO | no | Richiesta di firma non utilizzabile | Il contenuto non è utilizzabile per l'operazione (es. non PDF per PAdES). | Correggere il contenuto o il tipo di firma. |

## ONB

Attivazione e gestione dei clienti, degli utenti, delle chiavi API e delle classi documentali.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `ONB-001` | 409 | RIFIUTATO | no | Cliente già registrato | Un cliente con lo stesso codice fiscale è già presente. | Usare il cliente esistente. |
| `ONB-201` | 422 | RIFIUTATO | no | Transizione di stato non ammessa | Il cambio di stato richiesto non è consentito dallo stato attuale. | Usare una delle transizioni ammesse indicate nel dettaglio. |
| `ONB-202` | 422 | NON_VALIDO | no | Stato non valido | Lo stato indicato non esiste. | Usare uno dei valori ammessi indicati nel dettaglio. |
| `ONB-203` | 422 | NON_VALIDO | no | Filtro di stato non valido | Lo stato usato come filtro non esiste. | Usare uno dei valori ammessi. |
| `ONB-400` | 400 | NON_VALIDO | no | Richiesta non valida | Un parametro o il corpo della richiesta non sono validi. | Correggere la richiesta. |
| `ONB-404` | 404 | NON_VALIDO | no | Risorsa non trovata | Cliente, utente o chiave API non trovati. | Verificare l'identificativo. |
| `ONB-409` | 409 | RIFIUTATO | no | Dato già registrato | Esiste già un utente con la stessa email o lo stesso codice fiscale nell'ente. | Usare l'utente esistente o correggere il dato. |
| `ONB-412` | 409 | RIFIUTATO | no | Preferenze modificate da un'altra sessione | R07 (D31): la versione delle preferenze indicata non è quella salvata; la versione attuale è in `dettagli.versione`. | Rileggere le preferenze e ripetere il salvataggio. |
| `ONB-422` | 422 | NON_VALIDO | no | Dato non valido | Un campo obbligatorio è vuoto o non è valido. | Correggere il campo indicato. |
| `ONB-503` | 503 | ERRORE | sì (dopo 30 s) | Servizio utenti temporaneamente non disponibile | Il database non ha risposto. | Riprovare tra poco; se persiste segnalare l'identificativo della richiesta. |
| `ONB-530` | 503 | ERRORE | no | Generazione delle chiavi API non disponibile | Le chiavi API non si possono generare in questo ambiente (configurazione incompleta). | Segnalare al conservatore. |

## S3

Storage conservativo (Object Lock / WORM).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `S3-500` | 503 | ERRORE | sì | Storage conservativo non disponibile | Lo storage conservativo non ha accettato la scrittura; nessun oggetto è stato scritto. | Riprovare più tardi. |
| `S3-CONFIG` | 503 | ERRORE | no | Configurazione dello storage non valida | La configurazione dello storage conservativo è incompleta o non valida; nessun oggetto è stato scritto. | Nessuna per il cliente; il conservatore corregge la configurazione. |
| `S3-NO-OBJECT-LOCK` | 503 | ERRORE | no | Bucket senza Object Lock | Il bucket non ha Object Lock attivo; lo storage non è conservativo e la scrittura è stata bloccata. | Nessuna per il cliente; il conservatore corregge la configurazione. |
| `S3-GIA-PRESENTE` | 409 | ERRORE | no | Oggetto già presente | Un oggetto con la stessa chiave è già nello storage conservativo e non si sovrascrive. | Nessuna per il cliente; il servizio confronta il contenuto esistente. |
| `S3-RETENTION` | 500 | ERRORE | no | Conservazione non determinabile | Non è stato possibile calcolare la scadenza di conservazione dell'oggetto; nessun oggetto è stato scritto. | Segnalare al conservatore. |
| `S3-ORIGINE-MODIFICATA` | 503 | ERRORE | sì | File d'origine modificato durante la copia | Il file nello storage di transito è cambiato fra il calcolo delle impronte e la copia nello storage conservativo; la copia non è avvenuta. | Nessuna per il cliente; il servizio ritenta e ricalcola le impronte. |
| `S3-COPIA-DIVERSA` | 500 | ERRORE | no | Copia conservativa diversa dall'origine | La copia nello storage conservativo non ha l'impronta o la dimensione del file d'origine. | Segnalare al conservatore. |

## VAL

Forma della richiesta non conforme allo schema dell'API (campi mancanti, tipi errati).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `VAL-422` | 422 | NON_VALIDO | no | Richiesta non conforme allo schema | Uno o più campi mancano o hanno un tipo o un formato errato; l'elenco è in `campi`. | Correggere i campi indicati e reinviare. |

## HTTP

Errori HTTP generici senza codice di dominio (rotta inesistente, metodo non ammesso, errori dei servizi che non indicano un codice).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `HTTP-400` | 400 | NON_VALIDO | no | Richiesta non valida | La richiesta non è valida. | Correggere la richiesta. |
| `HTTP-401` | 401 | NON_VALIDO | no | Autenticazione richiesta | La richiesta richiede credenziali valide. | Inviare credenziali valide. |
| `HTTP-403` | 403 | RIFIUTATO | no | Operazione non consentita | Le credenziali non consentono l'operazione. | Usare credenziali abilitate. |
| `HTTP-404` | 404 | NON_VALIDO | no | Risorsa non trovata | L'indirizzo o la risorsa richiesta non esistono. | Verificare l'indirizzo e l'identificativo. |
| `HTTP-405` | 405 | NON_VALIDO | no | Metodo non ammesso | Il metodo HTTP non è ammesso su questo indirizzo. | Usare uno dei metodi indicati nell'intestazione Allow. |
| `HTTP-406` | 406 | NON_VALIDO | no | Formato di risposta non disponibile | Il formato richiesto nell'intestazione Accept non è disponibile. | Accettare uno dei formati offerti. |
| `HTTP-409` | 409 | RIFIUTATO | no | Conflitto | La richiesta è in conflitto con lo stato della risorsa. | Ricaricare la risorsa e ripetere se ancora necessario. |
| `HTTP-413` | 413 | NON_VALIDO | no | Richiesta troppo grande | Il corpo della richiesta supera la dimensione ammessa. | Ridurre la richiesta. |
| `HTTP-415` | 415 | NON_VALIDO | no | Tipo di contenuto non supportato | Il tipo di contenuto della richiesta non è supportato. | Inviare il corpo nel formato atteso. |
| `HTTP-422` | 422 | NON_VALIDO | no | Richiesta non elaborabile | La richiesta è ben formata ma non elaborabile. | Correggere il contenuto della richiesta. |
| `HTTP-429` | 429 | ERRORE | sì (dopo 60 s) | Troppe richieste | Sono state inviate troppe richieste. | Attendere e riprovare. |
| `HTTP-500` | 500 | ERRORE | no | Errore del servizio | Il servizio ha restituito un errore senza codice. | Segnalare al conservatore indicando l'identificativo della richiesta. |
| `HTTP-501` | 501 | RIFIUTATO | no | Funzione non disponibile | La funzione richiesta non è disponibile. | Concordare l'operazione con il conservatore. |
| `HTTP-502` | 502 | ERRORE | sì | Servizio interno non raggiungibile | Un servizio interno non ha risposto correttamente. | Riprovare tra poco. |
| `HTTP-503` | 503 | ERRORE | sì (dopo 30 s) | Servizio temporaneamente non disponibile | Il servizio non è al momento disponibile. | Riprovare tra poco. |
| `HTTP-504` | 504 | ERRORE | sì | Tempo di risposta superato | Un servizio interno non ha risposto in tempo. | Riprovare tra poco. |

## INTERNAL

Errori interni non previsti. Il dettaglio resta nei log, il client riceve solo l'identificativo della richiesta.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `INTERNAL-001` | 500 | ERRORE | no | Errore interno del servizio | Errore non previsto; il dettaglio è nei log del conservatore. | Segnalare al conservatore indicando l'identificativo della richiesta. |

## UPL

Sessioni di caricamento dei file (caricamento in streaming e a parti, ripresa).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `UPL-404` | 404 | NON_VALIDO | no | File caricato non trovato | Il versamento cita un file caricato che non esiste nello storage di transito o non appartiene al cliente. | Verificare l'identificativo del caricamento o caricare di nuovo il file. |
| `UPL-422` | 422 | NON_VALIDO | no | File caricato incompleto o incoerente | Il file caricato citato dal versamento non ha impronte e testa complete oppure la sua dimensione non corrisponde all'oggetto nello storage. | Concludere il caricamento oppure caricare di nuovo il file. |
| `UPL-001` | 413 | NON_VALIDO | no | File oltre la dimensione massima | La dimensione dichiarata all'apertura della sessione supera il limite del servizio di caricamento. | Ridurre il file o chiedere al conservatore il limite applicabile. |
| `UPL-002` | 422 | NON_VALIDO | no | Parte non valida | Il numero della parte è fuori dall'intervallo della sessione oppure la sua dimensione è diversa da quella attesa (dimensione_parte, l'ultima il resto del file). | Inviare la parte con il numero e la dimensione indicati all'apertura della sessione. |
| `UPL-003` | 422 | NON_VALIDO | no | Impronta della parte errata | L'impronta SHA-256 dichiarata in X-Impronta-Parte non corrisponde ai byte ricevuti; la parte non è stata registrata. | Rileggere la parte dalla sorgente e inviarla di nuovo. |
| `UPL-004` | 409 | NON_VALIDO | no | Parte già ricevuta con un contenuto diverso | Una parte con lo stesso numero è già stata ricevuta con byte diversi. Ripetere la stessa parte con lo stesso contenuto è invece ammesso. | Verificare la sorgente; per ricominciare annullare la sessione e aprirne una nuova. |
| `UPL-005` | 409 | NON_VALIDO | no | Parti mancanti | La sessione non può essere completata perché alcune parti non sono state ricevute (elenco in dettagli.parti_mancanti). | Inviare le parti mancanti e richiedere di nuovo il completamento. |
| `UPL-006` | 422 | NON_VALIDO | no | Impronta del file diversa da quella dichiarata | *Asincrono.* L'impronta calcolata dal servizio sul file assemblato non corrisponde a quella dichiarata all'apertura della sessione. Compare nel campo errore della sessione. | Verificare il file sorgente e caricarlo in una nuova sessione. |
| `UPL-007` | 409 | NON_VALIDO | no | Idempotency-Key già usata per un'altra sessione | La stessa Idempotency-Key ha già aperto una sessione con nome, dimensione o impronta diversi. | Usare una nuova chiave per un file diverso. |
| `UPL-008` | 409 | RIFIUTATO | no | Caricamento già usato da un altro versamento | Una sessione di caricamento si usa in un solo versamento; questa è già legata a un altro PdV. | Caricare di nuovo il file in una nuova sessione. |
| `UPL-009` | 409 | ERRORE | sì (dopo 5 s) | Caricamento non ancora verificato | Il versamento cita una sessione che non è nello stato COMPLETO (stato attuale in dettagli.stato). | Attendere lo stato COMPLETO (GET della sessione); se lo stato è ERRORE caricare di nuovo il file. |
| `UPL-400` | 400 | NON_VALIDO | no | Richiesta di caricamento non valida | Un'intestazione o un campo della sessione di caricamento non è nella forma prevista (es. X-Impronta-Parte, algoritmo d'impronta non ammesso). | Correggere la richiesta secondo la documentazione delle sessioni di caricamento. |
| `UPL-409` | 409 | NON_VALIDO | no | Operazione non ammessa nello stato della sessione | Lo stato attuale della sessione (in dettagli.stato) non ammette l'operazione richiesta. | Leggere lo stato della sessione e procedere di conseguenza. |
| `UPL-410` | 410 | NON_VALIDO | no | Sessione scaduta o annullata | La sessione di caricamento è scaduta o è stata annullata; i file caricati sono eliminati. | Aprire una nuova sessione e caricare di nuovo il file. |
| `UPL-413` | 413 | NON_VALIDO | no | Corpo della richiesta oltre il limite | Il corpo della richiesta supera la dimensione ammessa per questa operazione. | Ridurre la richiesta; per i file grandi usare le sessioni di caricamento. |
| `UPL-502` | 502 | ERRORE | sì | Verifica del file non riuscita | *Asincrono.* Il file non è stato verificato dopo più tentativi per un errore dello storage. Compare nel campo errore della sessione. | Caricare di nuovo il file in una nuova sessione; se si ripete segnalare l'identificativo della sessione. |
| `UPL-503` | 503 | ERRORE | sì (dopo 30 s) | Servizio di caricamento temporaneamente non disponibile | Lo storage o il database non sono raggiungibili in questo momento. | Ripetere la stessa richiesta più tardi. |
| `UPL-503-OCCUPATO` | 503 | ERRORE | sì (dopo 5 s) | Troppi versamenti multipart contemporanei | La replica del servizio sta già elaborando il numero massimo di versamenti multipart contemporanei (impostazione VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX), oppure lo spazio temporaneo su disco della replica (DISCO_TEMPORANEO_BUDGET_MB, condiviso con PDND) è già prenotato da altre richieste in corso. Il corpo della richiesta non è stato letto (R03 f3). | Ripetere la stessa richiesta (con la stessa Idempotency-Key) dopo i secondi indicati in Retry-After; per i file grandi usare le sessioni di caricamento. |

## SFTP

Canale di versamento via SFTP (cartelle di deposito, esiti, ricevute).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `SFTP-001` | 422 | NON_VALIDO | no | Chiave pubblica SSH non valida | Il testo inviato non è una chiave pubblica in formato OpenSSH («tipo base64 [commento]») oppure il tipo dichiarato non corrisponde al contenuto. | Incollare la riga del file .pub generato con ssh-keygen. |
| `SFTP-002` | 422 | RIFIUTATO | no | Tipo o lunghezza della chiave non ammessi | Sono ammesse solo chiavi dei tipi e delle lunghezze consentiti dal servizio (ed25519, ecdsa, rsa di almeno 3072 bit con le impostazioni predefinite). | Generare una chiave ammessa (es. ssh-keygen -t ed25519) e registrarla. |
| `SFTP-003` | 409 | NON_VALIDO | no | Chiave già registrata per l'account | Fra le chiavi attive dell'account ce n'è già una con la stessa impronta. | Nessuna; per sostituirla revocare prima quella esistente. |
| `SFTP-004` | 409 | NON_VALIDO | no | Nome utente SFTP non disponibile | Il nome di accesso scelto è già in uso (i nomi sono unici fra tutti gli enti). | Scegliere un altro nome utente. |
| `SFTP-404` | 404 | NON_VALIDO | no | Account o chiave SFTP non trovati | L'account o la chiave indicati non esistono in questo ente. | Verificare gli identificativi. |
| `SFTP-409` | 409 | RIFIUTATO | no | Operazione non ammessa nello stato dell'account | L'operazione richiede un account (o un utente responsabile) in uno stato diverso da quello attuale, ad esempio la riattivazione di un account il cui responsabile non è più attivo. | Leggere il dettaglio; se serve indicare un altro utente responsabile. |
| `SFTP-503` | 503 | ERRORE | sì (dopo 30 s) | Gestione degli account SFTP temporaneamente non disponibile | Il database non ha risposto. | Riprovare tra poco; se il problema continua comunicare l'identificativo della richiesta. |
| `SFTP-101` | 422 | NON_VALIDO | no | Indice della cartella assente | *Asincrono.* La cartella di versamento non contiene il file indice.json. | Caricare la cartella con indice.json e rinominarla di nuovo. |
| `SFTP-102` | 422 | NON_VALIDO | no | Indice della cartella non leggibile | *Asincrono.* indice.json non è un oggetto JSON valido in UTF-8 oppure supera la dimensione massima ammessa. | Correggere indice.json (stesso schema dell'indice dell'API REST) e reinviare la cartella. |
| `SFTP-103` | 422 | NON_VALIDO | no | File dichiarato nell'indice assente dalla cartella | *Asincrono.* Un nome_file dell'indice non corrisponde ad alcun file della cartella. | Caricare tutti i file dichiarati (stesso nome, maiuscole comprese) e reinviare la cartella. |
| `SFTP-104` | 422 | NON_VALIDO | no | File della cartella non dichiarato nell'indice | *Asincrono.* La cartella contiene un file che l'indice non dichiara. | Togliere il file o dichiararlo nell'indice e reinviare la cartella. |
| `SFTP-105` | 422 | NON_VALIDO | no | Contenuto della cartella non ammesso | *Asincrono.* La cartella contiene sottocartelle, collegamenti, file temporanei non rinominati oppure un nome_file dell'indice non è un semplice nome di file. | Una cartella per pacchetto, solo file a un livello con nomi semplici; reinviare la cartella. |
| `SFTP-201` | 503 | ERRORE | sì | Servizio di versamento non raggiungibile | *Asincrono.* Il canale SFTP non è riuscito a consegnare il pacchetto al servizio di versamento dopo tutti i tentativi previsti (rete, tempi di risposta, risposte non interpretabili). | Reinviare la stessa cartella più tardi; il reinvio non crea duplicati. |
| `SFTP-202` | 503 | ERRORE | sì | Verifica dei file caricati non conclusa | *Asincrono.* La verifica dei file caricati (impronte e formato) non si è conclusa entro il tempo previsto. | Nessuna se lo stato è IN_RIPROVA; altrimenti reinviare la stessa cartella più tardi. |
| `SFTP-500` | 500 | ERRORE | sì | Errore interno del canale SFTP | *Asincrono.* Errore non previsto durante la lavorazione della cartella; il dettaglio è nei log del conservatore. | Nessuna se lo stato è IN_RIPROVA; altrimenti segnalare al conservatore con l'identificativo della lavorazione. |
| `SFTP-422-IP` | 422 | NON_VALIDO | no | Elenco di indirizzi non valido | Un indirizzo o una rete non è valido, è più ampio del consentito o l'elenco supera il numero massimo di voci. | Correggere l'elenco e salvarlo di nuovo. |

## WH

Webhook firmati verso i sistemi dei clienti (sottoscrizioni, consegne, firme).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `WH-001` | 422 | NON_VALIDO | no | Indirizzo del webhook non valido | L'indirizzo non è un URL https assoluto, contiene credenziali o un frammento, oppure supera la lunghezza ammessa. | Indicare un URL https assoluto senza utente, password e frammento (#). |
| `WH-002` | 422 | RIFIUTATO | no | Indirizzo del webhook non ammesso | L'indirizzo punta a una rete privata, di loopback, locale o riservata (protezione SSRF). | Indicare un indirizzo raggiungibile su Internet. |
| `WH-003` | 422 | NON_VALIDO | no | Tipo di evento sconosciuto | Fra i tipi di evento della sottoscrizione ce n'è uno che non esiste nel catalogo degli eventi. | Usare solo i tipi elencati nella documentazione dei webhook (`dettagli.ammessi`). |
| `WH-004` | 409 | RIFIUTATO | no | Numero massimo di webhook raggiunto | L'ente ha già il numero massimo di sottoscrizioni ammesse. | Eliminare una sottoscrizione non più usata o aggiungere i tipi di evento a una esistente. |
| `WH-010` | 404 | NON_VALIDO | no | Consegna non trovata | La consegna non esiste per questa sottoscrizione e questo ente. | Verificare gli identificativi della sottoscrizione e della consegna. |
| `WH-011` | 409 | RIFIUTATO | no | Consegna non ripetibile | Si può ripetere solo una consegna FALLITA (ritentativi esauriti); lo stato attuale è in `dettagli.stato`. | Attendere l'esito dei ritentativi automatici. |
| `WH-012` | 422 | RIFIUTATO | no | Consegna oltre la retention | La consegna riguarda un evento più vecchio della retention degli eventi per i clienti (RETENZIONE_EVENTI_CLIENTE_GIORNI) e non si può più ripetere; data dell'evento e retention in `dettagli`. | Recuperare lo stato attuale della risorsa con le API di consultazione. |
| `WH-013` | 409 | RIFIUTATO | no | Nessuna rotazione in corso | Il webhook non ha un segreto precedente ancora valido da ritirare. | Nessuna; per un segreto nuovo usare la rotazione. |
| `WH-030` | 503 | ERRORE | no | Webhook non disponibili in questo ambiente | La chiave di cifratura dei segreti dei webhook non è configurata. | Usare l'elenco degli eventi (GET /api/v1/eventi) o segnalare al conservatore. |
| `WH-101` | 504 | ERRORE | sì | Tempo di risposta del destinatario superato | *Asincrono.* Esito di una consegna (campo `ultimo_codice`). Il sistema del cliente non ha risposto entro il tempo massimo. | Rispondere 2xx subito e lavorare l'evento dopo; la consegna sarà ritentata. |
| `WH-102` | 502 | ERRORE | sì | Connessione al destinatario non riuscita | *Asincrono.* Esito di una consegna. Nome non risolto, connessione rifiutata o certificato TLS non valido. | Verificare l'indirizzo, il DNS e il certificato del sistema che riceve; la consegna sarà ritentata. |
| `WH-103` | 502 | ERRORE | sì | Risposta non positiva del destinatario | *Asincrono.* Esito di una consegna. Il destinatario ha risposto con uno stato diverso da 2xx (i reindirizzamenti non si seguono); lo stato è in `ultimo_stato_http`. | Rispondere 2xx quando l'evento è stato ricevuto; la consegna sarà ritentata. |
| `WH-104` | 422 | RIFIUTATO | no | Indirizzo risolto non ammesso | *Asincrono.* Esito di una consegna. Al momento dell'invio il nome del destinatario risolveva verso un indirizzo privato, di loopback o riservato (protezione SSRF); la consegna non è partita. | Correggere il DNS o l'indirizzo del webhook, poi ripetere la consegna. |
| `WH-106` | 500 | ERRORE | no | Segreto del webhook non leggibile | *Asincrono.* Esito di una consegna. Il segreto della sottoscrizione non si può decifrare con le chiavi configurate; la consegna non è partita. | Segnalare al conservatore; eliminare e ricreare il webhook per ottenere un nuovo segreto. |
| `WH-404` | 404 | NON_VALIDO | no | Webhook non trovato | La sottoscrizione non esiste, è stata eliminata o appartiene a un altro ente. | Verificare l'identificativo della sottoscrizione. |
| `WH-503` | 503 | ERRORE | sì (dopo 30 s) | Gestione dei webhook temporaneamente non disponibile | Il database dei webhook non ha risposto. | Riprovare tra poco. |

## EVT

Elenco degli eventi consultabile dai clienti (cursore, filtri).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `EVT-001` | 422 | NON_VALIDO | no | Tipo di evento sconosciuto | Il filtro `tipo` contiene un tipo di evento che non esiste nel catalogo degli eventi. | Usare solo i tipi elencati nella documentazione (`dettagli.ammessi`). |
| `EVT-503` | 503 | ERRORE | sì (dopo 30 s) | Elenco degli eventi temporaneamente non disponibile | Il database degli eventi non ha risposto. | Riprovare tra poco con lo stesso cursore. |

## CMP

Adattatori di compatibilità, tolti in R18.2 (VD-P-191). Nessun codice della famiglia è più emesso; restano elencati perché un codice pubblicato non si riusa.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `CMP-001` | 400 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-002` | 400 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-003` | 400 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-004` | 422 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-010` | 422 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-011` | 422 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-012` | 404 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-013` | 413 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-014` | 422 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-015` | 422 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-016` | 422 | NON_VALIDO | no | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |
| `CMP-503` | 503 | ERRORE | sì (dopo 5 s) | Dismesso: adattatori di compatibilità tolti in R18.2 | *Dismesso: non più emesso.* Codice degli adattatori di compatibilità del gateway, tolti in R18.2 (VD-P-191). Non è più emesso. | Versare con le interfacce di VerumDoc (REST, sessioni di caricamento, PDND, SFTP), descritte in docs/integrazione/README.md. |

## RGL

Motore delle regole (metadati per classe, severità, versioni delle regole).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `RGL-101` | 422 | RIFIUTATO | no | Metadato minimo dell'Allegato 5 mancante | Regola (rilievo in `dettagli.rilievi` di VRS-130 o fra gli `avvisi`). Il documento principale non ha uno dei metadati minimi dell'Allegato 5 alle Linee guida AgID. Regola bloccante imposta dalla norma (locked). | Valorizzare il metadato indicato in `campo` nei `parametri` del documento principale. |
| `RGL-102` | 422 | RIFIUTATO | no | Metadato obbligatorio della classe mancante | Regola (rilievo in `dettagli.rilievi` di VRS-130 o fra gli `avvisi`). Il documento principale non ha un metadato obbligatorio per la classe documentale o per il suo profilo di metadati. | Valorizzare il metadato indicato in `campo` (nei `parametri` o in `parametri.metadati_extra`). |
| `RGL-103` | 422 | NON_VALIDO | no | Metadato non previsto | Regola (predefinito AVVISA, fra gli `avvisi`; se configurata per bloccare, rilievo di VRS-130). Un metadato dei `parametri` non è previsto né dallo schema né dal profilo della classe; è conservato così come versato, mai scartato. | Verificare il nome del metadato o chiedere di aggiungerlo al profilo della classe. |
| `RGL-104` | 422 | RIFIUTATO | no | File oltre la dimensione massima della classe | Regola (rilievo in `dettagli.rilievi` di VRS-130 o fra gli `avvisi`). Un file supera la dimensione massima per file della classe documentale. | Ridurre il file o versarlo in una classe che lo ammette. |
| `RGL-105` | 422 | RIFIUTATO | no | Formato non ammesso dalla classe documentale | Regola (rilievo in `dettagli.rilievi` di VRS-130 o fra gli `avvisi`). Il formato riconosciuto dal contenuto del file non è fra quelli ammessi dalla classe. Sostituisce VRS-112. | Convertire il file in uno dei formati ammessi indicati nel messaggio. |
| `RGL-106` | 422 | RIFIUTATO | no | Metadato di tipo non valido | Regola (rilievo in `dettagli.rilievi` di VRS-130 o fra gli `avvisi`). Un metadato del profilo della classe non rispetta il tipo previsto (data, numero, codice fiscale, partita IVA, codice IPA, valori ammessi). Il messaggio non riporta il valore. | Correggere il valore del metadato indicato in `campo`. |
| `RGL-107` | 422 | RIFIUTATO | no | Formato rifiutato dal catalogo dei formati | Regola (rilievo in `dettagli.rilievi` di VRS-130, o del passo 10 della verifica con VLD-1002). Il formato del file, riconosciuto dal contenuto (MIME al versamento, PUID PRONOM e veraPDF nella verifica), ha esito RIFIUTATO nel catalogo dei formati (FORMATO.<ID>.ESITO del conservatore, dell'ente o della classe; R08, D02). | Convertire il file in un formato ammesso dal catalogo (vedi il validatore preventivo, `file_rifiutati`). |
| `RGL-108` | 422 | NON_VALIDO | no | Formato ammesso con avviso o da convertire | Avviso (fra gli `avvisi`, poi nel rapporto di versamento). Il formato del file ha esito AVVISO o DA_CONVERTIRE nel catalogo dei formati (R08, D02). | Nessuna per il versamento; pianificare la conversione dei file segnalati. |
| `RGL-109` | 422 | RIFIUTATO | no | Firme insufficienti per il livello di verifica | Regola valutata dalla verifica (passo 11, VLD-1101 se bloccante; altrimenti fra gli avvisi del RdV). La firma di un file non soddisfa FIRME_LIVELLO_VERIFICA della classe (RILEVATA, CRITTOGRAFICA, CON_REVOCHE): il motivo in parole è nel messaggio, l'esito normalizzato nel rapporto di verifica (R08, D01). | Versare il documento con una firma valida, di un certificato non revocato e riconducibile a un prestatore fidato. |
| `RGL-113` | 422 | NON_VALIDO | no | Firma indeterminata ammessa con avviso | Regola valutata dalla verifica (passo 11): una firma INDETERMINATE (firma integra e catena attendibile, ma per esempio certificato scaduto o revoca non determinabile) che il conservatore ha scelto di ammettere con avviso (FIRME_INDETERMINATE_EFFETTO = AVVISA). Con il valore predefinito (BLOCCA) le firme indeterminate sono rilievi di RGL-109 (revisione R08, X1). | Nessuna azione obbligatoria; verificare il certificato del firmatario e, se possibile, versare il documento con una firma valida o con marca temporale. |
| `RGL-503` | 503 | ERRORE | sì (dopo 300 s) | Configurazione delle regole non valida | Un valore di configurazione del motore delle regole (impostazione o profilo di metadati) salvato per il cliente o la classe non è valido; il versamento è sospeso per non applicare regole indovinate. | Segnalare al conservatore indicando l'identificativo della richiesta. |
| `RGL-110` | 422 | NON_VALIDO | no | Impostazioni personalizzate per il versamento | Avviso (fra gli `avvisi`, mai una risposta HTTP). Il versamento porta una personalizzazione motivata delle impostazioni (`override` dell'indice, livello PDV); motivo e valori sono nella fotografia delle regole del PdV e nel rapporto di versamento. | Nessuna; verificare che la personalizzazione sia voluta. |
| `RGL-111` | 422 | NON_VALIDO | no | Personalizzazione del versamento non ammessa | L'`override` dell'indice non è ammesso. Motivo mancante o più corto di 10 caratteri, impostazione sconosciuta, non personalizzabile sul singolo versamento (imposta dalla norma, riservata al conservatore o fuori dal suo ambito), oppure valore del tipo sbagliato. L'elenco è in `dettagli.problemi`. | Correggere o togliere l'`override` e reinviare. |
| `RGL-112` | 422 | NON_VALIDO | no | Accordo di versamento non valido | L'accordo indicato nel campo `accordo` dell'indice non esiste per l'ente, non è in vigore nel giorno del versamento o non vale per la classe documentale. Il servizio non sceglie un altro accordo al suo posto. | Indicare un accordo in vigore per la classe, oppure omettere `accordo` per usare quello dell'ente. |

## CFG

Configurazione del servizio per conservatore ed enti (impostazioni, regole, profili di metadati, accordi di versamento, quote) con bozze, anteprima, applicazione e proposte; validatore preventivo (R05).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `CFG-400` | 400 | NON_VALIDO | no | Richiesta di configurazione non valida | La bozza, l'ambito o il cursore non sono coerenti (livello non ammesso per la risorsa, ente o classe mancanti, cursore alterato). Il motivo è nel dettaglio. | Correggere la richiesta. |
| `CFG-403` | 403 | RIFIUTATO | no | Modifica della configurazione non consentita | Il ruolo non può modificare la configurazione a quel livello (il conservatore configura i livelli CONSERVATORE ed ENTE; l'amministratore dell'ente i livelli ENTE, CLASSE e ACCORDO del proprio ente). | Chiedere a chi ha il ruolo adatto. |
| `CFG-404` | 404 | NON_VALIDO | no | Elemento della configurazione non trovato | La bozza, la proposta, l'ente, la classe o il profilo non esistono o non sono visibili a chi chiede. | Verificare l'identificativo. |
| `CFG-409` | 409 | RIFIUTATO | no | Piano cambiato | PIANO_CAMBIATO. Dall'anteprima la configurazione è cambiata (un'altra modifica applicata, la bozza aggiornata): l'impronta del piano non coincide e nulla è stato scritto. Il piano nuovo è in `dettagli.piano`. | Rileggere il piano e ripetere l'applicazione con la nuova `impronta_piano`. |
| `CFG-410` | 409 | RIFIUTATO | no | Bozza modificata da altri | La versione della bozza indicata non è quella attuale. La versione attuale è in `dettagli.versione`. | Rileggere la bozza e ripetere la modifica. |
| `CFG-411` | 409 | RIFIUTATO | no | Bozza non più modificabile | La bozza è già stata applicata o annullata. | Creare una nuova bozza. |
| `CFG-412` | 409 | RIFIUTATO | no | Proposta già decisa | La proposta è già stata accettata o rifiutata. | Nessuna. |
| `CFG-422` | 422 | NON_VALIDO | no | Piano con elementi bloccati | L'anteprima ha elementi bloccati (valore imposto dalla norma, fuori ambito, tipo errato, permessi): la bozza non si applica. L'elenco è in `dettagli.bloccati`. | Correggere la bozza togliendo gli elementi bloccati. |
| `CFG-423` | 422 | NON_VALIDO | no | Decorrenza non ammessa | La data `valido_dal` non è ammessa. Gli accordi e i profili decorrono da oggi o da una data futura (mai nel passato né prima della versione precedente); R07 (D28): le impostazioni di conservatore, ente e classe si possono programmare da una data futura (la bozza diventa PROGRAMMATA); le proposte valgono dall'applicazione. | Indicare una decorrenza ammessa o ometterla. |
| `CFG-424` | 409 | RIFIUTATO | no | Modifica programmata non annullabile | R07 (D28): la bozza non è programmata (già applicata, annullata o ancora aperta) e non si può annullare come modifica programmata. | Rileggere lo stato della bozza. |
| `CFG-503` | 503 | ERRORE | sì (dopo 30 s) | Configurazione temporaneamente non disponibile | Il database non ha risposto o la configurazione salvata non è leggibile; nulla è stato scritto. | Ritentare più tardi; se persiste, segnalare al conservatore con l'identificativo della richiesta. |

## QTA

Quote e limiti di consumo per piano contrattuale.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `QTA-001` | 429 | ERRORE | sì (dopo 60 s) | Limite di richieste al minuto superato | L'ente ha superato il numero di richieste al minuto previsto dal suo piano (vale per token, chiavi API e canali). | Attendere i secondi indicati in Retry-After e riprovare; per limiti più alti rivolgersi al conservatore. |
| `QTA-002` | 429 | ERRORE | sì (dopo 3600 s) | Limite giornaliero di versamenti superato | L'ente ha raggiunto il numero di pacchetti di versamento al giorno previsto dal suo piano. | Riprovare dal giorno successivo (Retry-After indica i secondi alla mezzanotte, ora italiana) o chiedere al conservatore un piano adeguato. |

## CAT

Registro delle impronte a due livelli (checkpoint firmati, prove d'inclusione e di consistenza, contratto K1 di R04).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `CAT-400-CURSORE` | 400 | NON_VALIDO | no | Cursore non valido | Il cursore indicato non è stato prodotto da questo elenco del registro (allarmi). | Ripartire dalla prima pagina, senza cursore. |
| `CAT-403` | 403 | RIFIUTATO | no | Lettura del registro non autorizzata | Le foglie del registro sono riservate ai ruoli del conservatore e ai token di servizio con lo scope di lettura del registro (testimone, verificatori). | Usare un'utenza del conservatore o il token di servizio previsto. |
| `CAT-404` | 404 | NON_VALIDO | no | Elemento del registro non trovato | Il checkpoint, il PdA o il documento indicati non esistono (o il PdA appartiene a un altro ente). | Verificare URN del PdA, identificativo del documento e dimensione del checkpoint. |
| `CAT-409` | 409 | ERRORE | sì (dopo 60 s) | PdA non ancora coperto da un checkpoint | Il PdA non è ancora nel registro oppure è stato aggiunto dopo il checkpoint richiesto. | Riprovare dopo il prossimo checkpoint (cadenza CATENA_CHECKPOINT_SECONDI) o chiedere la prova rispetto all'ultimo checkpoint. |
| `CAT-409-RISCONTRO` | 409 | RIFIUTATO | no | Foglia del ciclo di vita senza riscontro | Il registro aggiunge una foglia di scarto, annullamento o rettifica solo se il fatto risulta dal database: pratica del tipo indicato, dello stesso ente del PdA, nello stato in cui la foglia si chiede, e PdA (o PdV) nello stato atteso (scarto: oggetto ELIMINATO e PdA in ELIMINAZIONE; annullamento: PdV ANNULLATO), con il fatto di esecuzione scritto dal lavoratore di ciclo-vita (scarto: PdA eliminato; annullamento: atto archiviato con l'impronta della foglia; rettifica: PdV di rettifica versato, R06). | Verificare la pratica indicata in riferimento.pratica e lo stato del PdA; il servizio ciclo-vita chiede la foglia solo dopo aver registrato il fatto. |
| `CAT-422` | 422 | NON_VALIDO | no | Dimensioni del registro non valide | La prova di consistenza richiede 0 ≤ da ≤ a ≤ dimensione dell'ultimo checkpoint. | Indicare dimensioni di checkpoint emessi. |
| `CAT-503` | 503 | ERRORE | sì (dopo 30 s) | Registro temporaneamente non disponibile | Il database del registro non ha risposto. | Riprovare tra poco. |
| `CAT-WORM-DIVERSO` | 500 | ERRORE | no | Oggetto del registro diverso nello storage conservativo | Un oggetto del registro delle impronte (checkpoint, segmento di foglie, albero del PdA o RdV) è già presente nello storage conservativo con un contenuto diverso; nulla è stato sovrascritto ed è stato registrato un allarme di integrità. | Nessuna per il cliente; il conservatore indaga l'allarme di integrità. |

## TST

Testimone indipendente della catena delle impronte (controfirma dei checkpoint del registro, protocollo ispirato a C2SP tlog-witness).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `TST-400` | 400 | NON_VALIDO | no | Richiesta di controfirma non valida | Il corpo non rispetta il protocollo del testimone (riga «old», righe della prova in base64, checkpoint in formato signed note) oppure la dimensione precedente supera quella del checkpoint. | Correggere il client del registro secondo il protocollo K3 e reinviare. |
| `TST-401` | 401 | RIFIUTATO | no | Registro non autenticato | Manca il token di servizio del registro oppure non è fra quelli accettati dal testimone. | Configurare nel registro il token concordato con il testimone (TESTIMONE_TOKEN). |
| `TST-403` | 403 | RIFIUTATO | no | Firma del registro non valida | Il checkpoint non porta una firma valida della chiave del registro configurata nel testimone. | Verificare che il registro firmi con la chiave pubblicata e che il testimone abbia la chiave pubblica corretta. |
| `TST-404` | 404 | NON_VALIDO | no | Checkpoint non trovato | Il testimone non ha controfirmato alcun checkpoint con l'origine o la dimensione richieste. | Controllare origine e dimensione; il testimone conserva solo i checkpoint che ha controfirmato. |
| `TST-405` | 404 | RIFIUTATO | no | Origine del registro non ammessa | L'origine del checkpoint (prima riga) non è fra quelle che questo testimone osserva. | Usare l'origine dell'ambiente corretto o aggiungerla alle origini ammesse del testimone. |
| `TST-409` | 409 | NON_VALIDO | no | Dimensione precedente non coincidente | La dimensione «old» indicata dal registro non è l'ultima controfirmata dal testimone. Per compatibilità con il protocollo tlog-witness il corpo della risposta è text/plain con la sola dimensione nota al testimone. | Ricalcolare la prova di consistenza a partire dalla dimensione restituita e reinviare. |
| `TST-413` | 413 | NON_VALIDO | no | Richiesta di controfirma troppo grande | Il corpo supera la dimensione massima o il numero massimo di righe della prova ammessi dal testimone. | Controllare il client del registro; una prova di consistenza ha al più qualche decina di righe. |
| `TST-422` | 422 | RIFIUTATO | no | Prova di consistenza non valida | La prova non dimostra che il nuovo checkpoint estende l'ultimo controfirmato. L'esito è registrato come allarme del testimone. | Verificare il registro (un registro integro produce sempre una prova valida). Se la prova è corretta, segnalare al conservatore. |
| `TST-423` | 423 | RIFIUTATO | no | Testimone bloccato per incoerenza del registro | Il testimone ha ricevuto due checkpoint firmati dal registro con la stessa dimensione e radici diverse (fork) e non controfirma più nulla finché un operatore del testimone non interviene. | Avviare la procedura di incidente della catena; lo sblocco è solo manuale e registrato. |
| `TST-500` | 500 | ERRORE | sì | Errore interno del testimone | Errore non previsto del testimone; il dettaglio è solo nei log con l'identificativo della richiesta. | Ritentare più tardi; se persiste segnalare con l'identificativo della richiesta. |
| `TST-503` | 503 | ERRORE | sì (dopo 30 s) | Testimone non disponibile | Il database del testimone non risponde o il testimone non è configurato (chiave, chiave del registro). | Ritentare più tardi. |

## IDEM

Idempotency-Key delle richieste che creano una risorsa fuori dal versamento (richiesta di un PdD, creazione di un webhook).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `IDEM-400` | 400 | NON_VALIDO | no | Idempotency-Key non valida | L'intestazione Idempotency-Key deve avere da 8 a 200 caratteri fra lettere, cifre e i segni punto, trattino basso, due punti e trattino (stessa regola del versamento). | Correggere la chiave; usarne una nuova per ogni operazione diversa. |
| `IDEM-409` | 409 | NON_VALIDO | no | Idempotency-Key già usata con una richiesta diversa | La stessa Idempotency-Key è già stata usata, entro la sua validità, con un corpo diverso. Nessuna risorsa nuova è stata creata. | Usare una nuova chiave per una richiesta diversa; per ripetere la stessa richiesta inviare lo stesso corpo. |
| `IDEM-409-IN-CORSO` | 409 | ERRORE | sì (dopo 2 s) | Richiesta con la stessa Idempotency-Key in corso | Una richiesta identica con la stessa chiave è ancora in elaborazione. | Ripetere la richiesta dopo l'attesa indicata in Retry-After; riceverà lo stesso esito. |

## INT

Verifica continua dell'integrità dell'archivio (rilettura periodica e su richiesta degli oggetti conservati, registro delle verifiche).

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `INT-400` | 400 | NON_VALIDO | no | Cursore del registro delle verifiche non valido | Il cursore indicato non è nel formato emesso dal servizio (copiato male o costruito a mano). | Ripartire dalla prima pagina senza cursore e usare il campo `cursore_successivo` della risposta. |
| `INT-403` | 403 | RIFIUTATO | no | Ruolo non autorizzato a richiedere verifiche d'integrità | Le verifiche su richiesta le avviano il personale del conservatore (SUPER_ADMIN, RSC_CONSERVATORE) e l'amministratore dell'ente sui propri pacchetti. | Chiedere la verifica a un utente con il ruolo adatto. |
| `INT-404` | 404 | NON_VALIDO | no | Pacchetto, ente o richiesta di verifica non trovati | Uno o più PdA indicati, l'ente o la richiesta di verifica non esistono o non sono visibili con le credenziali usate. | Controllare gli identificativi (URN o id del PdA, id dell'ente, id della richiesta). |
| `INT-409` | 409 | RIFIUTATO | no | Verifica dell'ente già in corso | Per l'ente è già attiva una verifica di tutti i pacchetti; una seconda rileggerebbe gli stessi oggetti. | Seguire lo stato della richiesta indicata in `dettagli.richiesta_id`. |
| `INT-422` | 422 | NON_VALIDO | no | Richiesta di verifica non valida | Va indicato un elenco di PdA (al più il numero ammesso) oppure un ente, non entrambi né nessuno dei due. | Correggere il corpo della richiesta e reinviarla. |
| `INT-503` | 503 | ERRORE | sì (dopo 30 s) | Registro delle verifiche non disponibile | Il database del servizio non ha risposto; nessuna verifica avviata o letta. | Ritentare più tardi. |

## ACC

R10 (D08): accesso delle persone con password e codice a tempo o con passkey, inviti per e-mail con attivazione guidata, fornitori d'identità e metodi d'accesso degli enti.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `ACC-401` | 401 | RIFIUTATO | no | Accesso non riuscito | Indirizzo e-mail, password o codice non corretti, oppure accesso temporaneamente bloccato dopo troppi tentativi. Il messaggio è lo stesso in ogni caso: non rivela se l'utente esiste. | Controllare i dati e riprovare; dopo il blocco attendere qualche minuto. |
| `ACC-403-METODO` | 403 | RIFIUTATO | no | Metodo d'accesso non ammesso | L'ente non ammette questo metodo d'accesso, o il conservatore non l'ha abilitato. | Accedere con un metodo ammesso dall'ente o chiedere all'amministratore dell'ente. |
| `ACC-400-PASSAGGIO` | 400 | NON_VALIDO | no | Passaggio scaduto o già usato | Il passaggio dell'accesso o dell'attivazione non è valido, è scaduto o è stato iniziato in un altro browser. | Ricominciare l'accesso o riaprire il collegamento d'invito. |
| `ACC-404-INVITO` | 404 | RIFIUTATO | no | Invito non valido | Il collegamento d'invito non è valido, è scaduto, è già stato usato o l'indirizzo e-mail non corrisponde. | Chiedere un nuovo invito all'amministratore dell'ente. |
| `ACC-409-INVITO` | 409 | RIFIUTATO | no | Invito non possibile | L'utente è già attivo, oppure l'indirizzo e-mail è già usato nell'ente. | Controllare l'elenco degli utenti dell'ente. |
| `ACC-422-PASSWORD` | 422 | NON_VALIDO | no | Password non ammessa | La password non rispetta la lunghezza minima o le altre regole indicate. | Scegliere una password più lunga. |
| `ACC-422-CODICE` | 422 | NON_VALIDO | no | Codice a tempo non valido | Il codice a sei cifre non corrisponde o è già stato usato. | Inserire il codice attuale dell'app di autenticazione. |
| `ACC-400-PASSKEY` | 400 | RIFIUTATO | no | Passkey non verificata | La passkey non è stata riconosciuta o la firma non è valida. | Riprovare o accedere con un altro metodo. |
| `ACC-422-FORNITORE` | 422 | NON_VALIDO | no | Configurazione del fornitore non valida | Mancano campi obbligatori o un indirizzo non è https. | Correggere la configurazione e salvare di nuovo. |
| `ACC-429` | 429 | ERRORE | sì (dopo 900 s) | Troppi tentativi | Troppi tentativi d'accesso da questo indirizzo in poco tempo. | Attendere qualche minuto e riprovare. |
| `ACC-403-CREDENZIALI` | 403 | RIFIUTATO | no | Gestione delle credenziali non disponibile | Le proprie credenziali si gestiscono solo da una sessione personale della console (non con una chiave API o un token di canale), oppure l'ente ammette solo SPID o CIE, oppure il metodo chiesto non è ammesso dall'ente. | Accedere alla console con la propria utenza; con solo SPID o CIE le credenziali si gestiscono presso il gestore dell'identità digitale. |
| `ACC-403-RIAUTENTICAZIONE` | 403 | RIFIUTATO | no | Conferma dell'identità richiesta | Ogni modifica delle credenziali, e ogni decisione sensibile del ciclo di vita (R17), richiede di confermare la propria identità da poco (password e codice a tempo, o passkey; con solo SPID o CIE l'accesso appena fatto) in questa sessione; la conferma è scaduta, manca o è già stata usata per un'altra decisione. | Confermare l'identità e ripetere l'operazione entro la finestra indicata. |
| `ACC-403-CONFERMA-DECISIONE` | 403 | RIFIUTATO | no | Conferma della decisione non concessa | Il gateway firma la conferma di una decisione sensibile (scarto, annullamento, rettifica, riversamento in uscita, ripresa) solo per una sessione personale della console, per un'azione del proprio ruolo e del proprio ente (o del conservatore), dopo una conferma dell'identità recente in questa sessione; ogni conferma dell'identità vale per UNA sola decisione. | Confermare di nuovo la propria identità e ripetere la decisione; per un'azione di un altro ruolo, farla fare a chi ne ha titolo. |
| `ACC-422-CONFERMA-DECISIONE` | 422 | NON_VALIDO | no | Richiesta di conferma non valida | L'azione, la pratica, la versione attesa o l'impronta del contenuto indicate non sono valide (impronta SHA-256 esadecimale; una richiesta che crea la pratica non indica pratica né versione). | Ricaricare la pagina e ripetere la decisione. |
| `ACC-503-CONFERMA-DECISIONE` | 503 | ERRORE | sì (dopo 60 s) | Conferma delle decisioni non disponibile | La chiave con cui il gateway firma le conferme delle decisioni non è configurata o non è valida: nessuna decisione sensibile si conferma (fail-closed). | Segnalare all'operatore dell'infrastruttura: chiave CONFERME_CHIAVE_PRIVATA e CONFERME_KID nel Secret del gateway (runbook conferme_decisione). |
| `ACC-422-RIAUTENTICAZIONE` | 422 | RIFIUTATO | no | Credenziali non corrette | La password, il codice o la passkey indicati per confermare l'identità non sono corretti, oppure l'accesso con password è temporaneamente bloccato dopo troppi tentativi. | Controllare i dati e riprovare; dopo il blocco attendere qualche minuto. |
| `ACC-404-CREDENZIALE` | 404 | RIFIUTATO | no | Credenziale non trovata | La passkey indicata non esiste o non è tua, oppure non hai una password o un codice a tempo da cambiare. | Ricaricare la pagina delle credenziali. |
| `ACC-409-ULTIMA` | 409 | RIFIUTATO | no | Ultima credenziale utilizzabile | Non si può revocare l'ultima credenziale con cui accedere con i metodi ammessi dall'ente: resteresti senza accesso. | Aggiungere prima un'altra passkey (o un altro metodo ammesso) e poi revocare questa. |
| `ACC-503` | 503 | ERRORE | sì (dopo 30 s) | Accesso locale non disponibile | La chiave di cifratura delle credenziali non è configurata o un servizio non ha risposto. | Riprovare più tardi; se il problema continua avvisare il conservatore. |

## RUO

R10 (D30): ruoli personalizzati dell'ente composti dai permessi concedibili.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `RUO-403` | 403 | RIFIUTATO | no | Permessi non concedibili | Il ruolo chiede permessi non concedibili, mai delegabili o che chi lo crea non ha; oppure un ruolo base non ammesso. | Togliere i permessi indicati. |
| `RUO-404` | 404 | RIFIUTATO | no | Ruolo non trovato | Il ruolo personalizzato non esiste in questo ente. | Ricaricare l'elenco dei ruoli. |
| `RUO-409` | 409 | RIFIUTATO | no | Ruolo in conflitto | Esiste già un ruolo con questo nome, il ruolo è stato modificato da un'altra persona o è ancora assegnato a qualcuno. | Ricaricare la pagina e riprovare. |

## NTF

R10 (D32): campanella «da fare per te» e preferenze delle notifiche.

| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |
|---|---|---|---|---|---|---|
| `NTF-409` | 409 | RIFIUTATO | no | Preferenze cambiate | Le preferenze sono state salvate da un'altra sessione. | Ricaricare la pagina e riprovare. |
| `NTF-422-OBBLIGATORIA` | 422 | RIFIUTATO | no | Avviso obbligatorio | Questo avviso lo ha reso obbligatorio l'ente (o il conservatore) e non si può spegnere. | Lasciare l'avviso attivo o chiedere all'amministratore dell'ente. |
| `NTF-403-PEC` | 403 | RIFIUTATO | no | Canali PEC riservati al conservatore | Registro, prova e ricevute dei canali PEC li gestisce un ruolo fisso del conservatore con il permesso mai delegabile pec_canali:gestisci. | Chiedere al conservatore. |
| `NTF-404-PEC` | 404 | RIFIUTATO | no | Canale, messaggio o ricevuta PEC non trovati | L'elemento indicato non esiste o non appartiene all'ente. | Ricaricare l'elenco. |
| `NTF-409-PEC` | 409 | RIFIUTATO | no | Canale PEC modificato da un'altra persona | La versione letta non è più quella attuale. | Ricaricare la pagina e ripetere la modifica. |
| `NTF-422-PEC` | 422 | NON_VALIDO | no | Configurazione del canale PEC non valida | I motivi sono in `dettagli.motivi` (campi mancanti, segreti nella configurazione, canale di sviluppo in produzione, connessioni non cifrate). | Correggere la configurazione; i segreti vanno nel Secret del lavoratore della posta (PEC_SEGRETI_JSON). |
| `NTF-429-PEC` | 429 | ERRORE | sì (dopo 600 s) | Troppe prove di invio PEC | Nell'ultima ora sono già state chieste le prove ammesse (PEC_PROVE_PER_ORA). | Ritentare più tardi. |
| `NTF-503` | 503 | ERRORE | sì (dopo 30 s) | Notifiche non disponibili | Il database del servizio non ha risposto. | Ritentare più tardi. |
