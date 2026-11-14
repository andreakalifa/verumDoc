# GUIDA TEST LOCALE — VerumDoc
# Dal primo avvio ai canali R03 e alla consultazione

**Versione: 21 ottobre 2026 — R03**

Guida ai test manuali in locale con `docker-compose.yml`. Allineata al codice del rilascio R03 (`changelog/CHANGELOG-2026-10-21-rilascio-R03.md`). Per i contratti completi: `docs/integrazione/README.md` (guida per chi integra), `docs/catalogo/endpoint.md` (elenco delle rotte), `docs/catalogo/errori.md` (codici d'errore), `METADATI_AGID_VERSAMENTO.md` (indice e metadati).

> **Cosa non è.** L'ambiente locale non conserva documenti a norma: firma e marca non sono qualificate (il servizio di firma in compose non ha un fornitore configurato), l'IPdA non è verificato contro lo XSD UNI 11386, PEC e scarto non esistono. Vedi `README.md`, «Stato del progetto».

---

## PREREQUISITI

- Docker Desktop avviato (con OpenSearch, Kafka e dieci servizi applicativi conviene assegnare almeno 6 GB di RAM).
- **PowerShell 7** come terminale principale. Gli esempi usano `Invoke-RestMethod -Form`, `-SkipHttpErrorCheck`, `-StatusCodeVariable` e `[Convert]::ToHexString`, che Windows PowerShell 5.1 non ha: installalo con `winget install Microsoft.PowerShell` e apri `pwsh`. Solo l'avvio (segreti, docker compose) funziona anche in 5.1.
- Nessun bash necessario: i segreti si generano con `python tools/dev/genera_segreti_dev.py` (o con gli script `.ps1` in PowerShell 7). Su Windows `bash` senza una distribuzione WSL installata dà «execvpe(/bin/bash) failed».
- Client OpenSSH di Windows (`ssh-keygen`, `sftp`) per il canale SFTP.
- `curl.exe` (incluso in Windows 10/11). Negli esempi si scrive sempre `curl.exe`, così non si confonde con l'alias di `Invoke-WebRequest`.
- Node.js 18 o successivo per il frontend; Python 3.12 per i test.

**File di testo senza BOM.** Il gateway riconosce il formato dai primi byte del file e sftp-ingest legge `indice.json` come UTF-8 puro: un BOM in testa rende il PDF «non riconosciuto» (`422 VRS-109`) e l'indice illeggibile (`SFTP-102`). Gli esempi scrivono i file con `[IO.File]::WriteAllText`, che non mette il BOM. `[IO.File]` usa la cartella corrente del processo, non quella di PowerShell: per questo i percorsi sono costruiti con `Join-Path $PWD`.

Variabili usate in tutta la guida (porte da `docker-compose.yml`, vedi la tabella in fondo):

```powershell
$GW  = "http://localhost:8000"   # gateway: versamento, sessioni di caricamento, stato, dev-token, SPID
$SIG = "http://localhost:8003"   # signature
$HC  = "http://localhost:8004"   # hashchain
$NOT = "http://localhost:8006"   # notification: eventi e webhook
$ONB = "http://localhost:8007"   # onboarding: enti, utenti, chiavi API, account SFTP
$DST = "http://localhost:8008"   # distribution: ricerca, PdD
$DSH = "http://localhost:8009"   # dashboard-api: portale, audit, catena
```

---

## FASE 1 — AVVIO INFRASTRUTTURA

### 1.1 Chiavi e segreti di sviluppo (una volta)

```powershell
cd C:\percorso\del\progetto\verumdoc

# Tutti i segreti di sviluppo in un colpo: chiavi JWT e pepper (infra/dev_jwt.env) e segreti dei canali R03.
# Funziona anche con Windows PowerShell 5.1 e senza bash; serve il pacchetto cryptography nel venv.
pip install cryptography
python tools/dev/genera_segreti_dev.py
```

In alternativa, con PowerShell 7: `.\tools\dev\genera_chiavi_dev.ps1` e `.\infra\scripts\genera_segreti_dev_r03.ps1`; con bash: gli script `.sh` omonimi. Se Windows rifiuta gli script `.ps1` «non firmati digitalmente», sbloccali una volta con `Get-ChildItem -Recurse -Include *.ps1 | Unblock-File`.

I segreti R03 sono tre file (esclusi da git), senza stamparne i valori:

| File | Variabile | Chi la usa |
|---|---|---|
| `infra/dev_sftp.env` | `SFTP_TOKEN_CHIAVE_PRIVATA` | sftp-ingest: firma i token di canale verso il gateway |
| `infra/dev_canali_pub.env` | `SFTP_TOKEN_CHIAVE_PUBBLICA` | gateway: verifica quei token |
| `infra/dev_webhook.env` | `WEBHOOK_CHIAVE_CIFRATURA` | notification: cifra i segreti dei webhook |

Senza questi file gli altri servizi partono, ma sftp-ingest non parte e le API dei webhook rispondono `503 WH-030`. Un file già presente non viene toccato (`--forza` per rigenerarlo: i webhook creati prima diventano illeggibili).

### 1.2 Avvia i container

```powershell
docker compose up -d --build
docker compose ps -a
```

Al primo avvio il gateway esegue `alembic upgrade head` e poi il seed (classi documentali di base, ente dimostrativo `00000000-0000-0000-0000-000000000001` e bucket dello storage S3 di sviluppo, versitygw nel servizio `minio`); notification e sftp-ingest aspettano che il gateway sia healthy. Contenitori e porte attesi:

```
NAME               PORTE (host -> container)
vd-postgres        5434->5432
vd-redis           6379->6379
vd-kafka           9092->9092
vd-minio           9000->9000          (versitygw, R05: nessuna console web)
vd-mailpit         1025->1025, 8025->8025
vd-opensearch      9200->9200
vd-opa             8181->8181
vd-vault           8200->8200
vd-gateway         8000->8000
vd-validation      8001->8000
vd-signature       8003->8000
vd-hashchain       8004->8000
vd-aip-builder     8005->8000
vd-notification    8006->8000
vd-onboarding      8007->8000
vd-distribution    8008->8000
vd-dashboard-api   8009->8000
vd-sftp-ingest     2222->2222, 8010->8000
vd-ciclo-vita      8012->8000          (R05: pratiche del ciclo di vita)
```

Hanno un controllo di salute (stato `healthy`): postgres, redis, kafka, mailpit, opensearch, vault, gateway, dashboard-api, notification, sftp-ingest, ciclo-vita. Gli altri risultano `Up`; lo storage `minio` (versitygw, immagine senza shell) non ha un controllo proprio: lo attende il contenitore `minio-pronto`, che termina con `Exited (0)` quando `/health` risponde.

Controllo del gateway:

```powershell
docker compose logs gateway | Select-String "Schema DB a head|Seed OK|seed_bucket"
```

Un volume creato da una versione precedente a R02, o un bucket `verumdoc-aip` creato senza Object Lock (log `seed_bucket_senza_object_lock`), va ricreato: `docker compose down -v`, poi di nuovo `up`.

### 1.3 Crea i topic Kafka

```powershell
.\CREA_TOPIC_KAFKA.ps1          # equivalente di «make kafka-topics»; legge infra/kafka/topics.yaml
```

### 1.4 Migrazioni a mano (solo per un database di test)

Nell'ambiente compose non servono: le esegue il gateway. Servono per preparare il database template dei test (FASE 9) o per controllare la revisione:

```powershell
cd services\gateway
$env:DATABASE_URL = "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/verumdoc_dev"
alembic current          # atteso: 20261021_024 (head)
cd ..\..
```

### 1.5 Verifica che i servizi rispondano

```powershell
$salute = [ordered]@{
  gateway        = "$GW/api/v1/health/ready"
  validation     = "http://localhost:8001/api/v1/health/ready"
  signature      = "$SIG/api/v1/health/ready"
  hashchain      = "$HC/api/v1/health/ready"
  "aip-builder"  = "http://localhost:8005/api/v1/health/ready"
  notification   = "$NOT/api/v1/health/ready"
  onboarding     = "$ONB/api/v1/health/ready"
  distribution   = "$DST/api/v2/health/ready"
  "dashboard-api"= "$DSH/api/health/ready"
  "sftp-ingest"  = "http://localhost:8010/api/v1/health/ready"
}
foreach ($s in $salute.GetEnumerator()) {
  Invoke-WebRequest $s.Value -SkipHttpErrorCheck -StatusCodeVariable sc | Out-Null
  "{0,-14} {1}  {2}" -f $s.Key, $sc, $s.Value
}
```

Atteso `200` ovunque. Una readiness risponde `503` se una dipendenza bloccante (database, Kafka, S3) non risponde. Le liveness (`.../health/live`, per dashboard-api `/api/health/live`, per distribution `/api/v2/health/live`) rispondono `{"status":"alive","service":"<nome>"}`. Lo stato della firma richiede un token: si legge in FASE 3.4.

---

## FASE 2 — CREA L'ENTE (da amministratore del servizio)

Tutte le rotte di onboarding richiedono un JWT. In sviluppo il gateway espone `POST /api/v1/auth/dev-token` (solo con `VERUMDOC_DEV_AUTH=1` ed `ENV` development/test, come in compose; altrove la rotta non esiste).

### 2.1 Token SUPER_ADMIN

```powershell
$body = @{
  tenant_id = "00000000-0000-0000-0000-000000000001"   # ente dimostrativo del seed
  user_id   = "00000000-0000-0000-0000-000000000099"
  ruolo     = "SUPER_ADMIN"
} | ConvertTo-Json
$ADMIN_TOKEN = (Invoke-RestMethod "$GW/api/v1/auth/dev-token" -Method Post `
  -Body $body -ContentType "application/json").access_token
