# CHANGELOG — R11 — Conformità dei pacchetti (31/10/2026)

Quinto rilascio della roadmap R07–R13 (decisioni D07 e D03, opzione consigliata). Prerequisito: R10 applicato (migrazioni fino a `20261102_049`). Migrazione nuova: `20261103_050` (riprendibile, `lock_timeout`, vincolo `NOT VALID` convalidato fuori dal lock, nessun `CONCURRENTLY`).

**Fatto senza il prerequisito previsto**: la UNI 11386:2020 con il suo XSD non è ancora stata acquistata. D07 è completa sul testo ufficiale AgID; D03 è solo predisposta (VD-C-071 resta aperta).

> **Avviso operativo**
> - **Migrazione 050**: si ferma se il conservatore ha già creato dalla console una versione 2 di una tipologia (stesso numero delle versioni predefinite R11). Procedura: `docs/runbooks/migrazioni_audit.md`, sezione R11.
> - **RGL-102 e RGL-106 «solo più severe»** (catalogo regole 4): l'ente non le può più allentare; un ente o una classe con IGNORA o AVVISA torna all'effetto del conservatore. Controllare in console (Configurazione → Regole) chi le aveva su IGNORA o AVVISA prima del deploy e avvisarlo.
> - **Tipologia non abbassabile**: `PROFILO_TIPOLOGIA` a livello accordo si ignora e una classe non scende sotto la tipologia (e la versione) dell'ente o del conservatore; solo il conservatore, con motivo, può impostare queste due voci sulle classi di un ente (VD-P-114).
> - **Rapporto pre-deploy obbligatorio**: `python tools/profili/conflitti_classi.py` sul database di produzione, in sola lettura, PRIMA della migrazione: elenca le classi il cui profilo cambia esito con la nuova composizione, con l'anteprima sui PdV recenti (VD-P-113; `README_RILASCIO.md`).
> - **Le versioni 2 dei profili non entrano da sole**: nessun ente cambia comportamento finché una classe non sceglie `PROFILO_TIPOLOGIA_VERSIONE = 2`. Prima di attivarle per un ente serve la firma del responsabile sul riscontro (`docs/conformita/riscontro_allegato5.md`, decisione `VD-N`).

## Per chi usa la console
- **Profili di metadati**: ogni tipologia mostra la versione in vigore e la versione 2 «scelta per classe»; per ogni campo il metadato e il paragrafo dell'Allegato 5, l'obbligo (anche «obbligatorio se…»), i sottocampi delle strutture; i profili di fattura, registro di protocollo e contratto sono indicati come profili propri del conservatore.
- **Passaggio di una classe alla versione 2**: bozza «impostazioni» di livello classe con `PROFILO_TIPOLOGIA` e `PROFILO_TIPOLOGIA_VERSIONE = 2`; l'anteprima conta i versamenti recenti che sarebbero rifiutati; si applica subito o dalla data scelta (se la versione non vale ancora alla data, CFG-423 chiede di programmarla).
- **Profilo della classe**: si aggiungono solo campi facoltativi propri; un campo con il nome di un campo dell'Allegato 5 o un campo obbligatorio è bloccato nel piano con il motivo (l'obbligo si aggiunge con «Metadati obbligatori»).
- **Cosa l'ente non può più fare**: impostare RGL-102 o RGL-106 su IGNORA o AVVISA (solo più severe del conservatore); scegliere la tipologia su un accordo; abbassare tipologia o versione di una classe rispetto al profilo in vigore, anche con una modifica programmata. Il piano mostra il motivo del blocco.
- **Pacchetti**: nella pagina del pacchetto i metadati strutturati (Soggetti, Modifiche, parole chiave, valori sì/no) sono in chiaro.

