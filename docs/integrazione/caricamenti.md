# Sessioni di caricamento e versamento per riferimento

> Contratto C1 del piano R03 (§3.1). Servizio: `gateway`.
> Codici d'errore: famiglia `UPL` di [`docs/catalogo/errori.md`](../catalogo/errori.md), più i codici del versamento (`VRS`, `RGL`).

Il versamento multipart (`POST /api/v1/versamento/ud`) accetta file fino a 100 MB ciascuno (`VERSAMENTO_REST_MAX_FILE_MB`). Per i file più grandi, o quando la rete è instabile, il file si carica prima in una **sessione di caricamento**: a parti, anche in parallelo, riprendendo da dove ci si era fermati. Poi il versamento **cita** il file caricato invece di trasportarlo.

Il servizio non tiene mai un file intero in memoria: ogni parte va direttamente nello storage di transito, e impronte e formato si calcolano rileggendo il file assemblato a blocchi.

## 1. Autenticazione

Come il versamento: `Authorization: Bearer <token>` oppure la chiave API (`X-Api-Key`, o `X-Api-Key-Id` + `X-Api-Key-Secret`), con il permesso `versamento:ud`. Le sessioni sono usate anche dal canale SFTP, con il suo token di canale (pubblico `verumdoc-canale`): in quel caso la sessione e il versamento risultano del canale `SFTP`. Una sessione appartiene all'ente che l'ha aperta: per ogni altro ente risponde `404 UPL-404`, come se non esistesse.

Ogni richiesta conta nel limite di richieste al minuto del piano dell'ente (`429 QTA-001` con `Retry-After`): un file a molte parti consuma una richiesta per parte, quindi la concorrenza delle parti va scelta tenendone conto.

## 2. Ciclo di vita

```
APERTO ──completa──▶ IN_VERIFICA ──(verifica)──▶ COMPLETO ──versamento──▶ usata dal PdV
   │                       └───────────────────▶ ERRORE
   ├──DELETE──▶ ANNULLATO
   └──(scadenza, se non usata)──▶ SCADUTO
```