$ADMIN_H = @{ Authorization = "Bearer $ADMIN_TOKEN" }
```

Senza `scopes` il token riceve quelli del ruolo (`SUPER_ADMIN`: `*`).

### 2.2 Registra e attiva l'ente

```powershell
$ente = @{
  ragione_sociale = "Acme S.r.l. (prova locale)"
  codice_fiscale  = "12345678901"      # 11-16 caratteri, unico fra gli enti (409 ONB-001 se ripetuto)
  email_referente = "admin@acme.invalid"
  piano           = "business"         # starter | business | enterprise
} | ConvertTo-Json
$t = Invoke-RestMethod "$ONB/api/v1/onboarding/tenant" -Method Post -Headers $ADMIN_H `
  -Body $ente -ContentType "application/json"
$TENANT_ID = $t.id                      # stato TRIAL

Invoke-RestMethod "$ONB/api/v1/onboarding/tenant/$TENANT_ID/attiva" -Method Post -Headers $ADMIN_H
# stato ATTIVO
```

### 2.3 Crea l'amministratore dell'ente

Serve un utente vero: è il responsabile della chiave API e dell'account SFTP, e il codice fiscale gli permette l'accesso SPID di prova. Solo il SUPER_ADMIN assegna `ADMIN_TENANT`.

```powershell
$utente = @{
  email = "amministratore@acme.invalid"; nome = "Mario"; cognome = "Rossi"
  ruolo = "ADMIN_TENANT"
  codice_fiscale = "FTZPRV00A01Z999V"   # codice di controllo valido; è il CF predefinito del mock SPID
} | ConvertTo-Json
$u = Invoke-RestMethod "$ONB/api/v1/onboarding/tenant/$TENANT_ID/utenti" -Method Post `
  -Headers $ADMIN_H -Body $utente -ContentType "application/json"
