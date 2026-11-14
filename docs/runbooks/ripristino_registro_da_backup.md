# Ripristino del registro delle impronte da backup senza biforcazioni

Riferimenti:
- `docs/architettura/catena.md`, §5 (testimone), §6 (completamento) e §11 (garanzie);
- correzioni R04: secondo giro (N3, allarmi del testimone raggruppati) e terzo giro (T3, T4, T5);
- codice: `services/hashchain/app/registro/coerenza.py` (controllo di coerenza e blocchi persistenti), `services/hashchain/app/registro/ricostruzione.py` (R05, VD-C-111: ricostruzione dal WORM).

## Quando serve

Quando il database del conservatore torna a un backup: snapshot di RDS o ripristino point-in-time (PITR).

Il registro delle impronte vive nelle tabelle `catena_*`. Dopo il ripristino può essere **indietro** rispetto a ciò che è già stato pubblicato fuori dal database:

| Misura | Cos'è | Dove sta |
|---|---|---|
| `D` | dimensione del registro nel database ripristinato | `catena_stato.dimensione` |
| `N` | ultimo checkpoint **emesso**. È pubblicato anche se il testimone non lo ha controfirmato: evento `catena.checkpoint` a tutti gli enti, `GET /checkpoint` | WORM, `registro/checkpoint/<N>.txt` |
| `X` | fine dell'ultimo segmento di foglie esportato | WORM, `registro/foglie/<da>-<X>.jsonl` |
| `T` | ultima dimensione controfirmata dal testimone | testimone (account separato) |

Gli oggetti WORM sono in Object Lock COMPLIANCE: nessun ripristino li tocca.

**Soglia: il registro può ripartire solo se `D ≥ max(N, X, T)` e le foglie e le radici già pubblicate coincidono con quelle del database.**

Non basta `T`. Prendiamo un checkpoint `N > T`, emesso e non ancora controfirmato. È comunque in mano ai clienti. Se il sequenziatore rimettesse in registro le foglie da `D` in poi in un altro ordine, i clienti riceverebbero checkpoint firmati dalla stessa chiave e inconsistenti con `N`: una **biforcazione firmata dal registro**.

## Cosa fa il registro da solo (terzo giro, T4)

**Quando parte il controllo.** All'avvio del ciclo, e ogni volta che si toglie `CATENA_REGISTRO_SOSPESO`, la hashchain misura `D`, `N`, `X` e `T`. Confronta anche i contenuti: i segmenti WORM oltre l'ultima esportazione nota al database (riga per riga) e la radice del checkpoint `N`.

**Se il registro è indietro o diverge:**
- si apre un **blocco persistente** (`catena_blocchi_registro`, causa `REGISTRO_INDIETRO` o `REGISTRO_DIVERGENTE`);
- il registro **non** sequenzia, non emette checkpoint, non scrive nel WORM e non contatta il testimone;
- gli allarmi continuano:
  - log `registro_bloccato` all'apertura e `registro_bloccato_aperto` a ogni giro;
  - metriche `verumdoc_catena_registro_bloccato` e `verumdoc_catena_registro_soglia`;
  - regola `RegistroBloccato`;
- il controllo si ripete ogni minuto. Quando passa, il blocco si chiude da solo con una voce `catena.blocco_registro_chiuso` in `audit_global`.

**Se il controllo non si può fare** (quarto giro, F1): blocco `COERENZA_NON_VERIFICABILE`, subito per un errore di permesso, dopo 3 tentativi per gli altri errori. Cause tipiche:
- il ruolo IAM della hashchain senza `s3:ListBucket` sul prefisso `registro/` del bucket dei PdA (`services.hashchain.iam` nei values: `elenco` con `elenco_prefissi: {aip: ["registro/"]}`; `tofu apply` del modulo `iam_servizi`);
- un WORM irraggiungibile.

