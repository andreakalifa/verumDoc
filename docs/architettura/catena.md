# Catena delle impronte di VerumDoc — registro a due livelli

> Rilascio R04 («Scala e catena», 23/10/2026); aggiornato in R05 («Configurazione, cruscotti e ciclo di vita»): foglie di formato 2 del ciclo di vita e dell'audit (contratto Q3, §4.1), ricostruzione dal WORM (VD-C-111, §5), controllo dell'esportazione (VD-C-118, §5), export dell'audit nel WORM (VD-C-112, §11). Descrizione tecnica per il Manuale di conservazione e per i clienti. Riferimenti: piano `PIANO_R03_R05.md` §5; contratti K1, K2, K3, K5 di R04; RFC 6962 §2.1 e RFC 9162 §2.1.3–2.1.4 (alberi di Merkle e prove); specifiche C2SP «signed-note» e «tlog-checkpoint». Decisione del committente: catena **interna**, nessun registro pubblico (VD-P-29).
>
> Codice: `libs/catena/` (algoritmi, formati, puro), `services/aip-builder/app/services/aip_engine.py` (albero del PdA), `services/hashchain/app/registro/` (registro, checkpoint, testimone, RdV; R05: `interne.py`, `ricostruzione.py`, `audit_worm.py`), `services/hashchain/app/routers/interno.py` (API interna, R05), migrazioni `20261023_025` e `20261023_039`.

## 1. In breve

Ogni pacchetto di archiviazione (PdA) ha un **albero di Merkle** delle impronte dei suoi file, dei loro metadati e del suo indice (IPdA): la **radice del PdA** riassume tutto il pacchetto in 32 byte. Ogni radice entra, una dopo l'altra, in un **registro globale append-only**, anch'esso un albero di Merkle. A intervalli regolari il registro pubblica un **checkpoint**: dimensione e radice dell'albero, firmati. Con il rapporto di versamento (RdV) il cliente riceve le **prove**: il suo documento è nel PdA, il PdA è nel registro, il registro a quella dimensione ha quella radice firmata. Conservando i checkpoint, il cliente può chiedere in ogni momento una **prova di consistenza**: se qualcuno riscrivesse il registro, la prova non tornerebbe. Un **testimone** indipendente controfirma i checkpoint solo se sono consistenti con quelli che ha già visto.

Algoritmi e formati sono quelli dei registri di trasparenza (Certificate Transparency, sum.golang.org): chiunque può verificare con strumenti indipendenti, anche il pacchetto Go `golang.org/x/mod/sumdb` (la compatibilità è provata in `libs/catena/tests/test_oracolo_go.py`).

## 2. Albero di Merkle (RFC 6962)

| Elemento | Definizione |
|---|---|
| hash di foglia | `SHA-256(0x00 ‖ dati della foglia)` |
| hash di nodo | `SHA-256(0x01 ‖ sinistro ‖ destro)` |
| albero vuoto | `SHA-256("")` = `e3b0c442…b855` |
| radice di n > 1 foglie | `nodo(MTH(D[0:k]), MTH(D[k:n]))`, k = la più grande potenza di 2 minore di n |
| prova d'inclusione | percorso di audit (RFC 6962 §2.1.1), verifica RFC 9162 §2.1.3.2 |
| prova di consistenza | RFC 6962 §2.1.2, verifica RFC 9162 §2.1.4.2 |

L'ultima foglia non si duplica mai (diversamente dai blocchi di formato 1) e foglie e nodi hanno prefissi diversi: non si può far passare un nodo per una foglia. Gli hash nei formati JSON sono in esadecimale minuscolo.

## 3. Livello 1 — albero del PdA

Costruito dall'aip-builder quando costruisce il PdA, dopo aver ricalcolato le impronte sui byte letti dallo storage (REGOLE §10.3).

**Foglie**, in quest'ordine totale (REGOLE §12.8): una per ogni file del PdA, ordinate per posizione del documento nell'IPdA, poi identificativo del documento, poi nome del file; per ultima una foglia per l'IPdA. Dati della foglia = **JSON canonico** (chiavi ordinate, UTF-8 senza escape dei caratteri non ASCII, nessuno spazio, nessun numero decimale):

```json
{"doc":"doc-1","file":"fattura.pdf","metadati":"<sha256 hex>","pda":"urn:IT:…","sha256":"<sha256 hex del file>","v":1}
{"ipda":"<sha256 hex dell'IPdA>","pda":"urn:IT:…","v":1}
```

- `doc`: identificativo del documento nell'indice del versamento (e nell'IPdA);
- `metadati`: SHA-256 del JSON canonico dei metadati del documento usati per l'IPdA (`{}` se assenti); un numero decimale nei metadati si scrive come testo con la sua forma più breve (`1.5` → `"1.5"`) prima del calcolo;
- radice del PdA = MTH delle foglie.

Dove sta: tabella `catena_pda` (append-only) e oggetto WORM `<ente>/<pda>/catena_pda.json` (`{"foglie":[…],"pda":…,"radice":…,"v":1}`), con la retention del PdA. Nella stessa transazione il PdA entra nella coda del registro (`catena_coda`). La radice è anche nel manifest del PdA (`radice_pda`).

## 4. Livello 2 — registro globale

**Foglia per un PdA**:

```json
{"conservato_il":"2026-10-23T08:15:00.000000Z","ente":"<tenant_id>","pda":"urn:IT:…","radice_pda":"<hex>","v":1}
```

`conservato_il` è l'istante (UTC) in cui la foglia entra nel registro. L'indice della foglia è la sua posizione (0, 1, 2…).

**Sequenziatore** (hashchain, `app/registro/sequenziatore.py`): uno solo attivo grazie a un **lease** con scadenza (`catena_stato`, REGOLE §12.9); ogni lotto è una transazione che blocca la riga di stato (nessun indice assegnato due volte, neppure con due processi), legge i PdA in coda nell'ordine d'ingresso, scrive foglie e nodi nuovi e porta i PdA a `IN_CATENA`. Un errore annulla tutto il lotto.

**Nodi salvati** (`catena_nodi`): radici dei sottoalberi perfetti allineati (livello 0 = hash di foglia), circa 2 righe per foglia, mai modificate. Radice, prova d'inclusione e prova di consistenza si calcolano leggendo **O(log n)** nodi con una sola query (`libs/catena/albero.py`), mai l'albero intero.

### 4.1 Foglie di formato 2: ciclo di vita e audit (R05, contratto Q3)

