# Verifica indipendente delle ricevute e del registro delle impronte

Per clienti, responsabili della conservazione degli enti e ispettori. Spiega che cosa
conservare, come verificare una ricevuta **senza VerumDoc** e che cosa garantisce
(e che cosa non garantisce) il testimone indipendente.

Riferimenti: piano R03–R05 §5; contratti R04 K1 (prove), K2 (checkpoint), K3
(testimone), K5 (rapporto di versamento definitivo v2). Formati pubblici: RFC 6962
(alberi di Merkle), C2SP *signed note* e *tlog-checkpoint* (stesso formato dei
registri di trasparenza e di `golang.org/x/mod/sumdb/note`).

## 1. In breve

Ogni documento conservato entra in due alberi di Merkle:

1. **albero del PdA**: una foglia per ogni file del pacchetto (impronta SHA-256 del file
   e dei metadati) più una per l'indice del PdA; la radice è la *radice del PdA*;
2. **registro globale**: una foglia per ogni PdA conservato, in ordine, solo in
   aggiunta. A intervalli regolari il registro pubblica un **checkpoint**: dimensione
   del registro e radice, firmati con la chiave del registro.

Un secondo servizio, il **testimone**, gestito in un account diverso e da amministratori
diversi, riceve ogni checkpoint, controlla che estenda quello che ha già firmato e lo
**controfirma**. Il rapporto di versamento definitivo contiene le prove che legano il
vostro file al checkpoint controfirmato.

## 2. Che cosa conservare

| Che cosa | Dove si trova | Perché |
|---|---|---|
| Rapporto di versamento definitivo v2 (XML) | API del versamento, area riservata, notifica | Contiene impronte, prove d'inclusione e checkpoint firmato |
| Chiave pubblica del registro (`verumdoc-registro+…`) | Manuale di conservazione | Verifica la firma dei checkpoint |
| Chiave pubblica del testimone (`verumdoc-testimone+…`) | Manuale di conservazione e `GET /api/v1/testimone/stato` del testimone | Verifica la controfirma |
| **Checkpoint periodici** (file di testo) | `GET /api/v1/hashchain/checkpoint`, evento `catena.checkpoint`, `GET /api/v1/testimone/checkpoint` | Permettono di dimostrare che il registro non è stato riscritto |

Sui checkpoint:

- salvatene uno **almeno al mese** (o a ogni lotto di versamenti importante) e
  conservatelo **fuori** dai sistemi del conservatore, così com'è (è un file di testo:
  non modificatelo, neanche gli a capo);
- preferite la copia letta **dal testimone** (`GET /api/v1/testimone/checkpoint`): porta
  entrambe le firme e non dipende dal registro;
- un checkpoint vecchio non scade: più è vecchio, più storia copre.

Le chiavi pubbliche cambiano solo con una rotazione annunciata nel manuale; tenete anche le
chiavi precedenti per verificare i checkpoint emessi prima della rotazione.

## 3. Verificare una ricevuta

Servono soltanto le chiavi pubbliche e i file. Due strumenti, stessi controlli.

### 3.1 Da riga di comando

Requisiti: Python 3.12 con `cryptography` e la cartella `libs/catena` (codice pubblico,
solo libreria standard). Copiate `tools/verificatore/verifica_ricevuta.py` e
`libs/catena/` nella stessa cartella (lo script trova `libs/catena` accanto a sé).

```
python verifica_ricevuta.py --rdv rapporto.xml --documento fattura.pdf \
    --chiave-registro chiave_registro.txt --chiave-testimone chiave_testimone.txt
```

In alternativa a `--rdv` si può usare la prova JSON (`--prova prova.json`, risposta di
`GET /api/v1/hashchain/prova/inclusione`). Le chiavi si passano come testo o come file.
La chiave del registro deve chiamarsi `verumdoc-registro` e quella del testimone
`verumdoc-testimone`, e devono essere **diverse**: la stessa chiave nei due campi è
rifiutata (la «controfirma» non sarebbe di un soggetto indipendente). L'origine attesa
dei checkpoint è quella di produzione, `verumdoc.it/registro/v1`; per un ambiente di
prova del conservatore indicatela con `--origine verumdoc.it/registro/v1/<ambiente>`.