$ADMIN_USER_ID = $u.id
```

---

## FASE 3 — CREDENZIALI

### 3.1 Metodo A — token di sviluppo per l'amministratore dell'ente

```powershell
$body = @{ tenant_id = $TENANT_ID; user_id = $ADMIN_USER_ID; ruolo = "ADMIN_TENANT" } | ConvertTo-Json
$TOKEN = (Invoke-RestMethod "$GW/api/v1/auth/dev-token" -Method Post `
  -Body $body -ContentType "application/json").access_token
$H = @{ Authorization = "Bearer $TOKEN" }
```

Scope del ruolo `ADMIN_TENANT`: `versamento:ud`, `versamento:doc`, `read`, `distribution`, `lifecycle`, `admin:tenant`. Il token dura un'ora. I servizi controllano a ogni richiesta (cache di 30 s) che l'utente non sia disattivato, che l'ente non sia sospeso e che il ruolo del token coincida con quello attuale dell'utente: altrimenti `401 AUTH-003`.

### 3.2 Metodo B — accesso SPID simulato

In sviluppo `GET /api/v1/auth/spid/login` reindirizza al mock `/api/v1/auth/spid/mock-callback`, che simula solo la verifica dell'identità: associazione all'utente per codice fiscale, codice monouso nel frammento dell'URL e scambio sono quelli reali. Lo scambio deve arrivare dalla stessa sessione (cookie `vd_spid_sessione`), quindi si usa una `WebSession`:

```powershell
$login = "$GW/api/v1/auth/spid/login?provider=spid&redirect_uri=http://localhost:5173/login&livello=2"
$r1 = Invoke-WebRequest $login -SessionVariable spid -MaximumRedirection 0 -SkipHttpErrorCheck
$mock = [string]$r1.Headers.Location                      # /api/v1/auth/spid/mock-callback?state=...
$r2 = Invoke-WebRequest "$GW$mock&cf=FTZPRV00A01Z999V" -WebSession $spid `
  -MaximumRedirection 0 -SkipHttpErrorCheck
$ritorno = [string]$r2.Headers.Location                   # http://localhost:5173/login#spid_codice=...
$codice = [Uri]::UnescapeDataString(($ritorno -split "spid_codice=")[1])

$s = Invoke-RestMethod "$GW/api/v1/auth/spid/scambio" -Method Post -WebSession $spid `
  -Body (@{ codice = $codice } | ConvertTo-Json) -ContentType "application/json"
$s.esito          # "accesso" (oppure "scelta_ente" se il CF è utente di più enti: ripetere con tenant_id)
$TOKEN_SPID = $s.access_token
```

`redirect_uri` ammessi in sviluppo: `http://localhost:5173/cb`, `/login` e gli stessi su `localhost:3000`; altri → ritorno con `SPID-010`. Un CF senza utente attivo in un ente attivo → `SPID-008`. Il codice monouso vale 120 secondi. Dal browser lo stesso flusso parte dalla pagina `http://localhost:5173/login`.

### 3.3 Metodo C — chiave API (sistemi versanti)

```powershell
$k = Invoke-RestMethod "$ONB/api/v1/onboarding/tenant/$TENANT_ID/api-keys" -Method Post `
  -Headers $ADMIN_H -ContentType "application/json" `
  -Body (@{ descrizione = "Gestionale di prova"; user_id = $ADMIN_USER_ID } | ConvertTo-Json)
$API_KEY = $k.api_key            # vd_<identificativo>_<segreto>: mostrata solo ora
$K = @{ "X-Api-Key" = $API_KEY }
```

La chiave vale per il proprio ente con i permessi `versamento:ud`, `versamento:doc`, `read`. Forma equivalente (usata dall'SDK): `X-Api-Key-Id` + `X-Api-Key-Secret`. La rotta `/api/dashboard/auth/api-key` non esiste più.

Identità di una credenziale:

```powershell
Invoke-RestMethod "$GW/api/v1/me" -Headers $K
# tenant_id, ruolo (API_SYSTEM), scopes, piano, auth_method (api_key), canale, ente{...}, chiave_api{...}
```

### 3.4 Stato della firma

```powershell
Invoke-RestMethod "$SIG/api/v1/signature/status" -Headers $H | ConvertTo-Json -Depth 5
```

In compose `FIRMA_FORNITORE` e `MARCA_FORNITORE` non sono impostati: la risposta riporta `firma.fornitore = "non_configurato"`, `marca.fornitore = "non_configurato"`, `firma_qualificata` «non configurata» e `production_ready: false`, più i `limiti` dichiarati. Valori ammessi: firma `non_configurato | sviluppo | csc`, marca `non_configurato | sviluppo | rfc3161`; l'adattatore `sviluppo` produce firme vere ma non qualificate ed è rifiutato in produzione. Senza token la rotta risponde `401`.

---

## FASE 4 — VERSA UN DOCUMENTO (REST multipart)

### 4.1 Crea un file PDF di prova (senza BOM)

```powershell
$FILE = Join-Path $PWD "test_fattura.pdf"
[IO.File]::WriteAllText($FILE, "%PDF-1.4`n% VerumDoc prova locale $(Get-Date -Format o)`n%%EOF`n")
```

Il formato si riconosce dai primi byte (`%PDF-`). Non è un PDF/A: la validazione di conformità non è ancora realizzata.

### 4.2 Calcola l'impronta SHA-256

```powershell
$SHA256 = (Get-FileHash $FILE -Algorithm SHA256).Hash.ToLower()
```

### 4.3 Costruisci l'IndiceUD e versa

Regole R03 che l'esempio rispetta:
- i **7 metadati minimi dell'Allegato 5** stanno **al primo livello di `parametri`** del documento `PRINCIPALE` (regola RGL-101 del motore, bloccante e non disattivabile; in `metadati_extra` non valgono);
- `modalita_formazione` è una lettera `a`–`d`;
- l'intestazione **`Idempotency-Key`** è presente (8–200 caratteri fra lettere, cifre e `. _ : -`);
- la classe `fattura` è una classe di base del seed (formati PDF, P7M e testo).

```powershell
$indice = @{
  classe_documentale = "fattura"
  chiave = @{ registro = "FATTURE"; anno = "2026"; numero = "FT-2026-001" }
  documenti = @(@{
    id        = "doc-001"
    tipo      = "PRINCIPALE"
    nome_file = "test_fattura.pdf"
    formato   = "application/pdf"
    hash      = @{ impronta = $SHA256; algoritmo = "SHA-256" }
    parametri = @{
      # minimi Allegato 5 (RGL-101), al primo livello
      identificativo_documento = "FT-2026-001"
      modalita_formazione      = "a"
      tipologia_documentale    = "fattura"
      data_documento           = "2026-10-21"
      soggetto_produttore_id   = "12345678901"
      soggetto_produttore_nome = "Acme S.r.l."
      oggetto                  = "Fornitura servizi IT - ottobre 2026"
      # facoltativi previsti
      numero_registrazione     = "FT-2026-001"
      metadati_extra           = @{ importo = "15000.00"; valuta = "EUR" }
      # metadato non previsto: conservato così com'è, con l'avviso RGL-103
      centro_di_costo          = "IT-01"
    }
  })
} | ConvertTo-Json -Depth 6

$r = Invoke-RestMethod "$GW/api/v1/versamento/ud" -Method Post `
  -Headers @{ "X-Api-Key" = $API_KEY; "Idempotency-Key" = "prova-FT-2026-001-v1" } `
  -Form @{ indice = $indice; files = Get-Item $FILE } `
  -StatusCodeVariable sc -ResponseHeadersVariable rh
$sc                         # 202
$rh.Location                # /api/v1/versamento/<pdv_id>
$PDV_ID = $r.pdv_id
$r | ConvertTo-Json -Depth 5
```

Lo stesso con curl (da PowerShell; l'indice va prima salvato in un file senza BOM):

```powershell
[IO.File]::WriteAllText((Join-Path $PWD "indice.json"), $indice)
curl.exe -sS -i "$GW/api/v1/versamento/ud" `
  -H "X-Api-Key: $API_KEY" -H "Idempotency-Key: prova-FT-2026-001-v1" `
  -F "indice=<indice.json" -F "files=@test_fattura.pdf;type=application/pdf"
```

(Con la stessa `Idempotency-Key` del comando PowerShell, curl riceve la risposta di ripetizione descritta sotto.)

**La risposta `202 Accepted`** dice che il PdV è **preso in carico**, non conservato: verifica, pacchetto di archiviazione e catena proseguono in modo asincrono.

```json
{
  "pdv_id": "<uuid>",
  "stato": "RICEVUTO",
  "message": "Versamento ricevuto. La validazione è in corso.",
  "avvisi": [
    {"codice": "RGL-103", "effetto": "AVVISA", "campo": "centro_di_costo", "documento": "doc-001",
     "messaggio": "Metadato centro_di_costo non previsto dallo schema né dal profilo della classe: conservato così come versato"}
  ],
  "link_stato": "/api/v1/versamento/<uuid>",
  "rdv_preliminare_disponibile": true,
  "nota_rdv": "RdV preliminare nel campo rdv_preliminare di GET /api/v1/versamento/{pdv_id}; RdV definitivo in GET /api/v1/versamento/{pdv_id}/rdv quando il PdV è CONSERVATO."
}
```

- Intestazione `Location` uguale a `link_stato`.
- **`avvisi`**: rilievi non bloccanti, salvati sul PdV e riportati nel RdV preliminare. Qui `RGL-103` perché `centro_di_costo` non è un metadato previsto: per non riceverlo, metterlo in `metadati_extra` o chiedere di aggiungerlo al profilo della classe. Senza `Idempotency-Key` compare anche `VRS-126` (versamento accettato fino al 31/03/2027 con una chiave ricavata dal contenuto) e la risposta porta le intestazioni `Deprecation` e `Sunset`; dopo quella data `428 VRS-125`.
- **Ripetizione**: rilanciare lo stesso comando con la stessa chiave e lo stesso contenuto risponde **`200`**, stesso `pdv_id`, intestazione `Idempotent-Replayed: true`, `message` «Versamento già ricevuto: stessa richiesta, stesso PdV.». Stessa chiave con contenuto diverso: `409 VRS-121`; altra chiave con la stessa chiave UD (FATTURE/2026/FT-2026-001) e contenuto diverso: `409 VRS-124`.

### 4.4 Esempio di errore (problem+json)

Stesso versamento con i metadati minimi spostati in `metadati_extra` (dove RGL-101 non li cerca), un'altra chiave UD e un'altra `Idempotency-Key`:

```powershell
$minimi = @{
  identificativo_documento = "FT-2026-002"; modalita_formazione = "a"
  tipologia_documentale = "fattura"; data_documento = "2026-10-21"
  soggetto_produttore_id = "12345678901"; soggetto_produttore_nome = "Acme S.r.l."
  oggetto = "Fornitura servizi IT - ottobre 2026"
}
$indiceErrato = @{
  classe_documentale = "fattura"
  chiave = @{ registro = "FATTURE"; anno = "2026"; numero = "FT-2026-002" }
  documenti = @(@{
    id = "doc-001"; tipo = "PRINCIPALE"; nome_file = "test_fattura.pdf"; formato = "application/pdf"
    hash = @{ impronta = $SHA256; algoritmo = "SHA-256" }
    parametri = @{ metadati_extra = $minimi }
  })
} | ConvertTo-Json -Depth 6

$e = Invoke-WebRequest "$GW/api/v1/versamento/ud" -Method Post -SkipHttpErrorCheck `
  -Headers @{ "X-Api-Key" = $API_KEY; "Idempotency-Key" = "prova-FT-2026-002-v1" } `
  -Form @{ indice = $indiceErrato; files = Get-Item $FILE }
$e.StatusCode                          # 422
$e.Headers["Content-Type"]             # application/problem+json
$e.Headers["X-Request-ID"]             # id della richiesta (anche nel corpo)
$p = $e.Content | ConvertFrom-Json
$p.codice; $p.esito; $p.ritentabile    # VRS-130  RIFIUTATO  False
$p.dettagli.rilievi | Format-Table codice, campo, documento
```

Forma del corpo (estratto):

```json
{
  "type": "https://docs.verumdoc.it/errori/VRS-130",
  "title": "Versamento non conforme alle regole di conservazione",
  "status": 422,
  "detail": "7 rilievi bloccanti: Metadato minimo dell'Allegato 5 mancante: identificativo_documento; …",
  "instance": "/api/v1/versamento/ud",
  "codice": "VRS-130",
  "esito": "RIFIUTATO",
  "ritentabile": false,
  "dettagli": {
    "rilievi": [
      {"codice": "RGL-101", "effetto": "BLOCCA", "campo": "identificativo_documento", "documento": "doc-001",
       "messaggio": "Metadato minimo dell'Allegato 5 mancante: identificativo_documento"}
    ],
    "avvisi": []
  },
  "id_richiesta": "…",
  "messaggio": "Versamento non conforme alle regole di conservazione"
}
```

Come leggerlo: `codice` è il codice stabile del catalogo (`docs/catalogo/errori.md`), `esito` dice cosa fare (`NON_VALIDO`: correggere la richiesta; `RIFIUTATO`: regola o policy, ripetere identico non serve; `ERRORE` con `ritentabile: true`: riprovare dopo `Retry-After` con la stessa `Idempotency-Key`; `ERRORE` non ritentabile: segnalare citando `id_richiesta`). `VRS-130` elenca **tutti** i rilievi in una volta (qui sette `RGL-101`). `type` è un URL; `messaggio` duplica `title` per i client del vecchio formato.

Errore rapido di forma, per confronto: `"Idempotency-Key" = "abc"` → `400 VRS-122`, esito `NON_VALIDO`.

### 4.5 Segui lo stato del PdV

```powershell
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep 3
  $stato = Invoke-RestMethod "$GW/api/v1/versamento/$PDV_ID" -Headers $K
  Write-Host "[$i] $($stato.stato)"
  if ($stato.stato -in @("CONSERVATO", "ERRORE")) { break }
}
$AIP_ID = $stato.aip_id
$stato.rdv_preliminare            # RdV preliminare (non firmato)
```

Sequenza attesa:

```
RICEVUTO → IN_VALIDAZIONE → VALIDATO → IN_BUILD → CONSERVATO
                    └──────────────→ ERRORE   (motivi in «errori», codici VLD)
```

- `IN_VALIDAZIONE`: 9 passi di verifica sui file riletti dallo storage (impronte ricalcolate a blocchi, metadati minimi con la stessa definizione di RGL-101).
- `IN_BUILD`: IPdA SInCRO (namespace provvisorio del conservatore `https://schemi.verumdoc.it/ipda/provvisorio/1` dal R14, non verificato contro lo XSD) e scrittura WORM con Object Lock COMPLIANCE; scadenza dalla classe documentale.
- `CONSERVATO`: da R04 arriva solo quando il PdA è nel registro delle impronte ed è coperto da un checkpoint firmato (e controfirmato dal testimone, che in compose risponde subito); evento `versamento.conservato` con `fase: CONSERVATO`, `pda`, `foglia_indice` e `checkpoint_dimensione`. La risposta riporta anche `aip_id` e `urn`.

RdV definitivo (XML, formato 2 con le prove d'inclusione e il checkpoint), disponibile quando il PdV è `CONSERVATO` (in compose entro un checkpoint, `CATENA_CHECKPOINT_SECONDI` = 60 s); prima risponde `409`: riprovare. Si verifica in modo indipendente con `tools/verificatore` (vedi `docs/integrazione/verifica_indipendente.md`).

```powershell
Invoke-WebRequest "$GW/api/v1/versamento/$PDV_ID/rdv" -Headers $K -OutFile "RdV_$PDV_ID.xml" `
  -ResponseHeadersVariable rh
$rh["X-Impronta-SHA256"]
```

---

## FASE 5 — CONSULTAZIONE E CATENA DELLE IMPRONTE

### 5.1 Pacchetti dell'ente (portale)

```powershell
$aip = Invoke-RestMethod "$DSH/api/dashboard/aip?limit=20" -Headers $K
$aip.items | Format-Table aip_id, stato, pdv_id, urn
# paginazione a cursore: se $aip.altri, ripetere con &cursore=$($aip.cursore); skip non è più supportato (422 DSH-422)
```

Elenco dei versamenti: `GET $DSH/api/dashboard/versamenti?stato=&limit=&cursore=` (stessa forma `{items, limit, cursore, altri}`).

### 5.2 Dettaglio di un AIP (distribution)

```powershell
Invoke-RestMethod "$DST/api/v2/distribution/aip/$AIP_ID" -Headers $K
# aip_id, urn, stato, hash_sha256, dimensione_bytes, data_versamento, distribuibile
```

### 5.3 Catena delle impronte

```powershell
# Stato della catena: ogni rotta di hashchain richiede un token
Invoke-RestMethod "$HC/api/v1/hashchain/status" -Headers $H

# Verifica completa, asincrona: solo SUPER_ADMIN o RSC_CONSERVATORE (202, poi lettura dell'esito)
$v = Invoke-RestMethod "$DSH/api/dashboard/hashchain/verifica-completa" -Method Post -Headers $ADMIN_H
Invoke-RestMethod "$DSH/api/dashboard/hashchain/verifica-completa/$($v.verifica_id)" -Headers $ADMIN_H
```

Senza fornitore di firma i blocchi restano `NON_FIRMATO`. Una seconda verifica mentre la prima è in corso risponde `409 HC-409` con il `verifica_id` di quella in corso.

---

## FASE 6 — CANALI R03

Prerequisiti: segreti della FASE 1.1, container `vd-notification` e `vd-sftp-ingest` healthy, chiave API `$API_KEY` e token amministratore `$TOKEN` della FASE 3. Ogni canale chiama lo stesso caso d'uso di versamento: stesse regole, stessi codici, stessa idempotenza, stesse quote.

### 6.1 Sessione di caricamento a parti e versamento per riferimento

Servono per file oltre i 100 MB del multipart (fino a 5 GiB) o per reti instabili. Guida: `docs/integrazione/caricamenti.md`. Per vedere più parti con poco spazio si usa un file di 10 MB (parti da 8 MB → 2 parti).

```powershell
# 1. File di prova da 10 MB che inizia come un PDF
$GRANDE = Join-Path $PWD "registro_grande.pdf"
$testa  = [Text.Encoding]::ASCII.GetBytes("%PDF-1.4`n% VerumDoc prova sessioni`n")
$dati   = [byte[]]::new(10MB)
[Array]::Copy($testa, $dati, $testa.Length)
[IO.File]::WriteAllBytes($GRANDE, $dati)
$SHA_GRANDE = (Get-FileHash $GRANDE -Algorithm SHA256).Hash.ToLower()

# 2. Apertura (Idempotency-Key facoltativa: la stessa chiave restituisce la stessa sessione)
$apertura = @{
  nome_file = "registro_grande.pdf"; dimensione = $dati.Length
  impronta  = @{ algoritmo = "SHA-256"; valore = $SHA_GRANDE }
} | ConvertTo-Json
$s = Invoke-RestMethod "$GW/api/v1/caricamenti" -Method Post -ContentType "application/json" `
  -Headers @{ "X-Api-Key" = $API_KEY; "Idempotency-Key" = "prova-sessione-grande-v1" } -Body $apertura
$CAR_ID = $s.caricamento_id
$s | Select-Object stato, dimensione_parte, numero_parti, scade_il      # APERTO, 8388608, 2, +24 h

# 3. Parti (in qualsiasi ordine, anche in parallelo), con l'impronta della parte
for ($n = 1; $n -le $s.numero_parti; $n++) {
  $inizio = ($n - 1) * $s.dimensione_parte
  $lung   = [Math]::Min($s.dimensione_parte, $dati.Length - $inizio)
  $parte  = [byte[]]::new($lung)
  [Array]::Copy($dati, $inizio, $parte, 0, $lung)
  $imp = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($parte)).ToLower()
  Invoke-RestMethod "$GW/api/v1/caricamenti/$CAR_ID/parti/$n" -Method Put `
    -Headers @{ "X-Api-Key" = $API_KEY; "X-Impronta-Parte" = "sha256=$imp" } `
    -ContentType "application/octet-stream" -Body $parte
}   # ogni risposta: {parte, dimensione, ricevute: [...]}

# 4. Completamento (202 IN_VERIFICA) e attesa della verifica, che gira fuori dalla richiesta
Invoke-RestMethod "$GW/api/v1/caricamenti/$CAR_ID/completa" -Method Post -Headers $K
do { Start-Sleep 2; $st = Invoke-RestMethod "$GW/api/v1/caricamenti/$CAR_ID" -Headers $K }
while ($st.stato -eq "IN_VERIFICA")
$st | Select-Object stato, formato_rilevato, impronte, errore          # COMPLETO, application/pdf
```

Con curl, una parte (dopo aver scritto i byte in `parte1.bin`):

```powershell
curl.exe -sS -X PUT "$GW/api/v1/caricamenti/$CAR_ID/parti/1" -H "X-Api-Key: $API_KEY" `
  -H "Content-Type: application/octet-stream" --data-binary "@parte1.bin"
```

Errori da provare: parte di dimensione sbagliata `422 UPL-002`; `X-Impronta-Parte` errata `422 UPL-003`; stessa parte con contenuto diverso `409 UPL-004`; `completa` con parti mancanti `409 UPL-005` (`dettagli.parti_mancanti`). Ripresa: `GET .../caricamenti/$CAR_ID` → `parti_ricevute`, si inviano solo le mancanti.

**Versamento per riferimento**: stesso percorso `POST /api/v1/versamento/ud`, in JSON, con `file` = mappa `nome_file → caricamento_id` di sessioni `COMPLETO`.

```powershell
$perRif = @{
  indice = @{
    classe_documentale = "fattura"
    chiave = @{ registro = "FATTURE"; anno = "2026"; numero = "FT-2026-003" }
    documenti = @(@{
      id = "doc-001"; tipo = "PRINCIPALE"; nome_file = "registro_grande.pdf"; formato = "application/pdf"
      hash = @{ impronta = $SHA_GRANDE; algoritmo = "SHA-256" }
      parametri = @{
        identificativo_documento = "FT-2026-003"; modalita_formazione = "a"
        tipologia_documentale = "fattura"; data_documento = "2026-10-21"
        soggetto_produttore_id = "12345678901"; soggetto_produttore_nome = "Acme S.r.l."
        oggetto = "Registro di prova caricato a parti"
      }
    })
  }
  file = @{ "registro_grande.pdf" = $CAR_ID }
} | ConvertTo-Json -Depth 7
$r2 = Invoke-RestMethod "$GW/api/v1/versamento/ud" -Method Post -ContentType "application/json" `
  -Headers @{ "X-Api-Key" = $API_KEY; "Idempotency-Key" = "prova-FT-2026-003-v1" } `
  -Body $perRif -StatusCodeVariable sc
$sc; $r2.pdv_id                          # 202 e il nuovo PdV
```

Stesse risposte del multipart, più: sessione inesistente o di un altro ente `404 UPL-404`, non ancora `COMPLETO` `409 UPL-009`, scaduta o annullata `410 UPL-410`, già usata da un altro versamento `409 UPL-008` (una sessione si usa una volta sola). Dopo il versamento `GET .../caricamenti/$CAR_ID` riporta il `pdv_id`; `DELETE` risponde `409 UPL-409`.

### 6.2 Elenco eventi a cursore

Servizio notification (porta 8006), con la chiave API o un Bearer di qualunque ruolo dell'ente. Guida: `docs/integrazione/webhook.md` §4.

```powershell
$cursore = 0
do {
  $pag = Invoke-RestMethod "$NOT/api/v1/eventi?dopo=$cursore&limite=100" -Headers $K
  $pag.eventi | Format-Table id, posizione, tipo, @{ n = "risorsa"; e = { "$($_.risorsa.tipo) $($_.risorsa.id)" } }
  $cursore = $pag.cursore
} while ($pag.altri)
"Ultimo cursore: $cursore"     # da conservare e ripassare come «dopo»

# Solo alcuni tipi (il parametro si ripete)
Invoke-RestMethod "$NOT/api/v1/eventi?dopo=0&tipo=versamento.conservato&tipo=caricamento.completato" -Headers $K
```

Per i versamenti delle FASI 4 e 6.1 ci si aspettano `versamento.ricevuto`, `versamento.validato`, `versamento.conservato` (con `dati.fase = "CONSERVATO"`, `aip_id`, `pda` e `checkpoint_dimensione`), un `catena.checkpoint` per ogni checkpoint e `caricamento.completato`. Il cursore è la **`posizione`**, non l'`id`: `id` serve a scartare i duplicati ed è lo stesso dei webhook. Un tipo sconosciuto (anche `esibizione.pronta`, che non esiste più) → `422 EVT-001` con `dettagli.ammessi`.

### 6.3 Webhook in sviluppo e verifica della firma

Gestione dei webhook: solo `Authorization: Bearer` di un `ADMIN_TENANT` (la chiave API riceve `403 AUTH-010`). In compose `WEBHOOK_CONSENTI_INDIRIZZI_PRIVATI=true`: sono ammessi indirizzi privati e nomi della rete locale (vietato in produzione, dove il servizio non parte). Restano obbligatori **https** (`http://` → `422 WH-001`) e un certificato valido per il destinatario: notification verifica sempre il TLS.

```powershell
$wh = @{
  url  = "https://host.docker.internal:8443/verumdoc"     # ricevitore sul PC, visto dal container
  tipi = @("versamento.ricevuto", "versamento.validato", "versamento.conservato",
           "caricamento.completato", "pdd.pronto")
  descrizione = "Ricevitore di prova"
} | ConvertTo-Json
$w = Invoke-RestMethod "$NOT/api/v1/webhook" -Method Post -Headers $H `
  -ContentType "application/json" -Body $wh -StatusCodeVariable sc
$sc                                    # 201
$WH_ID = $w.id
$WH_SEGRETO = $w.segreto               # whsec_…: mostrato SOLO in questa risposta
Invoke-RestMethod "$NOT/api/v1/webhook" -Headers $H     # elenco: solo segreto_suffisso
```

Errori: `422 WH-002` indirizzo non ammesso, `422 WH-003` tipo sconosciuto, `409 WH-004` oltre 10 webhook, `503 WH-030` se manca `infra/dev_webhook.env`.

**Consegne.** Dopo un nuovo versamento (FASE 4.3 con un'altra chiave UD e un'altra `Idempotency-Key`) notification prova a consegnare:

```powershell
Invoke-RestMethod "$NOT/api/v1/webhook/$WH_ID/consegne" -Headers $H |
  Select-Object -ExpandProperty consegne |
  Format-Table id, tipo_evento, stato, tentativi, ultimo_stato_http, ultimo_codice
```

Senza un ricevitore https raggiungibile e con certificato riconosciuto le consegne restano `IN_CODA` con `ultimo_codice` `WH-102` (connessione o TLS) e si ritentano dopo 60 s, 5 min, 25 min e 2 h; dopo il quinto tentativo diventano `FALLITA` (`?stato=FALLITA`) e si possono ripetere con `POST .../consegne/{consegna_id}/riprova` (`202`; `409 WH-011` se la consegna non è `FALLITA`). Gli eventi persi si recuperano comunque dall'elenco (6.2).

**Verifica della firma** (`VerumDoc-Firma: t=<unix>,v1=<hex>`, con `v1 = hex(HMAC-SHA256(segreto, "<t>." + corpo))` sui byte ricevuti). Prova con l'esempio di `docs/integrazione/webhook.md` §5.3:

```powershell
$segreto = "whsec_esempio-di-segreto-non-usare"
$t       = "1792576800"
$corpo   = '{"creato_il":"2026-10-21T10:00:00+00:00","dati":{"pdv_id":"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},"id":42,"risorsa":{"id":"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90","tipo":"versamento"},"tipo":"versamento.ricevuto"}'
$hmac    = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($segreto))
[Convert]::ToHexString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes("$t.$corpo"))).ToLower()
# atteso: 362a779b910d922709231ed641f88061f03db35fa8a4a1bc939f4afe65b228ad
```

Con openssl (Git Bash): `printf '%s' "1792576800.$CORPO" | openssl dgst -sha256 -hmac 'whsec_esempio-di-segreto-non-usare'`. Il destinatario deve anche rifiutare `t` più vecchio o più nuovo di 5 minuti e scartare i duplicati per `VerumDoc-Id-Consegna` (consegna almeno una volta).

**Ricevitore locale (facoltativo).** Per una consegna vera serve un ricevitore https il cui certificato notification riconosca. In sviluppo si può usare un certificato autofirmato passato a notification con `WEBHOOK_CA_PEM`:

```powershell
# certificato per host.docker.internal (Git Bash o openssl nel PATH)
openssl req -x509 -newkey rsa:2048 -nodes -days 7 -keyout ricevitore.key -out ricevitore.crt `
  -subj "/CN=host.docker.internal" -addext "subjectAltName=DNS:host.docker.internal"
```

`ricevitore_webhook.py` (verifica la firma e risponde 204):

```python
import hashlib, hmac, http.server, os, ssl, time

SEGRETO = os.environ["VD_WEBHOOK_SEGRETO"]

class Ricevitore(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        campi = dict(p.split("=", 1) for p in self.headers["VerumDoc-Firma"].split(","))
        atteso = hmac.new(SEGRETO.encode(), f"{campi['t']}.".encode() + corpo,
                          hashlib.sha256).hexdigest()
        valida = (hmac.compare_digest(atteso, campi["v1"])
                  and abs(time.time() - int(campi["t"])) <= 300)
        print(self.headers["VerumDoc-Evento"], self.headers["VerumDoc-Id-Consegna"],
              "firma valida" if valida else "FIRMA NON VALIDA")
        self.send_response(204 if valida else 401)
        self.end_headers()

server = http.server.HTTPServer(("0.0.0.0", 8443), Ricevitore)
contesto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
contesto.load_cert_chain("ricevitore.crt", "ricevitore.key")
server.socket = contesto.wrap_socket(server.socket, server_side=True)
server.serve_forever()
```

```powershell
$env:VD_WEBHOOK_SEGRETO = $WH_SEGRETO
python ricevitore_webhook.py
```

Poi, in un file locale `docker-compose.override.yml` (da non committare), `services: notification: environment: WEBHOOK_CA_PEM: |` seguito dal contenuto di `ricevitore.crt`, e `docker compose up -d notification`. Le consegne dovrebbero passare a `CONSEGNATA` con `ultimo_stato_http` 204.

### 6.4 Account SFTP con chiave e invio di una cartella

Guida: `docs/integrazione/sftp.md`. In compose il server SFTP e il lavoratore girano nello stesso container: porta **2222** sull'host. Il lavoratore versa con un token di canale firmato con `SFTP_TOKEN_CHIAVE_PRIVATA` (FASE 1.1), accettato dal gateway solo per il responsabile di un account SFTP attivo.

```powershell
# 1. Chiave SSH del sistema versante (premere Invio alle domande sulla passphrase)
ssh-keygen -t ed25519 -f "$HOME\.ssh\verumdoc_dev" -C "acme-protocollo"
ssh-keygen -l -f "$HOME\.ssh\verumdoc_dev.pub"          # impronta SHA256:… da confrontare

# 2. Account (nome unico fra tutti gli enti: 3-64 caratteri minuscoli, cifre, . _ -)
$acc = Invoke-RestMethod "$ONB/api/v1/onboarding/tenant/$TENANT_ID/sftp/account" -Method Post `
  -Headers $ADMIN_H -ContentType "application/json" `
  -Body (@{ nome_utente = "acme.protocollo"; descrizione = "Protocollo di prova"; user_id = $ADMIN_USER_ID } | ConvertTo-Json)
$ACC_ID = $acc.id
$acc | Select-Object nome_utente, stato, connessione           # ATTIVO

# 3. Chiave pubblica sull'account
$pub = (Get-Content "$HOME\.ssh\verumdoc_dev.pub" -Raw).Trim()
Invoke-RestMethod "$ONB/api/v1/onboarding/tenant/$TENANT_ID/sftp/account/$ACC_ID/chiavi" -Method Post `
  -Headers $ADMIN_H -ContentType "application/json" -Body (@{ chiave_pubblica = $pub } | ConvertTo-Json)
# 201 {id, algoritmo: ssh-ed25519, bit: 256, impronta: SHA256:…, attiva: true}
```

Le stesse operazioni le può fare un `ADMIN_TENANT` del proprio ente (`$H`). In compose `SFTP_HOST_PUBBLICO` non è impostato e la porta pubblica predefinita è 22: il campo `connessione` mostra quindi `host` nullo e `porta` 22, ma in locale ci si collega a `localhost:2222`. Errori: nome già usato `409 SFTP-004`, chiave non valida `422 SFTP-001`, tipo o lunghezza non ammessi `422 SFTP-002` (RSA almeno 3072 bit), chiave già registrata `409 SFTP-003`.

```powershell
# 4. Cartella del pacchetto: file + indice.json (stesso IndiceUD del REST), senza BOM
$PACCHETTO = Join-Path $PWD "pacchetto_sftp"
New-Item -ItemType Directory -Force $PACCHETTO | Out-Null
Copy-Item $FILE (Join-Path $PACCHETTO "fattura.pdf")
$indiceSftp = @{
  classe_documentale = "fattura"
  chiave = @{ registro = "FATTURE"; anno = "2026"; numero = "FT-2026-SFTP-001" }
  documenti = @(@{
    id = "doc-001"; tipo = "PRINCIPALE"; nome_file = "fattura.pdf"; formato = "application/pdf"
    hash = @{ impronta = $SHA256; algoritmo = "SHA-256" }
    parametri = @{
      identificativo_documento = "FT-2026-SFTP-001"; modalita_formazione = "a"
      tipologia_documentale = "fattura"; data_documento = "2026-10-21"
      soggetto_produttore_id = "12345678901"; soggetto_produttore_nome = "Acme S.r.l."
      oggetto = "Fattura versata via SFTP"
    }
  })
} | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $PACCHETTO "indice.json"), $indiceSftp)

