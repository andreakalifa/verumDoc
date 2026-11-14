# Metadati AgID — come versare un documento conforme

**Allineato al codice del rilascio R03 (21/10/2026); aggiornato in R05 (23/10/2026): campi `accordo` e `override`, livelli ACCORDO e PDV, catalogo delle regole 2, profili di tipologia, validatore preventivo (§2, §7).** Riferimenti normativi: Linee guida AgID sulla formazione, gestione e conservazione dei documenti informatici (2021), Allegato 5 (metadati) e Allegato 2 (formati).

Questa guida descrive l'**indice dell'unità documentaria** (`IndiceUD`) e i metadati che lo accompagnano, comuni a tutti i canali di versamento. Canali, autenticazione, idempotenza, quote ed errori sono nella [guida all'API per i produttori](docs/integrazione/README.md); i codici d'errore nel [catalogo](docs/catalogo/errori.md).

## 1. Dove si mettono i metadati

Con l'API REST il versamento è `POST /api/v1/versamento/ud` in `multipart/form-data`:

- campo `indice`: il JSON dell'`IndiceUD`;
- un campo `files` per ogni file dichiarato nell'indice;
- intestazione `Idempotency-Key` (§6);
- autenticazione: chiave API (`X-Api-Key: vd_<id>_<segreto>`) oppure `Authorization: Bearer <token>`, con il permesso `versamento:ud`.

Per file grandi lo stesso indice si invia in JSON citando sessioni di caricamento (`{"indice": {…}, "file": {nome_file: caricamento_id}}`, vedi [caricamenti.md](docs/integrazione/caricamenti.md)). Gli altri canali usano lo stesso indice (SFTP: `indice.json`) o lo ricavano dal proprio formato (PDND: campo `metadati`).

## 2. Struttura dell'indice

```json
{
  "classe_documentale": "fattura",
  "chiave": {"registro": "FATTURE", "anno": "2026", "numero": "FT-2026-001"},
  "documenti": [
    {
      "id": "doc-principale-001",
      "tipo": "PRINCIPALE",
      "nome_file": "fattura_2026_001.pdf",
      "formato": "application/pdf",
      "hash": {"impronta": "<SHA-256 esadecimale>", "algoritmo": "SHA-256"},
      "parametri": {
        "identificativo_documento": "FT-2026-001",
        "modalita_formazione": "a",
        "tipologia_documentale": "fattura",
        "data_documento": "2026-03-18",
        "soggetto_produttore_id": "12345678901",
        "soggetto_produttore_nome": "Acme S.r.l.",
        "oggetto": "Fattura servizi IT marzo 2026",
        "lingua": "it",
        "numero_registrazione": "FT-2026-001",
        "metadati_extra": {"importo": "15000.00", "valuta": "EUR"}
      }
    },
    {
      "id": "doc-allegato-001",
      "tipo": "ALLEGATO",
      "nome_file": "distinta.pdf",
      "hash": {"impronta": "<SHA-256 esadecimale>", "algoritmo": "SHA-256"}
    }
  ]
}
```

| Campo | Obbligatorio | Regole |
|---|---|---|
| `classe_documentale` | sì | Codice di una classe documentale censita e attiva per l'ente (o di base del servizio): altrimenti `422 VRS-105` (non censita) o `422 VRS-106` (non attiva). Al più 50 caratteri. |
| `chiave` {`registro`, `anno`, `numero`} | consigliata | Identifica l'unità documentaria. Se `anno` e `numero` sono valorizzati, la chiave è **univoca** fra i versamenti attivi dell'ente (§5). |
| `documenti[]` | sì, almeno uno | Elenco **piatto** di tutti i documenti dell'unità, ciascuno con il suo file. |
| `documenti[].id` | sì | Identificativo del documento nell'indice, al più 100 caratteri. |
| `documenti[].tipo` | sì | `PRINCIPALE`, `ALLEGATO`, `ANNESSO` o `ANNOTAZIONE`. |
| `documenti[].nome_file` | sì | Nome del file allegato, al più 255 caratteri, unico nell'indice (`400 VRS-111` se ripetuto). Estensioni accettate oggi: `.pdf`, `.xml`, `.p7m`, `.tif`, `.tiff`, `.jpg`, `.jpeg`, `.png` (altrimenti `400 SEC-006`). |
| `documenti[].formato` | no | MIME dichiarato. Il formato si **rileva dal contenuto** e si confronta con quello dichiarato (§4). |
| `documenti[].hash` | sì | `impronta` esadecimale e `algoritmo` (`SHA-256` predefinito, `SHA-512`, `SHA3-256`). Il servizio ricalcola l'impronta sui byte ricevuti: se è diversa, `400 VRS-102`. L'SDK la calcola da solo se manca. |
| `documenti[].parametri` | sì per il `PRINCIPALE` | Metadati del documento (§3). |
| `accordo` | no | (R05) Codice dell'**accordo di versamento** dell'ente da applicare (`[A-Za-z0-9._-]`, al più 50 caratteri). Assente: quello dell'ente per la classe, poi quello dell'ente per tutte le classi, poi nessuno. Un accordo inesistente, non in vigore nel giorno del versamento o non valido per la classe → `422 RGL-112` (mai un ripiego su un altro accordo). §7.1. |
| `override` {`motivo`, `valori`} | no | (R05) **Personalizzazione motivata** delle impostazioni per questo solo versamento (livello PDV): `motivo` di almeno 10 caratteri, `valori` = codice dell'impostazione → valore. Solo per le voci che lo ammettono (oggi l'effetto di `RGL-103`); altrimenti `422 RGL-111` con l'elenco in `dettagli.problemi`. Il versamento accettato porta l'avviso `RGL-110`. §7.2. |

