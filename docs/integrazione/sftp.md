# Versamento via SFTP

> Piano R03 §3.2. Servizio: `sftp-ingest`. Gestione degli account: `onboarding`.
> Codici d'errore: famiglia `SFTP` di [`docs/catalogo/errori.md`](../catalogo/errori.md), più i codici del versamento (`VRS`, `RGL`, `UPL`) che il canale riporta così come li restituisce il servizio di versamento.

Il canale SFTP serve ai sistemi che preferiscono depositare file invece di chiamare un'API. Il pacchetto passa dallo **stesso caso d'uso di versamento dell'API REST**: stesse regole, stessi controlli, stessi codici d'errore, stessa idempotenza. Il canale traduce soltanto: da una cartella a un versamento.

## 1. Account e accesso

| Operazione | Chi | Come |
|---|---|---|
| Creare, modificare, sospendere, riattivare un account | `ADMIN_TENANT` o `RSC_CONSERVATORE` del proprio ente, `SUPER_ADMIN` | API di onboarding (§7) |
| Aggiungere o revocare una chiave pubblica | gli stessi | API di onboarding (§7) |
| Collegarsi via SFTP | il sistema del cliente, con una chiave registrata | `sftp -i <chiave> <nome_utente>@<host>`: host e porta sono nel campo `connessione` dell'account (porta pubblica predefinita **22**) |

- **Solo chiave pubblica.** Le password non sono accettate. Tipi ammessi (predefiniti): `ssh-ed25519`, `ecdsa-sha2-nistp256/384/521`, `ssh-rsa` di almeno **3072 bit**. Le firme RSA con SHA-1 non sono accettate. Il controllo si ripete a ogni accesso: se la politica diventa più severa, vale subito anche per le chiavi già registrate.
- **Solo SFTP.** Niente shell, comandi, `scp`, inoltro di porte.
- **Nome utente** unico fra tutti gli enti, 3–64 caratteri: minuscole, cifre, `.`, `_`, `-`.
- **Utente responsabile.** Ogni account versa per conto di un utente dell'ente, scelto con le stesse regole del responsabile di una chiave API: utente attivo dello stesso ente, con ruolo non superiore a quello di chi lo indica. Versamenti e registro attività sono attribuiti a lui, con canale `SFTP`.
- L'accesso è negato se l'account è sospeso, se l'ente è sospeso o disattivato, se l'utente responsabile è disattivato o se la chiave è revocata. Nelle stesse condizioni le cartelle già depositate non vengono lavorate: restano in `in/`, senza esito, finché l'account non torna operativo.
- Ogni account ha una **quota di spazio** (predefinita 50 GiB, tutte le cartelle comprese). Oltre la quota la scrittura fallisce con «Quota di spazio dell'account superata».
- Valgono anche le **quote del piano dell'ente**, come per ogni canale, perché il canale versa attraverso l'API REST: richieste al minuto (`QTA-001`) e pacchetti di versamento al giorno (`QTA-002`). Sono errori temporanei: la cartella va `IN_RIPROVA` con `prossimo_tentativo` non prima del `Retry-After` (per `QTA-002`, la mezzanotte) e **i rinvii per quota non consumano i tentativi**. Solo se la quota resta esaurita oltre 72 ore dalla prima presa in carico (`LAVORATORE_QUOTA_ATTESA_MASSIMA_ORE`) il pacchetto è scartato con quel codice.

## 2. Cartelle dell'account

Dopo l'accesso il cliente vede soltanto la propria area:

| Cartella | Contenuto | Il cliente può |
|---|---|---|
| `in/` | pacchetti da versare, una cartella per pacchetto | creare cartelle, scrivere, rinominare ed eliminare file |
| `out/` | esiti: `out/<cartella>/esito.json` | leggere, eliminare |
| `lavorazione/` | pacchetti presi in carico | solo leggere |
| `fatti/` | pacchetti versati | leggere, eliminare |
| `scarti/` | pacchetti scartati | leggere, eliminare |

`fatti/`, `scarti/` e `out/` sono copie di transito: si svuotano da sole dopo 7, 30 e 30 giorni (valori predefiniti). Il pacchetto versato è conservato nello storage del conservatore, non qui.

## 3. Come versare un pacchetto

Un pacchetto è una cartella `in/<identificativo>/` con:

- i file del pacchetto, tutti allo **stesso livello** (nessuna sottocartella);
- `indice.json`: lo **stesso indice dell'API REST** (`IndiceUD`: `classe_documentale`, `chiave`, `documenti[]` con `nome_file`, `hash`, `parametri`, `allegati[]`). Ogni `nome_file` è il nome di un file della cartella; ogni file della cartella è dichiarato nell'indice.

Il nome della cartella è l'identificativo del pacchetto per il produttore: 1–128 caratteri fra lettere, cifre, `.`, `_`, `-`, che inizia con una lettera o una cifra.

