# Guida all'API per i produttori

> Per chi integra un sistema (protocollo, gestionale, ERP, piattaforma di una PA) con VerumDoc per versare documenti in conservazione e leggerne lo stato. Rilascio R03.
>
> Riferimenti: contratto completo [`docs/catalogo/openapi.yaml`](../catalogo/openapi.yaml) (OpenAPI 3.1 unica, generata dal codice) e il suo indice [`endpoint.md`](../catalogo/endpoint.md); codici d'errore [`errori.md`](../catalogo/errori.md); SDK Python [`sdk/`](../../sdk/README.md).

Tutti i canali portano allo **stesso caso d'uso di versamento**: stesse regole di conservazione, stessi controlli su file e metadati, stessi codici d'errore, stessa idempotenza, stesse quote. Cambia solo il modo in cui il pacchetto arriva. Il versamento è asincrono: la risposta positiva dice che il pacchetto di versamento (PdV) è **preso in carico**; verifica e conservazione proseguono dopo e si seguono con lo stato, gli eventi o i webhook.

Indirizzo dell'API: `https://api.verumdoc.it` (le guide indicano i percorsi). Il server SFTP ha il suo indirizzo, indicato nell'account.

## 1. Quale canale usare

| Canale | Quando usarlo | Ingresso | Guida |
|---|---|---|---|
| **REST multipart** | Il caso normale: un'unità documentaria con file fino a 100 MB ciascuno, in una sola richiesta. | `POST /api/v1/versamento/ud` (`multipart/form-data`) | §3 di questa guida |
| **Sessioni di caricamento** + versamento per riferimento | File grandi (fino a 5 GiB), reti instabili, invio a parti parallele e riprendibili. | `POST /api/v1/caricamenti`, poi `POST /api/v1/versamento/ud` in JSON | [caricamenti.md](caricamenti.md) |
| **SFTP** | Sistemi che depositano file invece di chiamare un'API (lotti notturni, sistemi legacy). | cartelle `in/<pacchetto>/` con `indice.json` | [sftp.md](sftp.md) |
| **PDND** | PA che usano la Piattaforma Digitale Nazionale Dati (interoperabilità ModI), con voucher PDND. Un documento per richiesta, file in base64. | `POST /api/pdnd/v1/versamento` | §8 di questa guida |

Per una nuova integrazione conviene il REST (multipart o, per file grandi, sessioni di caricamento), meglio ancora con l'[SDK Python](../../sdk/README.md), che sceglie da solo fra i due modi, calcola le impronte, gestisce `Idempotency-Key`, ritentativi e ripresa. **Non esiste un'interfaccia SOAP** né un adattatore verso interfacce di altri soggetti (tolti in R18.2, §12).

## 2. Autenticazione

| Canale | Credenziali | Note |
|---|---|---|
| REST, sessioni di caricamento, stato, RdV | Chiave API (`X-Api-Key: vd_<id>_<segreto>`, oppure `X-Api-Key-Id` + `X-Api-Key-Secret`) o `Authorization: Bearer <token utente>` | Serve il permesso `versamento:ud` (la chiave API lo ha). |
| SFTP | Chiave pubblica SSH registrata sull'account (niente password) | Verso il gateway il canale usa un proprio token di canale: il cliente non lo vede. |
| PDND | `Authorization: Bearer <voucher PDND>` | Il `client_id` del voucher deve essere registrato come fruitore dell'ente; altrimenti `403 PDND-403`. |
| Eventi (`GET /api/v1/eventi`), elenco versamenti, ricerca, PdD | Chiave API o Bearer utente | |
| Gestione dei webhook (`/api/v1/webhook`) | Solo `Authorization: Bearer` di un amministratore dell'ente (`ADMIN_TENANT`) | La chiave API riceve `403 AUTH-010`. |