Ogni file inviato deve comparire nell'indice (`400 VRS-104` altrimenti) e ogni file dell'indice deve essere inviato (`400 VRS-101`). Un indice che non rispetta lo schema (tipi, date, valori ammessi) è rifiutato con `400 VRS-001`.

> Il modello dell'indice ha anche un campo `allegati` annidato dentro un documento, ma il servizio di versamento **non lo usa**: dichiarare allegati, annessi e annotazioni come voci di `documenti[]` con il loro `tipo`. Dalla R03 un `allegati` annidato **non vuoto** è rifiutato con `400 VRS-114` (con l'elenco dei documenti interessati); `allegati: []` resta ammesso.

## 3. Metadati (`parametri`)

### 3.1 Metadati minimi dell'Allegato 5 (documento principale)

Sul documento `PRINCIPALE` sono obbligatori i 7 metadati minimi. Li verifica la regola **RGL-101**, che è bloccante e non si può disattivare né per ente né per classe:

| Campo | Contenuto |
|---|---|
| `identificativo_documento` | Identificativo univoco del documento nel sistema del produttore |
| `modalita_formazione` | Lettera `a`, `b`, `c` o `d` (§3.4) |
| `tipologia_documentale` | Tipologia del documento (es. fattura, contratto, delibera) |
| `data_documento` | Data di formazione o firma, `AAAA-MM-GG` |
| `soggetto_produttore_id` | Codice fiscale o partita IVA del produttore |
| `soggetto_produttore_nome` | Denominazione del produttore |
| `oggetto` | Descrizione sintetica, da 5 a 1000 caratteri |

Se ne mancano, la risposta è **`422 VRS-130`** con **tutti** i mancanti in `dettagli.rilievi[]` (un rilievo `RGL-101` per campo, con `campo` e `documento`). Per gli altri tipi di documento i metadati sono facoltativi.

Mettere i metadati minimi **al primo livello di `parametri`**: è l'unico punto in cui li cerca la regola RGL-101 (dalla R03 un metadato minimo presente solo in `metadati_extra` non vale: `422 VRS-130` con `RGL-101`), e la verifica asincrona (passo 6, `VLD-601`) usa la stessa definizione, quindi un PdV accettato con `202` non è rifiutato dopo per lo stesso motivo.

### 3.2 Metadati facoltativi previsti

