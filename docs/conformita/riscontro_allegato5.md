# Riscontro dei profili di metadati sull'Allegato 5 (R11, D07, VD-C-136)

> Ultima modifica: 2026-10-31 (R11, con la revisione indipendente). Documento di lavoro per il responsabile del servizio: prima di attivare la versione 2 di un profilo per un ente serve la sua firma su questo riscontro (decisione `VD-N` prevista da VD-C-136).

## Fonte

| Voce | Valore |
|---|---|
| Documento | *Linee guida sulla formazione, gestione e conservazione dei documenti informatici*, **Allegato 5 «I metadati»** (AgID) |
| Edizione | «Maggio 2021», data riportata sul frontespizio dell'Allegato e delle Linee guida. Nel repository AgID non è stata trovata un'edizione successiva dell'Allegato 5 (la copia della cartella 2024-06 porta la stessa data); la pagina di notizie AgID sulle modifiche del 2021 rimanda a una pagina di manutenzione e non è stata letta. |
| URL ufficiale usato | <https://www.agid.gov.it/sites/agid/files/2024-06/allegato_5_metadati.pdf> (repository AgID, cartella 2024-06) |
| Testo delle Linee guida (stessa edizione) | <https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf> |
| Consultazione | 31/10/2026, con lo strumento di lettura web della sessione (lo scaricamento diretto da `www.agid.gov.it` è negato dalla rete di sviluppo; il vecchio indirizzo `…/repository_files/allegato_5_metadati.pdf` rimanda a una pagina di manutenzione) |
| Parte letta | §1–§3 completi con i loro schemi (pp. 1–73) e §4 fino a p. 84. **Il testo oltre p. 84 (resto del §4 e schema delle aggregazioni, p. 89) non è stato estratto**: i metadati che descrive restano «da riscontrare». |
| Fonti escluse | Bozze in consultazione (`docs.italia.it`), manuali e documenti di altri conservatori, siti non istituzionali. |

Avvertenza: il testo è stato letto con un estrattore automatico. I valori qui sotto (nomi dei ruoli, valori ammessi, condizioni) vanno ricontrollati a vista sul PDF ufficiale dal responsabile prima della firma. Le stringhe dei ruoli di documento informatico e documento amministrativo sono quelle fisse degli schemi del §2 (pp. 29–32) e del §3 (pp. 63–66).

## Esiti in sintesi

- **Documento informatico** e **documento amministrativo informatico**: tutti i campi riscontrati; versione 2 con nessun campo da riscontrare.
- **Aggregazione documentale**: riscontrati i metadati fino a p. 84; restano da riscontrare Soggetti (stringhe dei ruoli e obbligo per ruolo), procedimento amministrativo, collegamento ad altre aggregazioni, indice dei documenti, tempo di conservazione e note.
- **Fattura, registro di protocollo, contratto**: l'Allegato 5 non li disciplina. La versione 2 li dichiara **profili propri del conservatore** (`origine: conservatore`), con i campi riscontrati della base e i campi propri invariati rispetto alla v1.
- Le versioni 1 (R05) **non sono modificate** (impronte in `libs/verumdoc_rules/dati/profili/impronte.json`). Le v2 hanno **adozione esplicita**: una classe le sceglie con `PROFILO_TIPOLOGIA_VERSIONE = 2` (bozza con anteprima, applicazione subito o programmata).

## Documento informatico (§2) — `documento_informatico` v1 → v2