**La chiave API** è la credenziale dei sistemi versanti. La genera un amministratore dell'ente dal portale (o con `POST /api/v1/onboarding/tenant/{tenant_id}/api-keys`), indicando l'utente dell'ente che ne risponde. Il segreto si vede **una sola volta**, alla creazione; VerumDoc ne conserva solo l'impronta. Una chiave vale solo per il proprio ente (permessi: versare e leggere) e smette di funzionare se viene revocata, se scade, se l'ente è sospeso o se l'utente responsabile è disattivato (`401 AUTH-003`).

Per sapere a quale ente e a quale piano corrisponde una credenziale: `GET /api/v1/me` restituisce `tenant_id`, `ruolo`, `scopes`, `piano`, `auth_method` (`jwt`, `api_key`, `token_canale`), `canale`, `ente` {`id`, `ragione_sociale`, `stato`} e, con la chiave API, `chiave_api` {`id`, `responsabile` {`user_id`, `nome`, `cognome`}}.

Ogni risorsa (PdV, sessione, evento, webhook) è visibile solo all'ente che l'ha creata: un identificativo di un altro ente risponde `404`, come se non esistesse.

## 3. Versare (REST)

```
POST /api/v1/versamento/ud
X-Api-Key: vd_<id>_<segreto>
Idempotency-Key: erp-2026-000123
Content-Type: multipart/form-data

indice = { …IndiceUD in JSON… }
files  = fattura.pdf            (un campo «files» per ogni file dell'indice)
```