| Campo | Contenuto |
|---|---|
| `lingua` | Lingua del documento (ISO 639-1, predefinita `it`) |
| `classificazione` | Classificazione archivistica |
| `fascicolo_id` | Fascicolo di appartenenza |
| `numero_registrazione`, `data_registrazione` | Registrazione di protocollo (`data_registrazione` in `AAAA-MM-GG`) |
| `tipo_firma` | Tipo di firma (es. CAdES, PAdES, XAdES) |
| `soggetto_destinatario` | Elenco dei destinatari (array di stringhe) |
| `codice_ipa`, `aoo` | Codice IPA dell'ente e area organizzativa omogenea |
| `versione_documento` | Versione del documento (predefinita `1.0`) |
| `metadati_extra` | Oggetto libero per i metadati propri del produttore o della classe |

### 3.3 Metadati della classe e metadati non previsti

- **Metadati obbligatori della classe.** Una classe (o il suo profilo di metadati) può richiedere metadati in più sul documento principale: se mancano, rilievo **RGL-102** in `VRS-130`. Si possono indicare al primo livello di `parametri` o dentro `parametri.metadati_extra`.
- **Profilo di metadati della classe.** Il conservatore può associare a una classe un profilo versionato che dichiara nomi, tipi (testo, data, intero, decimale, booleano, elenco di valori, codice fiscale, partita IVA, codice IPA) e campi obbligatori. Vale il profilo valido nel giorno del versamento (ora italiana). Un valore di tipo errato è il rilievo **RGL-106** (il messaggio non riporta mai il valore).
- **Metadati non previsti.** Una chiave di `parametri` che non è fra quelle previste (§3.1, §3.2) né nel profilo della classe **non viene scartata**: è conservata così come versata (indice, PdV, rapporto di versamento preliminare come avviso, IPdA come metadato aggiuntivo) e segnalata con l'avviso **RGL-103** nella risposta `202`. Per non ricevere l'avviso, mettere i metadati propri in `metadati_extra` o chiedere di aggiungerli al profilo della classe.

### 3.4 `modalita_formazione`