| Campo VerumDoc | Metadato dell'Allegato 5 | v1 | Esito del riscontro → v2 | Riferimento |
|---|---|---|---|---|
| `identificativo_documento` | IdDoc, identificativo | obbligatorio | Confermato | §2, p. 5 |
| `impronta_documento` | IdDoc, impronta e algoritmo (predefinito SHA-256) | calcolato | Confermato (calcolato dal servizio) | §2, p. 5 |
| `modalita_formazione` | Modalità di formazione | a/b/c/d obbligatorio | Confermato. Lo schema riporta la descrizione estesa della modalità; VerumDoc conserva la lettera (VD-N-02) — scelta da confermare VD-P-107 | §2, p. 6 |
| `tipologia_documentale` | Tipologia documentale | obbligatorio | Confermato (testo libero) | §2, p. 7 |
| `tipologia_flusso` | Dati di registrazione, tipologia di flusso | facoltativo, da riscontrare | **Obbligatorio** (E, U, I) | §2, pp. 8–9 |
| `tipo_registro` | Dati di registrazione, tipo registro | facoltativo, da riscontrare | **Obbligatorio**; valori confermati: Nessuno, protocollo ordinario/emergenza, repertorio/registro | §2, pp. 8–9; schema p. 26–27 |
| `data_registrazione` | Dati di registrazione, data | data, facoltativo | **Obbligatorio, data e ora** | §2, p. 9 |
| `numero_registrazione` | Dati di registrazione, numero documento | facoltativo | **Obbligatorio** | §2, p. 9 |
| `codice_registro` | Dati di registrazione, codice registro | facoltativo | **Obbligatorio se** il tipo registro è protocollo o repertorio/registro | §2, p. 9 |
| `soggetti` (nuovo, struttura) | Soggetti: ruolo, tipo di soggetto (PF, PG, PAI, PAE, AS, SW) e dati per tipo | non strutturato (`soggetto_destinatario` elenco) | **Struttura** con ruoli fissi dello schema, tipo ammesso per ruolo, campi obbligatori per tipo (cognome e nome per PF, denominazione per PG/PAE/AS, codice IPA per PAI, sistema per SW); sempre il soggetto che effettua la registrazione e almeno un autore o un mittente | §2, pp. 10–13; schema pp. 29–32 |
| `soggetto_produttore_id` / `_nome` | — | obbligatori (minimi del servizio, RGL-101) | Restano metadati minimi del servizio (non dell'Allegato 5) | — |
| `oggetto` | Chiave descrittiva, oggetto | obbligatorio | Confermato | §2, p. 14 |
| `parole_chiave` | Chiave descrittiva, parole chiave | elenco | Confermato, **al più 5** | §2, p. 15 |
| `numero_allegati` | Allegati, numero | calcolato | Confermato (0–9999); l'indice degli allegati è il FileGroup dell'IPdA | §2, p. 15 |
| `classificazione` (+ `classificazione_descrizione`, `piano_classificazione`) | Classificazione: indice, descrizione, piano | indice facoltativo | Confermati facoltativi; aggiunti descrizione e URI del piano | §2, p. 16 |
| `riservato` | Riservato | obbligatorio, da riscontrare | **Confermato obbligatorio** (vero/falso) | §2, p. 17 |
| `formato` | Identificativo del formato | calcolato | Confermato (dal contenuto, §8.15) | §2, p. 18 |
| `prodotto_software_*` | Prodotto software (nome, versione, produttore) | facoltativi | Obbligatori «quando rilevabili»: restano facoltativi (condizione non verificabile dal servizio) | §2, p. 18 |
| `firmato_digitalmente`, `sigillato_elettronicamente`, `marcatura_temporale` | Verifica | obbligatori sempre | **Obbligatori solo per le modalità a e b** | §2, pp. 19–20 |
| `conformita_copie_immagine` | Verifica, conformità copie per immagine | facoltativo | **Obbligatorio per la modalità b** | §2, pp. 19–20 |
| `fascicolo_id` | Identificativo dell'aggregazione | facoltativo | Confermato facoltativo | §2, p. 20 |
| `id_documento_primario` | Identificativo del documento primario | facoltativo | Obbligatorio se esiste un documento primario: facoltativo (non verificabile) | §2, p. 20 |
| `nome_documento` | Nome del documento | facoltativo | **Obbligatorio, calcolato**: se manca il servizio usa il nome del file | §2, p. 21 |
| `versione_documento` | Versione del documento | facoltativo | **Obbligatorio** | §2, p. 21 |
| `tracciature_modifiche` (nuovo, struttura) | Tracciature modifiche: tipo (annullamento, rettifica, integrazione, annotazione), autore, data e ora, versione precedente | assente | **Struttura obbligatoria se la versione non è «1» o se il documento è annullato** (revisione R11: aggiunta la condizione dell'annullamento con il campo del servizio `documento_annullato`) | §2, p. 22; schema p. 38 |
| `tempo_conservazione` | Tempo di conservazione | intero | Confermato facoltativo, **1–9999** (9999 = permanente) | §2, p. 23 |
| `note` | Note | facoltativo | Confermato | §2, p. 23 |
| `data_documento`, `lingua` | — | servizio | Metadati del servizio, non dell'Allegato 5 | — |

Revisione indipendente di R11: riscontro a campione su 23 voci senza difetti di obbligatorietà o valori; aggiunta la condizione dell'annullamento per le tracciature; riferimenti di pagina corretti secondo la revisione (§2 p. 15 e pp. 19–20, §3 pp. 45–48, §4 pp. 75, 76, 82–84). Le pagine vengono dallo stesso tipo di estrattore: vanno ricontrollate a vista prima della firma. La v2 è stata corretta prima del rilascio, perché non era mai stata adottata (VD-P-112).

Non controllati dal servizio (indicati nella descrizione del campo): il mittente obbligatorio per i documenti protocollati, l'operatore obbligatorio quando ci sono tracciature, i soggetti PAI/PAE solo in entrata o in uscita.

**R16 (VD-C-158, VD-P-171) — versione 3 del documento informatico** (`documento_informatico.v3.json`, adozione esplicita con `PROFILO_TIPOLOGIA_VERSIONE = 3`, campi della v2 invariati): gli obblighi qui sopra, e l'elemento «Annullamento» fra le tracciature di un documento annullato, sono **controllati** con le `regole_incrociate` del motore dei profili (condizioni fra un campo di primo livello e gli elementi di un elenco):

| Regola | Condizione | Obbligo | Riferimento |
|---|---|---|---|
| `MITTENTE_DOCUMENTI_PROTOCOLLATI` | `tipo_registro` = «Protocollo Ordinario/Protocollo Emergenza» | un soggetto con ruolo Mittente | §2, pp. 10–13 |
| `OPERATORE_CON_TRACCIATURE` | `tracciature_modifiche` presenti | un soggetto con ruolo Operatore | §2, pp. 10–13, p. 22 |
| `PAI_PAE_SOLO_ENTRATA_USCITA` | un soggetto PAI o PAE | `tipologia_flusso` E o U | §2, pp. 10–13 |
| `ANNULLAMENTO_TRACCIATO` | `documento_annullato` = true | una tracciatura di tipo Annullamento | §2, p. 22 |

Nessun obbligo nuovo: solo quelli già scritti in questo riscontro. Restano non verificabili dal servizio il prodotto software «quando rilevabile» e il documento primario «se presente»; il documento amministrativo informatico (§3) e l'aggregazione (§4) non hanno ancora regole incrociate (VD-C-180).

## Documento amministrativo informatico (§3) — v2 estende il documento informatico v2

| Campo | v1 | Esito → v2 | Riferimento |
|---|---|---|---|
| `tipo_registro` | come il documento informatico | **Senza «Nessuno»**: solo protocollo o repertorio/registro | §3, p. 43; schema pp. 61–62 |
| `codice_registro` | obbligatorio | Confermato obbligatorio | §3, p. 44 |
| `segnatura` | facoltativo | **Obbligatoria per i documenti protocollati** | §3, p. 40 |
| `soggetti` | `codice_ipa`, `aoo`, `codice_ruo`, `responsabile_procedimento`, `operatore` separati | **Struttura** con i ruoli del §3 (amministrazione che effettua la registrazione sempre, almeno autore o mittente, RUP con cognome, nome, codice IPA e UOR); i campi separati della v1 non servono più | §3, pp. 45–48; schema pp. 63–66 |
| `classificazione`, `classificazione_descrizione` | indice obbligatorio | Indice e **descrizione obbligatori** | §3, p. 52 |
| `fascicolo_id` | facoltativo | **Obbligatorio** | §3, p. 56 |
| `tracciature_modifiche` | assente | Come il documento informatico | §3, p. 58 |
| Tutti gli altri | — | Come il documento informatico (§3 li ripete) | §3, pp. 40–59 |

## Aggregazione documentale (§4) — `aggregazione_documentale` v2

| Campo | v1 | Esito → v2 | Riferimento |
|---|---|---|---|
| `identificativo_aggregazione` | obbligatorio | Confermato | §4, p. 74 |
| `tipo_aggregazione` | Fascicolo, Serie Documentale, **Serie Fascicoli** | **Corretto**: Fascicolo, Serie Documentale, **Serie Di Fascicoli** | §4, p. 75; schema pp. 37–38 |
| `tipologia_fascicolo` | facoltativo | Valori confermati (affare, attività, persona fisica, persona giuridica, procedimento amministrativo); **obbligatoria per i fascicoli** | §4, p. 76 |
| `soggetti` | `codice_ipa`, `amministrazioni_partecipanti`, `responsabile_procedimento` | **Struttura** con i ruoli descritti (amministrazione titolare, amministrazioni partecipanti, assegnatario, intestatari PF e PG, RUP); **resta da riscontrare** (stringhe dello schema a p. 89 e obbligo per ruolo non letti) | §4, pp. 76–79 |
| `assegnazione` (nuovo) | assente | **Struttura obbligatoria per i fascicoli**: tipo (per competenza, per conoscenza), assegnatario, inizio; fine facoltativa | §4, p. 80 |
| `data_apertura` | obbligatoria | Confermata | §4, p. 81 |
| `classificazione`, `classificazione_descrizione`, `piano_classificazione` | indice obbligatorio | Indice e descrizione obbligatori, piano facoltativo | §4, p. 82 |
| `progressivo` | testo facoltativo | **Numero obbligatorio** | §4, p. 82 |
| `oggetto`, `parole_chiave` | — | Confermati (al più 5 parole chiave) | §4, pp. 82–83 |
| `data_chiusura` | facoltativa | Obbligatoria alla chiusura: facoltativa al versamento | §4, p. 84 |
| `procedimento_amministrativo`, `id_aggregazione_precedente`, `indice_documenti`, `tempo_conservazione`, `note` | da riscontrare | **Da riscontrare** (testo oltre p. 84 non estratto) | §4, oltre p. 84 |

## Profili non disciplinati dall'Allegato 5

| Profilo | Esito | Base della v2 |
|---|---|---|
| `fattura` | Profilo proprio del conservatore; campi propri della v1 conservati (fonti DM MEF 17/6/2014 e specifiche FatturaPA indicate in `norm_ref`, non riscontrate in R11) | documento informatico v2 |
| `registro_protocollo` | Profilo proprio del conservatore; tolti dalla v2 i campi ora nei Soggetti e nei dati di registrazione (`codice_ipa`, `aoo`, `codice_registro`, responsabile della gestione documentale) | documento amministrativo informatico v2 |
| `contratto` | Profilo proprio del conservatore (D.Lgs. 36/2023 indicato in `norm_ref`, non riscontrato) | documento informatico v2 |

## Cosa resta

- Riscontro del §4 oltre p. 84 e dello schema delle aggregazioni (p. 89).
- Verifica a vista del responsabile e decisione `VD-N` prima di attivare le v2 per un ente.
- Mappatura definitiva nell'IPdA con l'XSD UNI 11386 (D03, VD-C-071): oggi i metadati escono con i nomi dell'Allegato 5 in un contenitore provvisorio (`MetadatiAllegato5`).