Il dettaglio è in `python -m app.registro.operatore blocchi` e nel log `registro_coerenza_non_verificabile`. Il blocco si chiude da solo al primo controllo riuscito.

**Scelta: fermo con allarme, non ricostruzione automatica dal WORM.** Una ricostruzione sbagliata trasforma un registro fermo in una biforcazione firmata; un registro fermo non perde nulla (i PdA attendono in coda). La ricostruzione esiste (R05) ma è un comando dell'operatore, con prova obbligatoria prima (§4 B.1).

**Esportazione dichiarata ma assente** (R05, VD-C-118): se il database dichiara esportate nel WORM (`esportate_db`, `E`) foglie i cui segmenti non ci sono (database di un altro ambiente o bucket, `S3_BUCKET_AIP` sbagliato, ripristino incoerente), `controlla-worm` riporta in `divergenze` «esportazione: il database dichiara E foglie esportate, ma …» e il blocco è `REGISTRO_DIVERGENTE`. Prima di tutto verificare bucket e ambiente della hashchain; se sono giusti è un incidente d'integrità (§3, punto 3).

**Scrittura WORM con lo stesso nome e contenuto diverso:**
- apre il blocco `WORM_CONTENUTO_DIVERSO` invece di ritentare a ogni giro;
- dopo l'indagine lo chiude l'operatore con `sblocca-registro`;
- se il contenuto è ancora diverso, il blocco si riapre al primo tentativo.

## 1. Sospendere il registro prima di riaprire il database

Il controllo automatico protegge comunque. La sospensione evita che la hashchain parta mentre il ripristino è a metà.

```bash
kubectl -n verumdoc set env deploy/hashchain CATENA_REGISTRO_SOSPESO=true
kubectl -n verumdoc scale deploy/hashchain-verifica --replicas=0
```

**Con `CATENA_REGISTRO_SOSPESO=true`:**
- il ciclo non sequenzia, non emette checkpoint e non contatta il testimone;
- la API resta attiva per i controlli: checkpoint, prove, foglie;
- gli allarmi continuano: controfirme, rifiuti, blocchi;
- log `registro_sospeso` a **ogni** giro e metrica `verumdoc_catena_registro_sospeso = 1`;
- dopo 30 minuti scatta l'allarme di pagina `RegistroSospeso`.

La variabile si imposta **solo a mano**: `make verifiche` rifiuta dei values che la contengano.

**Al testimone non si fa nulla:** niente `sblocca`, niente `nuova-base`.

## 2. Misurare D, N, X, T

Un solo comando, dal container della hashchain:

```bash
kubectl -n verumdoc exec deploy/hashchain -- python -m app.registro.operatore controlla-worm
```

| Riga stampata | Significato |
|---|---|
| `dimensione_db` | `D` |
| `esportate_db` | `E`: fine dell'ultima esportazione nota al database |
| `checkpoint_db` | `C`: ultimo checkpoint nel database |
| `fine_segmenti_worm` | `X` |
| `checkpoint_worm` | `N` |
| `testimone` | `T` (`None` se il testimone non risponde) |
| `soglia` | `max(N, X, T)` |
| `divergenze` | segmenti o radici diversi da quelli del database |
| `ESITO` | riga finale. Codice d'uscita 0 solo se coerente |

**Come si trovano `X` e `N`:**
- i segmenti hanno nomi a 12 cifre, quindi l'ordine alfabetico è quello numerico; si elencano solo quelli da `E` in poi;
- ogni checkpoint `n` ha un segmento che finisce in `n`, perché l'emissione esporta prima le foglie.

Controprova a mano (sola lettura):

```bash
# X: ultimi segmenti di foglie
aws s3api list-objects-v2 --bucket <bucket> --prefix registro/foglie/ \
  --start-after "registro/foglie/$(printf %012d <E>)" --query 'Contents[].Key'
# N: il checkpoint alla fine del segmento più alto, se esiste
aws s3api head-object --bucket <bucket> --key registro/checkpoint/<X>.txt
# T: dal testimone (account separato)
kubectl --context testimone -n verumdoc-testimone exec deploy/testimone -- python -m app.operatore stato
```