# 5. Invio con cartella temporanea e rinomina atomica
@"
mkdir in/ACME-2026-0001.tmp
put "$PACCHETTO\fattura.pdf" in/ACME-2026-0001.tmp/fattura.pdf
put "$PACCHETTO\indice.json" in/ACME-2026-0001.tmp/indice.json
rename in/ACME-2026-0001.tmp in/ACME-2026-0001
"@ | Set-Content invio_sftp.txt
sftp -P 2222 -i "$HOME\.ssh\verumdoc_dev" -o StrictHostKeyChecking=accept-new `
  -b invio_sftp.txt acme.protocollo@localhost
```

Il lavoratore prende la cartella quando contiene `indice.json`, nessun file `.tmp-` e non cambia da 30 secondi; la sposta in `lavorazione/`, carica i file con le sessioni di caricamento, versa e scrive l'esito. Dopo circa un minuto:

```powershell
"get out/ACME-2026-0001/esito.json esito_sftp.json" | Set-Content leggi_esito.txt
sftp -P 2222 -i "$HOME\.ssh\verumdoc_dev" -b leggi_esito.txt acme.protocollo@localhost
Get-Content esito_sftp.json | ConvertFrom-Json | Select-Object stato, pdv_id, stato_pdv, reinvio, errori
```