Il registro contiene anche fatti SUCCESSIVI alla conservazione, con una foglia di **formato 2** (`v: 2`, campo `tipo` obbligatorio). Non provano mai la conservazione di un contenuto: un verificatore che le trovasse al posto della foglia di un PdA deve rifiutarle (`libs/catena/foglie.py`, `analizza_foglia_registro`; un formato o un tipo sconosciuti sono sempre un errore).

```json
{"ente":"<tenant_id>","id":"<uuid della foglia>","impronta_documento_pratica":"<sha256 hex>","pda":"urn:…","registrato_il":"…Z","riferimento":{"pratica":"<id>",…},"tipo":"scarto","v":2}
{"id":"<uuid>","impronta_precedente":"<hex>|null","impronta_segmento":"<hex>","periodo_a":"…Z","periodo_da":"…Z","radice_righe":"<hex>","registrato_il":"…Z","righe":12,"segmento":"registro/audit/…jsonl","tipo":"audit","v":2}
```

- `scarto` è un **tombstone**: prova che lo scarto è avvenuto (pratica, impronta del verbale) senza rivelare il contenuto; `annullamento` e `rettifica` registrano l'annullamento logico e la rettifica dei metadati (l'originale resta immutato). `riferimento` è un oggetto piatto (al più 16 chiavi, valori testo, interi, booleani o null) con `pratica` obbligatoria.
- `audit`: impronta di un segmento di `audit_global` esportato nel WORM (§11).
- **Chi le aggiunge**: per il ciclo di vita il servizio `ciclo-vita` (solo il suo lavoratore, che ha il token), con l'API interna (§9) e un token di servizio con scope `registro:aggiungi`; per l'audit la hashchain stessa. Revisione 2 di R05 (S2): la hashchain accoda una foglia del ciclo di vita **solo se il fatto risulta dal database** (`interne.riscontro_ciclo_vita`): scarto = pratica SCARTO dello stesso ente con l'esecuzione avviata, oggetto `ELIMINATO` e PdA in `ELIMINAZIONE` e — revisione 3 di R05 (T5) — la riga `ELIMINATO` di `esiti_esecuzione`, che solo il login del lavoratore di ciclo-vita può scrivere (docs/architettura/sicurezza.md); annullamento = pratica ANNULLAMENTO `ESEGUITA` del PdV del PdA, PdV `ANNULLATO`; rettifica = pratica RETTIFICA `IN_VERSAMENTO` del PdV del PdA. Altrimenti `409 CAT-409-RISCONTRO`, nulla scritto: un token rubato non basta a far firmare uno scarto mai avvenuto.
- **Come entrano**: il JSON canonico e l'hash si fissano alla richiesta (`catena_foglie_interne`); la richiesta entra in `catena_coda` nello **stesso ordine totale** dei PdA. Il sequenziatore la tratta come le altre foglie (indice, nodi, segmento WORM, checkpoint, sigillo, controfirma); in `catena_registro` la foglia ha `aip_id` vuoto (vincolo `ck_catena_registro_pda`: una foglia PDA o RIVERSAMENTO ha sempre il suo PdA, una foglia interna mai), l'indice si scrive una sola volta nella richiesta.
- **Idempotenza**: per `(tipo, pda, riferimento.pratica)`; la stessa chiave con dati diversi è un conflitto (409 `IDEM-409`, nulla scritto). L'`id` della richiesta è dentro la foglia: la ricostruzione dal WORM la ricrea con lo stesso id.
- **Stato del documento**: la prova d'inclusione di un documento riporta le foglie del ciclo di vita del suo PdA coperte dallo stesso checkpoint (`ciclo_vita`); il verificatore pubblico mostra «scartato il … con pratica …» e, con uno scarto o un annullamento verificati, l'esito è «DOCUMENTO SCARTATO/ANNULLATO» (CLI: codice 2), **mai** «verificata». Una prova senza `ciclo_vita` (RdV, prove precedenti) lo dichiara: lo stato vale fino al checkpoint della prova; per lo stato attuale serve una prova aggiornata. Revisione R05 (R4): scarto e annullamento sono **sempre tutti** nella prova; le rettifiche al più `CATENA_CICLO_VITA_MAX_FOGLIE` (le più recenti); la risposta dichiara `ciclo_vita_totale` e `ciclo_vita_completo`. Con un elenco incompleto (o un totale maggiore delle foglie presenti) il verificatore non conclude mai «conservato»: lo stato è «non determinabile» (controllo NON ESEGUITO, ricevuta non verificata) salvo uno scarto o un annullamento presenti, che hanno sempre la precedenza. Revisione 2 di R05 (S8): la prova porta anche `attestazione_ciclo_vita`, una nota firmata con la **stessa chiave del registro** che impegna, per quel PdA e quel checkpoint, il numero delle foglie del ciclo di vita e l'impronta del loro elenco (`libs/catena/attestazione.py`: righe `<indice> <hash di foglia>`; il corpo non è mai leggibile come un checkpoint). Chi presenta la prova a un terzo non può più togliere una foglia di scarto (e i campi del conteggio): il numero firmato non torna. Senza attestazione valida l'assenza di scarti **non è dimostrabile** e il verificatore lo dice (stato NON ESEGUITO, mai «conservato»). Limite: l'attestazione è firmata dal solo registro (non controfirmata dal testimone): protegge da chi presenta la prova, non da chi controlla la chiave del registro. Revisione 3 di R05 (T2, T3): attestazione **v2**, rilasciata **solo per l'ultimo checkpoint** (una prova chiesta con `dimensione` di un checkpoint più vecchio non la porta: `attestazione_assente` ne dice il motivo e il verificatore non conclude «conservato»), con l'**istante del rilascio** (`rilasciata`, orologio del database) e lo **stato del PdA** (`stato`: `CONSERVATO`, `SCARTO` = scarto avviato o eseguito, `SCARTO_PARZIALE` = `ELIMINAZIONE_INCOMPLETA`) firmati; la risposta riporta anche `stato_pda` e `ultimo_checkpoint`. Il verificatore (CLI e pagina web) scrive sempre «stato al checkpoint N del <data>»; con l'ultimo checkpoint pubblicato (`--checkpoint-corrente`, passo 5 della pagina) più recente di quello della prova lo stato attuale **non è dimostrato** (codice 1: chiedere una prova aggiornata); `SCARTO_PARZIALE` dà «DOCUMENTO SCARTATO IN PARTE» e `SCARTO` «DOCUMENTO SCARTATO» (codice 2), anche senza foglia di scarto. Una prova conservata dal cliente resta quindi una prova dello stato a quella data, mai dello stato attuale. Revisione 4 di R05 (U3): il checkpoint corrente indicato si **confronta** con quello della prova (controllo «Checkpoint corrente rispetto alla prova»): più vecchio → FALLITO («checkpoint fornito più vecchio della prova»); stessa dimensione e radice diversa → FALLITO «REGISTRO BIFORCATO»; in entrambi i casi mai «VERIFICATA». Se è più recente, la prova di consistenza dal checkpoint della prova al corrente (`--consistenza-corrente`, campo facoltativo del passo 5 della pagina) dimostra che il registro non è stato riscritto (FALLITO «REGISTRO BIFORCATO o riscritto» se non regge); lo stato attuale resta comunque non dimostrato. Test: `libs/catena/tests/test_attestazione.py`, `tools/verificatore/tests/`, `services/hashchain/tests/test_r05_foglie_interne.py`.