Se il testimone è già **bloccato** (stato COMPROMESSO), vedi il §5.

## 3. Caso A: `ESITO COERENTE`, cioè `D ≥ max(N, X, T)` senza divergenze

1. Prova indipendente della consistenza dal checkpoint pubblicato più alto. Se `N > T`, quel checkpoint è `N` (`registro/checkpoint/<N>.txt`); se `N ≤ T`, è `<T>.controfirmato.txt`. Indichiamo con `S` la sua dimensione:

   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" https://<api>/api/v1/hashchain/checkpoint > ultimo.txt
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://<api>/api/v1/hashchain/prova/consistenza?da=<S>&a=<D>" > consistenza.json
   python tools/verificatore/verifica_ricevuta.py \
     --chiave-registro chiave_registro.txt --chiave-testimone chiave_testimone.txt \
     --checkpoint-vecchio <S>.txt --checkpoint-nuovo ultimo.txt \
     --prova-consistenza consistenza.json
   ```

   Per un checkpoint `N` non controfirmato aggiungere `--senza-testimone`.

2. Esito **VERIFICATA**: riattivare il registro:

   ```bash
   kubectl -n verumdoc set env deploy/hashchain CATENA_REGISTRO_SOSPESO-
   kubectl -n verumdoc scale deploy/hashchain-verifica --replicas=<prima>
   ```

   Il ciclo ripete da solo il controllo (log `registro_coerente`) e riparte. Il prossimo checkpoint viene controfirmato normalmente.

3. Esito **NON VERIFICATA**: incidente di integrità. Il registro resta sospeso e al testimone non si fa nulla.

## 4. Caso B: `ESITO NON COERENTE`

### B.1 Registro indietro (`D < soglia`)

1. **Via preferita: un secondo ripristino PITR.** Scegliere un istante successivo all'ultima scrittura WORM del registro, cioè l'ora dell'oggetto con la soglia:

   ```bash
   aws s3api head-object --bucket <bucket> --key registro/checkpoint/<N>.txt --query LastModified
   ```

   Aggiungere un margine. Il registro è append-only: un PITR più recente contiene le foglie fino alla soglia nell'ordine originale. Ripetere il §2 fino a `ESITO COERENTE`, poi proseguire con il §3.

2. **Se nessun backup copre quell'istante: ricostruzione dal WORM** (R05, VD-C-111). Il registro resta sospeso (§1). Si ricostruiscono foglie, nodi, checkpoint e collegamenti da `D` a `max(N, X)` dai segmenti e dai checkpoint nel WORM.

   a. **Prova** (non scrive nulla, nemmeno il lease; si può ripetere):

      ```bash
      kubectl -n verumdoc exec deploy/hashchain -- \
        python -m app.registro.operatore ricostruisci-da-worm --fino-a <max(N, X)> --prova
      ```

      | Riga stampata | Significato |
      |---|---|
      | `versione` | versione del comando (REGOLE §16.7), da riportare nel registro degli incidenti |
      | `dimensione_db`, `base` | `D` e il checkpoint del database da cui si legge il WORM (verificato: nota identica, firma, radice dei nodi); 0 = da capo |
      | `foglie_lette`, `foglie_nuove`, `fine_segmenti` | foglie lette dal WORM, quelle da scrivere (da `D`), fine dei segmenti letti |
      | `checkpoint_verificati`, `ultimo_checkpoint`, `controfirme_verificate` | checkpoint del WORM con firma, origine e radice controllate; controfirme verificate con `TESTIMONE_VERIFICATORE` |
      | `ultimo_checkpoint_controfirmato` | limite della ricostruzione: si ricostruiscono SOLO le foglie coperte da un checkpoint firmato e controfirmato (revisione R05, R5) |
      | `coda_scartata` | foglie oltre quel limite scartate senza scriverle (solo con `--scarta-coda-non-coperta`) |
      | `collegamenti` | foglie per tipo (pda, riversamento, scarto, annullamento, rettifica, audit) collegate al database |
      | `AVVISO` | da leggere; per esempio la coda scartata con `--scarta-coda-non-coperta` |
      | `PROBLEMA` | qualcosa non torna: **nessuna scrittura** |
      | `ESITO` | `PROVA RIUSCITA` (codice 0) o `NON RICOSTRUIBILE` (codice 1) |

      Problemi tipici e cosa fare:
      - `PdA … assente dal database`: il PdA è stato conservato dopo il backup e la sua riga `aip` è persa. La ricostruzione non inventa PdA: serve un backup che li contenga (anche con il registro indietro) oppure il recupero dei PdA dal loro manifest nel WORM (procedura separata, incidente). Il registro resta fermo;
      - `checkpoint n: firma del registro non valida` o `radice diversa`: nel WORM c'è un checkpoint che non è di questo registro. Incidente d'integrità: non procedere, avvisare il responsabile della conservazione;
      - chiave del registro ruotata: aggiungere `--chiave-precedente <verifier key>` (una per chiave);
      - `controfirma … TESTIMONE_VERIFICATORE non configurato`: configurarlo (la controfirma non si accetta senza verificarla);
      - `buco nel WORM` o `solo fino a …`: `--fino-a` oltre i segmenti presenti; usare la `fine_segmenti` riportata da `controlla-worm`;
      - `foglie a-b … non coperte da un checkpoint firmato dal registro e controfirmato dal testimone`: segmenti che nessuna firma copre (il testimone non ha ancora controfirmato, o un segmento depositato da altri nel bucket). Non si ricostruiscono mai: rieseguire con `--fino-a <ultimo_checkpoint_controfirmato>` oppure con `--scarta-coda-non-coperta` (stesso risultato, la coda resta solo nel WORM e si indaga). Prima di riattivare il registro indagare la coda rimasta solo nel WORM (`controlla-worm` la mostra): se è legittima (testimone in ritardo), attendere la controfirma e ricostruire fino a lì invece di scartarla.

   b. **Ricostruzione** (solo dopo una prova riuscita, con operatore e motivo; il lease del registro deve essere libero: con `CATENA_REGISTRO_SOSPESO=true` il ciclo non lo prende):

      ```bash
      kubectl -n verumdoc exec deploy/hashchain -- \
        python -m app.registro.operatore ricostruisci-da-worm --fino-a <max(N, X)> \
          --operatore "Mario Rossi" --motivo "ripristino del <data>: nessun PITR dopo la soglia, prova riuscita"
      ```

      Il comando rilegge tutto, ripete ogni controllo e scrive a lotti (`--lotto`, 10.000 foglie per transazione). Se si interrompe, quello che ha scritto è un prefisso verificato: rieseguire lo stesso comando (riparte da `D`). Due voci in `audit_global`: `catena.ricostruzione_da_worm_avviata` e `…_conclusa`. Tempi (prova di volume, 23/10/2026): ~14.500 foglie/s in prova, ~2.700/s in scrittura; 10 milioni di foglie ≈ 12 minuti di prova e un'ora di scrittura.

   c. **Dopo**: `controlla-worm` deve dare `ESITO COERENTE`; poi §3 (consistenza indipendente dal checkpoint pubblicato più alto) e riattivazione. Le richieste del ciclo di vita e le esportazioni dell'audit perse con il backup sono ricreate dalle loro foglie (stesso `foglia_id`); l'ultimo checkpoint ricostruito è sempre controfirmato (revisione R05, R5); i checkpoint senza controfirma prima di lui risultano `SUPERATO`.

3. **Ultima risorsa:** `nuova-base` del testimone. Apre un'epoca nuova e perde la continuità con le foglie già pubblicate. Servono:
   - la decisione scritta del responsabile della conservazione (`VD-DR`);
   - l'esecuzione da parte di un amministratore dell'account del testimone;
   - la comunicazione agli enti, che hanno checkpoint fino a `N`.

### B.2 Storia divergente (`divergenze` non vuote)

Il database ripristinato contiene foglie in un ordine diverso da quello pubblicato. È successo, per esempio, se un registro è ripartito prima di questo runbook senza il controllo. La correzione è il PITR del §4.B.1 a un istante **precedente** alla ripartenza sbagliata e successivo alla soglia.

Non chiudere il blocco a mano: `sblocca-registro` lo rifiuta. Si chiude da solo quando il controllo passa.

## 5. Il testimone è già bloccato (423)

1. Sospendere il registro (§1).
2. Sul testimone, `python -m app.operatore allarmi` mostra la biforcazione.
3. Riportare il registro alla storia pubblicata (§4) e verificare la consistenza (§3).
4. Solo **dopo**, l'amministratore del testimone esegue:

   ```bash
   python -m app.operatore sblocca --operatore "…" --motivo "ripristino da backup del <data>: registro riportato alla storia pubblicata, consistenza verificata (runbook ripristino_registro_da_backup)"
   ```

5. Mai `sblocca` con il registro ancora divergente.

## 6. Dopo la ripartenza: allarmi del registro

I checkpoint rifiutati durante l'incidente formano un allarme per causa, con un contatore. La gestione in blocco richiede la **conferma di ogni causa** (T3). Le cause gravi (`HTTP_423` biforcazione, `DIMENSIONE_TESTIMONE_MAGGIORE`) si gestiscono **solo una per una**:

```bash
python -m app.registro.operatore rifiuti
python -m app.registro.operatore gestisci-rifiuti --tutti --conferma-causa HTTP_422 \
  --operatore "Mario Rossi" --motivo "ripristino da backup del <data>: …"
