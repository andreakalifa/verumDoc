# Runbook — scarto con cancellazione incompleta (PdA `ELIMINAZIONE_INCOMPLETA`)

Revisione 2 di R05 (rilievo S3). Servizio `ciclo-vita`; documentazione
`docs/architettura/ciclo_vita.md` §2 e §5; migrazione `20261023_042`.

## Quando si usa

- Allarme **`ScartoEliminazioneIncompleta`** (`verumdoc_ciclo_vita_pda_eliminazione_incompleta > 0`, gravità pagina).
- Una pratica di scarto è **`ESEGUITA_CON_ECCEZIONI`** (evento `pratica.cambio_stato`, conteggio `NON_COMPLETATO` nella pratica).
- Allarme **`PraticaLavoroInErrore`** (§ «Lavoro in errore»).
- Allarme **`ScartoAutorizzazioniNonVerificate`** (§ «Autorizzazioni non verificate», revisione 4 di R05).
- Allarme **`ScartoNonDeterminabile`** (R14, VD-C-122): PdA chiusi con `verifica_storage = non_determinabile`; il controllo manuale con `list-object-versions` descritto sotto va fatto prima di riprendere o di chiudere con eccezioni (la ripresa automatica parte comunque solo se lo storage si legge).

## R09 (D20): ripresa automatica e chiusura con eccezioni

- **Ripresa automatica.** Il lavoro periodico del lavoratore controlla ogni 15 minuti gli scarti `ESEGUITA_CON_ECCEZIONI` senza lavoro: se almeno un PdA incompleto non ha più versioni bloccate nello storage (legal hold tolto, retention scaduta) chiede da solo la ripresa (ruolo SISTEMA, `dati.riprese_automatiche`), al più `SCARTO_RIPRESE_AUTOMATICHE` (3) volte, e avvisa l'ente (`ciclo_vita.avviso`, RIPRESA_SCARTO). Non serve più togliere il blocco e poi ricordarsi di riprendere.
- **Chiudi con eccezioni.** Se il blocco è definitivo (es. sequestro), il conservatore chiude la pratica dalla console («Chiudi con eccezioni», motivo di almeno 20 caratteri). Il lavoratore verifica che chi chiede sia ancora del conservatore e che la richiesta sia nell'audit, scrive per ogni PdA incompleto il fatto `CHIUSO_CON_ECCEZIONI` e la foglia `chiusura_eccezioni`, poi la pratica è `CHIUSA_CON_ECCEZIONI` (finale). Le versioni rimaste restano nel WORM e la verifica continua le controlla (D26).
- Il controllo manuale prima della ripresa resta per gli oggetti **senza inventario** (`verifica_storage = non_determinabile`, VD-C-122): la ripresa automatica parte comunque solo se lo storage si legge.

## Cosa è successo

Lo scarto aveva iniziato a cancellare un PdA (autorizzazioni complete, retention scaduta e nessun legal hold al momento del controllo, con il PdA bloccato) e non ha potuto finire:

| Causa (`pratiche_oggetti.motivo`) | Significato |
|---|---|
| `LEGAL_HOLD` | un oggetto del PdA ha ricevuto il legal hold DURANTE lo scarto (es. ordine dell'autorità giudiziaria) |
| `RETENTION_NON_SCADUTA` / `RETENTION_ASSENTE` | una versione con retention futura o senza retention (tipicamente scritta dopo l'elenco) |
| `ERRORE_PERSISTENTE` | lo storage ha rifiutato la cancellazione `SCARTO_TENTATIVI_PDA` volte (parametro del motore, R09) (es. permesso IAM mancante, chiave fuori dai prefissi del lavoratore) |
| `VERSIONI_RIMASTE` | dopo 3 giri di elenco e cancellazione restano versioni sotto il prefisso del PdA |

Il servizio NON ha forzato nulla (mai `BypassGovernanceRetention`) e si è fermato al primo blocco per quel PdA. Lo stato è:

- oggetto della pratica `NON_COMPLETATO` con `dettagli.versioni_eliminate`, `dettagli.versioni_rimaste`, campioni di chiavi (`chiavi_eliminate`, `chiavi_rimaste`) e `causa`;
- PdA `ELIMINAZIONE_INCOMPLETA`;
- verbale di scarto nel WORM con `<Esito>ESEGUITO_CON_ECCEZIONI</Esito>` e, per il PdA, ciò che è stato cancellato e ciò che resta;
- **nessun tombstone** per quel PdA (le foglie «scarto» ci sono solo per i PdA cancellati per intero);
- voce d'audit `ciclo_vita.scarto.pda_eliminazione_incompleta`.

Un PdA per cui non si era cancellata nessuna versione torna invece `CONSERVATO` con l'oggetto `BLOCCATO`: nulla da fare. Revisione 3 di R05 (T1): lo si decide dallo **storage** (inventario delle versioni in `dettagli.inventario`, registrato prima della prima cancellazione: tutte ancora presenti), anche dopo un lavoratore ucciso a metà (`dettagli.verifica_storage = 'inventario'`); con lo storage non leggibile o un oggetto senza inventario (iniziato prima della correzione) il PdA resta incompleto (`verifica_storage = 'non_determinabile'`, `versioni_rimaste` nullo, nel verbale `versioniRimaste="non determinabile" verificaStorage="non_determinabile"`; revisione 4 di R05, U1: anche quando lo storage non risponde alla ripresa o dopo `ERRORE_PERSISTENTE` l'oggetto si CHIUDE così, senza ciclo di errori): verificarlo a mano con `list-object-versions` prima di qualunque altra azione. Revisione 4 di R05 (U4): una versione comparsa sotto il prefisso dopo l'inventario entra nell'inventario (con commit) prima di essere cancellata (`dettagli.versioni_dopo_inventario` > 0: capire chi l'ha scritta, anomalia da aprire come criticità).

Effetti sul resto del sistema (revisione 3 di R05): il PdA `ELIMINAZIONE_INCOMPLETA` esce dalla verifica continua (nessun falso `integrita.anomalia`, T4); la prova d'inclusione dichiara `stato_pda: SCARTO_PARZIALE` e il verificatore pubblico dà «DOCUMENTO SCARTATO IN PARTE» (T3).

## Procedura

1. **Individuare i PdA** (conservatore, sola lettura):
   ```sql
   SELECT p.id AS pratica, o.aip_id, o.urn, o.motivo, o.dettagli
   FROM public.pratiche_oggetti o JOIN public.pratiche p ON p.id = o.pratica_id
   WHERE o.esito = 'NON_COMPLETATO' ORDER BY p.id, o.id;
   ```
   oppure `GET /api/v1/ciclo-vita/pratiche/{id}/oggetti?esito=NON_COMPLETATO`.
2. **Capire la causa** e NON toccare lo storage a mano:
   - `LEGAL_HOLD`: chiedere al responsabile del servizio di conservazione l'atto che ha disposto il blocco. Finché il blocco resta, il PdA resta incompleto: è lo stato corretto, documentato nel verbale. Annotare nel registro delle criticità il riferimento all'atto.
   - `ERRORE_PERSISTENTE`: leggere `ultimo_errore` della pratica e i log `scarto_pda_errore_persistente`; correggere la causa (es. ruolo IAM del lavoratore: `infra/helm/values.yaml`, `services.ciclo-vita.workers.lavoratore.iam`, poi `tofu apply`).
   - `RETENTION_*` / `VERSIONI_RIMASTE`: elencare le versioni rimaste (`aws s3api list-object-versions --prefix <ente>/<pda>/`) e capire chi le ha scritte (aip-builder o hashchain dopo l'avvio dello scarto: anomalia da aprire come criticità). Una versione con retention futura NON si cancella prima della scadenza.
3. **Quando il blocco cessa** (legal hold tolto dall'autorità, permesso corretto, retention scaduta): un RSC_CONSERVATORE o SUPER_ADMIN chiede la **ripresa**:
   ```
   POST /api/v1/ciclo-vita/pratiche/{id}/transizione
   {"stato_atteso": "ESEGUITA_CON_ECCEZIONI", "nuovo_stato": "ESEGUITA",
    "allegati": [], "motivo": "<riferimento all'atto che ha revocato il blocco>"}
   ```
   Risposta `202 esecuzione_richiesta`. Il lavoratore riporta i soli PdA `NON_COMPLETATO` a `IN_CORSO` (tentativi azzerati; voce d'audit `ciclo_vita.scarto.ripresa`), il PdA torna `ELIMINAZIONE`, rielenca e cancella. Prima di cancellare la ripresa **ricontrolla i blocchi lato database** come la prima esecuzione (classe diventata a conservazione permanente, scadenza estesa, fascicolo riaperto, esibizione in corso: T6): con un blocco nulla si cancella e l'oggetto resta `NON_COMPLETATO` con quel motivo. Esiti:
   - tutti completati: verbale integrativo `verbale_scarto_<pratica>_ripresa_<n>.xml`, tombstone per i PdA ora cancellati per intero (citano il verbale integrativo), pratica `ESEGUITA`, allarme spento al prossimo aggiornamento della metrica;
   - ancora bloccati: la pratica torna `ESEGUITA_CON_ECCEZIONI` con un nuovo verbale integrativo; ripetere dal punto 2.
4. **Mai**: `RESPINTA` (non ammessa: una cancellazione iniziata non si annulla), modifiche SQL di `aip.stato` o di `pratiche_oggetti`, cancellazioni manuali nel bucket.

## Lavoro in errore (`PraticaLavoroInErrore`)

Una pratica con lavoro del lavoratore ha almeno `LAVORATORE_SOGLIA_ALLARME_TENTATIVI` tentativi falliti: il lavoratore ritenta con attesa crescente (`LAVORATORE_RITARDI_SECONDI`, l'ultimo si ripete) ma un servizio rifiuta. Leggere `ultimo_errore` (`GET /api/v1/ciclo-vita/pratiche/{id}`):

- `LC-503-REGISTRO`, `LC-503-*`: servizio irraggiungibile o token scaduto (ruotare i token del Secret `ciclo-vita-lavoratore-secrets`, `infra/k8s/secrets/README.md`);
- `CAT-409-RISCONTRO`: la hashchain non trova il fatto della foglia nel database (pratica, stato del PdA; per lo scarto la riga `esiti_esecuzione` scritta dal lavoratore, revisione 3 di R05, T5): anomalia da aprire come criticità P1, non ritentabile. Un oggetto `ELIMINATO` senza la riga in `esiti_esecuzione` NON l'ha scritto il lavoratore: trattarlo come possibile manomissione (verificare chi ha scritto, `audit_global`, e le versioni nello storage);
- per lo scarto, la cancellazione dei PdA ha il suo limite di tentativi (sopra): questo allarme riguarda il verbale, le foglie o gli altri tipi di pratica.

## Autorizzazioni non verificate (`ScartoAutorizzazioniNonVerificate`)

Revisione 4 di R05 (U2). Prima di cancellare, il lavoratore ricontrolla da sé la catena delle autorizzazioni della pratica (`services/ciclo-vita/app/application/autorizzazioni.py`): cronologia completa e continua secondo la macchina a stati, nulla osta della Soprintendenza se la pratica lo richiede o se l'ente è ORA una pubblica amministrazione (`tenants.natura`), autorizzazione del produttore e nulla osta riletti dal WORM con l'impronta registrata, piano approvato (impronta ricalcolata, PdA nei criteri). Se qualcosa non torna la pratica resta nel suo stato **senza lavoro e senza alcuna cancellazione**: `ultimo_errore.codice = LC-409-AUTORIZZAZIONI`, difetti in `ultimo_errore.difetti`, voce d'audit `ciclo_vita.scarto.autorizzazioni_non_verificate`, log `scarto_autorizzazioni_non_verificate`.

1. **Trattarlo come incidente di sicurezza** finché non è spiegato: una pratica che arriva a `ESEGUIBILE` senza iter coerente non nasce dall'API legittima (possibile pod API compromesso, sicurezza.md §6). Leggere i difetti, la cronologia (`GET /api/v1/ciclo-vita/pratiche/{id}`), `audit_global` (chi ha creato la pratica e gli allegati) e i log del pod API.
2. Difetti che hanno una spiegazione d'esercizio:
   - `NULLA_OSTA_SALTATO` / `NULLA_OSTA_ASSENTE` dopo un cambio di natura dell'ente (`PUT /onboarding/tenant/{id}/natura`): la pratica era stata autorizzata come ente privato. Respingerla e aprirne una nuova con il nulla osta (non c'è un passo NULLA_OSTA dopo ESEGUIBILE);
   - `ALLEGATO_ASSENTE_DAL_WORM` / `ALLEGATO_IMPRONTA_DIVERSA`: documento autorizzativo non leggibile o diverso nello storage conservativo: P1, verificare bucket e versione (`pratiche_allegati.chiave`, `version_id`).
3. **Mai** forzare l'esecuzione modificando righe a mano: la verifica si ripete a ogni richiesta di esecuzione. Una pratica fabbricata si **respinge** (`ESEGUIBILE → RESPINTA`, ammessa: l'esecuzione non è avviata) e si annota nel registro delle criticità.
4. Gli oggetti che il piano approvato non selezionava, se trovati da eseguire, sono chiusi `BLOCCATO` con motivo `FUORI_PIANO` (mai cancellati): stessa procedura del punto 1.

## Lavoro automatico (`PassoPeriodicoInErrore`, `PdaPermanenteSenzaOggetti`)
Revisione R09 (Z4, Z5). Console del conservatore → Configurazione → «Ciclo di vita automatico» → «Lavoro automatico»: ultimo esito di ogni passo, `errore` o `elementi_in_errore` con i primi elementi.
- **Rinnovo impossibile** (`RinnovoNonPossibile`): `RETENTION_PERMANENTE_ANNI` deve superare `RETENTION_RINNOVO_SOGLIA_ANNI`; correggere con una bozza (la console rifiuta la combinazione). Finché non si corregge il passo non risulta fatto e si riprova a ogni controllo: allegati, verbali e atti (senza legal hold) non si rinnovano.
- **Elementi in errore**: un PdA o un allegato che lo storage rifiuta non ferma gli altri; si ritenta al giro dopo (il termine non avanza). Una foglia non scritta (registro giù) si richiede al giro dopo per lo stesso rinnovo, senza un fatto nuovo.
- **PdA permanenti senza oggetti** (`retention_fino_a = 9999-12-31`): il rinnovo li ha tolti dalla coda; è un'anomalia d'integrità: verifica continua del PdA, registro delle impronte, audit dello storage. Dopo l'indagine, riportare `retention_fino_a` a NULL per farli ricensire.

## Annullamento fermo (`PuntualeAutorizzazioniNonVerificate` su un annullamento non applicato)
Revisione R09 (W1). Il versamento è intatto. I casi «qualcosa è cambiato» (versamento preso dalla validazione, persone o regola cambiate) il lavoratore li chiude da solo RESPINTI con avviso all'ente. Restano fermi solo i difetti da manomissione (`ultimo_errore.difetti`: catena, audit, approvazione posteriore, firma della richiesta): indagare con l'audit `ciclo_vita.annullamento.autorizzazioni_non_verificate`, poi un amministratore dell'ente o il conservatore apre la pratica e usa «Respingi» (riquadro «Annullamento fermo»); l'allarme si spegne e l'ente può chiedere di nuovo.