**Volume** (prova `services/hashchain/tests/test_r04_registro_volume.py`, 100.000 foglie su PostgreSQL 16 e S3 di prova, 23/10/2026): sequenziatore ~3.400 foglie/s con lotti da 1.000 (100.000 foglie in 29 s); checkpoint a 100.000 foglie con esportazione nel WORM 1,0 s; radice dai nodi 7 ms; prova d'inclusione 1,0 ms in media (al più 21 nodi letti); prova di consistenza 6 ms. L'obiettivo di 1 milione di documenti al giorno corrisponde a 1.000–10.000 PdA al giorno secondo la politica dei PdA: ampiamente entro questi numeri.

## 5. Checkpoint

Formato **C2SP tlog-checkpoint** dentro una **C2SP signed note**:

```
verumdoc.it/registro/v1
1234
<radice in base64 standard>

— verumdoc-registro <base64(key hash 4 byte ‖ firma Ed25519 64 byte)>
— verumdoc-testimone <base64(…)>
```

- origine: `verumdoc.it/registro/v1` in produzione, `verumdoc.it/registro/v1/<ambiente>` altrove (un checkpoint di prova non si scambia per uno di produzione);
- firma **Ed25519** sui byte del corpo (le prime tre righe, a capo finale compreso); key hash = primi 4 byte di `SHA-256(nome ‖ "\n" ‖ 0x01 ‖ chiave pubblica)`;
- chiave pubblica distribuita come «verifier key» `verumdoc-registro+<key hash>+<base64(0x01 ‖ chiave)>`, pubblicata nel Manuale e riportata in ogni RdV (`ChiaveRegistro`); chi verifica deve usare la chiave ottenuta dal Manuale, non quella scritta nel documento che sta verificando;
- cadenza: un checkpoint ogni `CATENA_CHECKPOINT_SECONDI` (60) se il registro è cresciuto;
- prima della pubblicazione: foglie non ancora esportate scritte nel WORM (`registro/foglie/<da>-<a>.jsonl`, una foglia per riga), nota scritta nel WORM (`registro/checkpoint/<dimensione>.txt`), riga in `catena_checkpoint`; solo allora evento `catena.checkpoint` (`{"dimensione","radice","emesso_il","testo"}`) via outbox e visibilità dalle API. Gli oggetti WORM del registro sono in Object Lock COMPLIANCE con legal hold (conservazione permanente, orizzonte `RETENTION_PERMANENTE_ANNI` del motore, predefinito 30 anni: R09, D04; il vecchio `RETENTION_PERMANENTE_ORIZZONTE_ANNI` vale solo senza la riga del conservatore).

**Sigillo qualificato e marca temporale.** Dopo l'emissione il corpo del checkpoint si invia al servizio `signature` (CAdES con marca RFC 3161). Con un fornitore qualificato: stato `FIRMATO`, busta nel WORM (`registro/checkpoint/<dimensione>.p7m`), data della marca registrata. **Oggi nessun fornitore qualificato è configurato (VD-C-068): lo stato registrato e riportato nel RdV è `NON_FIRMATO` con il motivo.** Il sigillo sta accanto al checkpoint (tabella e WORM), non dentro la nota.

**Testimone** (protocollo K3, ispirato a C2SP tlog-witness). Il registro invia al testimone (`TESTIMONE_URL`) l'ultimo checkpoint con la prova di consistenza dall'ultima dimensione che il testimone ha controfirmato; il testimone verifica firma, dimensione precedente e prova e risponde con la propria firma. La controfirma si **verifica** con la chiave pubblica del testimone (`TESTIMONE_VERIFICATORE`) prima di registrarla; la nota controfirmata va nel WORM (`registro/checkpoint/<dimensione>.controfirmato.txt`). Stati: `NON_CONFIGURATO` (testimone non impostato: dichiarato; ammesso solo fuori da produzione), `IN_ATTESA`, `CONTROFIRMATO`, `RIFIUTATO` (403 firma, 422 prova, 423 testimone bloccato da una biforcazione, controfirma non verificabile: rifiuti **non ritentabili**), `SUPERATO` (un checkpoint successivo è stato inviato al posto di uno ancora in attesa; un checkpoint `RIFIUTATO` non diventa mai `SUPERATO`). L'ultimo checkpoint in attesa di controfirma da oltre `CATENA_CONTROFIRMA_MAX_SECONDI` (600) è un **allarme**: log `checkpoint_senza_controfirma` e metrica `verumdoc_catena_checkpoint_senza_controfirma_secondi`. Un rifiuto è un **allarme persistente** (correzioni R04, rev4 rilievo 1): resta nel database (`rifiuto_gestito_il` vuoto), a ogni giro log `checkpoint_rifiutato_aperto` e metrica `verumdoc_catena_checkpoint_rifiutati_aperti`, e **sospende il completamento della conservazione** finché un operatore non registra la gestione; il checkpoint rifiutato non si usa mai. **Allarmi raggruppati** (secondo giro R04, N3, migrazione `20261023_033`): i rifiuti consecutivi con la stessa causa (`HTTP_403`, `HTTP_422`, `HTTP_423`, `CONTROFIRMA_NON_VERIFICABILE`, `DIMENSIONE_TESTIMONE_MAGGIORE`) formano **un** allarme aperto con un contatore (`catena_allarmi_testimone`, al più uno aperto per causa; log `checkpoint_rifiutato_dal_testimone` al primo, `rifiuto_testimone_raggruppato` ai successivi; metrica `verumdoc_catena_allarmi_testimone_aperti`): un'ora di 403 è un allarme con 60 rifiuti, non 60 allarmi. L'operatore gestisce l'incidente **in blocco**, con un motivo, in **una** operazione registrata (una voce `catena.rifiuti_testimone_gestiti` in `audit_global` con allarmi, numero di checkpoint e intervallo di dimensioni): `python -m app.registro.operatore rifiuti` (allarmi aperti), `gestisci-rifiuti --allarme ID` oppure `gestisci-rifiuti --tutti --conferma-causa CAUSA [--conferma-causa …]`, con `--operatore … --motivo …`; `gestisci-rifiuto --dimensione N` chiude l'allarme di quel checkpoint. **In blocco solo le cause confermate una per una** (terzo giro, T3): un `--tutti` senza conferme non chiude nulla e stampa le cause da confermare; le cause gravi `HTTP_423` (biforcazione) e `DIMENSIONE_TESTIMONE_MAGGIORE` non si chiudono mai in blocco né dal checkpoint, solo con `--allarme ID`. La voce di audit elenca anche gli allarmi rimasti aperti. Il registro continua a inviare al testimone i checkpoint successivi mentre l'allarme è aperto. Ripristino del database da backup senza biforcazioni: `docs/runbooks/ripristino_registro_da_backup.md`.