python -m app.registro.operatore gestisci-rifiuti --allarme <id DIMENSIONE_TESTIMONE_MAGGIORE> \
  --operatore "Mario Rossi" --motivo "ripristino da backup del <data>: registro era indietro, storia ripristinata e verificata"
python -m app.registro.operatore blocchi
```

## 7. Ripristino delle difese dell'audit dopo un incidente (T2)

Una partizione dei registri creata a mano senza difese ferma la manutenzione e gli ALTER TABLE dei registri. Dalla migrazione `20261023_034` non è più possibile crearla, perché l'event trigger lo rifiuta. Se ne esistesse una da prima, l'amministratore (superutente) applica le difese con la procedura di `docs/runbooks/migrazioni_audit.md` («Partizione senza difese»).

## 8. Controlli finali

- [ ] `controlla-worm`: `ESITO COERENTE`, codice 0.
- [ ] `verumdoc_catena_registro_bloccato` = 0 e `verumdoc_catena_registro_sospeso` = 0.
- [ ] `verumdoc_catena_allarmi_testimone_aperti` = 0 e `verumdoc_catena_checkpoint_rifiutati_aperti` = 0.
- [ ] Nuovo checkpoint `CONTROFIRMATO` entro `CATENA_CONTROFIRMA_MAX_SECONDI`.
- [ ] PdA in coda passati a CONSERVATO.
- [ ] Verifica continua riattivata, senza anomalie nuove.
- [ ] Voce nel registro degli incidenti con `D`, `N`, `X`, `T` e l'istante del PITR usato (o, se ricostruito dal WORM, l'uscita della prova e della ricostruzione con la `versione` del comando).
- [ ] `verifica-audit` (runbook `audit_worm.md`): le righe di `audit_global` perse con il ripristino risultano «cancellate» rispetto al WORM; vanno elencate nel registro degli incidenti (i segmenti nel WORM restano la copia di riferimento).