Atteso `stato: VERSATO` con un `pdv_id` («preso in carico», come la `202` del REST) e la cartella in `fatti/`. Con `SCARTATO` gli `errori` sono in problem+json (es. `SFTP-101` indice assente, `SFTP-102` indice non leggibile — spesso un BOM —, `SFTP-104` file non dichiarato, `VRS-124` chiave UD già usata). Ripetere lo stesso invio con un'altra cartella dello stesso nome e lo stesso `indice.json` non crea un secondo PdV: `"reinvio": true`, stesso `pdv_id`.

La chiave host di sviluppo si rigenera a ogni nuovo container: se `sftp` segnala «REMOTE HOST IDENTIFICATION HAS CHANGED», `ssh-keygen -R "[localhost]:2222"`. Se sftp-ingest non parte: `docker compose logs sftp-ingest` (di solito mancano `infra/dev_sftp.env` o `infra/dev_canali_pub.env`).

### 6.5 Ricerca

L'indice OpenSearch si popola in modo asincrono (aip-builder accoda, il lavoratore di distribution indicizza a lotti): un PdA appena conservato può comparire con qualche secondo di ritardo. Le parole si cercano intere; i metadati marcati sensibili nel profilo della classe non sono cercabili.

```powershell
# API (distribution): a cursore, total contato al massimo fino a 1000 (total_esatto)
$q = @{ query = "Fornitura"; classe_documentale = "fattura"; limit = 10 } | ConvertTo-Json
$res = Invoke-RestMethod "$DST/api/v2/distribution/search" -Method Post -Headers $K `
  -ContentType "application/json" -Body $q
