# Regole e configurazione — come si configura VerumDoc

**Allineato al rilascio R05 (23/10/2026), contratto Q1.** Riferimenti: REGOLE_SVILUPPO §8 (regole come dati), §13 (anteprima e applicazione), §14 (permessi); piano R03–R05 §6. Questo documento è la base del capitolo «Configurazione» del manuale per il conservatore e per gli enti.

## 1. Il principio: una sola catena, un solo motore

Ogni parametro che deriva da una norma o da un accordo (formati ammessi, metadati obbligatori, dimensione massima, effetto di una regola, tipologia dell'Allegato 5, quote…) è un'**impostazione** del registro `libs/verumdoc_rules`, con tipo, predefinito, riferimento normativo (`norm_ref`) e livelli a cui si può personalizzare (`ambito_ammesso`). Il valore effettivo si risolve sempre con la stessa catena, dal più specifico al più generale:

| Livello | Chi lo imposta | Dove sta |
|---|---|---|
| **PDV** | il produttore, sul singolo versamento, con motivo | campo `override` dell'indice |
| **ACCORDO** | l'amministratore dell'ente | tabella `accordi_versamento` (versioni con decorrenza) |
| **CLASSE** | l'amministratore dell'ente (classi dell'ente), il SUPER_ADMIN (classi di base) | colonne di `classi_documentali` e `impostazioni_valori` |
| **ENTE** | il conservatore, o l'amministratore dell'ente per le voci non bloccate | `impostazioni_valori` |
| **CONSERVATORE** | il conservatore | `impostazioni_valori` (e alcune variabili d'ambiente del gateway) |
| **PREDEFINITO** | la norma o la scelta di servizio | il codice del registro |

Il primo livello che ha un valore vince; ogni valore mostrato dice la sua **origine** («10 MB — da: accordo di versamento AV-FATT v2»). Una voce `locked` (imposta dalla norma: metadati minimi dell'Allegato 5, obbligo di `Idempotency-Key`, effetto di RGL-101) si risolve solo fra conservatore e predefinito: i livelli del cliente non contano.

Lo stesso motore gira nel **gateway** (versamento), nella **verifica** (che legge la fotografia del PdV), nella **console** (onboarding), nel **validatore preventivo** (API e CLI) e in `tools/confronta_regole.py`. Le query che leggono la configurazione sono definite una volta in `libs/verumdoc_rules/lettura.py`.

## 2. Cosa si configura

| Gruppo | Voci | Livelli | Note |
|---|---|---|---|
| versamento | `FORMATI_AMMESSI`, `METADATI_OBBLIGATORI`, `CLASSE_DIMENSIONE_MAX_MB`, `PROFILO_TIPOLOGIA` | conservatore, ente, classe, accordo | `PROFILO_TIPOLOGIA` vuoto = nessun profilo di tipologia (comportamento di prima) |
| versamento (R11) | `PROFILO_TIPOLOGIA` (dal catalogo 4: conservatore, ente, classe; un livello inferiore vale solo se chiede almeno gli obblighi di quello superiore, VD-P-114), `PROFILO_TIPOLOGIA_VERSIONE` | solo classe | 0 = versione più alta valida ad adozione automatica (comportamento di prima); N = versione N del profilo di tipologia (catalogo regole 4) |
| versamento | `IDEMPOTENZA_TRANSIZIONE_FINO_AL` | conservatore, ente | |
| versamento (norma) | `METADATI_MINIMI_PRINCIPALE`, `IDEMPOTENZA_OBBLIGATORIA` | solo conservatore | `locked` |
| regole | `REGOLA.RGL-10x.EFFETTO` (BLOCCA, AVVISA, IGNORA) | conservatore, ente, classe, accordo; RGL-103 anche PDV | RGL-101 `locked` |
| quote | `QUOTA_PDV_GIORNO`, `QUOTA_RICHIESTE_MINUTO` | ente | le stabilisce solo il conservatore |
| registri | `RETENZIONE_EVENTI_CLIENTE_GIORNI`, `RETENZIONE_OUTBOX_GIORNI` | conservatore | |
| normative | `ALGORITMI_IMPRONTA_AMMESSI`, `ALGORITMI_IMPRONTA_PDA`, `CONSERVAZIONE_ANNI_PREDEFINITI`, `OGGETTO_LUNGHEZZA_MINIMA`, `CHIAVE_ANNO_MINIMO`, `MODALITA_FORMAZIONE_AMMESSE`, `TIPOLOGIE_SINCRO`, `TIPI_DOCUMENTO_INDICE` | **sola lettura** | valori ancora scritti nel codice dei servizi: si mostrano con il punto del codice, non si modificano finché un servizio non li legge dal registro (REGOLE §9.9) |

`GET /api/v1/configurazione/impostazioni` restituisce per ogni voce valore, origine, valore al livello richiesto, valore ereditato dal livello superiore, livelli ammessi e `azioni_consentite` (con il motivo di blocco).

## 3. Accordi di versamento

Un accordo è un insieme di valori concordato con l'ente per **una classe** o per **tutte le classi** dell'ente. È versionato e mai modificato: una nuova versione decorre da `valido_dal` (giorno italiano, oggi o futuro) e vale fino alla decorrenza della successiva; una versione «cessata» chiude l'accordo.

Al versamento l'accordo si sceglie così: quello indicato nel campo `accordo` dell'indice (se non è in vigore o non vale per la classe: `422 RGL-112`, mai un ripiego); altrimenti quello dell'ente per la classe; altrimenti quello dell'ente per tutte le classi; altrimenti nessuno. Due accordi in vigore per lo stesso ambito non si possono creare (l'anteprima lo blocca).

## 4. Personalizzazione del singolo versamento (PDV)

```json
"override": {"motivo": "Metadato gestionale concordato col responsabile",
             "valori": {"REGOLA.RGL-103.EFFETTO": "IGNORA"}}
```

Solo per le voci che ammettono il livello PDV (oggi l'effetto di RGL-103), con un motivo di almeno 10 caratteri. Altrimenti `422 RGL-111` con l'elenco dei problemi. Il versamento accettato porta l'avviso `RGL-110` e la fotografia `regole_applicate.override` (motivo e valori).

## 5. Profili di metadati

- **Profili di tipologia dell'Allegato 5** (dati in `libs/verumdoc_rules/dati/profili/*.json`, versione 1 dal 1/1/2022): documento informatico, documento amministrativo informatico (estende il precedente), aggregazione documentale, fattura (estende il documento informatico), registro di protocollo (estende l'amministrativo), contratto. Ogni campo ha nome, tipo, obbligatorietà, nome del metadato nell'Allegato 5, `calcolato` (lo valorizza il servizio: impronta, formato, numero di allegati) e `da_riscontrare` (da verificare sul testo ufficiale prima di attivare la tipologia per un ente).
- **Profili della classe** (`profili_metadati`): metadati propri dell'ente, versionati. Da R11 una classe aggiunge solo campi **facoltativi propri**: un campo con il nome di un campo dei profili di tipologia, obbligatorio, obbligatorio a condizione o calcolato è bloccato nel piano (l'obbligo si aggiunge con `METADATI_OBBLIGATORI`).
- Il profilo applicato è la **composizione** della tipologia scelta con `PROFILO_TIPOLOGIA` (versione scelta con `PROFILO_TIPOLOGIA_VERSIONE`) e del profilo della classe. Da R11 un campo della classe con lo stesso nome di uno della tipologia la **restringe soltanto** (obbligatorio se uno dei due lo è, meno valori ammessi), mai il contrario; RGL-102 e RGL-106 si possono solo rendere più severe. Una nuova versione si crea con una bozza `profilo` (le versioni del conservatore ereditano l'adozione dell'ultima); le versioni precedenti restano per i PdV già versati.
- **R11 (D07):** versioni 2 riscontrate sul testo ufficiale dell'Allegato 5 (`docs/conformita/riscontro_allegato5.md`), formato file `verumdoc-profilo-tipologia-2`: eredità a versione fissata (`estende_versione`), `adozione` esplicita, `origine` (allegato5, servizio, conservatore), `rif` al testo, obblighi a condizione (`obbligatorio_se`), strutture (`struttura`, `elenco_strutture` con `sottocampi`, `valori_ammessi_se`, `almeno_uno`), intervalli, data e ora. Le impronte delle versioni pubblicate sono in `dati/profili/impronte.json` (una versione pubblicata non cambia: `test_r11_profili.py`).

## 6. Come si cambia una configurazione: bozza → anteprima → applica

1. `POST /api/v1/configurazione/bozze` con `{risorsa, ambito: {livello, ente?, classe?, accordo?}, modifiche, motivo}`. Risorse: `impostazioni`, `regole`, `classe`, `accordo`, `quote`, `profilo`. Modifiche: `{codice, valore}`, `{codice, eredita: true}` («torna al valore ereditato»), `{regola, effetto}`; per l'accordo anche `{campo: "descrizione"|"cessato", valore}`; per il profilo `[{tipologia?, campi, descrizione?}]`.
2. `PATCH /bozze/{id}` con la `versione` letta (`409 CFG-410` se un altro l'ha cambiata).
3. `POST /bozze/{id}/anteprima`: **non scrive nulla** (sessione di sola lettura). Restituisce `impronta_piano`, le modifiche con valore e origine prima e dopo, gli elementi bloccati con il motivo, gli avvisi e l'**impatto**: quanti PdV recenti sarebbero rifiutati o avrebbero avvisi nuovi, con esempi. L'impatto si calcola riapplicando il motore sugli ultimi PdV (al più `CONFIGURAZIONE_ANTEPRIMA_CAMPIONE_MAX`, predefinito 2000, degli ultimi `CONFIGURAZIONE_ANTEPRIMA_FINESTRA_GIORNI`, predefinito 30, in al più `CONFIGURAZIONE_ANTEPRIMA_TEMPO_MAX_S` secondi, predefinito 5): la risposta dichiara il campione (`campione.letti`, `raggiunto_limite`, `troncato_per_tempo`).
4. `POST /bozze/{id}/applica` con `{impronta_piano, valido_dal?}`: atomico; ricalcola il piano dentro il lock e, se l'impronta non coincide, non scrive e risponde `409 CFG-409` (PIANO_CAMBIATO) con il piano nuovo. Con elementi bloccati: `422 CFG-422`. Crea sempre versioni nuove (accordi, profili) o una riga di storico (`impostazioni_storico`, append-only) accanto al valore corrente; una voce d'audit `configurazione.bozza.applicata`.

`valido_dal` vale per accordi e profili (oggi o futuro, mai prima della versione precedente); per le impostazioni di conservatore, ente e classe la modifica vale dall'applicazione (`422 CFG-423` altrimenti).

## 7. Proposte del conservatore agli enti (REGOLE §8.13)

Una bozza al livello CONSERVATORE con `proposta: {enti?: [...]}` non cambia il valore del conservatore: crea una proposta per ogni ente indicato (o per tutti gli enti attivi). L'amministratore di ogni ente la vede in `GET /proposte` e la accetta (il valore diventa quello dell'ente, con storico) o la rifiuta, sempre con motivo. Un valore imposto dalla norma non si propone: si applica.

## 8. Chi può fare cosa

| Operazione | SUPER_ADMIN | RSC_CONSERVATORE | ADMIN_TENANT | OPERATORE / VIEWER | chiave API |
|---|---|---|---|---|---|
| leggere impostazioni, regole, profili | tutti gli enti | tutti gli enti | proprio ente | proprio ente | no |
| bozze livello CONSERVATORE | sì | sì | no | no | no |
| bozze livello ENTE | ogni ente | ogni ente | proprio ente, voci non bloccate | no | no |
| bozze CLASSE / ACCORDO | solo classi di base (SUPER_ADMIN) | no | proprio ente (non classi di base) | no | no |
| quote | sì | sì | no | no | no |
| decidere una proposta | no | no | sì (proprio ente) | no | no |
| validatore preventivo e profilo esportato | indicando l'ente | indicando l'ente | sì | OPERATORE sì | sì |

## 9. Validatore preventivo

- API: `POST /api/v1/configurazione/valida` con `{indice, file_impronte?}` (anche con `X-Api-Key`): stessi rilievi del versamento (VRS, SEC, RGL), nulla si scrive.
- Senza rete: scaricare il profilo `GET /api/v1/configurazione/profilo-validatore?classe=…[&accordo=…]` e usare la CLI:

```sh
python tools/validatore/valida_pdv.py --url https://api.verumdoc.it --scarica-profilo profilo.json --classe fattura
python tools/validatore/valida_pdv.py indice.json --profilo profilo.json --file fattura.pdf
```

Il validatore non riconosce il formato dal contenuto, non fa antivirus, non controlla duplicati, idempotenza e quote: lo dice nel risultato (`controlli_non_eseguiti`). Solo il versamento accetta un PdV.

## 10. Prima di un rilascio che cambia le regole

```sh
python tools/confronta_regole.py --database-url … --modifiche modifica.json [--ente …] [--classe …]
```

Sola lettura, stesso campione e stessa funzione dell'anteprima. Uscita 0 = nessuna differenza; 1 = differenze da approvare prima di applicare (REGOLE §8.11).

## 11. Limiti noti (R05)

- Le variabili d'ambiente `IDEMPOTENZA_OBBLIGATORIA` e `IDEMPOTENZA_TRANSIZIONE_FINO_AL` del gateway valgono al livello CONSERVATORE: onboarding le legge con gli stessi nomi; se differiscono fra i due servizi la console mostra valori diversi da quelli applicati. Meglio impostarle come righe del registro.
- Le voci del gruppo «normative» sono in sola lettura finché i servizi indicati non le leggono dal registro.
- I campi `da_riscontrare` dei profili di tipologia vanno verificati sul testo ufficiale prima di attivare una tipologia (VD-C-088). Da R11 le versioni 2 di documento informatico e amministrativo non ne hanno; l'aggregazione v2 ne ha 6 (VD-C-157); le v1 restano com'erano.
