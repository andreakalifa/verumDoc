# Eventi e webhook firmati

> Contratto C3 del piano R03 (VD-C-078, parte webhook). Servizio: `notification`.
> Codici d'errore: famiglie `WH` ed `EVT` di [`docs/catalogo/errori.md`](../catalogo/errori.md).

VerumDoc avvisa il sistema del cliente quando succede qualcosa ai suoi pacchetti. Ci sono due modi equivalenti, e si possono usare insieme:

1. **Webhook** — VerumDoc chiama con `POST` un indirizzo https del cliente, con firma HMAC.
2. **Elenco degli eventi** — il cliente chiede `GET /api/v1/eventi` dopo l'ultimo cursore letto. Serve a chi non può ricevere chiamate dall'esterno, e serve anche a recuperare gli eventi persi da un webhook.

Entrambi mostrano gli stessi eventi, con lo stesso `id` e la stessa forma.

## 1. Autenticazione e permessi

| Operazione | Chi può farla | Credenziali |
|---|---|---|
| `GET /api/v1/eventi` | ogni utente dell'ente e ogni chiave API dell'ente | `Authorization: Bearer <token>` oppure `X-Api-Key` |
| `/api/v1/webhook/**` | solo l'amministratore dell'ente (`ADMIN_TENANT`) | `Authorization: Bearer <token>` utente (la chiave API riceve `403 AUTH-010`) |