**Il pacchetto è pronto quando è completo.** Due modi, a scelta:

1. **Cartella temporanea** (consigliato): caricare tutto in `in/<identificativo>.tmp/`, poi rinominare la cartella in `in/<identificativo>`. La rinomina è atomica: VerumDoc non vede mai un pacchetto a metà.
2. **File temporanei**: caricare ogni file con il prefisso `.tmp-` (es. `.tmp-fattura.pdf`) e rinominarlo a fine trasferimento; caricare `indice.json` **per ultimo**.

VerumDoc prende in carico una cartella di `in/` solo se contiene `indice.json`, nessun file `.tmp-` e non cambia da almeno 30 secondi (valore predefinito). Una cartella `in/<identificativo>/` che non diventa mai completa (indice assente, file `.tmp-` rimasti) viene comunque lavorata dopo 24 ore e scartata con il motivo, così c'è sempre un esito. Le cartelle `in/<identificativo>.tmp/` non vengono mai lavorate né eliminate da VerumDoc (occupano la quota finché il cliente non le rinomina o le elimina).

Esempio con OpenSSH:

```
sftp -i ~/.ssh/verumdoc_ed25519 comune.esempio@sftp.verumdoc.it
sftp> mkdir in/PROT-2026-000123.tmp
sftp> put fattura.pdf in/PROT-2026-000123.tmp/fattura.pdf
sftp> put indice.json in/PROT-2026-000123.tmp/indice.json
sftp> rename in/PROT-2026-000123.tmp in/PROT-2026-000123
```

## 4. Cosa succede dopo

1. La cartella passa da `in/` a `lavorazione/` (da qui il cliente non la può più modificare).
2. VerumDoc controlla la cartella (indice leggibile, file dichiarati presenti, nessun file in più), carica i file nel servizio di versamento a parti, calcolando le impronte durante il trasferimento, e confronta le impronte dichiarate nell'indice.
3. Versa il pacchetto con lo stesso caso d'uso dell'API REST.
4. Scrive `out/<identificativo>/esito.json` e sposta la cartella in `fatti/` (versato) o `scarti/` (scartato).

## 5. Esito: `out/<identificativo>/esito.json`

Formato versione `1`:

```json
{
  "versione": "1",
  "cartella": "PROT-2026-000123",
  "stato": "VERSATO",
  "pdv_id": "3f0c…",
  "stato_pdv": "RICEVUTO",
  "reinvio": false,
  "avvisi": [{"codice": "RGL-103", "effetto": "AVVISA", "messaggio": "…", "campo": "…", "documento": "1"}],
  "errori": [],
  "link": {
    "stato": "https://api.verumdoc.it/api/v1/versamento/3f0c…",
    "rapporto": "https://api.verumdoc.it/api/v1/versamento/3f0c…/rdv"
  },
  "chiave_idempotenza": "sftp-…",
  "lavorazione_id": "9b1e…",
  "tentativi": 1,
  "prossimo_tentativo": null,
  "elaborato_il": "2026-10-21T10:15:02.123456+00:00"
}
```