$res | Select-Object total, total_esatto, altri, cursore
$res.items | Format-Table

# Portale (dashboard-api): con testo o classe usa l'indice (motore "indice"), con soli stato e anno il database
$q2 = @{ query = "Fornitura"; limit = 10 } | ConvertTo-Json
(Invoke-RestMethod "$DSH/api/dashboard/search" -Method Post -Headers $K `
  -ContentType "application/json" -Body $q2).motore                   # indice
$q3 = @{ anno = "2026"; limit = 10 } | ConvertTo-Json
(Invoke-RestMethod "$DSH/api/dashboard/search" -Method Post -Headers $K `
  -ContentType "application/json" -Body $q3).motore                   # database
```

Con curl:

```powershell
[IO.File]::WriteAllText((Join-Path $PWD "ricerca.json"), $q)
curl.exe -sS "$DST/api/v2/distribution/search" -H "X-Api-Key: $API_KEY" `
  -H "Content-Type: application/json" --data-binary "@ricerca.json"
```

`skip` > 0 → `422 DST-422` (distribution) o `422 DSH-422` (portale); cursore di un'altra ricerca `422 DSH-104`/`DST-104`; OpenSearch non raggiungibile → `503 DST-503`. Limite proprio della ricerca: 30/120/600 al minuto per piano (`429 QTA-001`).