Si trasmette la **lettera** della modalità di formazione del documento informatico (Linee guida §2.1.1, metadato `ModalitaDiFormazione` dell'Allegato 5). Unica definizione nel codice: `libs/shared/models` (`ModalitaFormazione`).

| Valore | Modalità |
|---|---|
| `a` | creazione tramite strumenti software che assicurino la produzione di documenti nei formati dell'Allegato 2 |
| `b` | acquisizione di un documento informatico per via telematica o su supporto informatico, della copia per immagine o della copia informatica di un documento analogico |
| `c` | memorizzazione su supporto informatico delle informazioni risultanti da transazioni o processi informatici o dalla presentazione telematica di dati attraverso moduli o formulari |
| `d` | generazione o raggruppamento, anche automatico, di un insieme di dati o registrazioni provenienti da una o più banche dati, secondo una struttura logica predeterminata e memorizzata in forma statica |

I vecchi valori (`INFORMATICA_AB_ORIGINE`, `SCANSIONE`, `COPIA_IMMAGINE`, `TRASCRIZIONE` e simili) non sono accettati: l'indice non rispetta lo schema (`400 VRS-001`). Corrispondenza indicativa: nato digitale → `a`; scansione o copia per immagine di un analogico → `b`. La scelta spetta al produttore; le descrizioni vanno verificate sul testo ufficiale delle Linee guida. Senza una modalità valida il pacchetto di archiviazione non si costruisce (`AIP-METADATI-001`): non c'è un valore di ripiego.

## 4. Formati e dimensioni

- **Formato dal contenuto.** Il servizio riconosce il formato dai primi 4 KB del file: oggi PDF, PNG, JPEG, TIFF, PKCS#7 (`.p7m`), XML e testo. Un contenuto non riconosciuto o diverso dal `formato` dichiarato è `422 VRS-109` (`text/xml` e `application/xml` sono equivalenti). Un eseguibile, con qualunque nome, è `422 VRS-108`. Il riconoscimento è per firme iniziali: non sostituisce l'identificazione PRONOM né la validazione di conformità (es. PDF/A), non ancora realizzate.
- **Formati ammessi dalla classe** (Allegato 2): se la classe elenca dei formati, un file di un formato diverso è il rilievo **RGL-105** in `VRS-130`. Una classe senza elenco ammette ogni formato riconosciuto.
- **Estensione del nome del file**, prima del trasferimento: deve corrispondere a uno dei formati ammessi dalla classe (o, se la classe non li limita, del registro dei formati del servizio, versione 1); altrimenti `400 SEC-006` con `dettagli.ammesse`. Il controllo sul contenuto (RGL-105) resta.
- **Dimensione per file della classe**: predefinita 100 MB, configurabile per classe fino a 5 GiB; oltre, rilievo **RGL-104** in `VRS-130` (il trasferimento si interrompe).
- **Limiti del canale**: 100 MB per file nel multipart (`VERSAMENTO_REST_MAX_FILE_MB`, oltre `413 VRS-010`), 5 GiB con le sessioni di caricamento, al più 500 file per versamento (`413 VRS-113`).

## 5. Unicità: chiave dell'unità documentaria e duplicati

La chiave dell'unità documentaria esiste quando `anno` e `numero` sono valorizzati; `registro` è confrontato senza spazi ai lati e in maiuscolo, `numero` distingue maiuscole e minuscole.

| Caso | Risposta |
|---|---|
| Stessa chiave UD di un PdV attivo dell'ente, contenuto diverso | **`409 VRS-124`**, con `dettagli.pdv_id_esistente`, `stato_esistente` e `link` |
| Stesso indice di un PdV attivo, con un'altra `Idempotency-Key` | `409 VRS-120` |
| Stessa `Idempotency-Key`, contenuto diverso | `409 VRS-121` |
| Stessa `Idempotency-Key`, stesso contenuto | `200`, stesso PdV |
| Stessa richiesta (stessa chiave o, senza chiave, stesso contenuto con la chiave derivata) di un PdV **rifiutato, in `ERRORE` o annullato** | **`409 VRS-127`**, con `dettagli.pdv_id_esistente`, `stato_esistente` e `link`: il PdV non è conservato |

Un PdV rifiutato dalla verifica asincrona, o portato in `ERRORE` durante la costruzione del pacchetto di archiviazione, **libera** la chiave UD: si può versare di nuovo con il contenuto corretto (e un'altra `Idempotency-Key`).

## 6. `Idempotency-Key`

Obbligatoria su ogni versamento: un identificativo del produttore per quel versamento (8–200 caratteri fra lettere, cifre e `. _ : -`; altrimenti `400 VRS-122`), riusato su ogni ritentativo.

- **Transizione** fino al **31/03/2027** compreso (ora italiana; impostazione `IDEMPOTENZA_TRANSIZIONE_FINO_AL`, personalizzabile per ente): un versamento senza chiave è accettato con una chiave ricavata dal contenuto, l'avviso **`VRS-126`** fra gli `avvisi` e le intestazioni `Deprecation` e `Sunset`. Due invii identici senza chiave restituiscono lo stesso PdV (`200`).
- **Dopo la transizione**: `428 VRS-125`. L'obbligo (`IDEMPOTENZA_OBBLIGATORIA`) lo può spegnere solo il conservatore.
- SFTP calcola la chiave da solo (vedi la [guida all'API](docs/integrazione/README.md#4-idempotenza)).

## 7. Regole, profili e impostazioni

Le verifiche sui metadati, sui formati e sulle dimensioni sono **regole** di un catalogo versionato (`libs/verumdoc_rules`), non controlli sparsi nel codice. Dalla R05 i PdV nuovi hanno la fotografia del catalogo `2`: stesse regole ed effetti del catalogo `1`, più accordo, personalizzazione del versamento e profilo di tipologia; i PdV già versati restano con la fotografia `1`.

| Regola | Verifica | Fase | Effetto predefinito |
|---|---|---|---|
| `RGL-101` | metadati minimi dell'Allegato 5 sul principale | indice | blocca (non modificabile) |
| `RGL-102` | metadati obbligatori della classe e del profilo | indice | blocca |
| `RGL-103` | metadati non previsti (conservati come versati) | indice | avvisa |
| `RGL-104` | dimensione massima per file della classe | file | blocca |
| `RGL-105` | formati ammessi dalla classe | file | blocca |
| `RGL-106` | tipi dei metadati del profilo | indice | blocca |
| `RGL-110` | il versamento porta una personalizzazione motivata (`override`, livello PDV) | indice | avvisa (solo avviso, mai una risposta HTTP) |
| `RGL-111` | `override` non ammesso (motivo corto, voce sconosciuta o non personalizzabile, tipo sbagliato) | indice | rifiuta il versamento (`422`) |
| `RGL-112` | `accordo` indicato non valido (inesistente, non in vigore, altra classe) | indice | rifiuta il versamento (`422`) |

- Un rilievo **bloccante** fa rifiutare il versamento con `422 VRS-130`, che li elenca tutti in `dettagli.rilievi[]` ({`codice`, `effetto`, `campo`, `documento`, `messaggio`}). Un rilievo che **avvisa** compare negli `avvisi` della risposta `202`, nel PdV e nel rapporto di versamento preliminare.
- Valori ed effetti sono **impostazioni** risolte con la catena dei livelli `CONSERVATORE` → `ENTE` → `CLASSE` → `ACCORDO` → `PDV` (vince il livello più specifico; ogni valore riporta la sua `origine`); quelle marcate come non modificabili (metadati minimi, obbligo di `Idempotency-Key`, effetto di RGL-101) valgono comunque. Dalla R05 si configurano con bozza → anteprima (impatto sui PdV recenti) → applica, dalla console o con `/api/v1/configurazione` (guida: `docs/architettura/regole.md`); le impostazioni della classe (`formati_ammessi`, `metadati_obbligatori`, `max_dimensione_mb`) restano anche nell'API delle classi documentali.
- Il PdV conserva una **fotografia** delle regole applicate (`regole_applicate`: versione del catalogo, profilo usato, valore e origine di ogni impostazione): un cambio di regole non cambia l'esito dei versamenti già fatti, e la verifica asincrona usa la stessa fotografia per formati ammessi e dimensione massima.
- Configurazione delle regole non valida lato servizio: `503 RGL-503` (temporaneo).

I codici `VRS-103` (metadati mancanti) e `VRS-112` (formato non ammesso) **non sono più emessi**: al loro posto `VRS-130` con i rilievi `RGL-101` e `RGL-105`.

### 7.1 Livello ACCORDO (accordo di versamento, R05)

Un accordo è un insieme di valori concordato con l'ente per una classe o per tutte le classi dell'ente, versionato e mai modificato: una nuova versione decorre da `valido_dal` (giorno italiano) e vale fino alla decorrenza della successiva; una versione «cessata» chiude l'accordo. Al versamento si applica l'accordo indicato in `accordo` oppure, se manca, quello dell'ente per la classe, poi quello per tutte le classi. La fotografia del PdV riporta codice e versione dell'accordo applicato.

### 7.2 Livello PDV (personalizzazione del singolo versamento, R05)

```json
"override": {"motivo": "Metadato gestionale concordato col responsabile",
             "valori": {"REGOLA.RGL-103.EFFETTO": "IGNORA"}}
```

Solo per le impostazioni che ammettono il livello PDV (oggi l'effetto di `RGL-103`, metadati non previsti) e con un motivo di almeno 10 caratteri; i valori imposti dalla norma non si personalizzano mai. Il versamento accettato porta l'avviso `RGL-110` e la fotografia `regole_applicate.override` (motivo e valori).

### 7.3 Profili di tipologia dell'Allegato 5 (R05)

Profili versionati (versione 1 dal 1/1/2022): documento informatico, documento amministrativo informatico, aggregazione documentale, fattura, registro di protocollo, contratto. Si attivano per scelta esplicita (impostazione `PROFILO_TIPOLOGIA` dell'ente, della classe o dell'accordo), mai in automatico per i clienti esistenti; il profilo applicato è la composizione della tipologia con il profilo della classe (sullo stesso nome vince la classe). Alcuni campi sono marcati «da riscontrare» sul testo ufficiale dell'Allegato 5 prima dell'attivazione per un ente.

**Versioni 2 (R11).** Riscontrate sul testo ufficiale dell'Allegato 5 (`docs/conformita/riscontro_allegato5.md`); valgono per una classe solo quando la classe le sceglie (`PROFILO_TIPOLOGIA_VERSIONE = 2`). Cosa cambia per chi versa: dati di registrazione obbligatori (`tipologia_flusso`, `tipo_registro`, `data_registrazione` con data e ora, `numero_registrazione`; `codice_registro` per protocollo e repertorio), `riservato` e `versione_documento` obbligatori (la prima versione è «1»), `firmato_digitalmente`, `sigillato_elettronicamente`, `marcatura_temporale` obbligatori per le modalità a e b (`conformita_copie_immagine` per la b), `soggetti` come elenco di oggetti `{ruolo, tipo_soggetto, …}` con il soggetto che effettua la registrazione e almeno un autore o un mittente, `tracciature_modifiche` (elenco di `{tipo_modifica, autore_modifica, data_ora_modifica, id_doc_versione_precedente}`) dalla versione 2 del documento o se il documento è annullato (`documento_annullato: true`), al più 5 `parole_chiave`. Esempio:

```json
"soggetti": [
  {"ruolo": "Soggetto Che Effettua La Registrazione", "tipo_soggetto": "PG", "denominazione": "Ente"},
  {"ruolo": "Autore", "tipo_soggetto": "PF", "cognome": "Cognome", "nome": "Nome"}
]
```

I nomi dei ruoli, i tipi di soggetto ammessi per ruolo e i campi richiesti per tipo sono nella console (Configurazione → Profili di metadati) e nel validatore preventivo.

### 7.4 Validatore preventivo (R05)

`POST /api/v1/configurazione/valida` con `{indice, file_impronte?}` (anche con chiave API) restituisce gli stessi rilievi del versamento senza scrivere nulla; senza rete, la CLI `tools/validatore/valida_pdv.py` con il profilo scaricato da `GET /api/v1/configurazione/profilo-validatore`. Il validatore non riconosce il formato dal contenuto, non fa antivirus né controlli di duplicati, idempotenza e quote (li elenca in `controlli_non_eseguiti`): solo il versamento accetta un PdV.

## 8. Dopo la presa in carico: la verifica

Dopo la risposta `202` il servizio di validazione ripete i controlli sul pacchetto, leggendo i file dallo storage. Un fallimento porta il PdV in `ERRORE` (evento `versamento.rifiutato`, codici della famiglia [VLD](docs/catalogo/errori.md#vld)):

| Passo | Verifica |
|---|---|
| 1 | Struttura dell'indice (classe, documenti, `id`/`tipo`/`nome_file`/`hash`) |
| 2 | Nessun documento ripetuto |
| 3 | Almeno un documento `PRINCIPALE` |
| 4 | Formato ammesso (dichiarato e rilevato), con l'elenco della classe fotografato nel PdV |
| 5 | Impronte ricalcolate sui file letti dallo storage, a blocchi |
| 6 | Metadati minimi dell'Allegato 5 sul principale (al primo livello di `parametri`), `modalita_formazione` valida, `oggetto` di almeno 5 caratteri |
| 7 | Classe documentale censita e attiva |
| 8 | Dimensione dei file entro il limite della classe fotografato nel PdV |
| 9 | Coerenza della chiave (anno numerico plausibile, numero non vuoto) |

## 9. Pacchetto di archiviazione

- **Tipologia SInCRO dell'indice del PdA** (DI, DAI o AGG): è un dato della classe documentale (`tipologia_xml`), non si ricava dal nome della classe.
- **IPdA** con struttura e namespace **provvisori** di VerumDoc (dal R14 il namespace proprio del conservatore `https://schemi.verumdoc.it/ipda/provvisorio/1`, dichiarato tale in un commento e senza `schemaLocation`, VD-C-096; fino a R13b `urn:uni:uninfo:sincro:2020`, un URI inventato a nome di UNI), non verificati contro lo schema UNI 11386:2020 (non ancora acquistato, VD-C-071): l'IPdA non si dichiara conforme. I metadati dei profili v2 escono con i nomi dell'Allegato 5 (`MetadatiAllegato5`, R11); quelli non previsti come metadati aggiuntivi con il loro nome.
- **Scadenza della conservazione**: dalla classe documentale (`anni_conservazione` o `conservazione_permanente`), non da un valore fisso.
- **Catena delle impronte**: ogni PdA entra nella catena (albero di Merkle). Da R04 `CONSERVATO` e l'evento `versamento.conservato` (`fase: CONSERVATO`) arrivano solo quando il PdA è nel registro delle impronte (due livelli, RFC 6962) ed è coperto da un checkpoint firmato e controfirmato dal testimone.
- **Rapporto di versamento**: preliminare nello stato del PdV (`GET /api/v1/versamento/{pdv_id}`, campo `rdv_preliminare`, non firmato); definitivo con `GET /api/v1/versamento/{pdv_id}/rdv` quando il PdV è `CONSERVATO`.

## 10. Esempi

### curl

```sh
SHA=$(sha256sum fattura.pdf | cut -d' ' -f1)
cat > indice.json <<EOF
{"classe_documentale": "fattura",
 "chiave": {"registro": "FATTURE", "anno": "2026", "numero": "FT-2026-001"},
 "documenti": [{"id": "doc-001", "tipo": "PRINCIPALE", "nome_file": "fattura.pdf",
   "formato": "application/pdf", "hash": {"impronta": "$SHA", "algoritmo": "SHA-256"},
   "parametri": {"identificativo_documento": "FT-2026-001", "modalita_formazione": "a",
     "tipologia_documentale": "fattura", "data_documento": "2026-03-18",
     "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
     "oggetto": "Fornitura servizi IT - Marzo 2026"}}]}
EOF

curl -sS https://api.verumdoc.it/api/v1/versamento/ud \
  -H "X-Api-Key: $VERUMDOC_API_KEY" \
  -H "Idempotency-Key: erp-FT-2026-001-v1" \
  -F "indice=<indice.json" \
  -F "files=@fattura.pdf;type=application/pdf"
```

Ripetere lo stesso comando (stessa `Idempotency-Key`) restituisce `200` e lo stesso `pdv_id`.

### PowerShell 7

```powershell
$sha = (Get-FileHash "fattura.pdf" -Algorithm SHA256).Hash.ToLower()
$indice = @{
  classe_documentale = "fattura"
  chiave = @{ registro = "FATTURE"; anno = "2026"; numero = "FT-2026-001" }
  documenti = @(@{
    id = "doc-001"; tipo = "PRINCIPALE"; nome_file = "fattura.pdf"; formato = "application/pdf"
    hash = @{ impronta = $sha; algoritmo = "SHA-256" }
    parametri = @{
      identificativo_documento = "FT-2026-001"; modalita_formazione = "a"
      tipologia_documentale = "fattura"; data_documento = "2026-03-18"
      soggetto_produttore_id = "12345678901"; soggetto_produttore_nome = "Acme S.r.l."
      oggetto = "Fornitura servizi IT - Marzo 2026"
    }
  })
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Uri "https://api.verumdoc.it/api/v1/versamento/ud" -Method Post `
  -Headers @{ "X-Api-Key" = $env:VERUMDOC_API_KEY; "Idempotency-Key" = "erp-FT-2026-001-v1" } `
  -Form @{ indice = $indice; files = Get-Item "fattura.pdf" }
```

### SDK Python

```python
from verumdoc_sdk import VerumDoc, ChiaveApi

with VerumDoc(credenziali=ChiaveApi("vd_<id>_<segreto>")) as vd:
    esito = vd.versa(indice, file={"fattura.pdf": "fattura.pdf"})   # impronte e chiave dall'SDK
    stato = vd.attendi_versamento(esito.pdv_id)
```

In sviluppo locale (`ENV=development` o `test` e `VERUMDOC_DEV_AUTH=1`) il gateway espone anche `POST /api/v1/auth/dev-token` per ottenere un token di prova; negli altri ambienti la rotta non esiste.