| `stato` | Significato | Cosa fare |
|---|---|---|
| `VERSATO` | Il pacchetto è stato preso in carico: c'è un PdV (`pdv_id`). La verifica e la conservazione proseguono come per l'API REST. | Seguire lo stato e scaricare il rapporto di versamento dai `link` (API con chiave dell'ente), oppure ricevere gli eventi `versamento.*` (webhook o elenco eventi). |
| `SCARTATO` | Il pacchetto non è stato versato. `errori` contiene i problemi in formato `problem+json`, con il `codice` del catalogo. | Correggere e depositare di nuovo la cartella (anche con lo stesso nome). |
| `IN_RIPROVA` | Errore temporaneo (es. servizio non raggiungibile). La cartella resta in `lavorazione/` e verrà ritentata a `prossimo_tentativo`. | Niente: l'esito sarà aggiornato. Dopo l'ultimo tentativo (predefiniti: dopo 1, 5, 25 minuti e 2 ore) il pacchetto è scartato con l'ultimo errore. |

`VERSATO` significa «preso in carico», non «conservato»: è lo stesso momento della risposta `202` dell'API REST. Gli errori della verifica successiva (es. formato non ammesso scoperto dalla validazione) si leggono nello stato del PdV, non in `esito.json`.

Errori propri del canale (famiglia `SFTP`):

| Codice | Quando |
|---|---|
| `SFTP-101` | `indice.json` assente |
| `SFTP-102` | `indice.json` non è JSON valido in UTF-8, non è un oggetto o supera 10 MiB |
| `SFTP-103` | un file dichiarato nell'indice non è nella cartella |
| `SFTP-104` | un file della cartella non è dichiarato nell'indice |
| `SFTP-105` | sottocartelle, collegamenti, file `.tmp-` rimasti, o `nome_file` che non è un semplice nome |
| `SFTP-201` | servizio di versamento non raggiungibile dopo tutti i tentativi |
| `SFTP-202` | verifica dei file caricati non conclusa in tempo |
| `SFTP-500` | errore interno del canale |

Tutti gli altri codici (es. `QTA-002` quota giornaliera esaurita, `VRS-102` impronta errata, `VRS-130` metadati non conformi alle regole della classe, `VRS-124` chiave UD già usata, `VRS-127` cartella già versata come PdV poi rifiutato, `VRS-114` allegati annidati, `SEC-006` estensione non ammessa) sono quelli del versamento REST, con lo stesso significato.

## 6. Idempotenza: reinviare non duplica

La chiave d'idempotenza del versamento è calcolata dal canale: impronta di **(ente, nome della cartella, impronta SHA-256 di `indice.json`)**.

- Stessa cartella con lo stesso `indice.json` depositata di nuovo (anche dopo che è finita in `fatti/`): nessun nuovo PdV; l'esito riporta lo stesso `pdv_id`, `"reinvio": true` e lo **stato attuale** del PdV (`stato_pdv`, chiesto al servizio di versamento a ogni reinvio). La seconda copia finisce in `fatti/<identificativo>__<istante>`.
- Se nel frattempo quel PdV è stato **rifiutato** dalla verifica (stato `ERRORE`) o annullato, il reinvio identico **non** risulta versato: è `SCARTATO` con `VRS-127` (`dettagli.pdv_id_esistente`, `stato_esistente`, `link`), come per il versamento REST. Per versare di nuovo si correggono i file e quindi l'`indice.json` (che ne dichiara le impronte): è un versamento nuovo.
- Una cartella corretta mentre la lavorazione precedente è ancora aperta (per esempio in `IN_RIPROVA`) riparte con sessioni di caricamento nuove: la chiave di ogni sessione comprende l'impronta di `indice.json`.
- Stesso nome di cartella con un indice diverso: è un versamento diverso, con le regole di duplicato del versamento REST (es. `VRS-120`, `VRS-124`).
- Dopo un'interruzione di VerumDoc a metà lavorazione, la cartella viene ripresa da dove era rimasta: i file già caricati non si ricaricano e un pacchetto già versato non crea un secondo PdV (stessa chiave d'idempotenza, stesso `pdv_id`).

## 7. API di gestione degli account (onboarding)

Base: `/api/v1/onboarding/tenant/{tenant_id}/sftp/account`. Autenticazione: `Authorization: Bearer <token>` di un amministratore dell'ente. Un identificativo di un altro ente risponde `404`.

| Metodo e percorso | Corpo | Risposta |
|---|---|---|
| `GET …` | — | `200 {"total", "items": [account]}` |
| `POST …` | `{"nome_utente", "descrizione"?, "user_id"?}` | `201 account`; `409 SFTP-004` nome già usato; `403 AUTH-010`/`404 ONB-404`/`422 ONB-422` responsabile non ammesso |
| `GET …/{account_id}` | — | `200 account`; `404 SFTP-404` |
| `PUT …/{account_id}` | `{"descrizione"?, "user_id"?}` | `200 account` |
| `POST …/{account_id}/sospendi` | — | `200 account` (idempotente) |
| `POST …/{account_id}/riattiva` | — | `200 account`; `409 SFTP-409` se il responsabile non è più attivo |
| `POST …/{account_id}/chiavi` | `{"chiave_pubblica": "ssh-ed25519 AAAA… commento"}` | `201 {"id", "algoritmo", "bit", "impronta", "commento", "attiva", …}`; `422 SFTP-001` chiave non valida; `422 SFTP-002` tipo o lunghezza non ammessi; `409 SFTP-003` già registrata |
| `DELETE …/{account_id}/chiavi/{chiave_id}` | — | `204`; `404 SFTP-404` se inesistente o già revocata |

`account` contiene: `id`, `nome_utente`, `descrizione`, `stato` (`ATTIVO`/`SOSPESO`), `sospeso_il`, `responsabile` {id, email, nome, cognome, ruolo, attivo}, `chiavi[]` (anche le revocate, con `attiva: false` e `revocata_il`), `connessione` {protocollo, utente, host, porta}, `created_at`, `azioni_consentite`.

L'impronta è nella forma di `ssh-keygen -l -f chiave.pub` (`SHA256:…`): confrontarla prima del primo collegamento. Ogni operazione è registrata nel registro attività.

## 8. Limiti e impostazioni (valori predefiniti)