### 6.6 Richiesta di un pacchetto di distribuzione (PdD)

Tipi: `COMPLETO`, `ESTRATTO` (con `file_richiesti`), `ESIBIZIONE`, `MIGRAZIONE`. Consegna `LINK` (predefinita fino a 50 MB: collegamenti nella risposta) o `ZIP` (preparato fuori dalla richiesta, evento `pdd.pronto`). Il richiedente è l'utente del token o il responsabile della chiave API; `richiedente_id` nel corpo è ignorato.

```powershell
$req = @{
  aip_id = $AIP_ID; tipo = "ESIBIZIONE"; motivo = "Verifica test locale"
  consegna = "LINK"; url_expiry_seconds = 3600
} | ConvertTo-Json
$pdd = Invoke-RestMethod "$DST/api/v2/distribution/richiedi" -Method Post -Headers $K `
  -ContentType "application/json" -Body $req
$pdd | Select-Object pdd_id, tipo, stato, consegna, hash_pdd, url_scadenza   # stato PRONTO
$pdd.documenti | Format-Table nome_file, hash_sha256, impronta_verificata

# Consegna ZIP: stato IN_PREPARAZIONE, poi PRONTO (ed evento pdd.pronto con dati.tipo = "ESIBIZIONE")
$reqZip = @{ aip_id = $AIP_ID; tipo = "COMPLETO"; motivo = "Prova consegna ZIP"; consegna = "ZIP" } | ConvertTo-Json
$z = Invoke-RestMethod "$DST/api/v2/distribution/richiedi" -Method Post -Headers $K `
  -ContentType "application/json" -Body $reqZip
Invoke-RestMethod "$DST/api/v2/distribution/pdd/$($z.pdd_id)" -Headers $K
Invoke-RestMethod "$DST/api/v2/distribution/storico" -Headers $K      # storico delle richieste
```

I collegamenti prefirmati puntano all'host interno `minio:9000` (endpoint S3 di distribution in compose): per aprirli dal PC serve che `minio` risolva in `127.0.0.1` (file hosts), perché la firma include l'host. AIP di un altro ente → `404 DST-404`. Limite proprio: 10/60/300 richieste di PdD al minuto per piano. La richiesta non è idempotente (VD-C-106): ripeterla crea un altro PdD.

---

## FASE 7 — ENDPOINT DI GOVERNO

### 7.1 Registro delle operazioni

```powershell
# SUPER_ADMIN e RSC_CONSERVATORE vedono tutto (o l'ente indicato), ADMIN_TENANT solo il proprio ente
Invoke-RestMethod "$DSH/api/dashboard/audit?limit=20" -Headers $ADMIN_H
# «evento» è cercato come parte del testo: sftp → sftp.account.creato, sftp.chiave.aggiunta…
Invoke-RestMethod "$DSH/api/dashboard/audit?evento=sftp&limit=10" -Headers $H
```

### 7.2 Manuale di conservazione e piano di cessazione (bozze generate dai dati)

```powershell
# Solo SUPER_ADMIN e RSC_CONSERVATORE; ogni sezione ha stato_sezione (generata, da_compilare, da_realizzare…)
Invoke-RestMethod "$DSH/api/dashboard/manuale-conservazione" -Headers $ADMIN_H
Invoke-RestMethod "$DSH/api/dashboard/piano-cessazione" -Headers $ADMIN_H
```

### 7.3 Scadenze imminenti

```powershell
# PdA CONSERVATO o IN_CATENA con scadenza entro N giorni; ADMIN_TENANT solo del proprio ente
Invoke-RestMethod "$GW/api/v1/lifecycle/scadenze?giorni=90" -Headers $H
```

`POST /api/v1/lifecycle/{aip_id}/avvia` esiste ma non è disponibile in questo rilascio (scarto e rettifica non realizzati).

---

## FASE 8 — DASHBOARD VISUALE

### 8.1 Avvia il frontend

```powershell
cd frontend
npm ci
npm run dev
```

Apri `http://localhost:5173/login`: in sviluppo ci sono i profili di prova (dev-token) e l'accesso SPID simulato.

### 8.2 Accesso per ruolo (dalle protezioni di `frontend/src/App.tsx`)

| Pagina | Percorso | Ruoli |
|---|---|---|
| Panoramica, AIP, catena (explorer), ricerca | `/overview`, `/aip`, `/blockchain`, `/search` | tutti gli autenticati |
| Versamento | `/versamento` | chi ha lo scope `versamento:ud` |
| Versamenti (pipeline) | `/versamenti` | SUPER_ADMIN, ADMIN_TENANT, OPERATORE, RSC_CONSERVATORE |
| Integrità della catena | `/hashchain` | SUPER_ADMIN, ADMIN_TENANT, RSC_CONSERVATORE |
| Distribuzione | `/distribution` | SUPER_ADMIN, ADMIN_TENANT, VIEWER, RSC_CONSERVATORE |
| Classi documentali | `/classi-documentali` | SUPER_ADMIN, ADMIN_TENANT, RSC_CONSERVATORE |
| Enti | `/tenants` | SUPER_ADMIN |
| Utenti e accessi | `/utenti` | SUPER_ADMIN, ADMIN_TENANT |
| Notifiche, fascicoli | `/notifiche`, `/fascicoli` | SUPER_ADMIN, ADMIN_TENANT, OPERATORE, RSC_CONSERVATORE |
| SLA e report, registro operazioni | `/sla`, `/audit` | SUPER_ADMIN, ADMIN_TENANT, RSC_CONSERVATORE |
| Manuale, contratti, nomine RSC | `/manuale`, `/contratti`, `/nomine-rsc` | SUPER_ADMIN, RSC_CONSERVATORE |
| Funzioni non realizzate | `/da-realizzare/:funzione` | pagina che lo dichiara |

---

## FASE 9 — TEST AUTOMATICI

I test d'integrazione usano un PostgreSQL **reale** migrato a head come template (`VD_TEST_DATABASE_URL`); senza la variabile quei test sono saltati con il motivo. Ogni servizio ha il proprio ambiente (`services\<servizio>\.venv`, creato con `make venv SVC=<servizio>` o a mano con `python -m venv` e `pip install -e ".[dev]"`).