- Ognuno vede soltanto gli eventi e i webhook del proprio ente.
- Un identificativo di un altro ente risponde `404`, come se non esistesse.
- Un utente disattivato o un ente sospeso ricevono `401 AUTH-003`.
- I token di canale (quelli che il canale SFTP usa verso il gateway, pubblico `verumdoc-canale`) non valgono qui: sono rifiutati con `401`.
- Le richieste contano nel limite al minuto del piano dell'ente (R03): oltre il limite `429 QTA-001` con `Retry-After`. Il contatore è proprio di questo servizio (vedi la [guida all'API](README.md#7-quote)).

## 2. Tipi di evento

I nomi dei tipi sono stabili: un tipo pubblicato non cambia significato.

| Tipo | Risorsa | Quando | `dati` |
|---|---|---|---|
| `versamento.ricevuto` | versamento (`pdv_id`) | il PdV è stato preso in carico (RICEVUTO) | `pdv_id`, `classe_documentale`, `canale`, `num_documenti`, `chiave` {registro, anno, numero}, `ricevuto_il`, `avvisi` (codici) |
| `versamento.validato` | versamento | la verifica non ha trovato errori bloccanti | `pdv_id`, `classe_documentale`, `chiave` |
| `versamento.rifiutato` | versamento | la verifica ha trovato errori bloccanti | `pdv_id`, `classe_documentale`, `chiave`, `errori` [{codice, messaggio}] (al massimo 20) |
| `versamento.conservato` | versamento | il PdA è nel registro delle impronte ed è coperto da un checkpoint firmato (R04) | `pdv_id`, `aip_id`, `urn`, `impronta_sha256`, `conservato_il`, `pda`, `foglia_indice`, `checkpoint_dimensione`, `fase` (`CONSERVATO`) |
| `versamento.staging_scaduto` | versamento | preavviso: le copie di transito di un PdV rifiutato (stato `ERRORE`) saranno eliminate a `scade_il` (90 giorni dall'errore, avviso 7 giorni prima); dopo il PdV non si può più rielaborare | `pdv_id`, `stato`, `scade_il`, `giorni_conservazione`, `preavviso_giorni`, `avvisato_il` |
| `catena.checkpoint` | checkpoint (dimensione) | nuovo checkpoint firmato del registro delle impronte: UN evento globale, visibile a ogni ente in `GET /api/v1/eventi` nell'ordine delle posizioni, consegnato via webhook solo a chi sottoscrive il tipo | `dimensione`, `radice`, `emesso_il`, `testo` (nota firmata) |
| `integrita.anomalia` | verifica_integrita (`<aip_id>@<verificato_il>`: una risorsa per occorrenza) | la verifica periodica dell'integrità ha trovato un'anomalia su un PdA dell'ente: `DIVERSO`, `MANCANTE`, `RETENTION_ASSENTE` o `ERRORE_LETTURA` ripetuto. Il conservatore è già allertato e gestisce il ripristino | `aip_id`, `pda`, `esito`, `verificato_il`, `tentativo`, `errore_lettura_ripetuto`, `oggetti` [{componente, documento_id, esito, impronta_attesa, impronta_trovata}] (al massimo 50) |
| `caricamento.completato` | caricamento (`caricamento_id`) | il file caricato è verificato | `caricamento_id`, `stato`, `dimensione`, `formato_rilevato`, `impronte` |
| `caricamento.errore` | caricamento | la verifica del file caricato è fallita | `caricamento_id`, `stato`, `errore` {codice, title} |
| `pdd.pronto` | pdd (`pdd_id`) | il pacchetto di distribuzione con consegna **ZIP** è pronto | `pdd_id`, `tipo` (`COMPLETO`, `ESTRATTO`, `ESIBIZIONE`, `MIGRAZIONE`), `scade_il` |

**Esibizioni.** Non esiste un tipo `esibizione.pronta`: un'esibizione è un pacchetto di distribuzione di tipo `ESIBIZIONE`. Con consegna ZIP il cliente riceve `pdd.pronto` con `dati.tipo = "ESIBIZIONE"`; con consegna LINK il pacchetto è già pronto nella risposta di `POST /api/v2/distribution/richiedi` e non c'è alcun evento. Una sottoscrizione o un filtro con un tipo sconosciuto (anche `esibizione.pronta`) risponde `422 WH-003` (webhook) o `422 EVT-001` (elenco eventi), con l'elenco dei tipi ammessi in `dettagli.ammessi`.

**`versamento.conservato` da R04.** L'evento parte solo quando il PdA è nel registro delle impronte ed è coperto da un checkpoint firmato (e controfirmato dal testimone, se risponde entro 10 minuti): `fase` vale `CONSERVATO`. Gli eventi prodotti prima di R04 (ancora nell'elenco entro la retention) hanno `fase: PDA_SU_STORAGE`.

**Cosa non c'è in `dati`.** Non ci sono dati personali, nomi di file, chiavi interne dello storage né link prefirmati. Il dettaglio si legge dall'API con l'identificativo della risorsa: per esempio `GET /api/v1/versamento/{pdv_id}`, oppure il PdD dal servizio di distribuzione.

**Unicità.** Per ogni risorsa esiste al massimo un evento di ciascun tipo.

## 3. Forma di un evento

```json
{
  "id": 42,
  "tipo": "versamento.ricevuto",
  "creato_il": "2026-10-21T10:00:00+00:00",
  "risorsa": {"tipo": "versamento", "id": "3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},
  "dati": {"pdv_id": "3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"}
}
```

`id` è un intero che cresce nel tempo. È unico per tutto il servizio, non per ente: fra due eventi dello stesso ente ci sono dei salti, ed è normale.

## 4. Elenco degli eventi

`GET /api/v1/eventi?dopo=<cursore>&limite=<1..500>&tipo=<tipo>[&tipo=…]`

| Parametro | Predefinito | Note |
|---|---|---|
| `dopo` | `0` | `cursore` della risposta precedente (la `posizione` dell'ultimo evento letto) |
| `limite` | `100` | da 1 a 500 (impostazioni `EVENTI_LIMITE_PREDEFINITO` ed `EVENTI_LIMITE_MASSIMO`) |
| `tipo` | tutti | si può ripetere; un tipo sconosciuto risponde `422 EVT-001` con `dettagli.ammessi` |

Risposta `200`:

```json
{"eventi": [ … ], "cursore": 57, "altri": true}
```

- `cursore` è la `posizione` dell'ultimo evento restituito; se l'elenco è vuoto, è il `dopo` ricevuto.
- Conservare `cursore` e passarlo come `dopo` alla chiamata successiva. **Non usare l'`id` dell'evento come cursore**: `id` identifica l'evento (per scartare i duplicati, ed è lo stesso dei webhook), `posizione` dice dove sta nell'elenco.
- Con `altri: true` conviene chiedere subito la pagina successiva.
- Con `altri: false` si riprova più tardi, per esempio dopo 30 secondi.

**Il cursore non salta eventi.** Ogni evento dell'elenco ha una `posizione`, assegnata DOPO che l'evento è stato registrato da un unico passo di pubblicazione alla volta (impostazioni `EVENTI_PUBBLICAZIONE_INTERVALLO_SECONDI`, predefinito 0,2, ed `EVENTI_PUBBLICAZIONE_LOTTO`, predefinito 5000). Le posizioni crescono senza buchi e diventano visibili in ordine: quando un cliente ha letto la posizione 58, nessun evento con posizione minore può comparire dopo. Un evento registrato tardi (per esempio con una scrittura lenta) riceve semplicemente una posizione più alta e compare alla lettura successiva. Un evento è quindi leggibile pochi decimi di secondo dopo la registrazione. Prima della R03 il cursore era l'`id` con un ritardo fisso di 2 secondi, che non bastava a escludere salti.

## 5. Webhook

### 5.1 Sottoscrizioni

| Metodo e percorso | Corpo | Risposta |
|---|---|---|
| `POST /api/v1/webhook` | `{"url", "tipi": [...], "descrizione"?, "attiva"?}` | `201` sottoscrizione **con `segreto`**; `422 WH-001` URL non valido, `422 WH-002` indirizzo non ammesso, `422 WH-003` tipo sconosciuto |
| `GET /api/v1/webhook` | — | `200 {"sottoscrizioni": [...]}` |
| `GET /api/v1/webhook/{id}` | — | `200` sottoscrizione |
| `PATCH /api/v1/webhook/{id}` | uno o più fra `url`, `tipi`, `descrizione`, `attiva` | `200` sottoscrizione |
| `DELETE /api/v1/webhook/{id}` | — | `204` |

Una sottoscrizione restituita dall'API ha questi campi: `id`, `url`, `tipi`, `descrizione`, `attiva`, `segreto_suffisso` (gli ultimi 4 caratteri del segreto), `creata_il`, `aggiornata_il`.

**Il segreto si vede una volta sola**, nella risposta di creazione, nel campo `segreto` (`whsec_…`, 256 bit casuali).
- VerumDoc lo salva cifrato (AES-256-GCM) e non lo mostra più.
- Se il segreto va perso, si elimina il webhook e se ne crea uno nuovo.

Regole sull'indirizzo:
- `https` obbligatorio;
- niente utente e password nell'URL, niente frammento `#`;
- l'host deve essere un nome Internet completo o un indirizzo IP pubblico.

La protezione SSRF è un **elenco di indirizzi ammessi**, non di vietati: si contattano solo indirizzi unicast globali (IPv4 da 1.0.0.0 a 223.255.255.255, IPv6 in `2000::/3`) fuori da tutti i blocchi per uso speciale del registro IANA. Tutto il resto è rifiutato con `422 WH-002`: indirizzi privati, di loopback, link-local, site-local (`fec0::/10`), ULA, di documentazione, di metadati cloud (per esempio `169.254.169.254`), IPv4 incapsulati in IPv6 (`::ffff:…`, 6to4, Teredo, NAT64). Lo stesso controllo si ripete a ogni consegna sugli indirizzi in cui il nome si risolve in quel momento (vedi §5.4). Con l'impostazione `WEBHOOK_PORTE_AMMESSE` (per esempio `[443]`; vuota = tutte, predefinito) si limitano anche le porte di destinazione (`WH-002` per le altre).

Altri limiti e comportamenti:
- un ente può avere al massimo 10 webhook (`WEBHOOK_MAX_SOTTOSCRIZIONI_PER_ENTE`); oltre risponde `409 WH-004`;
- `attiva: false` mette in pausa il webhook: le consegne restano in coda e ripartono quando si riattiva;
- `DELETE` annulla le consegne ancora in sospeso (stato `ANNULLATA`);
- ogni creazione, modifica ed eliminazione finisce nel registro attività dell'ente;
- **porte (R07, D16)**: la porta 443 è sempre ammessa. Altre porte le ammette solo il conservatore, per singolo ente e con un motivo, dalla console (impostazione `WEBHOOK_PORTE_AGGIUNTIVE`, gruppo «webhook»). Un URL con una porta non ammessa risponde `422 WH-001` alla creazione o alla modifica; la porta si ricontrolla a ogni consegna, quindi togliere una porta ferma le consegne verso di essa (`WH-104`).

**Coerenza con la NetworkPolicy.** In esercizio il lavoratore dei webhook di `notification` esce verso Internet solo dalle porte di `internetPorts` in `infra/helm/values.yaml` (oggi `[443]`). Ammettere una porta in console non basta: prima va aggiunta a `internetPorts` del lavoratore (e, se si usa un firewall di uscita, alle sue regole), poi si rigenerano i manifest (`python infra/k8s/genera_manifest.py`). Senza, le consegne verso quella porta falliscono per timeout di connessione (`WH-101` o `WH-102`) e restano visibili nell'elenco delle consegne. La variabile globale `WEBHOOK_PORTE_AMMESSE` resta `[443]`; vuota (solo sviluppo) vuol dire nessun limite di porta.

### 5.2 La chiamata

`POST <url>` con queste intestazioni:

| Intestazione | Valore |
|---|---|
| `Content-Type` | `application/json` |
| `VerumDoc-Evento` | tipo dell'evento, es. `versamento.conservato` |
| `VerumDoc-Id-Evento` | `id` dell'evento (lo stesso dell'elenco eventi) |
| `VerumDoc-Id-Consegna` | id della consegna; non cambia fra un tentativo e l'altro |
| `VerumDoc-Firma` | `t=<unix>,v1=<hex>` |

Il corpo è l'evento in forma JSON compatta, con le chiavi in ordine alfabetico, codificato in UTF-8.

Per confermare la ricezione il destinatario risponde con un codice **2xx entro 10 secondi**. Conviene rispondere subito e lavorare l'evento dopo. Ogni altra risposta conta come consegna fallita:
- uno stato 3xx (i reindirizzamenti non si seguono);
- uno stato 4xx o 5xx;
- un timeout;
- un errore TLS.

### 5.3 Verificare la firma

```
firma = hex( HMAC-SHA256( chiave = segreto (byte UTF-8), messaggio = "<t>." + corpo ) )
```

1. Leggere `t` e `v1` dall'intestazione `VerumDoc-Firma`.
2. Calcolare la firma sui **byte ricevuti**, senza rileggere né riformattare il JSON.
3. Confrontare a tempo costante.
4. Rifiutare se `|adesso − t|` supera 5 minuti: è la protezione contro le ripetizioni (anti-replay).
5. Usare `VerumDoc-Id-Consegna` per scartare i duplicati: la consegna avviene **almeno una volta**.

**Esempio verificabile.** Questi valori sono controllati da un test e da `openssl`:

- segreto: `whsec_esempio-di-segreto-non-usare`
- `t`: `1792576800`
- corpo (una riga):
  ```
  {"creato_il":"2026-10-21T10:00:00+00:00","dati":{"pdv_id":"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},"id":42,"risorsa":{"id":"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90","tipo":"versamento"},"tipo":"versamento.ricevuto"}
  ```
- intestazione attesa:
  `VerumDoc-Firma: t=1792576800,v1=362a779b910d922709231ed641f88061f03db35fa8a4a1bc939f4afe65b228ad`

Con `openssl`:

```sh
printf '%s' '1792576800.<corpo>' | openssl dgst -sha256 -hmac 'whsec_esempio-di-segreto-non-usare'
```

In Python:

```python
import hashlib, hmac, time

def verifica(segreto: str, intestazione: str, corpo: bytes, tolleranza: int = 300) -> bool:
    coppie = [p.split("=", 1) for p in intestazione.split(",")]
    t = int(next(v for k, v in coppie if k == "t"))
    if abs(time.time() - t) > tolleranza:
        return False
    atteso = hmac.new(segreto.encode(), f"{t}.".encode() + corpo, hashlib.sha256).hexdigest()
    # più valori v1 durante la rotazione del segreto (§5.5): basta che uno corrisponda
    return any(hmac.compare_digest(atteso, v) for k, v in coppie if k == "v1")
```

### 5.5 Rotazione del segreto (R13b)

`POST /api/v1/webhook/{id}/segreto/ruota` con `{"transizione_ore": 1..168}` (predefinito 24)
restituisce il **nuovo** segreto (si vede solo ora). Per tutto il periodo di transizione
ogni consegna porta **due firme** nella stessa intestazione, calcolate sugli stessi byte e
con lo stesso `t`:

```
VerumDoc-Firma: t=1792576800,v1=<firma con il nuovo>,v1=<firma con il precedente>
```

Un destinatario che conosce uno dei due segreti accetta (l'SDK e l'esempio qui sopra
accettano qualunque `v1`). Aggiornato il proprio sistema, il cliente può ritirare subito il
precedente con `POST /api/v1/webhook/{id}/segreto/chiudi-transizione` (409 WH-013 se non c'è
una transizione); altrimenti il precedente smette di firmare alla scadenza, misurata
dall'orologio del lavoratore a ogni consegna. Una seconda rotazione durante la transizione
rende «precedente» il segreto appena dato. Dalla console: «Canali → Webhook → Ruota il
segreto». Ogni rotazione è nell'audit (`webhook.segreto_ruotato`, solo i suffissi).

### 5.4 Ritentativi, coda di scarto e riprova

Una consegna fallita si ritenta dopo **60 s, 5 min, 25 min e 2 h** (`WEBHOOK_RITARDI_SECONDI`). In tutto i tentativi sono 5.
- Dopo l'ultimo tentativo fallito la consegna diventa **`FALLITA`** ed entra nella coda di scarto.
- Alcuni errori non si ritentano, perché ripetere non cambierebbe l'esito. La consegna diventa `FALLITA` subito quando:
  - il nome risolve verso un indirizzo non ammesso (`WH-104`);
  - il segreto non è più leggibile (`WH-106`).

| Metodo e percorso | Risposta |
|---|---|
| `GET /api/v1/webhook/{id}/consegne?stato=FALLITA&dopo=&limite=` | `200 {"consegne": [...], "cursore", "altri"}` |
| `POST /api/v1/webhook/{id}/consegne/{consegna_id}/riprova` | `202` consegna rimessa `IN_CODA` con i tentativi azzerati; `409 WH-011` se non è `FALLITA`; `422 WH-012` se l'evento è più vecchio della retention degli eventi (`RETENZIONE_EVENTI_CLIENTE_GIORNI`) |

Ogni consegna riporta questi campi:
- `stato` (`IN_CODA`, `IN_CONSEGNA`, `CONSEGNATA`, `FALLITA`, `ANNULLATA`);
- `tentativi` e `riprove`;
- `prossimo_tentativo`;
- `ultimo_stato_http`;
- `ultimo_codice` (`WH-101` timeout, `WH-102` connessione o TLS, `WH-103` risposta non 2xx, `WH-104` indirizzo non ammesso, `WH-106` segreto non leggibile);
- `ultimo_errore`.

Il corpo delle risposte del destinatario non si legge e non si salva.

**Ordine.** L'ordine delle consegne non è garantito: un evento ritentato può arrivare dopo uno più recente. Per l'ordine fa fede `id`. Per ricostruire lo stato completo si usa l'elenco degli eventi.

## 6. Note per chi gestisce il servizio

| Variabile | Predefinito | Note |
|---|---|---|
| `WEBHOOK_CHIAVE_CIFRATURA` | — | 32 byte in base64 (`openssl rand -base64 32`). **Obbligatoria in produzione**: senza, il servizio non parte. Fuori produzione, senza chiave le API dei webhook rispondono `503 WH-030`. |
| `WEBHOOK_CHIAVE_CIFRATURA_ID` | `k1` | Cambiarlo a ogni rotazione della chiave. |
| `WEBHOOK_CHIAVI_CIFRATURA_PRECEDENTI` | — | JSON `{"k0": "<base64>"}`: le chiavi precedenti, usate solo per decifrare. |
| `WEBHOOK_CONSENTI_INDIRIZZI_PRIVATI` | `false` | Solo sviluppo e test; in produzione il servizio non parte. |
| `WEBHOOK_CA_PEM` | — | CA in più per i certificati dei destinatari. |
| `WEBHOOK_RITARDI_SECONDI` | `[60,300,1500,7200]` | |
| `WEBHOOK_TIMEOUT_SECONDI` / `_CONNESSIONE_SECONDI` | `10` / `3` | |
| `WEBHOOK_LOTTO`, `WEBHOOK_CONCORRENZA` | `50`, `20` | Consegne per giro e in parallelo per replica. |
| `WEBHOOK_LEASE_SECONDI` | `120` | Una consegna `IN_CONSEGNA` oltre questo tempo (replica caduta) si riprende. |
| `WEBHOOK_LAVORATORE_ATTIVO` | `true` | Più repliche sono sicure (`FOR UPDATE SKIP LOCKED`). |
| `EVENTI_CONSUMER_ATTIVO`, `EVENTI_KAFKA_GROUP_ID` | `true`, `notification-eventi-cliente` | Consumer Kafka separato da quello della posta. |
| `EVENTI_TOPIC` | vedi `app/config.py` | Corrispondenza topic Kafka → tipo di evento. |
| `JWT_PUBLIC_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE` | — | Gli stessi del gateway. |
| `API_KEY_PEPPER` | — | Lo stesso di gateway e onboarding; obbligatorio fuori da development e test. |

**Protezione SSRF all'invio.** A ogni consegna il nome si risolve una volta e si controllano **tutti** gli indirizzi ottenuti. La connessione va all'indirizzo controllato: l'host e il nome TLS restano quelli dell'URL, così il certificato si verifica sul nome.
- Il proxy d'ambiente non si usa.
- I reindirizzamenti non si seguono.
- Le connessioni non si riusano fra una consegna e l'altra.