| Stato | Significato |
|---|---|
| `APERTO` | si accettano le parti |
| `IN_VERIFICA` | tutte le parti sono arrivate; il servizio assembla il file e lo verifica (fuori dalla richiesta) |
| `COMPLETO` | il file è verificato: `impronte` e `formato_rilevato` sono disponibili e il file si può citare in un versamento |
| `ERRORE` | verifica fallita (es. impronta diversa da quella dichiarata, `UPL-006`): il motivo è in `errore` (problem+json) e il file è eliminato |
| `ANNULLATO` | annullata dal cliente: file eliminati |
| `SCADUTO` | non usata entro `scade_il` (predefinito 24 ore dall'apertura): file eliminati |

Una sessione usata da un versamento non scade e non si annulla: il file appartiene al PdV.

## 3. Operazioni

### 3.1 Apertura — `POST /api/v1/caricamenti`

```json
{"nome_file": "registro-2026.pdf", "dimensione": 734003200, "mime_dichiarato": null,
 "impronta": {"algoritmo": "SHA-256", "valore": "9f86d0…"}}
```

- `dimensione`: byte esatti del file, al massimo 5 GiB (`CARICAMENTI_MAX_DIMENSIONE_MB`; oltre: `413 UPL-001`).
- `impronta`: facoltativa ma consigliata. Algoritmi: `SHA-256`, `SHA-512`, `SHA3-256`. Se c'è, il servizio la confronta con quella che calcola.
- `Idempotency-Key` (intestazione facoltativa ma consigliata, 8–200 caratteri `A-Z a-z 0-9 . _ : -`; formato errato: `400 VRS-122`): la stessa chiave con la stessa richiesta restituisce **la stessa sessione** (`200`, `Idempotent-Replayed: true`); con una richiesta diversa `409 UPL-007`.

Risposta `201`:

```json
{"caricamento_id": "6f1c…", "stato": "APERTO", "dimensione_parte": 8388608, "numero_parti": 88,
 "scade_il": "2026-10-22T09:00:00Z",
 "link": {"self": "/api/v1/caricamenti/6f1c…", "parti": "/api/v1/caricamenti/6f1c…/parti/{n}",
          "completa": "/api/v1/caricamenti/6f1c…/completa"}}
```

### 3.2 Parti — `PUT /api/v1/caricamenti/{id}/parti/{n}`

- Corpo binario (`Content-Type: application/octet-stream`), `n` da 1 a `numero_parti`.
- Ogni parte ha esattamente `dimensione_parte` byte; l'ultima il resto del file. Altrimenti `422 UPL-002`.
- Facoltativa: `X-Impronta-Parte: sha256=<64 cifre esadecimali>` (consigliata): se non corrisponde, la parte non è registrata (`422 UPL-003`).
- Le parti si inviano **in parallelo e in qualsiasi ordine**. Ripetere una parte con lo stesso contenuto è sicuro (`200`); con un contenuto diverso `409 UPL-004` (la parte già accettata non viene sovrascritta).
- Due invii **contemporanei** della stessa parte con byte diversi: il secondo riceve `503 UPL-503`, ritentabile (la parte non è ancora accettata). Se la scrittura di una parte nello storage fallisce, la parte non risulta ricevuta e si può reinviare; una parte rimasta a metà per un'interruzione del servizio si può reinviare dopo `CARICAMENTI_PRENOTAZIONE_PARTE_SECONDI` (predefinito 300 s).
- Risposta: `{"parte": 3, "dimensione": 8388608, "ricevute": [1, 2, 3]}`.

**Ripresa dopo un'interruzione:** `GET /api/v1/caricamenti/{id}` restituisce `parti_ricevute`: si inviano solo le mancanti.

### 3.3 Completamento — `POST /api/v1/caricamenti/{id}/completa`

- Se mancano parti: `409 UPL-005` con `dettagli.parti_mancanti`.
- Altrimenti `202` con `stato: IN_VERIFICA` (oppure `200` con `COMPLETO` se la verifica è già conclusa). Ripetere la chiamata è sicuro.
- La verifica gira fuori dalla richiesta: interrogare `GET /api/v1/caricamenti/{id}` finché lo stato è `COMPLETO` o `ERRORE`, oppure ricevere gli eventi `caricamento.completato` / `caricamento.errore` (webhook o elenco eventi, vedi [webhook.md](webhook.md)).

### 3.4 Stato — `GET /api/v1/caricamenti/{id}`

```json
{"caricamento_id": "6f1c…", "stato": "COMPLETO", "nome_file": "registro-2026.pdf",
 "dimensione": 734003200, "dimensione_parte": 8388608, "numero_parti": 88,
 "parti_ricevute": [1, 2, "…", 88], "scade_il": "2026-10-22T09:00:00Z",
 "impronte": {"SHA-256": "…", "SHA-512": "…", "SHA3-256": "…"},
 "formato_rilevato": "application/pdf", "errore": null, "pdv_id": null, "link": {"…": "…"}}
```

### 3.5 Annullamento — `DELETE /api/v1/caricamenti/{id}`

`204`; i file si eliminano. Ripetere è sicuro. Una sessione già usata da un versamento: `409 UPL-409`.

## 4. Versamento per riferimento

Stesso percorso del versamento multipart, in JSON:

```
POST /api/v1/versamento/ud
Content-Type: application/json
Idempotency-Key: <chiave del versamento>

{"indice": { …IndiceUD, come nel multipart… },
 "file": {"registro-2026.pdf": "6f1c…"}}
```

- Ogni `nome_file` dell'indice deve comparire in `file`, con l'id di una sessione **COMPLETA** dello stesso ente.
- Stesse regole, stesse risposte (`202` nuovo, `200` stessa richiesta ripetuta) e stessi errori del multipart. In più:
  - `404 UPL-404` sessione inesistente o di un altro ente;
  - `409 UPL-009` sessione non ancora `COMPLETO` (con `Retry-After`; `dettagli.stato` dice in che stato è);
  - `410 UPL-410` sessione scaduta o annullata;
  - `409 UPL-008` sessione già usata da un altro versamento: **una sessione si usa una volta sola**, anche quando due versamenti la citano nello stesso momento;
  - `413 UPL-413` corpo JSON oltre `VERSAMENTO_JSON_MAX_MB` (16 MB).
- La `Idempotency-Key` del versamento segue le regole del multipart (obbligatoria, con la transizione descritta nella [guida all'API](README.md#4-idempotenza)).
- Quota giornaliera di PdV del piano: `429 QTA-002` con `Retry-After` fino a mezzanotte (ora italiana). Si controlla dopo la ricerca per `Idempotency-Key`: ripetere un versamento già accettato restituisce sempre lo stesso PdV, anche a quota esaurita.
- L'impronta dichiarata nell'indice si confronta con quella calcolata dal servizio (`400 VRS-102` se diverse) e il formato si riconosce dal contenuto (`VRS-109`), come nel multipart.
- Un versamento rifiutato non consuma la sessione: si può correggere l'indice e ripetere.

## 5. Limiti e impostazioni

| Impostazione | Predefinito | Significato |
|---|---|---|
| `CARICAMENTI_MAX_DIMENSIONE_MB` | 5120 | dimensione massima di un file |
| `CARICAMENTI_DIMENSIONE_PARTE_MB` | 8 | dimensione delle parti (minimo 5) |
| `CARICAMENTI_SCADENZA_ORE` | 24 | scadenza di una sessione non usata |
| `VERSAMENTO_JSON_MAX_MB` | 16 | corpo del versamento per riferimento |

Il limite per file della **classe documentale** (`CLASSE_DIMENSIONE_MAX_MB`, regola RGL-104) vale anche per i file caricati a sessione.