**Coerenza con ciò che è già pubblicato** (terzo giro, T4, migrazione `20261023_034`, `app/registro/coerenza.py`).

*Il controllo.* All'avvio del ciclo, e quando si toglie `CATENA_REGISTRO_SOSPESO`, il registro confronta la propria dimensione `D` con tre valori:
- l'ultimo checkpoint emesso nel WORM, `N`;
- la fine dei segmenti di foglie esportati, `X`;
- la dimensione controfirmata dal testimone, `T`.

Confronta anche i contenuti già pubblicati coperti dal database: i segmenti riga per riga e la radice di `N`.

*Se `D < max(N, X, T)` o un contenuto diverge* (per esempio dopo un ripristino da backup):
- si apre un **blocco persistente** (`catena_blocchi_registro`: `REGISTRO_INDIETRO`, `REGISTRO_DIVERGENTE`);
- il registro **non** sequenzia, non emette checkpoint, non scrive nel WORM e non contatta il testimone. Altrimenti risequenzierebbe foglie già pubblicate: una biforcazione firmata visibile ai clienti;
- il controllo si ripete ogni minuto e chiude il blocco quando passa.

*Se il controllo non si può fare* (quarto giro, F1, migrazione `20261023_035`), per esempio per un ruolo IAM senza `s3:ListBucket` o un WORM irraggiungibile:
- si apre il blocco esplicito `COERENZA_NON_VERIFICABILE` (allarme `RegistroBloccato`, log `registro_coerenza_non_verificabile`): subito per un errore di permesso o di credenziali, dopo 3 tentativi consecutivi per gli altri errori;
- si chiude da solo quando il controllo passa.

Il ruolo della hashchain elenca il bucket dei PdA solo sotto `registro/` (`s3:ListBucket` con la condizione `s3:prefix`). L'esistenza di una chiave si verifica con un elenco del suo prefisso, mai con una GET: senza ListBucket incondizionato S3 risponde 403, non 404, anche per una chiave assente.

Una scrittura WORM con lo stesso nome e contenuto diverso apre il blocco `WORM_CONTENUTO_DIVERSO`: niente ritentativi a ogni giro. L'operatore lo chiude con `sblocca-registro` dopo l'indagine.

*`E` stesso* (R05, VD-C-118): i segmenti nel WORM devono andare, contigui, dall'ultimo checkpoint del database prima di `E` (`L`) fino a `E`, con le stesse foglie del database. Un `E` dichiarato oltre i segmenti presenti (database di un altro ambiente o bucket, esportazione registrata e mai scritta) è `REGISTRO_DIVERGENTE`: altrimenti il checkpoint successivo pubblicherebbe foglie mai esportate e la ricostruzione dal WORM non sarebbe più possibile. Un segmento riscritto dopo un errore dell'aggiornamento di `E` riparte dallo stesso punto: la sovrapposizione è ammessa, le righe in comune devono essere identiche.

*Scelta: fermo con allarme, non ricostruzione automatica dal WORM.* La ricostruzione è un comando versionato dell'operatore (R05, VD-C-111, `app/registro/ricostruzione.py`):

```bash
python -m app.registro.operatore ricostruisci-da-worm --fino-a <max(N, X)> --prova
python -m app.registro.operatore ricostruisci-da-worm --fino-a <max(N, X)> --operatore "…" --motivo "…"
```

- **base**: l'ultimo checkpoint del database non oltre `D` identico a quello nel WORM, firmato e con la radice dei nodi del database; da lì (o da 0) si leggono i segmenti, a lotti e con letture parallele limitate (memoria costante: frontiera O(log n), un lotto alla volta);
- **solo foglie coperte** (revisione R05, R5): si ricostruiscono soltanto le foglie coperte da un checkpoint **firmato dal registro e controfirmato dal testimone** (`ultimo_checkpoint_controfirmato`). Le foglie oltre (un segmento che nessuna firma copre può averlo depositato chiunque scriva nel bucket) sono un **problema**: nessuna scrittura; con `--scarta-coda-non-coperta` la ricostruzione si ferma a quel checkpoint e scarta la coda senza scriverla (`coda_scartata` nel rapporto). Mai una foglia non verificata nel registro, quindi mai firmata dal checkpoint successivo;
- **controlli**, tutti prima di scrivere: segmenti contigui, foglie già nel database con lo stesso hash e radice a `D` uguale, ogni checkpoint del WORM fino a `--fino-a` con firma del registro (chiave attuale o `--chiave-precedente`), origine e radice ricalcolata, controfirma del testimone verificata con `TESTIMONE_VERIFICATORE`, collegamenti PdA→foglia (URN, ente, radice del PdA o impronta del manifest; PdA doppi), richieste interne e esportazioni dell'audit;
- **qualcosa non torna** → nessuna scrittura e codice 1 (mai una ricostruzione «quasi giusta»: trasformerebbe un registro fermo in una biforcazione firmata); `--prova` non scrive nulla, neppure il lease;
- **scrittura**: seconda lettura identica, lotti (`--lotto`, 10.000) in una transazione ciascuno con il lease del registro (il ciclo non può scrivere insieme): foglie, nodi, richieste interne (stesso id), esportazioni dell'audit, checkpoint (sigillo FIRMATO se la busta è nel WORM, altrimenti DA_FIRMARE; testimone CONTROFIRMATO se la controfirma è verificata, SUPERATO un checkpoint senza controfirma coperto da uno successivo controfirmato), stato del registro, PdA IN_CATENA e coda. Un'interruzione lascia un prefisso verificato: si riesegue. Voci `catena.ricostruzione_da_worm_avviata` e `…_conclusa` in `audit_global`;
- **volume** (prova `test_r05_ricostruzione_volume.py`, PostgreSQL 16 e S3 di prova, 23/10/2026): 100.000 foglie con un checkpoint ogni 10.000 in 6 s di prova e 35 s di scrittura; 300.000 in 21 s e 112 s (~14.500 foglie/s in prova, ~2.700/s in scrittura); memoria del processo +117 MiB a 100.000 e +146 MiB a 300.000 foglie (finestra di letture). 10 milioni di foglie: circa 12 minuti di prova e un'ora di scrittura su queste macchine.