## Per chi gestisce il servizio
- Riscontro sul testo ufficiale: `docs/conformita/riscontro_allegato5.md` (fonte, edizione, tabella campo → esito → riferimento).
- XSD UNI 11386: istruzioni nel README («Schema UNI 11386 (IPdA)»); `make conformita-ipda`; `python tools/sincro/verifica_ipda.py stato|registra|verifica|impronte`.
- **Parte B solo predisposta**: la UNI 11386:2020 con l'XSD non è acquistata. Dopo l'acquisto: impronte SHA-256 dei file fissate in `libs/sincro/schemi_ufficiali.py` (oggi vuoto; ricavate con il comando `impronte`, con revisione del codice e decisione `VD-N`), poi `make conformita-ipda`. Fino ad allora `stato` risponde «schema ufficiale non disponibile» (uscita 2) e nessun IPdA è dichiarato «conforme».
- Rapporto pre-deploy delle classi: `tools/profili/conflitti_classi.py` (uscita 0 nessuna classe cambia, 1 classi da avvisare, 2 errore di connessione).
- Attivazione delle v2 per un ente: solo dopo la firma del responsabile sul riscontro.

## Contenuto
- **D07 — Profili dell'Allegato 5** (`libs/verumdoc_rules`):
  - formato dati `verumdoc-profilo-tipologia-2`: eredità a versione fissata (`estende_versione`; i file del formato 1 ereditano la v1 della base), `adozione` (automatica/esplicita), `origine` (allegato5, servizio, conservatore), `rif` al testo;
  - campi nuovi del motore (`profili.py`): `data_ora`, `struttura`, `elenco_strutture` con `sottocampi`, `obbligatorio_se`, `valori_ammessi_se`, `almeno_uno`, `minimo`/`massimo`, `min_elementi`/`max_elementi`; messaggi senza valori;
  - versioni 2 di documento informatico, documento amministrativo informatico, aggregazione documentale (riscontrate), fattura, registro di protocollo e contratto (profili propri del conservatore); impronte di tutte le versioni pubblicate in `dati/profili/impronte.json`;
  - catalogo regole 4: `PROFILO_TIPOLOGIA_VERSIONE` (solo classe; 0 = comportamento di prima), RGL-102/106 solo più severe, fotografia con tipologia e versione;
  - composizione restrittiva tipologia + classe (`restringi_campo`), piano della classe solo con campi facoltativi propri (`motivi_campi_classe`), controllo della versione scelta (`esito_versione_tipologia`) e alla decorrenza (CFG-423);
  - RGL-102 e RGL-106 applicano obblighi a condizione e strutture; il validatore preventivo usa lo stesso motore (l'esportazione porta il profilo strutturato).
- **IPdA** (`aip-builder/app/services/sincro_builder.py`): sezione `MetadatiAllegato5` con i nomi dell'Allegato 5 (strutture comprese, chiavi non previste conservate), nome del documento dal file se manca, codice IPA e AOO del registro dal soggetto registrante; dichiarati provvisori struttura e namespace.
- **D03 — XSD caricabile** (`libs/sincro/validatore.py`, `tools/sincro/verifica_ipda.py`): schema in `libs/sincro/schemas/<versione>/` registrato con l'impronta SHA-256 di ogni file e il flag «ufficiale»; esiti `CONFORME` (solo schema ufficiale integro), `NON_CONFORME`, `VALIDO/NON_VALIDO_SCHEMA_DI_PROVA`, `SCHEMA_NON_DISPONIBILE`, `SCHEMA_ALTERATO`; validatori intercambiabili (lxml predefinito, senza rete né entità esterne); XSD di prova NON ufficiale solo nei test.
- **Migrazione `20261103_050`**: controllo delle versioni in conflitto, colonna `profili_tipologia.adozione` con vincolo.
- **Frontend**: `src/data/allegato5.ts` (campi v2 e sottocampi), pagina del pacchetto, console dei profili.
- Dipendenza: `lxml` nell'aip-builder (BSD-3).

## Revisione indipendente: rilievi e correzioni
Riscontro a campione della tabella (23 voci): nessun errore di obbligatorietà o di valori. Una lacuna: le tracciature sono obbligatorie anche per il documento annullato. Corretta nelle v2 prima del rilascio, perché non erano mai state adottate (VD-P-112). Corretti anche i riferimenti di pagina. Quattro difetti medi, tutti corretti:
- **T1**: l'ente poteva togliere o abbassare il profilo con un accordo o sulla classe. Correzioni:
  - `PROFILO_TIPOLOGIA` si sceglie solo per conservatore, ente e classe;
  - a tempo di esecuzione un livello inferiore vale solo se chiede almeno gli obblighi di quello superiore (`tipologia_effettiva`);
  - nella console chi non è il conservatore non abbassa il profilo, anche per le classi che ereditano un cambio dell'ente e per le modifiche programmate;
  - uscita: il conservatore può impostare con motivo solo queste due voci sulle classi di un ente (VD-P-114).
- **T2**: la nuova composizione cambiava l'esito delle classi esistenti. Correzioni:
  - un elenco chiuso o una forma della classe sopra un campo libero si mantiene; un tipo incompatibile cede alla tipologia;
  - rapporto pre-deploy `tools/profili/conflitti_classi.py`, con l'anteprima sui PdV recenti (VD-P-113).
- **T3**: le impronte dello schema non coprivano i file caricati da lxml (TOCTOU). Correzioni:
  - byte letti una volta e schema compilato da quei byte;
  - resolver limitato ai file registrati della cartella (qualsiasi estensione);
  - riferimenti esterni o remoti danno l'esito `SCHEMA_NON_UTILIZZABILE`, senza eccezioni.
- **T4**: «ufficiale» si dichiarava nel JSON. Correzioni:
  - vale solo con le impronte fissate nel codice (`libs/sincro/schemi_ufficiali.py`, oggi vuoto);
  - `registra --ufficiale` rifiuta lo schema di prova e gli schemi diversi;
  - nuovo comando `impronte`;
  - lo schema di prova resta sotto `tests/`, esclusa dall'immagine (VD-P-111).

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| **T1** L'ente toglieva o abbassava il profilo dell'Allegato 5 con un accordo (`PROFILO_TIPOLOGIA=""`) o sulla classe (versione 2 → 0/1), anche programmato | Medio | `PROFILO_TIPOLOGIA` solo per conservatore, ente e classe; pavimento a tempo di esecuzione (`lettura.tipologia_effettiva`, `profili.almeno_severo`); console: nessun abbassamento di tipologia o versione, anche per le classi che ereditano e per le modifiche programmate; uscita del conservatore con motivo. VD-P-114 | `libs/verumdoc_rules/tests/test_r11_profili.py::test_accordo_non_toglie_ne_abbassa_la_tipologia`, `::test_la_classe_non_scende_sotto_la_tipologia_dell_ente`, `::test_verso_degli_obblighi`, `::test_uscita_del_conservatore_solo_per_la_tipologia`; `services/onboarding/tests/test_r11_profili_console.py::test_ente_non_abbassa_il_profilo_della_classe`, `::test_accordo_non_sceglie_la_tipologia`, `::test_ente_non_abbassa_le_classi_che_ereditano` |
| **T2** La nuova composizione cambiava l'esito delle classi esistenti (restrizioni perse, nuovi rifiuti) senza controllo prima del deploy | Medio | Sempre il più severo: elenco chiuso o forma della classe sopra un campo libero si mantiene, tipo incompatibile cede alla tipologia; rapporto pre-deploy `tools/profili/conflitti_classi.py` con anteprima sui PdV recenti. VD-P-113 | `libs/verumdoc_rules/tests/test_r11_profili.py::test_elenco_chiuso_della_classe_su_campo_libero_mantenuto`, `::test_tipo_piu_largo_della_classe_cede_alla_tipologia_e_si_segnala`; `tools/profili/tests/test_conflitti_classi.py` (2) |
| **T3** Le impronte dello schema non coprivano i file caricati da lxml (include fuori cartella o con altra estensione, seconda lettura dal disco); import remoto con eccezione non gestita | Medio | Byte letti una volta, impronta e compilazione sugli stessi byte; resolver limitato ai file registrati (qualsiasi estensione); riferimenti esterni o remoti → `SCHEMA_NON_UTILIZZABILE` | `libs/sincro/tests/test_r11_validatore_xsd.py::test_include_registrato_con_estensione_qualsiasi`, `::test_include_fuori_dalla_cartella_non_utilizzabile`, `::test_import_remoto_esito_gestito`, `::test_schema_compilato_dai_byte_controllati_non_dal_disco` |
| **T4** «Ufficiale» era un booleano nel JSON accanto allo schema: lo schema di prova si registrava come ufficiale e risultava «conforme» | Medio | «Ufficiale» solo con le impronte fissate nel codice (`libs/sincro/schemi_ufficiali.py`, vuoto fino all'acquisto); `registra --ufficiale` rifiuta schema di prova e schemi diversi; comando `impronte`; schema di prova sotto `tests/`, esclusa dall'immagine. VD-P-111 | `libs/sincro/tests/test_r11_validatore_xsd.py::test_ufficiale_dichiarato_nel_json_non_basta`, `::test_conforme_solo_con_impronte_ufficiali_fissate_nel_codice`, `::test_registrazione_ufficiale_rifiutata_senza_impronte_nel_codice`, `::test_schema_di_prova_mai_registrabile_come_ufficiale`, `::test_impronte_diverse_da_quelle_ufficiali_rifiutate`, `::test_schema_di_prova_fuori_dall_immagine`; `tools/sincro/tests/test_verifica_ipda.py::test_ufficiale_rifiutato_senza_impronte_nel_codice` |
| **Lacuna del riscontro** Tracciature obbligatorie anche per il documento annullato (Allegato 5, p. 22), non solo dalla versione 2 | — | Campo del servizio `documento_annullato` nelle v2, corrette in place con impronte riscritte (mai adottate, VD-P-112); riferimenti di pagina corretti. Dopo il rilascio le correzioni si fanno solo con una v3 | `libs/verumdoc_rules/tests/test_r11_profili.py::test_codice_registro_per_protocollo_e_tracciature_dalla_versione_2`, `::test_impronte_delle_versioni_pubblicate_invariate` |

## Conformità normativa
- Fonte: Allegato 5 «I metadati», edizione maggio 2021, <https://www.agid.gov.it/sites/agid/files/2024-06/allegato_5_metadati.pdf>, letto il 31/10/2026 fino a p. 84. Nessuna edizione successiva sul sito AgID; nessuna fonte non ufficiale usata.
- Esiti del riscontro (`docs/conformita/riscontro_allegato5.md`):
  - documento informatico e documento amministrativo informatico: tutti i campi riscontrati, nessun «da riscontrare» nelle v2;
  - aggregazione documentale: riscontrata fino a p. 84; restano 6 campi del §4 (Soggetti dell'aggregazione e obbligo per ruolo, procedimento amministrativo, collegamento ad altre aggregazioni, indice dei documenti, tempo di conservazione, note) da riscontrare sul PDF scaricato a mano (VD-C-157);
  - fattura, registro di protocollo, contratto: non disciplinati dall'Allegato 5, profili propri del conservatore (`origine: conservatore`);
  - verifica a campione della revisione indipendente (23 voci): nessun errore di obbligatorietà o di valori; una lacuna (tracciature per il documento annullato) corretta; riferimenti di pagina corretti.
- Correzioni rispetto alla v1: tipo di aggregazione «Serie Di Fascicoli» (era «Serie Fascicoli»); dati di registrazione, riservato, nome e versione del documento obbligatori; Verifica obbligatoria solo per le modalità a e b; segnatura per i protocollati; Soggetti, Tracciature e Assegnazione strutturati.
- Nessun elemento o namespace UNI inventato; nessun documento di altri conservatori usato. Nessuna copia ufficiale gratuita dell'XSD trovata.

## Criticità
Totale 159: 28 aperte, 12 parzialmente chiuse, 29 chiuse con riserve, 90 chiuse.
- Chiusa con riserve: VD-C-136 (riserve VD-C-157…159 e firma del responsabile prima dell'attivazione).
- Resta aperta: VD-C-071 (predisposizione fatta, manca la norma; adeguamento del costruttore e validazione prima della scrittura).
- Nuove: VD-C-157 (§4 oltre p. 84 non riscontrato), VD-C-158 (obblighi fra campi diversi non controllati), VD-C-159 (contenitore provvisorio nell'IPdA e forma dei valori).

## Cambi incompatibili
- RGL-102 e RGL-106: l'effetto scelto da ente o classe vale solo se più severo di quello del conservatore.
- Composizione dei profili: un campo della classe con il nome di un campo della tipologia la restringe soltanto (prima la sostituiva).
- Nuove versioni dei profili di classe: solo campi facoltativi propri.
- IPdA: i metadati dei profili v2 (anche `riservato`, `versione_documento`, `soggetti`… quando presenti) escono in `MetadatiAllegato5` invece che fra i metadati aggiuntivi.
- Catalogo regole in vigore: 4 (le fotografie 1–3 restano leggibili).
- `PROFILO_TIPOLOGIA` a livello accordo si ignora; una classe non scende sotto la tipologia dell'ente o del conservatore (revisione T1).
- Composizione: un tipo della classe incompatibile con quello della tipologia cede alla tipologia (revisione T2, rapporto pre-deploy).

## Cosa è simulato
- Lo schema XSD dei test è uno schema di PROVA scritto per VerumDoc e marcato NON ufficiale: descrive solo la struttura provvisoria dell'IPdA. I test di conformità con lo schema ufficiale sono saltati con il motivo.

## Migrazione `20261103_050`
Controllo dei conflitti con le versioni predefinite R11 (si ferma con l'elenco); `ADD COLUMN IF NOT EXISTS adozione` con predefinito costante (solo catalogo, nessun UPDATE); vincolo `ck_profili_tipologia_adozione` `NOT VALID` e `VALIDATE` in autocommit; `lock_timeout`. Downgrade rifiutato se esistono versioni ad adozione esplicita del conservatore. Provata: avanti, ripresa, indietro, arresto sul conflitto, script offline senza `CONCURRENTLY`.

## File eliminati o spostati
- `libs/sincro/schemas/.gitkeep`: segnaposto superfluo, la cartella contiene ora `LEGGIMI.md`. Lo toglie anche `tools/pulizia/pulisci_progetto.py`.

## Scelte da confermare
VD-P-105…114 (`docs/registro/decisioni.md`).

## Residui
- Acquisto della UNI 11386:2020 (VD-N-01) e adeguamento del costruttore dell'IPdA; validazione nel percorso di costruzione prima della scrittura WORM (VD-C-071).
- VD-C-157: 6 campi del §4 oltre p. 84 e lo schema delle aggregazioni da riscontrare sul PDF ufficiale scaricato a mano (`pdftotext -layout`, pp. 84–101), poi una v3; obblighi fra campi diversi (VD-C-158).
- Firma del responsabile del servizio sul riscontro prima di attivare le v2.

## Test eseguiti
Corsa completa dello sviluppo (`r11/esegui.sh`, PostgreSQL 16 reale, `r11_integ` dal template `r10_integ` migrato a `20261103_050`, Siegfried reale): frontend typecheck, lint, build ed e2e (**53**) verdi; `make verifiche` ok; 22 suite verdi. Sei suite (gateway, hashchain, distribution, dashboard-api, ciclo-vita, onboarding) fallite per una causa d'ambiente: le partizioni giornaliere di `outbox_eventi` del template arrivavano al 30/10 e la corsa è passata al 31/10. Un solo difetto di R11: il test della catena delle revisioni non conosceva la 050 (corretto). Ripresa (`r11/esegui_riprova.sh`) su un database nuovo con le partizioni mancanti create dalla funzione della manutenzione con il ruolo `verumdoc_manutenzione`: tutte verdi.

Totale: **3.990 test passati, 0 falliti** (4 saltati: i 3 test di conformità all'XSD ufficiale e 1 facoltativo della catena), più 53 end-to-end del frontend (R10: 3.941). Nuovi test: `libs/verumdoc_rules/tests/test_r11_profili.py` (25), `libs/sincro/tests/test_r11_validatore_xsd.py` (9), `tools/sincro/tests/test_verifica_ipda.py` (3), `services/aip-builder/tests/test_r11_ipda.py` (7 più 3 saltati), `services/onboarding/tests/test_r11_profili_console.py` (4).

Correzioni della revisione (T1–T4): suite toccate rilanciate su un database nuovo con le partizioni create prima (`r11/esegui_t.sh`, `esegui_t2.sh`). Tutte verdi: libs/verumdoc_rules 241, libs/sincro 18, tools/sincro 4, tools/profili 2, tools/validatore 7, aip-builder 192, validation 173, distribution 164, onboarding 279, gateway 786; `make verifiche` 0. **Totale dopo la revisione: 4.014 test passati, 0 falliti.** Prove di mutazione della revisione: 13 su 13 uccise (T1 5, T3 4, T4 4).

Prove di mutazione dello sviluppo: 14 su 14 uccise — «conforme» senza schema ufficiale (4), versioni dei profili append-only e adozione (4), l'ente che rende facoltativo un campo obbligatorio o spegne RGL-102/106 (6). Migrazione provata avanti, ripresa, indietro, arresto sul conflitto e in modalità offline.