Il risultato elenca ogni controllo con `OK`, `FALLITO`, `NON ESEGUITO`, `NON RICHIESTO`
(controllo facoltativo non chiesto, es. senza `--documento`) o `NON APPLICABILE`, e chiude
con `ESITO: VERIFICATA` (codice d'uscita **0**) o `ESITO: NON VERIFICATA` (codice **1**).
Un controllo fallito o non eseguibile non dà mai «verificata».

### 3.2 Dal browser

Aprite `tools/verificatore/web/index.html` (un solo file, nessuna risorsa esterna: si può
salvare e aprire anche senza rete). Caricate rapporto o prova, incollate le chiavi,
facoltativamente il file originale, e premete «Verifica». Il campo «Origine attesa del
registro» vale `verumdoc.it/registro/v1` (produzione); le stesse regole della riga di
comando valgono per le chiavi (nomi dei ruoli, chiavi diverse). **Nulla lascia il browser**: la
pagina vieta ogni connessione di rete (Content-Security-Policy `connect-src 'none'`).

Le firme Ed25519 si verificano con WebCrypto, disponibile in Chrome 137, Firefox 129,
Safari 17 e successivi. In un browser senza Ed25519 la pagina lo dichiara in un avviso, i
controlli delle firme risultano «Non eseguito» e la ricevuta **non** è dichiarata
verificata: usate un browser recente o la riga di comando.

### 3.3 I controlli

1. **Impronta del file**: lo SHA-256 del file che avete è quello scritto nella foglia del
   PdA (il file si legge a blocchi, anche se grande).
2. **Inclusione nel PdA**: la foglia, con il percorso di audit RFC 6962, ricostruisce la
   radice del PdA; la stessa radice è nella foglia del registro.
3. **Inclusione nel registro**: la foglia del PdA ricostruisce la radice del checkpoint,
   per la dimensione dichiarata dal checkpoint.
4. **Origine e firme del checkpoint**: la prima riga del checkpoint è l'origine attesa
   (un checkpoint di un ambiente di prova o di un altro registro non vale, anche se
   firmato); firma della chiave del registro e controfirma del testimone, con le chiavi
   che avete voi (mai quelle scritte nel rapporto) e distinte fra loro.
5. Le foglie devono essere in **JSON canonico** (chiavi ordinate, senza spazi): una foglia
   riscritta in un'altra forma non è accettata.

Per i PdA conservati prima del registro a due livelli (foglia di *riversamento*) il
controllo 2 non si applica: la foglia cita l'impronta del manifest del PdA; l'impronta
del singolo file si verifica sul pacchetto di distribuzione.

## 4. Dimostrare che il registro non è stato riscritto

Con due checkpoint salvati in momenti diversi (dimensioni *n1* < *n2*) chiedete la prova di
consistenza e verificatela:

```
GET /api/v1/hashchain/prova/consistenza?da=<n1>&a=<n2>      →  {"da","a","percorso"}

python verifica_ricevuta.py --checkpoint-vecchio cp_n1.txt --checkpoint-nuovo cp_n2.txt \
    --prova-consistenza consistenza.json \
    --chiave-registro chiave_registro.txt --chiave-testimone chiave_testimone.txt
```

Se il registro avesse cambiato, tolto o riordinato anche una sola foglia già coperta dal
checkpoint vecchio, **nessuna** prova potrebbe far tornare il controllo, nemmeno con firme
valide. Una prova che non torna è una prova documentale: conservate i due checkpoint e la
risposta ricevuta e segnalatelo al conservatore e all'autorità di vigilanza.

## 5. Che cosa garantisce il testimone

Il testimone (servizio `testimone`, protocollo ispirato a C2SP *tlog-witness*):

- verifica la firma del registro su ogni checkpoint con la chiave pubblica che ha in
  configurazione;
- controfirma un checkpoint **solo** se una prova di consistenza dimostra che estende
  l'ultimo che ha già controfirmato (il primo checkpoint è accettato per fiducia al primo
  uso, e da lì in poi ogni passo è dimostrato);
- conserva tutto in modo append-only nel proprio database (UPDATE, DELETE e TRUNCATE
  rifiutati), con una catena di impronte propria;
- se riceve due checkpoint firmati dal registro con **la stessa dimensione e radici
  diverse** (un *fork*: il registro mostra storie diverse a destinatari diversi) passa
  in stato **COMPROMESSO**, conserva entrambe le note come prova, alza un allarme e non
  controfirma più nulla finché un suo operatore non interviene; lo sblocco è un comando
  locale registrato con nome dell'operatore e motivo, non un'API remota;
- una prova di consistenza non valida è rifiutata e registrata come allarme.

Che cosa vuol dire per voi: **un checkpoint con la controfirma valida del testimone è
stato visto da una seconda parte indipendente come prosecuzione coerente di tutta la
storia precedente**. Dal lato del conservatore (correzioni R04): nessun PdA è dichiarato
conservato (evento `versamento.conservato`, rapporto di versamento definitivo) su un
checkpoint che il testimone non ha controfirmato; se il testimone **rifiuta** un
checkpoint la conservazione si sospende finché il conservatore non ha indagato, e quel
checkpoint non sarà mai citato in una ricevuta. In produzione il testimone è
obbligatorio. Per riscrivere la storia senza essere scoperti servirebbe
compromettere insieme registro e testimone, che stanno in account diversi con
amministratori diversi.

Lo stato del testimone è pubblico: `GET /api/v1/testimone/stato` (stato, ultima dimensione
controfirmata, chiave pubblica). I checkpoint controfirmati si leggono senza credenziali:
`GET /api/v1/testimone/checkpoint` e `GET /api/v1/testimone/checkpoint/{dimensione}`.

### Limiti dichiarati

- La chiave del registro e quella del testimone sono **chiavi di servizio**, non sigilli
  qualificati. Il sigillo qualificato con marca temporale del checkpoint è indicato a
  parte nel rapporto (`SigilloQualificato`) e resta `NON_FIRMATO` finché il fornitore
  qualificato non è configurato.
- La controfirma del testimone non contiene un istante: data e ora del checkpoint sono
  quelle dichiarate dal registro (e, quando presente, dalla marca temporale).
- Il testimone vede solo radici e dimensioni, non i documenti: garantisce la coerenza del
  registro, non il contenuto dei pacchetti, che si verifica con le impronte (§3.3).
- Un checkpoint senza controfirma (testimone non raggiungibile o bloccato) è segnalato nel
  rapporto (`Testimone stato=…`) e il verificatore non lo dà per verificato, salvo che si
  scelga esplicitamente di non verificare la controfirma (`--senza-testimone`).