L'indice dell'unità documentaria (`IndiceUD`) e i metadati sono descritti in [`METADATI_AGID_VERSAMENTO.md`](../../METADATI_AGID_VERSAMENTO.md). In breve: `classe_documentale`, `chiave` {`registro`, `anno`, `numero`}, `documenti[]` con `id`, `tipo` (`PRINCIPALE`, `ALLEGATO`, `ANNESSO`, `ANNOTAZIONE`), `nome_file`, `formato` (facoltativo), `hash` {`impronta`, `algoritmo`} e `parametri` (metadati dell'Allegato 5 e della classe). Ogni file allegato deve comparire nell'indice e viceversa. L'elenco dei documenti è **piatto**: `allegati` annidati non vuoti sono rifiutati con `400 VRS-114` (gli allegati sono documenti con `tipo` `ALLEGATO`, `ANNESSO` o `ANNOTAZIONE`).

Controlli prima di trasferire i file:
- **corpo multipart** al più `VERSAMENTO_REST_MAX_CORPO_MB` (predefinito 110 MB, come il limite dell'ingress), contato anche con `Transfer-Encoding: chunked`: oltre, `413 UPL-413`. Senza credenziali la risposta è `401` prima di leggere il corpo;
- **estensione dei file**: deve essere fra quelle dei formati ammessi dalla classe (o, se la classe non li limita, del registro dei formati); altrimenti `400 SEC-006` con `dettagli.ammesse`. Prima l'errore arrivava dopo il trasferimento (`422 VRS-130`, rilievo `RGL-105`);
- **metadati minimi** (Allegato 5) solo al primo livello di `parametri`: in `metadati_extra` non valgono (`422 VRS-130`, rilievo `RGL-101`). La verifica asincrona applica la stessa regola, quindi un PdV accettato con `202` non è poi rifiutato per lo stesso motivo.

Per file oltre 100 MB, o per riprendere un invio interrotto, si usa lo stesso percorso in JSON citando sessioni di caricamento già complete: vedi [caricamenti.md](caricamenti.md).

### Esiti

| Risposta | Significato |
|---|---|
| `202 Accepted` | PdV nuovo, preso in carico (`stato: RICEVUTO`). Intestazione `Location: /api/v1/versamento/{pdv_id}`. |
| `200 OK` + `Idempotent-Replayed: true` | Stessa richiesta già accettata (stessa `Idempotency-Key`, stesso contenuto): stesso PdV, nessun duplicato. |
| `4xx`/`5xx` `application/problem+json` | Rifiuto o errore, con il `codice` del catalogo (§6). |

Corpo di `202`/`200`: `{pdv_id, stato, message, avvisi[], link_stato, rdv_preliminare_disponibile, nota_rdv}`. Gli `avvisi` sono rilievi non bloccanti (es. `RGL-103` metadato non previsto, conservato così come versato; `VRS-126` versamento senza `Idempotency-Key`): vanno letti e, se serve, corretti nei versamenti successivi.

Dopo la presa in carico il PdV passa per questi stati:

```
RICEVUTO → IN_VALIDAZIONE → VALIDATO → IN_BUILD → CONSERVATO
                    └──────────────→ ERRORE  (verifica fallita: motivo in «errori»)
```

- Stato: `GET /api/v1/versamento/{pdv_id}`, con il rapporto di versamento (RdV) **preliminare** nel campo `rdv_preliminare`.
- RdV **definitivo** (XML): `GET /api/v1/versamento/{pdv_id}/rdv` quando il PdV è `CONSERVATO`; prima risponde `409 VRS-409` (ritentabile). L'intestazione `X-Impronta-SHA256` porta l'impronta del file restituito.
- Da R04 lo stato `CONSERVATO` e l'evento `versamento.conservato` arrivano solo quando il pacchetto di archiviazione è nel registro delle impronte ed è coperto da un checkpoint firmato (`fase: CONSERVATO`); il rapporto di versamento definitivo (formato 2) contiene le prove d'inclusione verificabili con il verificatore pubblico (`docs/integrazione/verifica_indipendente.md`).
- **Copie di transito (staging).** I file versati restano in un'area di transito finché il pacchetto di archiviazione non è sullo storage conservativo; un job del conservatore li elimina 24 ore dopo, solo dopo aver verificato che la copia conservata abbia la stessa impronta e la stessa dimensione. I file dei PdV **rifiutati o in `ERRORE` restano** nell'area di transito, perché il conservatore può rielaborarli dopo un errore tecnico; sono eliminati 90 giorni dopo l'ingresso in `ERRORE`, con l'evento `versamento.staging_scaduto` 7 giorni prima; dopo l'eliminazione il PdV non si può più rielaborare. Per riversare un PdV rifiutato il produttore invia di nuovo i file corretti: non si riusano le copie di transito.

## 4. Idempotenza

Ogni versamento ha una **`Idempotency-Key`**: un identificativo scelto dal produttore per *quel* versamento (8–200 caratteri fra lettere, cifre e `. _ : -`; formato errato `400 VRS-122`), da riusare identico su ogni ritentativo dello stesso versamento.

| Situazione | Risposta |
|---|---|
| Stessa chiave, stesso contenuto | `200`, stesso PdV (`Idempotent-Replayed: true`) |
| Stessa chiave, stesso contenuto, ma il PdV è stato **rifiutato**, è in `ERRORE` o è annullato | `409 VRS-127` con `dettagli.pdv_id_esistente`, `stato_esistente` e `link`: il PdV non è conservato. Per versare di nuovo si corregge il contenuto e si usa una chiave nuova. Vale per tutti i canali (REST, sessioni, SFTP, PDND); prima la risposta era `200` con lo stato del PdV |
| Stessa chiave, contenuto diverso | `409 VRS-121` |
| Chiave diversa, stesso indice di un PdV ancora attivo | `409 VRS-120` con `dettagli.pdv_id_esistente` |
| Stessa chiave dell'unità documentaria (registro, anno, numero) di un PdV attivo, contenuto diverso | `409 VRS-124` con `dettagli.pdv_id_esistente`. Un PdV rifiutato in verifica, o portato in `ERRORE` durante la costruzione del pacchetto di archiviazione, libera la chiave. |
| Due richieste uguali in contemporanea | Una crea il PdV, l'altra riceve lo stesso PdV o un `409` del gruppo VRS-12x; mai due PdV |

**Obbligatoria, con un periodo di transizione.** Fino al **31/03/2027** (ora italiana; data configurabile dal conservatore, anche per ente) un versamento REST o PDND senza `Idempotency-Key` è ancora accettato: il servizio ricava una chiave dal contenuto, aggiunge l'avviso `VRS-126` e le intestazioni `Deprecation` (RFC 9745) e `Sunset` (RFC 8594). Dal giorno dopo la stessa richiesta riceve **`428 VRS-125`**.

Per gli altri canali la chiave la calcola VerumDoc, quindi non ricevono mai `VRS-125`/`VRS-126`:
- **SFTP**: dall'ente, dal nome della cartella e dall'impronta di `indice.json` ([sftp.md §6](sftp.md#6-idempotenza-reinviare-non-duplica)).

Anche l'apertura di una sessione di caricamento accetta `Idempotency-Key` (facoltativa): la stessa chiave restituisce la stessa sessione.

## 5. Eventi e webhook

Gli eventi dicono cosa succede ai pacchetti dell'ente, qualunque sia il canale di versamento. Si leggono in due modi equivalenti, con lo stesso `id` e la stessa forma ([webhook.md](webhook.md)):
- **elenco a cursore** `GET /api/v1/eventi?dopo=<ultimo id letto>` (chiave API o Bearer);
- **webhook**: `POST` firmato HMAC-SHA256 (`VerumDoc-Firma`) verso un indirizzo https dell'ente, con ritentativi e coda di scarto. Lo registra l'amministratore dell'ente.

| Tipo | Quando |
|---|---|
| `versamento.ricevuto` | PdV preso in carico (ogni canale) |
| `versamento.validato` / `versamento.rifiutato` | Fine della verifica, senza o con errori bloccanti |
| `versamento.conservato` | Pacchetto di archiviazione nel registro delle impronte, coperto da un checkpoint firmato (`fase: CONSERVATO`) |
| `versamento.staging_scaduto` | Preavviso dell'eliminazione delle copie di transito di un PdV rifiutato |
| `catena.checkpoint` | Nuovo checkpoint firmato del registro delle impronte |
| `integrita.anomalia` | Anomalia trovata dalla verifica periodica dell'integrità su un PdA dell'ente |
| `caricamento.completato` / `caricamento.errore` | Fine della verifica di una sessione di caricamento (REST, SFTP) |
| `pdd.pronto` | Pacchetto di distribuzione con consegna ZIP pronto, anche un'esibizione (`dati.tipo = "ESIBIZIONE"`) |

Non esiste un evento `esibizione.pronta`. I `dati` di un evento non contengono dati personali, nomi di file né link: il dettaglio si legge dall'API con l'identificativo della risorsa.

Per canale:
- **SFTP**: in più agli eventi, l'esito di ogni cartella è in `out/<cartella>/esito.json`.
- **PDND**: il voucher non dà accesso a eventi, webhook né allo stato del PdV; l'ente li legge con una propria chiave API.

## 6. Errori

Ogni errore è `application/problem+json` (RFC 9457) con un `codice` stabile del [catalogo](../catalogo/errori.md), l'`esito` e `ritentabile`. L'eccezione sono le cartelle SFTP (problem+json dentro `esito.json`).

| Esito | Cosa fare |
|---|---|
| `NON_VALIDO` | Correggere richiesta o pacchetto e reinviare. |
| `RIFIUTATO` | Regola o policy: ripetere la stessa richiesta non serve. |
| `ERRORE` con `ritentabile: true` | Temporaneo: riprovare dopo `Retry-After` con la **stessa** `Idempotency-Key`. |
| `ERRORE` con `ritentabile: false` | Segnalare al conservatore citando `id_richiesta` (anche in `X-Request-ID`). |

Famiglie per canale:

| Canale | Famiglie |
|---|---|
| Tutti | [VRS](../catalogo/errori.md#vrs) (versamento), [RGL](../catalogo/errori.md#rgl) (regole, come rilievi di `VRS-130`), [SEC](../catalogo/errori.md#sec) (nomi e testi), [AUTH](../catalogo/errori.md#auth), [QTA](../catalogo/errori.md#qta), [VAL](../catalogo/errori.md#val) (richiesta non valida) |
| Sessioni di caricamento | [UPL](../catalogo/errori.md#upl) |
| SFTP | [SFTP](../catalogo/errori.md#sftp) |
| PDND | [PDND](../catalogo/errori.md#pdnd) |
| Adattatori di compatibilità (tolti in R18.2, codici dismessi) | [CMP](../catalogo/errori.md#cmp) |
| Eventi e webhook | [EVT](../catalogo/errori.md#evt), [WH](../catalogo/errori.md#wh) |
| Verifica asincrona (stato `ERRORE` del PdV, evento `versamento.rifiutato`) | [VLD](../catalogo/errori.md#vld) |

**`VRS-130`** (422) raccoglie in una volta **tutti** i rilievi bloccanti del motore delle regole in `dettagli.rilievi[]` ({`codice` RGL-nnn, `campo`, `documento`, `messaggio`}) e gli avvisi in `dettagli.avvisi[]`. Sostituisce i vecchi `VRS-103` (metadati minimi, ora rilievo `RGL-101`) e `VRS-112` (formato non ammesso dalla classe, ora `RGL-105`), che non sono più emessi.

## 7. Quote

Ogni ente ha un **piano** con due limiti, uguali per tutte le sue credenziali e per tutti i canali:

| Limite | Codice | `Retry-After` | Dove si applica |
|---|---|---|---|
| Richieste al minuto | `429 QTA-001` | secondi finché si libera la finestra | Tutte le rotte autenticate con chiave API o Bearer: gateway (anche con token di canale: versamento, sessioni di caricamento — una richiesta per parte —, adattatori, stato) e, dalla R03, anche distribuzione, ricerca e portale (`/api/v2/**`, `/api/dashboard/**`) ed eventi e webhook (`/api/v1/eventi`, `/api/v1/webhook`). Ogni servizio ha il suo contatore per ente. Ricerca e richiesta di un PdD hanno in più un limite proprio (`dettagli.categoria`). Dalla R03 vale anche per le rotte PDND, con lo stesso contatore del gateway. |
| PdV al giorno | `429 QTA-002` | secondi fino a mezzanotte (ora italiana) | Ogni versamento che crea un PdV, da qualsiasi canale: REST, sessioni, SFTP, PDND. |

- La quota giornaliera conta solo i PdV **nuovi** e si controlla dopo la ricerca per `Idempotency-Key`: ripetere un versamento già accettato restituisce sempre lo stesso PdV, anche a quota esaurita. Il posto si prenota in modo atomico prima del trasferimento e si restituisce se il PdV non nasce: richieste parallele dello stesso ente non superano mai il limite.
- Predefiniti del conservatore dalla R03 (modificabili; da confermare): piano `starter` **120** richieste/minuto e **1.000** PdV/giorno, `business` **600** e **20.000**, `enterprise` **6.000** e **200.000**. Prima erano 60/100, 300/500, 1000/2000.
- **Quota propria dell'ente.** Un ente può avere un valore proprio che prevale sul piano (per esempio una regione o un'ASL con volumi fuori scala): lo imposta il conservatore (impostazioni `QUOTA_PDV_GIORNO` e `QUOTA_RICHIESTE_MINUTO` dell'ente; 0 = vale il piano). Vale per il gateway e per gli altri servizi.
- Il piano effettivo dell'ente si legge in `GET /api/v1/me` (`piano`). Limiti propri al minuto (predefiniti, modificabili con `LIMITI_RICHIESTE_CATEGORIE`): ricerca `starter` 30, `business` 120, `enterprise` 600; richiesta di un PdD 10, 60, 300.
- Il canale SFTP tratta le quote come errori temporanei ([sftp.md §1](sftp.md#1-account-e-accesso)).

## 8. PDND in breve

E-service del gateway, voucher PDND nell'intestazione `Authorization`:

| E-service | Percorso | Risposta |
|---|---|---|
| 1 — Versamento | `POST /api/pdnd/v1/versamento` | come il REST: `202`/`200` con `{pdv_id, stato, avvisi, …}` |
| 2 — Consultazione AIP | `GET /api/pdnd/v1/aip/{aip_id}` | dati dell'AIP dell'ente |
| 3 — Registrazione nella catena | `GET /api/pdnd/v1/hashchain/verify/{aip_id}` | se l'AIP è registrato in un blocco della catena delle impronte; la risposta dichiara cosa verifica e cosa no |

Corpo del versamento (JSON): `classe_documentale`, `registro` (facoltativo), `anno` (4 cifre), `numero`, `id_documento`, `nome_file`, `formato` (facoltativo, confrontato con il formato rilevato), `hash_sha256`, `file_b64` (base64 standard, al più `PDND_MAX_FILE_MB`, predefinito 50 MB; oltre `413 PDND-413`), `metadati` (come `parametri` del REST). Un documento principale per richiesta. `Idempotency-Key` con le regole del §4. Errori del voucher: `401 PDND-401` (`WWW-Authenticate: Bearer error="invalid_token"`), `403 PDND-403`; configurazione PDND incompleta del servizio: `503 PDND-503`. Il corpo si legge in streaming (il file base64 è decodificato a blocchi su disco); i campi diversi dal file al più `PDND_MAX_METADATI_MB` (predefinito 4 MB). Troppe richieste contemporanee sulla stessa replica: **`503 PDND-503-OCCUPATO`**, ritentabile, con `Retry-After` (il corpo non viene letto). Limite di richieste al minuto dell'ente: `429 QTA-001`.

## 9. Leggere: stato, elenco, ricerca, distribuzione

| Operazione | Percorso | Note |
|---|---|---|
| Stato di un PdV | `GET /api/v1/versamento/{pdv_id}` | |
| Elenco dei versamenti dell'ente | `GET /api/dashboard/versamenti?stato=&limit=&cursore=` | Dal più recente, **a cursore**: risposta `{items, limit, cursore, altri}`, senza totale. Se `altri` è vero, ripetere con `cursore`. `tenant_id` facoltativo (l'ente è quello della credenziale). `skip` non è più supportato (`422 DSH-422`); cursore alterato `422 DSH-104`. |
| Ricerca nei documenti conservati | `POST /api/v2/distribution/search` | A cursore (`cursore`, `altri`); ogni parola deve comparire in almeno un campo; `total` contato al massimo fino a 1000 (`total_esatto`); `skip` > 0 → `422 DST-422`. I metadati marcati «sensibili» nel profilo della classe non sono cercabili. |
| Elenco e ricerca dei pacchetti (portale) | `GET /api/dashboard/aip?stato=&limit=&cursore=`, `POST /api/dashboard/search` | **A cursore**, senza totale: `{items, limit, cursore, altri, …}`. La ricerca con testo o classe usa l'indice di ricerca (`motore: "indice"`); con i soli stato e anno il database (`motore: "database"`). `skip` → `422 DSH-422`; cursore di un'altra ricerca `422 DSH-104`. |
| Pacchetto di distribuzione (anche esibizione) | `POST /api/v2/distribution/richiedi`, `GET /api/v2/distribution/pdd/{pdd_id}` | Consegna LINK: collegamenti nella risposta. Consegna ZIP: `IN_PREPARAZIONE`, poi evento `pdd.pronto`. |

## 10. SDK

L'[SDK Python](../../sdk/README.md) (`verumdoc-sdk` 3.x, sincrono e asincrono) copre: versamento multipart e per riferimento con scelta automatica, sessioni di caricamento a parti parallele e riprendibili, `Idempotency-Key` generata e riusata, ritentativi solo dove sono sicuri, attesa dello stato, RdV con verifica dell'impronta, eventi (`itera_eventi`), webhook e verifica della firma (`verifica_firma`), ricerca (`itera_ricerca`), elenco dei versamenti a cursore (`itera_versamenti`), PdD, identità (`identita()` → `GET /api/v1/me`). Gli errori diventano eccezioni tipizzate secondo l'`esito` (`NonValido`, `Rifiutato`, `ErroreTemporaneo`, `ErroreServizio`).

## 11. Cambi incompatibili di R03 per chi integra

- `Idempotency-Key` obbligatoria con transizione fino al 31/03/2027 (poi `428 VRS-125`).
- `VRS-103` e `VRS-112` non più emessi: al loro posto `422 VRS-130` con i rilievi `RGL-101`/`RGL-105`.
- Chiave dell'unità documentaria (registro, anno, numero) univoca fra i PdV attivi dell'ente: `409 VRS-124`.
- I metadati non previsti nei `parametri` sono conservati (prima erano scartati) e segnalati con l'avviso `RGL-103`.
- Quote per piano su tutti i canali: `429 QTA-001`/`QTA-002` (`RATE-001` non è più emesso).
- `GET /api/dashboard/versamenti`: paginazione a cursore, tolti `total` e `skip`.
- `GET /api/dashboard/aip` e `POST /api/dashboard/search`: paginazione a cursore, tolti `total` e `skip`; la ricerca testuale usa l'indice di ricerca (parole intere, non parti di parola).
- `POST /api/v2/distribution/search`: `skip` > 0 rifiutato (`422 DST-422`), `total` limitato (`total_esatto`).
- `GET /api/v1/eventi`: il cursore è la `posizione` (campo nuovo di ogni evento), non più l'`id`; chi usa `cursore` della risposta non deve cambiare nulla, chi salvava l'`id` dell'ultimo evento deve salvare `posizione` (per gli eventi già presenti al rilascio i due valori coincidono).
- La ripetizione di un versamento il cui PdV è stato rifiutato, è in errore o è annullato riceve `409 VRS-127` su ogni canale (prima `200` con lo stato).
- `allegati` annidati non vuoti → `400 VRS-114`.
- Estensione del file fuori dai formati della classe → `400 SEC-006` prima del trasferimento (prima `422 VRS-130`/`RGL-105` dopo il trasferimento).
- Metadati minimi solo in `metadati_extra` → `422 VRS-130` (`RGL-101`).
- Multipart REST oltre `VERSAMENTO_REST_MAX_CORPO_MB` → `413 UPL-413`, anche in chunked.
- PDND: `429 QTA-001` e `503 PDND-503-OCCUPATO`.
- Sessioni di caricamento: due invii contemporanei della stessa parte con byte diversi → `503 UPL-503` (ritentabile), non più `409`.
- Quote per piano più alte (§7) e quota propria dell'ente.
- Webhook: si contattano solo indirizzi unicast globali (elenco di ammessi): `::ffff:<IPv4>` e simili ora `422 WH-002`.
- Webhook ed eventi: il tipo `esibizione.pronta` non esiste (`422 WH-003`/`EVT-001` se richiesto); usare `pdd.pronto` con `tipo = "ESIBIZIONE"`.
- SDK Python 3.0.0, non compatibile con la 2.x.

## 12. Cambi incompatibili di R18.2 per chi integra

- Gli adattatori di compatibilità del gateway sono tolti (VD-P-191: niente adattatori verso interfacce di terzi senza una specifica pubblica verificata). Le rotte `/api/compat/...` non esistono più (`404`); i codici `CMP-*` restano nel [catalogo](../catalogo/errori.md) come dismessi. Chi li usava passa al REST (multipart o sessioni di caricamento, meglio con l'SDK), a SFTP o a PDND: i PdV già versati restano conservati e consultabili come gli altri.