```powershell
# Template dei test nel PostgreSQL di compose
docker exec vd-postgres createdb -U vd_user vd_template
cd services\gateway
$env:DATABASE_URL = "postgresql+asyncpg://vd_user:vd_secret_dev_only@localhost:5434/vd_template"
alembic upgrade head
cd ..\..
$env:VD_TEST_DATABASE_URL = $env:DATABASE_URL

# Un servizio
cd services\gateway; .\.venv\Scripts\python -m pytest tests -q; cd ..\..

# Prove mirate sui canali R03
cd services\gateway;      .\.venv\Scripts\python -m pytest tests\test_caricamenti.py tests\test_r03_canali_e2e.py tests\test_f3_correzioni_revisione.py -q; cd ..\..
cd services\notification; .\.venv\Scripts\python -m pytest tests\test_b3_api.py tests\test_f2_eventi_senza_salti.py tests\test_f2_ssrf.py -q; cd ..\..
cd services\sftp-ingest;  .\.venv\Scripts\python -m pytest tests\test_lavoratore.py tests\test_i3_reinvii.py -q; cd ..\..
cd services\distribution; .\.venv\Scripts\python -m pytest tests\test_b4_pdd.py tests\test_f2_ricerca.py -q; cd ..\..
```

Con make (bash): `VD_TEST_DATABASE_URL=… make test`, poi `make verifiche` e `make lint-critico`; frontend `npm run typecheck && npm run lint && npm run build`. Il rilascio R03 riporta 2.712 test passati, 0 falliti e 0 saltati con PostgreSQL reale; il dettaglio per suite è nel changelog.

---

## TROUBLESHOOTING

| Problema | Causa | Soluzione |
|----------|-------|-----------|
| Il gateway non parte | Migrazione o seed falliti, oppure manca `infra/dev_jwt.env` | `docker compose logs gateway`; generare le chiavi (FASE 1.1) |
| `seed_bucket_senza_object_lock` nei log | Volume dello storage con il bucket dei PdA creato senza Object Lock | `docker compose down -v` e riavvio: il seed del gateway crea `verumdoc-pdv`, `verumdoc-aip` (con Object Lock) e `verumdoc-pdd` all'avvio, non al primo versamento |
| sftp-ingest non parte | Mancano `infra/dev_sftp.env` / `infra/dev_canali_pub.env` | `.\infra\scripts\genera_segreti_dev_r03.ps1`, poi `docker compose up -d` |
| Webhook `503 WH-030` | Manca `infra/dev_webhook.env` | Come sopra, poi `docker compose up -d notification` |
| Webhook `422 WH-001` | URL `http://` | Serve `https://` anche in sviluppo |
| Consegne con `WH-102` | Ricevitore assente o certificato non riconosciuto | FASE 6.3, ricevitore locale con `WEBHOOK_CA_PEM` |
| Token `401` | Token scaduto (1 h) o firma non valida | Rigenerare con `/api/v1/auth/dev-token` |
| `401 AUTH-003` | Utente disattivato, ente sospeso o ruolo del token diverso da quello attuale | Nuovo token con il ruolo attuale dell'utente |
| Onboarding `401`/`403` | Rotte sempre autenticate; enti e `ADMIN_TENANT` solo da SUPER_ADMIN | Token della FASE 2.1 |
| Chiave API o account SFTP `422 ONB-422` | Chi crea non è un utente registrato e manca `user_id` | Passare `user_id` di un utente attivo dell'ente (FASE 2.3) |
| `SPID-008` | Nessun utente attivo con quel CF in un ente attivo | Creare l'utente con `codice_fiscale` (FASE 2.3) e attivare l'ente |
| `SPID-010` | `redirect_uri` fuori dall'elenco ammesso | `http://localhost:5173/login` o `/cb` |
| `422 VRS-130` con `RGL-101` | Metadati minimi assenti o solo in `metadati_extra` | Al primo livello di `parametri` (FASE 4.3) |
| `422 VRS-109` | PDF con BOM (es. `Out-File -Encoding utf8` di Windows PowerShell) | Scrivere il file con `[IO.File]::WriteAllText` |
| `400 SEC-006` | Estensione non fra i formati della classe | Usare un'estensione ammessa (`dettagli.ammesse`) |
| `409 VRS-124` | Chiave UD (registro, anno, numero) già usata da un PdV attivo | Cambiare `numero` o riusare la stessa `Idempotency-Key` per ripetere |
| Avviso `VRS-126` | Versamento senza `Idempotency-Key` | Aggiungere l'intestazione (dal 01/04/2027 `428 VRS-125`) |
| `429 QTA-001` / `QTA-002` | Quota del piano (richieste al minuto / PdV al giorno) | Attendere `Retry-After` |
| SFTP `SFTP-102` | `indice.json` con BOM o non JSON | Scriverlo con `[IO.File]::WriteAllText` |
| Cartella SFTP che resta in `in/` | Account, ente o responsabile non attivi, oppure file `.tmp-` rimasti | Controllare l'account; dopo 24 h la cartella è scartata con il motivo |
| PdV fermo in `RICEVUTO` | Topic Kafka non creati o validation fermo | FASE 1.3; `docker compose logs validation` |
| `/rdv` risponde `409 VRS-409` | RdV definitivo non ancora emesso (dopo il sigillo nella catena) | Riprovare dopo qualche secondo |
| Ricerca `503 DST-503` | OpenSearch non risponde | `docker compose ps opensearch` |
| Link del PdD non si apre | Il link punta a `minio:9000` | Aggiungere `127.0.0.1 minio` al file hosts |
| Porta 5432 occupata | PostgreSQL nativo di Windows | Il compose espone 5434 |

---

## PORTE DI RIFERIMENTO

Valori di `docker-compose.yml`, uguali alla tabella «Porte» del `README.md`. Swagger (`/docs`) è attivo fuori produzione.

| Servizio | Porta host | Salute (readiness) | Swagger | Note |
|---------|-------|-------------|-------------|------|
| Gateway | 8000 | `/api/v1/health/ready` | http://localhost:8000/docs | Versamento, sessioni di caricamento, stato e RdV, dev-token, SPID, PDND |
| Validation | 8001 | `/api/v1/health/ready` | http://localhost:8001/docs | Consumer Kafka (9 passi); HTTP solo salute e info |
| Signature | 8003 | `/api/v1/health/ready` | http://localhost:8003/docs | Firma e marca; stato con token |
| Hashchain | 8004 | `/api/v1/health/ready` | http://localhost:8004/docs | Stato e verifica completa (con token) |
| AIP Builder | 8005 | `/api/v1/health/ready` | http://localhost:8005/docs | Consumer Kafka; HTTP solo salute e info |
| Notification | 8006 | `/api/v1/health/ready` | http://localhost:8006/docs | Posta (Mailpit), elenco eventi, webhook firmati |
| Onboarding | 8007 | `/api/v1/health/ready` | http://localhost:8007/docs | Enti, utenti, classi, chiavi API, account SFTP |
| Distribution | 8008 | `/api/v2/health/ready` | http://localhost:8008/docs | PdD (4 tipi, LINK o ZIP), ricerca, fascicoli, migrazione |
| Dashboard API | 8009 | `/api/health/ready` | http://localhost:8009/docs | Portale, audit, SLA, manuale, piano di cessazione |
| Ciclo di vita | 8012 | `/api/v1/health/ready` | http://localhost:8012/docs | Pratiche: rettifica, annullamento, scarto, riversamento, restituzione (R05) |
| SFTP ingest | 2222 (SFTP), 8010 (salute) | `/api/v1/health/ready` (live: `/api/v1/health/live`) | http://localhost:8010/docs | `sftp -P 2222 <nome_utente>@localhost` |
| Frontend (dev) | 5173 | — | http://localhost:5173 | React + Vite |
| Storage S3 (versitygw, servizio `minio`) | 9000 (API S3) | `/health` | — (nessuna console web da R05) | minioadmin / minioadmin_dev_only; con un vecchio volume MinIO: `docker compose down -v` |
| Mailpit | 1025 (SMTP), 8025 (interfaccia) | — | http://localhost:8025 | Posta di sviluppo catturata |
| PostgreSQL | 5434 | `pg_isready -p 5434` | — | `psql -h localhost -p 5434 -U vd_user verumdoc_dev` (password vd_secret_dev_only) |
| Redis | 6379 | — | — | `redis-cli -a redis_secret_dev_only ping` |
| Kafka | 9092 | — | — | Indirizzo pubblicato `kafka:9092`: solo dai container |
| OpenSearch | 9200 | — | http://localhost:9200/_cat/indices | Indice di ricerca |
| OPA | 8181 | — | — | |
| Vault (dev) | 8200 | — | — | Token `dev-root-token-local-only` |