*Strumenti:*
- comandi `controlla-worm` (misure e codice d'uscita) e `blocchi`;
- metriche `verumdoc_catena_registro_bloccato` e `verumdoc_catena_registro_soglia`, regola `RegistroBloccato`.

*Con `CATENA_REGISTRO_SOSPESO`* (T5):
- metrica `verumdoc_catena_registro_sospeso`, log `registro_sospeso` a ogni giro, regola `RegistroSospeso` dopo 30 minuti;
- gli allarmi (controfirme, rifiuti, blocchi) si controllano a ogni giro anche con il registro sospeso o bloccato;
- la variabile non può stare nei values (`make verifiche`). Se nulla è da inviare, ogni `TESTIMONE_SONDA_SECONDI` (60) una lettura di prova del testimone: metrica `verumdoc_catena_testimone_raggiungibile` e log `testimone_non_risponde`. Con `ENV=production` `TESTIMONE_URL` è **obbligatorio**: senza, la hashchain non parte (rilievo 8).

## 6. Quando un PdA è «conservato» (VD-C-097)

1. aip-builder: PdA sullo storage (`SU_STORAGE`), albero del PdA e coda. **Nessun evento «conservato».**
2. Sequenziatore: foglia nel registro, PdA `IN_CATENA`.
3. Checkpoint che copre la foglia, con sigillo in stato definitivo (`FIRMATO` o `NON_FIRMATO`) e **controfirmato** dal testimone; a parità di copertura si preferisce sempre il controfirmato. Un checkpoint senza controfirma si usa solo se il testimone **non è configurato** in un ambiente che lo ammette (mai in produzione). Se il testimone tace (`IN_ATTESA`) il PdA **attende**, a qualunque età, e oltre `CATENA_CONTROFIRMA_MAX_SECONDI` scatta l'allarme; un checkpoint `RIFIUTATO` non porta mai a `CONSERVATO` e, finché il rifiuto non è gestito da un operatore, nessun PdA si completa (correzioni R04, rev4 rilievo 1). Se un tentativo precedente aveva già scritto il RdV nel WORM, il riferimento e l'evento citano il checkpoint **del documento conservato** (rilievo 12).
4. RdV definitivo v2 costruito, **verificato offline** con la chiave del registro (un RdV che non si verifica non si scrive), scritto nel WORM con la retention del PdA; in una transazione PdA e PdV `CONSERVATO` ed evento `aip.conserved` in outbox con i dati di prima più `pda`, `foglia_indice`, `checkpoint_dimensione`, `radice_pda`, `fase: "CONSERVATO"` (per i clienti: `versamento.conservato`).

Tempo atteso: pochi secondi per il sequenziatore, al più un intervallo di checkpoint (60 s) più il sigillo e la controfirma. Con il testimone irraggiungibile la conservazione si ferma (allarme), non procede senza la sua conferma.

## 7. Blocchi esistenti: riversamento nel registro

**Decisione (da confermare dal committente):** i blocchi della vecchia catena (formato 1 e 2) **restano come sono**, immutabili e verificabili con i loro algoritmi (REGOLE §10.1); i loro PdA **entrano nel registro nuovo** con una **foglia di riversamento** che cita il blocco d'origine:

```json
{"blocco":{"formato":2,"hash":"<hash del blocco>","numero":12},"conservato_il":"…","ente":"…","impronta_pda":"<SHA-256 del manifest>","pda":"urn:…","tipo":"riversamento","v":1}
```

Lo stesso vale, senza `blocco`, per i PdA già sullo storage ma non ancora nella vecchia catena al momento dell'aggiornamento. La migrazione `20261023_025` li mette in coda nell'ordine (blocco, posizione nel blocco; poi data di versamento): le foglie le scrive il sequenziatore, i dati conservati non si toccano (REGOLE §10.6).

**Perché** questa scelta e non «registro da zero con la storia dichiarata»: la vecchia catena è protetta solo da un trigger del database e i suoi blocchi non sono firmati (VD-C-068). Con il riversamento ogni PdA già conservato ottiene una foglia propria sotto checkpoint firmati e controfirmati: da quel momento riscrivere un PdA o un blocco vecchio diventa rilevabile come per i PdA nuovi, e ogni PdA ha una prova d'inclusione nel registro. I PdA in riversamento non hanno l'albero di livello 1 (il loro IPdA e i metadati originali non sono ricostruibili in modo certo): il RdV v2 lo dichiara (`PdA riversamento="true"`) e la catena di prove va dal manifest del PdA al registro; i RdV di formato 1 già emessi restano validi e invariati.

## 8. Rapporto di versamento definitivo v2 (K5, VD-C-096)

XML con namespace proprio `https://schemi.verumdoc.it/rdv/definitivo/2` (AgID non pubblica uno schema del RdV: nessun namespace AgID). Contiene: esito, PdV, PdA (URN, impronta del manifest, radice del PdA), documenti con impronte SHA-256/512 e prova d'inclusione nel PdA (con la foglia), IPdA con la sua prova, avvisi del versamento, foglia del registro con la prova d'inclusione, checkpoint (nota firmata ed eventuale controfirma, chiave del registro), stato del sigillo qualificato e della marca del checkpoint, stato del testimone. Si scarica da `GET /api/v1/versamento/{pdv_id}/rdv` (intestazioni `X-Formato-RdV`, `X-Checkpoint-Dimensione`); i RdV di formato 1 si scaricano come prima. Il RdV non porta una firma propria: vale per le prove che lo legano al checkpoint.

## 9. API (hashchain, prefisso `/api/v1/hashchain`, token verificato)

| Metodo e percorso | Risposta |
|---|---|
| `GET /checkpoint` | ultimo checkpoint (nota, `text/plain`), con controfirma se presente |
| `GET /checkpoint/{dimensione}` | checkpoint emesso a quella dimensione (404 `CAT-404`) |
| `GET /prova/inclusione?pda=&documento=[&dimensione=]` | `{pda, documento, foglia_dati, prova_pda, radice_pda, foglia_registro_dati, prova_registro, checkpoint, ciclo_vita, ciclo_vita_totale, ciclo_vita_completo, attestazione_ciclo_vita, attestazione_assente, ultimo_checkpoint, stato_pda}` (revisione 2 di R05, S8: attestazione firmata dal registro, §4.1; revisione 3: solo per l'ultimo checkpoint, v2 con istante e stato del PdA); R05: `ciclo_vita` = foglie di scarto, annullamento e rettifica del PdA coperte dallo stesso checkpoint, ciascuna `{tipo, foglia_dati, prova_registro}` (scarto e annullamento sempre tutti, rettifiche al più `CATENA_CICLO_VITA_MAX_FOGLIE`, revisione R05 R4); PdA di un altro ente → 404; non ancora coperto → 409 `CAT-409` |
| `GET /prova/consistenza?da=&a=` | `{da, a, percorso}`; `a` non oltre l'ultimo checkpoint (422 `CAT-422`) |
| `GET /registro/foglie?da=&limite=` | hash delle foglie fino all'ultimo checkpoint; solo SUPER_ADMIN, RSC_CONSERVATORE o token di servizio (audience `verumdoc-registro`, scope `registro:leggi`) |
| `POST /interno/v1/registro/foglie` (R05, Q3; revisione 2: fuori da `/api/`, **non** instradato dall'ingress, 404 con `X-Forwarded-For`, non pubblicato nell'OpenAPI) | `{tipo: scarto\|annullamento\|rettifica, pda, riferimento: {pratica, …}, impronta_documento_pratica}` → 202 `{foglia_id}`; idempotente per (tipo, pda, pratica), 409 `IDEM-409` con dati diversi, 409 `CAT-409-RISCONTRO` se il fatto non risulta dal database (§4.1), 404 `CAT-404` PdA inesistente, 422 `VAL-422`; solo token di servizio (audience `verumdoc-registro`, scope `registro:aggiungi`), 403 `CAT-403` senza lo scope |
| `GET /interno/v1/registro/foglie/{id}` (R05, Q3) | stato `IN_CODA`, `NEL_REGISTRO`, `IN_CHECKPOINT`, `CONTROFIRMATA` con indice, primo checkpoint e primo checkpoint controfirmato che la coprono, foglia (JSON canonico); stesso token |
| `GET /status` | anche `registro`: dimensione, ultimo checkpoint con sigillo e testimone |

Formati delle prove: PdA `{"foglia_indice","dimensione","percorso"}`; registro `{"foglia_indice","dimensione_albero","percorso"}`; consistenza `{"da","a","percorso"}`.

## 10. Come verificare da soli

Serve solo: SHA-256, Ed25519 e la chiave pubblica del registro presa dal Manuale.

1. **Firma del checkpoint.** Separare la nota alla riga vuota; per la riga `— verumdoc-registro <b64>` decodificare il base64, controllare che i primi 4 byte siano il key hash della chiave del Manuale e verificare con Ed25519 i 64 byte restanti sul corpo (con l'a capo finale). Con Go: `note.Open(nota, note.VerifierList(note.NewVerifier(chiave)))`.
2. **Corpo.** Riga 1 = origine attesa (`verumdoc.it/registro/v1` in produzione: un checkpoint di un altro ambiente non vale), riga 2 = dimensione n, riga 3 = radice R in base64. Il verificatore pubblico (CLI `--origine`, pagina «Origine attesa») confronta l'origine e rifiuta la stessa chiave nei due ruoli di registro e testimone (correzioni R04, rev4 rilievo 9).
3. **Registro.** `h = SHA-256(0x00 ‖ foglia_registro_dati)`; con `prova_registro` e l'algoritmo RFC 9162 §2.1.3.2 ricalcolare la radice e confrontarla con R (con n = `dimensione_albero`).
4. **PdA.** Nella foglia del registro leggere `radice_pda`; per il proprio documento: `SHA-256(file)` deve essere uguale a `sha256` della foglia del documento; con `prova_pda` ricalcolare la radice del PdA e confrontarla con `radice_pda`.
5. **Nel tempo.** Conservare i checkpoint ricevuti (evento `catena.checkpoint`, RdV). Per due checkpoint (n1, R1) e (n2, R2) chiedere `GET /prova/consistenza?da=n1&a=n2` e verificarla (RFC 9162 §2.1.4.2): se il registro avesse riscritto anche una sola foglia vecchia, la verifica fallirebbe.
6. **Testimone.** Se il checkpoint porta la riga `— verumdoc-testimone`, verificarla allo stesso modo con la chiave pubblica del testimone (pubblicata separatamente).
7. **Stato del documento** (R05). La foglia del registro del documento deve essere di formato 1 (senza `tipo` o `tipo: riversamento`): una foglia di formato 2 non prova una conservazione. Per ogni voce di `ciclo_vita`: foglia di formato 2 dello stesso PdA, inclusa nello stesso checkpoint (come al passo 3). Uno scarto o un annullamento inclusi: il documento **non** è più conservato (data e pratica nella foglia). Con `ciclo_vita_completo: false` (o `ciclo_vita_totale` maggiore delle voci) e nessuno scarto o annullamento: stato non determinabile, mai «conservato». Revisione 2 di R05 (S8): verificare `attestazione_ciclo_vita` con la chiave del registro (come al passo 1), con origine e dimensione del checkpoint della prova e il PdA della prova; il numero attestato deve essere uguale alle voci presentate (maggiore = elenco incompleto) e, a parità, l'impronta `SHA-256` delle righe `<indice> <hash di foglia hex>\n` ordinate per indice deve coincidere. Senza attestazione l'assenza di scarti non è dimostrabile. Revisione 3 di R05: attestazione v2 (righe `origine`, `checkpoint`, `rilasciata`, `pda`, `stato`, `foglie`, `elenco`); `stato` diverso da `CONSERVATO` = documento non più (integralmente) conservato; lo stato vale all'istante `rilasciata` e al checkpoint indicato: confrontarlo con l'ultimo checkpoint pubblicato.

La funzione `app.registro.rdv.verifica_rdv` della hashchain esegue i passi 1–4 su un RdV v2; il verificatore pubblico (CLI e pagina, `tools/verificatore`) li espone ai clienti.

## 11. Garanzie e limiti

**Cosa protegge la chiave del registro (chiave di servizio Ed25519, non un sigillo qualificato).** Autentica i checkpoint: un checkpoint con firma valida è stato emesso da chi detiene la chiave. Con i checkpoint in mano a clienti e testimone, una modifica successiva del registro (cancellare, alterare o riordinare foglie già pubblicate) è rilevabile da chiunque con la prova di consistenza. **Non** impedisce a chi controlla la chiave di presentare a soggetti diversi due storie diverse (split view) né di firmare uno stato falso **prima** che qualcuno lo veda: per questo servono il testimone e lo scambio dei checkpoint fra clienti. La chiave non dà valore legale né data certa.

**Cosa aggiunge il sigillo qualificato con marca temporale.** Valore giuridico (sigillo elettronico qualificato eIDAS del conservatore) e **data certa opponibile** (marca RFC 3161 di un prestatore qualificato) sul checkpoint, quindi su tutti i PdA che copre. **Oggi assente** (VD-C-068): lo stato è dichiarato `NON_FIRMATO` nel registro e in ogni RdV. L'istante `emesso_il` e il `conservato_il` delle foglie vengono dall'orologio del servizio, non da una terza parte.

**Cosa aggiunge il testimone.** Un secondo soggetto, con chiave, database, account e amministratori diversi (in produzione; in sviluppo può stare nello stesso cluster con database separato), che controfirma solo checkpoint consistenti con quelli già controfirmati: una riscrittura del registro o una storia biforcata non otterrebbe la controfirma, e senza controfirma nessun PdA diventa `CONSERVATO` (un rifiuto blocca il completamento fino all'intervento di un operatore, §5–§6). Non verifica il contenuto dei PdA (solo gli hash), e la sua garanzia vale quanto la sua indipendenza organizzativa.

**Altri limiti dichiarati.**
- **Registri nel database: cosa è protetto e da chi** (correzioni R04, rilievo 5, migrazioni `20261023_031` e `033`; dettagli in `docs/architettura/infrastruttura.md`, «Ruoli del database»). Registri: `audit_global`, `verifiche_registro` e le loro partizioni, `catena_pda`, `catena_registro`, `catena_nodi`, `catena_checkpoint`.
  - **Ruolo dei servizi** (membro di `verumdoc_applicazione`): solo INSERT e SELECT sui registri (niente UPDATE, DELETE, TRUNCATE); non è proprietario, quindi non stacca né elimina partizioni, non crea regole, trigger o politiche, non disattiva trigger o RLS.
  - **Ruolo della manutenzione** (membro di `verumdoc_manutenzione`): proprietario delle sole tabelle partizionate operative (outbox, eventi, consegne); dei registri crea le partizioni solo con `vd_crea_partizione_registro` (nome, limiti e difese decisi dalla funzione) e non le stacca mai.
  - **Proprietario dei registri** (`verumdoc_audit_proprietario`, NOLOGIN, nessun membro in esercizio): anche chi ne avesse i diritti trova `FORCE ROW LEVEL SECURITY` su `audit_global` e partizioni (solo INSERT e SELECT anche per il proprietario), i trigger append-only e gli **event trigger** che rifiutano: CREATE/ALTER/REPLACE e DROP delle funzioni di protezione; CREATE/ALTER RULE, TRIGGER e POLICY sui registri (ammessi solo i trigger e le politiche delle difese, nella forma attesa); DETACH, DROP e RENAME di registri e partizioni; DISABLE o NO FORCE della RLS, trigger disattivati o solo in replica, anche se nascosti in SQL dinamico (controllo dello stato a fine comando). Provato con un ruolo non superutente in `services/gateway/tests/test_r04_correzioni_registri_ruoli.py` (la prova del revisore `prova_audit_bypass.sh`).
  - **Struttura dei registri** (terzo giro, T1 e T2, migrazione 034):
    - un'impronta di colonne, tipi, NOT NULL, vincoli e indici unici di `audit_global` e `verifiche_registro` (`protezione.impronte_registri`) è controllata a fine di ogni ALTER TABLE e CREATE/ALTER INDEX. Un CHECK o un NOT NULL aggiunti per far fallire voci d'audit scelte sono rifiutati, sulla madre e sulle partizioni;
    - `CREATE TABLE … PARTITION OF` un registro è rifiutato: le partizioni si creano solo con `vd_crea_partizione_registro`;
    - le migrazioni legittime sui registri usano `registri_modificabili` (`docs/runbooks/migrazioni_audit.md`).
  - **Tabelle di R05** (migrazione `20261023_039`): `catena_foglie_interne` (contenuto immutabile, indice scritto una volta, nessuna cancellazione) e `audit_esportazioni` (append-only) hanno i loro trigger e il ruolo dei servizi non può cancellarne le righe, ma non sono negli elenchi degli event trigger: chi può sospendere i trigger le può alterare. Non sono una difesa: ciò che conta (foglie, segmenti, checkpoint) è nel WORM e nella catena, e la ricostruzione le ricrea dalle foglie.
  - **Il superutente del database** (su RDS l'utente master, membro di `rds_superuser`, usato solo per le migrazioni). Può sospendere gli event trigger e scavalcare la RLS: contro di lui le difese dentro PostgreSQL non bastano. Le difese sono fuori dal database: il **WORM** (Object Lock COMPLIANCE: checkpoint, foglie, RdV e, da R05, i segmenti dell'audit non si alterano né si cancellano), la **catena** (checkpoint firmati e distribuiti a clienti e testimone: una riscrittura del registro è rilevabile con la prova di consistenza; il testimone non controfirma una storia biforcata) e, quando ci sarà, il sigillo.
  - **Audit nel WORM** (R05, VD-C-112, `app/registro/audit_worm.py`, migrazione `20261023_039`). Ogni periodo chiuso di `audit_global` (`AUDIT_EXPORT_PERIODO_MINUTI`, impostazione del conservatore in console dal R07, 15 minuti, fra 5 e 60, esportato `AUDIT_EXPORT_MARGINE_SECONDI` dopo la fine e — revisione R05, R7 — solo quando nessuna transazione iniziata prima della sua fine è ancora aperta: `vd_transazioni_aperte()`, docs/architettura/sicurezza.md §7) diventa un segmento `registro/audit/<da>-<a>.jsonl` nel WORM: JSON Lines canonico, una riga di intestazione (periodo, numero di righe, radice di Merkle RFC 6962 delle righe, impronta del segmento precedente) e una riga per voce d'audit in ordine (created_at, id), con `dettagli` nel testo jsonb di PostgreSQL (nessuna conversione). L'impronta del segmento entra nel registro come foglia `audit` (§4.1), sotto checkpoint firmati e controfirmati. `python -m app.registro.operatore verifica-audit [--da --a]` confronta il database con i segmenti e stampa ogni riga **cancellata**, **modificata** o **aggiunta** in un periodo già esportato, oltre ai segmenti non (ancora) nel registro o non coperti da un checkpoint e alla catena dei segmenti interrotta (codice 1). Periodi vuoti consecutivi si uniscono; un periodo con più di `AUDIT_EXPORT_RIGHE_MASSIME` righe si divide a metà. Un segmento già nel WORM con contenuto diverso (righe cambiate dopo l'esportazione e esportazione persa) è un allarme dell'audit (`audit_worm_divergente`, metrica `verumdoc_catena_audit_worm_divergente`), **non** un blocco del registro: la conservazione continua. Metrica `verumdoc_catena_audit_ritardo_secondi`. Runbook `docs/runbooks/audit_worm.md`.
  - **Cosa resta scoperto dell'audit**: le righe del periodo non ancora esportato (al più un periodo più il margine: con i predefiniti 70 minuti) si possono ancora cancellare senza traccia nella catena; il superutente può fermare l'export (lo mostra la metrica del ritardo e lo dimostra il buco nei segmenti); una transazione rimasta aperta a lungo ritarda l'export dei periodi successivi al suo inizio (allarme di ritardo `AuditNonEsportato`), ma le sue righe non si perdono più (revisione R05, R7: prima finivano in un periodo già esportato e nessun segmento le conteneva).
- Il registro è **interno** (VD-P-29): non c'è un ancoraggio pubblico; la verificabilità esterna si basa sui checkpoint consegnati ai clienti e al testimone.
- Il RdV v2 non è firmato a sé (dipende dal checkpoint); i PdA in riversamento non hanno l'albero di livello 1.
- Un solo sequenziatore attivo: la scala oltre i numeri del §4 (sharding, §4.3 del piano) richiederebbe più registri con una radice comune, non realizzata.

## 12. Impostazioni (hashchain)

| Variabile | Predefinito | Uso |
|---|---|---|
| `CATENA_CHIAVE_PRIVATA` | — (obbligatoria, tranne ENV=test) | chiave Ed25519 `PRIVATE+KEY+verumdoc-registro+…`; in sviluppo `tools/dev/genera_segreti_dev.py` → `infra/dev_catena.env` |
| `CATENA_ORIGINE` | vuota (origine dall'ambiente) | origine dei checkpoint |
| `CATENA_CHECKPOINT_SECONDI` | 60 | cadenza dei checkpoint |
| `CATENA_CONTROFIRMA_MAX_SECONDI` | 600 | allarme del checkpoint in attesa di controfirma (il completamento attende comunque la controfirma) |
| `CATENA_SEQUENZIATORE_INTERVALLO_SECONDI` | 1 | giro del ciclo |
| `CATENA_SEQUENZIATORE_LOTTO` | 1000 | foglie per transazione |
| `CATENA_LEASE_SECONDI` | 30 | durata del lease del sequenziatore |
| `CATENA_COMPLETAMENTO_LOTTO` | 200 | PdA completati per giro |
| `CATENA_WORM_SEGMENTO_FOGLIE` | 10000 | foglie per segmento esportato |
| `CATENA_REGISTRO_SOSPESO` | false | registro sospeso dopo un ripristino del database: niente sequenziatore, checkpoint né testimone; allarmi attivi, metrica e allarme dopo 30 minuti. **Solo a mano** (`kubectl set env`), mai nei values (runbook `ripristino_registro_da_backup.md`) |
| `TESTIMONE_URL` | vuota (testimone «NON_CONFIGURATO», solo fuori da produzione) | indirizzo del testimone; **obbligatorio con `ENV=production`** |
| `TESTIMONE_SONDA_SECONDI` | 60 | lettura di prova del testimone quando non c'è nulla da inviare (allarme se non risponde) |
| `TESTIMONE_VERIFICATORE` | — (obbligatoria con l'URL) | chiave pubblica del testimone |
| `TESTIMONE_SERVICE_TOKEN` | — (obbligatorio con l'URL) | token con cui il registro si presenta al testimone |
| `CATENA_PUBBLICO_SERVIZIO`, `CATENA_SCOPE_LETTURA_FOGLIE` | `verumdoc-registro`, `registro:leggi` | token di servizio ammessi su `/registro/foglie` |
| `CATENA_SCOPE_AGGIUNTA_FOGLIE` | `registro:aggiungi` | R05: scope del token di servizio di `ciclo-vita` su `/interno/v1/registro/foglie` |
| `CATENA_CICLO_VITA_MAX_FOGLIE` | 50 | R05: foglie del ciclo di vita al più in una prova d'inclusione |
| `AUDIT_EXPORT_ATTIVO` | true | R05: export di `audit_global` nel WORM dal ciclo del registro |
| `AUDIT_EXPORT_PERIODO_MINUTI` (console, R07) | 15 | durata di un periodo (un segmento), allineata al tempo UTC; fra 5 e 60 |
| `AUDIT_EXPORT_MARGINE_SECONDI` | 600 | attesa dopo la fine del periodo (transazioni ancora aperte) |
| `AUDIT_EXPORT_RIGHE_MASSIME` | 200000 | righe al più in un segmento (oltre: il periodo si divide) |
| `AUDIT_EXPORT_INIZIO` | vuota (inizio dell'ora della voce d'audit più vecchia) | primo istante esportato (ISO 8601) |
| `AUDIT_EXPORT_PERIODI_PER_GIRO` | 24 | periodi esportati al più per giro (recupero di un arretrato) |
| `RETENTION_PERMANENTE_ANNI` (parametro del motore, R09) | 30 | retention degli oggetti WORM del registro (il rinnovo automatico degli oggetti `registro/` resta da fare: VD-C-148) |

Errori: famiglia `CAT` del catalogo (`CAT-403`, `CAT-404`, `CAT-409`, `CAT-422`, `CAT-503`); R05: `IDEM-409` per una foglia interna già richiesta con dati diversi.