| Impostazione | Predefinito | Variabile |
|---|---|---|
| Quota per account | 50 GiB | `SFTP_QUOTA_ACCOUNT_BYTE` |
| Tipi di chiave ammessi | `ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,ssh-rsa` | `SFTP_TIPI_CHIAVE_AMMESSI` (onboarding e sftp-ingest, **stesso valore** in entrambi): nomi OpenSSH separati da virgole; un array JSON (`["ssh-ed25519"]`) è accettato per compatibilità, un JSON non valido blocca l'avvio |
| Algoritmi di firma ammessi | `ssh-ed25519`, `ecdsa-sha2-nistp*`, `rsa-sha2-512`, `rsa-sha2-256` | `SFTP_ALGORITMI_FIRMA` (sftp-ingest, stesso formato; `ssh-rsa` cioè SHA-1 blocca l'avvio) |
| Lunghezza minima RSA | 3072 bit | `SFTP_RSA_BIT_MINIMI` (onboarding e sftp-ingest) |
| Stabilità prima della presa in carico | 30 s | `LAVORATORE_STABILITA_SECONDI` |
| Cartella mai completata, lavorata dopo | 24 h | `LAVORATORE_ABBANDONO_ORE` |
| Ritardi fra i tentativi | 60, 300, 1500, 7200 s | `LAVORATORE_RITARDI_SECONDI` |
| Attesa massima per una quota del piano esaurita | 72 h | `LAVORATORE_QUOTA_ATTESA_MASSIMA_ORE` |
| Dimensione massima di `indice.json` | 10 MiB | `LAVORATORE_INDICE_MAX_BYTE` |
| Permanenza di `fatti/`, `scarti/`, `out/` | 7, 30, 30 giorni | `LAVORATORE_CONSERVA_*_GIORNI` |

La dimensione massima di un file è quella delle sessioni di caricamento del servizio di versamento (predefinita 5 GiB).

### Per chi gestisce il servizio

| Impostazione | Predefinito | Variabile |
|---|---|---|
| Porta d'ascolto del server nel container | 2222 | `SFTP_PORTA` (sftp-ingest) |
| Host e porta mostrati ai clienti nell'account | — / 22 | `SFTP_HOST_PUBBLICO`, `SFTP_PORTA_PUBBLICA` (onboarding); il servizio pubblico inoltra la 22 alla 2222 |
| Durata del token di canale verso il gateway | 300 s | `TOKEN_CANALE_DURATA_SECONDI` (fra 61 e 3600, altrimenti il servizio non parte) |
| Attesa massima di un `Retry-After` dentro una chiamata al gateway | 60 s | `GATEWAY_RETRY_AFTER_MAX_SECONDI`: oltre, la cartella si rilascia e il nuovo tentativo si programma |
| Account segnalati guardati per giro da una replica | 100 | `LAVORATORE_ACCOUNT_PER_GIRO` |
| Controllo completo di tutte le aree (rete di sicurezza e pulizia) | ogni 3600 s e all'avvio | `LAVORATORE_CONTROLLO_COMPLETO_SECONDI` |
| Scrittura a lotti delle segnalazioni del server | 0,5 s | `SFTP_SEGNALAZIONI_INTERVALLO_SECONDI` |

**Come il lavoratore trova le cartelle (R03).** Il server SFTP segnala sul database l'account quando un cliente crea, scrive, rinomina o elimina qualcosa in `in/` (tabella `sftp_segnalazioni`, a lotti); la rinomina finale `<id>.tmp` → `<id>` si guarda subito, il resto dopo la stabilità. Ogni replica del lavoratore prende in lease solo account segnalati (una replica per account) e legge dal database le lavorazioni da riprendere: non scandisce più tutte le aree a ogni giro, quindi il costo cresce con le cartelle pronte, non con il numero di enti. Un controllo completo periodico (anche all'avvio) guarda una volta tutte le aree, diviso fra le repliche, e fa la pulizia. Le cartelle prese passano da una coda a `LAVORATORE_PARALLELISMO` lavoratori: una cartella grande non ferma le altre.

Il lavoratore versa con un **token di canale** firmato con `SFTP_TOKEN_CHIAVE_PRIVATA` per l'utente responsabile dell'account: pubblico (`aud`) fisso `verumdoc-canale`, `tipo_token: canale`, canale `SFTP`. Il gateway accetta questi token solo con quel pubblico (un token con il pubblico degli utenti, `verumdoc-api`, è rifiutato) e solo sulle rotte REST che il canale usa (sessioni di caricamento, versamento, stato); sulle rotte degli adattatori li rifiuta con `403 AUTH-010`, e le rotte PDND accettano soltanto il voucher PDND. `JWT_AUDIENCE` non è più letta da sftp-ingest: va tolta dalla configurazione. Gateway e sftp-ingest vanno rilasciati insieme (un sftp-ingest precedente firma con un altro pubblico, che il gateway ora rifiuta).
