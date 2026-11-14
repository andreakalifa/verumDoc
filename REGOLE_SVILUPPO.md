# REGOLE DI SVILUPPO — VerumDoc

> Servizio di conservazione digitale a norma (Linee guida AgID sulla formazione, gestione e conservazione dei documenti informatici, in vigore dal 1/1/2022, e allegati; Regolamento sui criteri per la fornitura dei servizi di conservazione, Det. AgID 455/2021; UNI 11386:2020 SInCRO; ISO 14721 OAIS; eIDAS; GDPR).
>
> Documento vivente. Si aggiorna ogni volta che emergono nuove regole. Sostituisce `Regole_Sviluppo_VerumDoc.docx` e `Regole_Sviluppo_VerumDoc_v4_2.docx`, che restano nello storico (§31) e si eliminano dal repository con lo script di pulizia.
>
> Ultima modifica: 2026-11-14 — R18, apertura open source: licenza EUPL-1.2, intestazioni SPDX obbligatorie nei file nuovi, controllo della pubblicazione, §2 «Regole per chi sviluppa» (§31). Precedente: 2026-11-01 — R17, revisione e audit: conferma valida per un solo giro, esenzioni della migrazione limitate al giro, campionamento non deciso dall'esterno, firma sulla versione leggibile, console dai permessi effettivi (§31). R17: decisioni irreversibili confermate e firmate da un servizio distinto e riverificate da chi esegue, deroghe per ente solo del conservatore con voce riservata, test indipendenti dalla data, rinumerazione all'unione e vettori canonici condivisi (§31). Unione di R15 e R16 (§31). R16: segreti monouso della persona solo con il suo token, punto d'aggancio generico dei documenti da firmare, limiti condivisi atomici in Redis, consegne senza doppioni con identificativo deterministico, versione per ambito dei parametri che autorizzano, politiche a livelli della federazione, tracing chiuso, lint senza cambiare l'AST (§31). R15: documentazione di conformità generata dalla configurazione, segnaposto dichiarati, versioni immutabili nel WORM (§31). R14: lavori che falliscono sempre in uno stato di errore esplicito con allarme e ripresa, vincoli di concorrenza nel database, osservabilità e cataloghi controllati in `make verifiche`; revisione: confronto di coerenza sempre fra checkpoint, formato fissato nel record di costruzione, indici CONCURRENTLY invalidi ricreati (§31). R13b: canali esterni con segreti legati agli host, prove esterne accettate solo sui dati firmati, rotazione a doppia firma, avvisi personali separati; revisione: struttura diretta delle ricevute firmate, chiavi di federazione dal livello superiore per impronta, dati di prova fittizi (§31). R12a: documenti autorizzativi legati alla pratica, segreti sigillati nelle code, fencing token, lavori esterni in processi terminabili, letture legate alla versione, pinning, rinnovo WORM del registro; revisione: riuso per impronta, diritti al solo gruppo del servizio, scadenza in ogni lettura, pulizia ripetuta dal lavoratore (§31). R11: dati normativi versionati con eredità a versione fissata, impronte delle versioni pubblicate, adozione esplicita per classe, livello inferiore solo restrittivo, conformità solo con schema ufficiale registrato (§31). R09: lavoro periodico con lease per passo, quattro occhi riverificati dal lavoratore, fatti di sistema provati prima di firmarli, mai in riduzione (§31). R08: verificatori e riconoscitori intercambiabili, regole del contenuto nella verifica, correzioni di sicurezza riverificate (§31). R04: catena, registri protetti, permessi minimi (§7.1, §31). R03: canali, errori unici, streaming, elenchi a cursore (§7.1, §31). Prima stesura del 2026-10-20: unione delle regole VerumDoc esistenti (ciclo PLAN→BUILD→VERIFY→DOCUMENT, Git, standard Python, STATO_PROGETTO, criteri di fase, regole per Claude, linee guida database v4.2) con le regole trasferibili da un progetto precedente (regola prioritaria di allineamento documentazione, checklist ordinata, test permanenti, perimetri fail-closed, audit automatico, plan/apply, regole come dati, frontend a token, registri criticità e decisioni), adattate a FastAPI, Kafka, PostgreSQL, S3 WORM e React.

---

## Indice

**Parte A — Processo**
- [0. Regola prioritaria: documentazione e manuale allineati a ogni rilascio](#0-regola-prioritaria-documentazione-e-manuale-allineati-a-ogni-rilascio)
- [1. Ciclo di lavoro per sessione](#1-ciclo-di-lavoro-per-sessione)
- [2. Regole per chi sviluppa](#2-regole-per-chi-sviluppa)
- [3. Consegna: ZIP differenziale, changelog, file eliminati](#3-consegna-zip-differenziale-changelog-file-eliminati)
- [4. Git, commit, versioni](#4-git-commit-versioni)
- [5. Criteri di avanzamento di fase e di rilascio](#5-criteri-di-avanzamento-di-fase-e-di-rilascio)
- [6. Gestione degli imprevisti e dei cambi normativi](#6-gestione-degli-imprevisti-e-dei-cambi-normativi)

**Parte B — Architettura e dominio**
- [7. Mappa dei servizi e struttura del codice](#7-mappa-dei-servizi-e-struttura-del-codice)
- [8. Predisposizione al cambio normativo: regole come dati](#8-predisposizione-al-cambio-normativo-regole-come-dati)
- [9. Una funzione per operazione, una fonte di verità](#9-una-funzione-per-operazione-una-fonte-di-verità)
- [10. Immutabilità e WORM](#10-immutabilità-e-worm)
- [11. Stati, transizioni e cancelli](#11-stati-transizioni-e-cancelli)
- [12. Concorrenza, idempotenza, eventi](#12-concorrenza-idempotenza-eventi)
- [13. Anteprima e applicazione (plan/apply)](#13-anteprima-e-applicazione-planapply)
- [14. Multi-tenant e permessi: fail-closed](#14-multi-tenant-e-permessi-fail-closed)
- [15. Registro attività (audit) e log di conservazione](#15-registro-attività-audit-e-log-di-conservazione)

**Parte C — Tecnica**
- [16. Database e migrazioni](#16-database-e-migrazioni)
- [17. Standard Python](#17-standard-python)
- [18. Errori: mai un 500](#18-errori-mai-un-500)
- [19. Sicurezza](#19-sicurezza)
- [20. Integrazioni esterne (TSA, firma, OCSP/CRL, IPA, PEC)](#20-integrazioni-esterne-tsa-firma-ocspcrl-ipa-pec)
- [21. Notifiche](#21-notifiche)
- [22. Job periodici e consumer](#22-job-periodici-e-consumer)
- [23. Test](#23-test)
- [24. Qualità del codice e pulizia tecnica](#24-qualità-del-codice-e-pulizia-tecnica)

**Parte D — Frontend**
- [25. Frontend: architettura e regole](#25-frontend-architettura-e-regole)
- [26. Sistema grafico](#26-sistema-grafico)

**Parte E — Esercizio e documentazione**
- [27. Distribuzione, backup, ambienti](#27-distribuzione-backup-ambienti)
- [28. Documentazione generata e cataloghi](#28-documentazione-generata-e-cataloghi)
- [29. Registri: criticità, decisioni, debito](#29-registri-criticità-decisioni-debito)
- [30. Naming](#30-naming)
- [31. Storico regole](#31-storico-regole)

### Indice per tema (rimandi rapidi)

| Tema | Sezioni |
|---|---|
| Norma che cambia senza toccare il codice | 8, 10.4, 13, 16.6 |
| Prova di conformità di ogni pacchetto | 8.3, 11.6, 15 |
| Dati conservati che non si toccano mai | 10, 16.5, 16.7 |
| Isolamento fra clienti | 14, 19.6, 23.4 |
| Doppioni, ritentativi, crash a metà | 12, 22 |
| Operazioni massive e normative (scarto, riversamento, cambio piano) | 13, 11, 15.6 |
| Una sola implementazione | 9, 25.3 |
| Interfaccia accessibile e coerente | 25, 26 |
| Rilascio | 0, 3, 5 |

---

# PARTE A — PROCESSO

## 0. Regola prioritaria: documentazione e manuale allineati a ogni rilascio

> **Priorità massima.** Prevale su tutte le altre regole in caso di conflitto di tempo o di scope. Una consegna con codice corretto e documentazione non allineata **non è una consegna valida**.
> Per un conservatore la documentazione non è un accessorio: il **Manuale di conservazione** è un documento con valore normativo, consegnato ai clienti e verificato in sede di qualificazione e ispezione. Se il manuale descrive una funzione che il codice non ha, il servizio dichiara il falso.

### 0.1 Principio
Ogni rilascio (anche di un solo file) lascia **tutti** i documenti di progetto allineati allo stato rilasciato: nessuna funzione descritta come disponibile se non lo è, nessun «✅» su ciò che non è provato da un test, nessun contatore non ricalcolato, nessun riferimento rotto, nessuna data vecchia.

*Motivo (audit del 20/10/2026):* README e STATO_PROGETTO dichiaravano il progetto «sostanzialmente completo» con fix «✅» smentiti dal codice (auth onboarding, login con API key, DLQ, firma, SInCRO, Object Lock). Da questa stesura un «✅» richiede il nome del test che lo dimostra.

### 0.2 Documenti da allineare a ogni rilascio

| Documento | Cosa aggiornare | Quando |
|---|---|---|
| `STATO_PROGETTO.md` | Snapshot, stato per fase, debito tecnico, prossima azione | Sempre |
| `docs/manuale/` (Manuale di conservazione) | Sezioni che descrivono il comportamento toccato; allegati generati (§28.3) | Se cambia un comportamento verso PdV, PdA, PdD, ruoli, sicurezza, formati, metadati |
| `docs/catalogo/` | Catalogo endpoint, route frontend, topic, codici d'errore, impostazioni, regole: **rigenerati**, mai a mano | Sempre se cambiano API, route, topic, registri |
| `docs/registro/criticita.md` | Chiudere le risolte (stato + risoluzione + rilascio), aggiungere le nuove, ricalcolare i totali | Sempre |
| `docs/registro/decisioni.md` | Nuove decisioni (normative `VD-N`, di prodotto `VD-P`), rilascio di realizzazione | Quando una decisione nasce o si realizza |
| `GUIDA_FUNZIONALITA.md` | Funzionalità per ruolo, «Ultima modifica» | Se cambia una funzionalità o un permesso |
| `REGOLE_SVILUPPO.md` | Nuovi pattern, storico (§31), «Ultima modifica», indice | Se emergono convenzioni o lezioni |
| `changelog/CHANGELOG-AAAA-MM-GG-<tema>.md` | Un file per rilascio: funzionalità, correzioni, conformità normativa, dismissioni, file eliminati, test eseguiti | Sempre |
| `README.md` | Installazione, dipendenze, avvio | Quando pertinente |
| `docs/design/` | Sistema grafico, schede di pagina, decisioni di design `VD-DR` | Se cambia una pagina o un componente |

Al 20/10/2026 non esistevano ancora `docs/manuale/`, `docs/catalogo/`, `GUIDA_FUNZIONALITA.md` e il documento di design: nascono con il primo contenuto (il manuale generato in Fase 5, i cataloghi dal R03, generati da `tools/docs/genera_cataloghi.py` dal R14, la guida con il redesign). Il sistema grafico è descritto in `frontend/DESIGN.md` (R12c).

### 0.3 Assenza di refusi — cosa verificare
1. **Testo:** ogni frase che descrive il comportamento toccato è riletta e corretta in *tutti* i documenti che la contengono (`grep` sul nome della funzione, dell'endpoint, del topic e degli ID `VD-C-nnn`).
2. **Numeri:** totali di criticità, test (letti dall'esecuzione, mai copiati), endpoint, topic coincidono con i dati reali.
3. **Riferimenti:** ogni codice citato esiste nel registro; ogni link punta a un'ancora esistente; ogni file citato esiste.
4. **Stati:** nessuna criticità risolta resta aperta; nessuna funzione rinviata è descritta come fatta; nessun «in arrivo» per cose già rilasciate; nessun «✅» senza test.
5. **Date:** «Ultima modifica» di GUIDA, REGOLE e STATO uguale alla data del changelog.
6. **Manuale:** ogni affermazione tecnica del manuale (algoritmi, formati, storage, firma, marca, backup) corrisponde a codice esistente e configurato; le parti non ancora vere stanno in una sezione «Funzioni pianificate», non nel testo corrente.

### 0.4 Checklist di rilascio (obbligatoria, in quest'ordine)
```bash
# 1. Qualità codice (ruff e mypy con le versioni fissate nel repository)
ruff check services/ libs/ sdk/ tools/
mypy --strict libs/verumdoc_rules libs/shared
cd frontend && npm run lint && npm run typecheck && cd ..

# 2. Test: test nuovi + servizi toccati + suite permanente di integrità (tests/integrity)
#    Suite intera obbligatoria se il rilascio tocca libs/, modelli condivisi, migrazioni,
#    middleware di autenticazione/tenant, schemi degli eventi Kafka (§23.2).
pytest services/<servizio_toccato> tests/integrity -q

# 2b. Test su PostgreSQL vero, MinIO e Kafka (testcontainers) per i servizi con lock,
#     letture in blocco, consumer o S3 (§23.3)
pytest -m "postgres or kafka or s3" services/<servizio_toccato>

# 3. Rigenerazione cataloghi (endpoint, route, topic, errori, impostazioni, regole, permessi)
python tools/docs/genera_cataloghi.py

# 4. Verifica coerenza documentazione (deve terminare con 0 problemi)
python tools/docs/verifica_documentazione.py

# 5. Frontend: build e test end-to-end e di accessibilità (foto a 390/1366 px solo a richiesta, §25.13)
cd frontend && npm run build && npm run e2e && cd ..
```
**Stato degli strumenti (R14, 31/10/2026):** `tests/integrity` (documentazione, cataloghi, osservabilità), `tools/docs/genera_cataloghi.py` (impostazioni, topic, metriche e allarmi; con `--controlla`), `tools/docs/verifica_documentazione.py` (collegamenti interni, file, criticità e decisioni citati) e i test end-to-end del frontend esistono e girano in `make verifiche` (gli ultimi nella corsa del frontend); `tools/confronta_regole.py` dalla Fase 1. **R15 (01/11/2026):** `tools/conformita/genera_documenti.py --controlla` (manuale di conservazione con gli allegati, piano della sicurezza, `GUIDA_FUNZIONALITA.md`, route del frontend, matrice ruoli × permessi, fotografia del repository per la console) e il controllo dei segnaposto dei documenti generati in `verifica_documentazione.py` girano in `make verifiche`. Restano a mano: catalogo delle regole e classificazione dei modelli (§28.1).

Poi rilettura manuale dei punti 0.3.1, 0.3.4 e 0.3.6, che nessuno script può garantire. Se un passo fallisce non si va avanti.

**Nei rilasci che cambiano regole di validazione, calcolo delle impronte, generazione dell'IPdA o selezione allo scarto** si lancia anche `python tools/confronta_regole.py` (§8.11): deve finire con «differenze: 0» oppure con differenze approvate in una decisione `VD-N`.

---

## 1. Ciclo di lavoro per sessione

### 1.1 PLAN → BUILD → VERIFY → DOCUMENT
Ogni sessione segue quattro passi, senza saltarne nessuno.

| Passo | Cosa si fa | Uscita obbligatoria |
|---|---|---|
| PLAN | Leggere STATO_PROGETTO, il piano e le regole; elencare i file da creare o modificare; individuare dipendenze, rischi e **decisioni che spettano al committente** (§2.3) | Elenco file + ordine concordato + domande aperte |
| BUILD | Un file alla volta, con i suoi test. Nessun file lasciato a metà. Nessuna funzionalità non pianificata | Codice + test verdi |
| VERIFY | Checklist §0.4 per la parte toccata; smoke test locale | Esito dei comandi (copiato dall'esecuzione) |
| DOCUMENT | Allineamento §0.2; STATO_PROGETTO aggiornato | Documenti allineati + ZIP (§3) |

### 1.2 Granularità
Una sessione affronta **una** sotto-fase del piano: abbastanza grande da lasciare il sistema funzionante, abbastanza piccola da essere verificata per intero. «Tutta la Fase 1» è troppo; «solo il main.py senza auth» lascia il sistema rotto.

### 1.3 Ogni sessione termina con il sistema funzionante
Compila, test della sotto-fase verdi, servizio locale che risponde. Non si chiude con «il test lo finisco domani».

### 1.4 Inizio sessione
1. Leggere `STATO_PROGETTO.md`, `REGOLE_SVILUPPO.md` e il registro decisioni: è la fonte di verità, non la memoria.
2. Verificare che ciò che funzionava funzioni ancora (`make health`, test permanenti). Se qualcosa è rotto si sistema **prima**.
3. Dichiarare in una riga l'obiettivo della sessione e l'elenco dei file.

### 1.5 Una sotto-fase più lunga del previsto
Se al 60% del tempo si è al 30% del lavoro, si ridefinisce il confine: si completa e verifica ciò che è fatto, si aggiorna STATO_PROGETTO, il resto diventa la sessione successiva. Meglio incompleto e documentato che «quasi completo» e non documentato.

---

## 2. Regole per chi sviluppa

Valgono per chiunque scriva codice per VerumDoc: persone e assistenti di programmazione automatici.

### 2.1 Sempre
- Leggere STATO_PROGETTO, le regole e il registro decisioni prima di scrivere codice; in caso di dubbio su cosa esiste, si guarda il codice, non la memoria.
- Dichiarare cosa si fa nella sessione, in una frase, prima di farlo.
- Scrivere i test nella stessa consegna del codice, senza chiedere conferma.
- Consegnare come da §3.

### 2.2 Durante l'implementazione
- Un file alla volta, completo (codice + test) prima del successivo.
- Nessuna funzionalità non pianificata: le idee vanno in STATO_PROGETTO (sezione «Idee»), non nel codice.
- Nessuna dipendenza nuova senza motivo, licenza verificata (§20.8) e versione bloccata.
- Se una sotto-fase richiede più del previsto, dirlo subito.

### 2.3 Fermarsi e chiedere (mai decidere da soli)
- **Permessi**: chi può fare cosa. Un cambio di permessi rispetto alla guida o ai test è una decisione, non un fix (§14.1).
- **Interpretazione della norma**: ogni scelta che interpreta le Linee guida, gli allegati o il regolamento (es. quali metadati sono obbligatori per una tipologia, quando una firma si considera valida, termini di versamento) è una **decisione normativa `VD-N`** firmata dal responsabile del servizio di conservazione.
- **Schema del database** e formati di scambio (IPdA, RdV, PdD, eventi).
- **Design**: direzione grafica, nuovi pattern d'interazione (`VD-DR`).
- **Scelte architetturali** non pianificate (due approcci possibili).

### 2.4 Mai
| Comportamento vietato | Perché | Invece |
|---|---|---|
| Codice con TODO e funzioni vuote | Sistema incoerente | Completare o non iniziare |
| Stub che risponde come se fosse reale (firma finta che dice «eIDAS attiva», marca = `datetime.now()`) | Il manuale e i clienti credono a una garanzia che non c'è | Uno stub dichiara se stesso: `production_ready=false`, stato «non disponibile» in UI, blocco all'avvio in produzione (§19.4) |
| Ignorare un test rosso | Debito invisibile | Capire e correggere |
| Test che cercano stringhe nel sorgente | Non provano il comportamento | Test che eseguono il codice (§23.1) |
| Segreti nel codice o nel repository | Irreversibile | Solo variabili d'ambiente / secret manager |
| Dire «funziona» senza test | L'ottimismo non compila | Prima il test |
| Segnare «✅» nella documentazione senza test | Documentazione falsa (§0.1) | Citare il test |
| Saltare VERIFY | Debito invisibile | Sempre |

### 2.5 Il documento prevale sulla memoria
Vale il documento, non la memoria: se qualcuno (o un assistente automatico) dice qualcosa che contraddice STATO_PROGETTO, il piano o il registro decisioni, prevalgono i documenti.

---

## 3. Consegna: ZIP differenziale, changelog, file eliminati

### 3.1 Cosa si consegna
- Alla fine di ogni richiesta si consegnano **solo i file modificati o creati in quella richiesta**, completi (non diff), nella **struttura del monorepo** a partire dalla radice: si estrae lo ZIP sopra il progetto e ogni file è al suo posto.
- Mai file di richieste precedenti già consegnati, salvo richiesta esplicita («dammi tutto»).
- Lo ZIP contiene sempre i documenti aggiornati al §0.2.
- In caso di dubbio, meno file piuttosto che troppi.

### 3.2 Nome dello ZIP
`R<nn>_<tema>.zip` (numero di rilascio progressivo, mai riusato), es. `R01_pulizia_e_regole.zip`.

### 3.3 Contenuto obbligatorio dello ZIP
- `README_RILASCIO.md` in radice: cosa contiene, come si estrae, comandi da lanciare (migrazioni, script, test), commit e push, cosa verificare dopo.
- `changelog/CHANGELOG-AAAA-MM-GG-<tema>.md` con le sezioni: Funzionalità, Correzioni, Conformità normativa, Dismissioni, **File eliminati** (con cosa li sostituisce), Test eseguiti (numeri copiati dall'esecuzione), Decisioni realizzate, Scelte di sviluppo da confermare, Limiti noti.
- Se il rilascio elimina file: lo script `tools/pulizia/elimina_<tema>.sh` (e `.ps1` per Windows) idempotente, con modalità di prova (`--prova` / `-WhatIf`) che stampa cosa toglierebbe, e l'elenco nel changelog. Gli script già applicati non restano nel repository dopo il rilascio successivo.
- Topic Kafka ed endpoint dismessi si dichiarano con data e sostituto.

### 3.4 Frammenti di codice nella discussione
Ammessi solo: un comando shell, 3-5 righe di pseudo-codice per spiegare una scelta, checklist e contenuto di documenti. Il codice applicativo sta nello ZIP.

---

## 4. Git, commit, versioni

### 4.1 Branch
| Branch | Uso | Regole |
|---|---|---|
| `main` | Solo codice funzionante | Nessun push diretto; merge da PR con CI verde |
| `feature/R<nn>-<servizio>-<descrizione>` | Sviluppo | Una per sotto-fase |
| `hotfix/VD-C-<nnn>` | Bug critico in produzione | Si ferma tutto, si risolve, si riprende |

### 4.2 Commit (Conventional Commits)
`{tipo}({scope}): {descrizione imperativa}`; corpo = perché; footer `Refs: R<nn>, VD-C-nnn, VD-N-nn`.
Tipi: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `perf`, `norm` (adeguamento a una norma o decisione normativa).
Scope: nome del servizio o lib (`gateway`, `validation`, `aip-builder`, `hashchain`, `signature`, `distribution`, `notification`, `onboarding`, `dashboard-api`, `rules`, `shared`, `frontend`, `infra`, `db`).
Un commit = una cosa sola: se nel messaggio c'è «e», sono due commit.

### 4.3 Tag
`v<fase>.<sottofase>.<patch>`; mai su codice non verificato dalla checklist di fase. Il tag di produzione richiede i criteri del §5.

### 4.4 Pull request
Titolo come il commit principale; descrizione con cosa, come si prova, riferimento al piano; massimo ~400 righe di codice cambiato (test esclusi); self-review; CI verde senza eccezioni. Etichetta `refactor-only` → la CI verifica che nessun test esistente sia modificato (§23.8).

---

## 5. Criteri di avanzamento di fase e di rilascio

### 5.1 Principio
Si passa di fase solo con **tutti** i criteri soddisfatti e dimostrati da un test o da un'evidenza archiviata. «Quasi tutto funziona» non basta. Se un criterio non è soddisfatto, si pianifica una sessione dedicata e non si aggiunge codice della fase successiva.

### 5.2 Criteri minimi di «pronto per un cliente reale» (da dettagliare nel piano)
1. Pipeline completa provata end-to-end su ambiente pulito (job CI «installazione da zero», §27.5): versamento → verifica → PdA con IPdA **valido contro lo schema UNI SInCRO**, firmato e marcato da TSA qualificata → RdV → esibizione (PdD) → verifica d'integrità.
2. Object Lock in modalità COMPLIANCE verificato **dal servizio all'avvio** (§19.4), non solo in Terraform.
3. Nessun endpoint senza autenticazione verificata (test permanente §23.4).
4. Isolamento fra clienti provato (§14, test con due tenant su ogni endpoint).
5. Manuale di conservazione generato e coerente con il codice (§0.3.6).
6. Backup e ripristino provati e registrati (§27.6).
7. Zero criticità P0/P1 aperte nel registro.

---

## 6. Gestione degli imprevisti e dei cambi normativi

### 6.1 Bug critico scoperto durante lo sviluppo
STOP del codice nuovo → valutazione dell'impatto → **test che riproduce il bug prima della correzione** → correzione → registro criticità con causa e correzione → si riprende.

### 6.2 Cambio normativo (nuove Linee guida, aggiornamento di un allegato, circolare)
1. Si apre una decisione `VD-N-nn` con norma, data di efficacia, impatto.
2. Si classifica l'impatto:
   - **solo parametri** (es. nuovo formato ammesso, nuovo metadato raccomandato) → nuova versione del registro o del profilo (§8), nessun rilascio di codice;
   - **nuova regola** di validazione o nuovo passo della pipeline → una funzione + una voce del catalogo + test dei tre effetti (§8.6), con predefinito che non cambia nulla per i clienti esistenti finché la data di efficacia non arriva;
   - **formato di scambio** (IPdA, RdV, PdD) → nuova versione del formato con il vecchio che resta leggibile (§8.9), sotto-fase dedicata.
3. I PdA già conservati **non si modificano mai** (§10): diventano oggetto di un report «conservati prima della regola X» (§8.10).

---

# PARTE B — ARCHITETTURA E DOMINIO

## 7. Mappa dei servizi e struttura del codice

### 7.1 Servizi attuali
| Servizio | Responsabilità (OAIS) | Note sullo stato al 20/10/2026 |
|---|---|---|
| `gateway` | Ingresso: autenticazione, versamento PdV (caso d'uso unico per tutti i canali), sessioni di caricamento, stato, PDND, adattatori di compatibilità | Unico proprietario delle migrazioni Alembic |
| `validation` | Verifica del PdV (passi della pipeline) | Consumer Kafka |
| `aip-builder` | Generazione PdA e IPdA, scrittura su storage WORM | Consumer Kafka |
| `hashchain` | Registro delle impronte a due livelli, checkpoint, prove, RdV definitivo, verifica continua dell'integrità | R04: non consuma più Kafka; sequenziatore unico con lease; processo `hashchain-verifica` |
| `signature` | Firma qualificata e marca temporale (adattatori) | Oggi stub senza chiamanti |
| `distribution` | PdD, esibizione, export | |
| `notification` | Email, PEC, webhook, eventi per i clienti | R03: API `/api/v1/eventi` e `/api/v1/webhook`, lavoratore `notification-webhook` |
| `onboarding` | Clienti, utenti, API key, classi documentali, configurazione (bozze, anteprima, applica, proposte, validatore) | R05: API `/api/v1/configurazione` sul motore `libs/verumdoc_rules` |
| `dashboard-api` | Lettura per il portale, report, manuale, piano di cessazione | |
| `testimone` | Testimone indipendente del registro delle impronte: verifica e controfirma i checkpoint (protocollo ispirato a C2SP tlog-witness) | Nuovo in R04 (VD-P-39); DB, account e chiave propri; nessuna dipendenza dal DB del registro |
| `sftp-ingest` | Canale SFTP d'ingresso: server a chiave pubblica, lavoratore che versa tramite le sessioni di caricamento del gateway con un token di canale | Nuovo in R03 (VD-P-29); client del gateway, nessuna logica di versamento propria |
| `ciclo-vita` | Ciclo di vita del conservato: pratiche di rettifica, annullamento, scarto (iter, nulla osta, verbale, tombstone), riversamento in uscita e in ingresso, restituzione. **API** esposta (`/api/v1/ciclo-vita`: pratiche, allegati, transizioni, anteprime) e **lavoratore** separato (esecuzioni: cancellazione nel WORM, versamenti di rettifica e riversamento tramite il canale `CICLO_VITA`, PdD di migrazione, foglie del registro) | Nuovo in R05 (VD-P-06, VD-P-43). API e lavoratore con Deployment, ServiceAccount, ruolo IAM, Secret e login DB propri: solo il lavoratore ha `scarto` sui prefissi dei PdA, le credenziali di servizio e l'INSERT su `esiti_esecuzione`; nessuna migrazione propria (stanno nel gateway) |

Le responsabilità mancanti (verifiche periodiche di leggibilità, interoperabilità tra conservatori oltre al riversamento SInCRO, gestione dei contratti) si assegnano con una decisione di architettura prima di implementarle: nessun servizio nuovo nasce senza riga in questa tabella.

### 7.2 Struttura interna di ogni servizio (struttura obiettivo)
Nessun servizio la rispetta ancora: si adotta servizio per servizio quando lo si riscrive, e `import-linter` si introduce con un elenco del debito (§29.4).
```
services/<servizio>/
  app/
    main.py              # factory FastAPI o avvio consumer
    config.py            # Settings Pydantic (§19.4)
    api/                 # router: parsing, auth, mapping errori. Niente regole
    application/         # servizi applicativi: casi d'uso, transazioni
    domain/              # puro: regole, stati, modelli di dominio (niente I/O)
    infrastructure/      # repository SQLAlchemy, Kafka, S3, client esterni
  tests/
    unit/  integration/  conftest.py
  Dockerfile  pyproject.toml
```
I confini si fanno rispettare con `import-linter`: `domain` non importa `fastapi`, `sqlalchemy`, `aiokafka`, `boto3`, `httpx`; `api` non importa `infrastructure` direttamente.

### 7.3 Librerie condivise
- `libs/shared/` — modelli SQLAlchemy (unico punto di definizione delle tabelle), sessione DB, Kafka, osservabilità (da collegare), sicurezza. L'accesso a S3 con Object Lock è **da realizzare** (lo stub vuoto è stato rimosso in R01).
- `libs/verumdoc_rules/` (**realizzato**: v1 in R03, completo in R05 con accordi, override, profili per tipologia, configurazione e validatore; VD-P-01) — pacchetto **puro** con le regole di conservazione: registro impostazioni e catena di risoluzione, catalogo regole di validazione, stati e transizioni, calcolo della scadenza di conservazione, modello dell'IPdA. Solo libreria standard + Pydantic; **nessun orologio**: il tempo entra come argomento (`now: datetime`). Usato da tutti i servizi e da una CLI che i produttori possono lanciare prima di versare.
- `libs/sincro/` — **da realizzare** (oggi è un modulo vuoto): costruzione e validazione dell'IPdA secondo UNI 11386 (schema versionato, §8.9). Il builder attuale sta in `services/aip-builder` e non è conforme.

### 7.4 Separazione delle responsabilità
- **Router (`api/`)**: richiesta → servizio → risposta. Nessuna regola di dominio, nessuna query.
- **Servizi applicativi**: casi d'uso, transazioni, chiamate al dominio, pubblicazione eventi via outbox.
- **Dominio**: regole pure che restituiscono valori, motivi o piani; mai scritture.
- Schema fisso: **leggi → converti → regole pure → salva**.

---

## 8. Predisposizione al cambio normativo: regole come dati

> Obiettivo del committente: «un servizio a norma con le Linee guida AgID, architetturalmente predisposto a variare qualora variassero le norme». Questa sezione è il modo in cui lo si ottiene. Nessuna eccezione senza decisione `VD-N`.

### 8.1 Nessun valore normativo come costante nel codice
Un numero, una soglia, un elenco o un modo che deriva da una norma è una voce di un **registro delle impostazioni** con: codice, tipo (`int`, `bool`, `choice`, `codes`, `duration`), ambito ammesso, predefinito, **riferimento normativo** (`norm_ref`), data di efficacia.
Vietati nei validatori: `if size > 500_000_000`, `years = 10`, `ALLOWED = ["application/pdf", …]`.
Esempi di voci: `PDV_DIMENSIONE_MAX`, `FORMATI_AMMESSI`, `METADATI_OBBLIGATORI`, `CONSERVAZIONE_ANNI_PREDEFINITI`, `ALGORITMO_IMPRONTA`, `MODO_VERIFICA_FIRMA`, `TERMINE_VERSAMENTO_GIORNI`, `RINNOVO_MARCA_MESI_PRIMA_SCADENZA`, `VERIFICA_INTEGRITA_PERIODO_MESI`.

### 8.2 Una sola catena di risoluzione
Ogni parametro si risolve sempre con la stessa catena, in un solo punto (`verumdoc_rules.settings.effective(ctx, code) -> (valore, origine)`):

**override motivato sul singolo PdV → accordo di versamento → tipologia documentale → cliente (tenant) → profilo del conservatore → predefinito normativo.**

Nessun servizio conosce le tabelle dei livelli. L'interfaccia mostra sempre l'origine («10 anni — ereditato da: accordo di versamento v3»).

### 8.3 Fotografia alla creazione: ogni pacchetto porta le sue regole
Al versamento il PdV fotografa la **versione delle regole applicate** (impostazioni effettive con origine, versione del catalogo regole, versione del profilo metadati, versione del formato IPdA). Il PdA la eredita. È la **prova di conformità**: il pacchetto è stato trattato secondo le regole vigenti in quel momento. I cambi successivi delle regole non toccano i pacchetti esistenti.

### 8.4 Versioni, mai modifiche di ciò che è in uso
Un set di regole, un accordo di versamento, un profilo di metadati, uno schema già applicato a pacchetti esistenti **non si modifica**: si crea la versione successiva con data di decorrenza. Ogni pacchetto registra l'id della versione applicata.

### 8.5 Personalizzazione con motivo; valori bloccati dalla norma
Tre azioni da un solo servizio: «Personalizza» (valore + motivo obbligatorio), «Torna al valore ereditato», «Aggiorna dal livello superiore»; ognuna in audit. Il conservatore marca `locked` i valori imposti dalla norma (es. algoritmo d'impronta minimo, antivirus obbligatorio): non personalizzabili dai clienti.

### 8.6 Catalogo delle regole di validazione con effetto configurabile
Ogni regola è una funzione pura che restituisce un esito con codice stabile, registrata in un catalogo (codice, etichetta, ambito, effetto predefinito, `norm_ref`). Effetti: **blocca / avvisa / ignora**, scelti per accordo, con `locked` quando la norma impone il blocco. I servizi non scrivono controlli fissi: chiamano il catalogo e applicano l'effetto. Gli avvisi si salvano sul PdV e finiscono nel Rapporto di versamento.
Una regola nuova = una funzione + una riga del catalogo + un test per ciascuno dei tre effetti.

### 8.7 La pipeline di verifica è una sequenza di passi dichiarati
L'ordine e i parametri dei passi (antivirus → identificazione del formato per contenuto → verifica firme → estrazione metadati → validazione metadati contro il profilo → controllo impronte → controllo duplicati → classificazione) sono dati versionati per accordo. Un requisito nuovo = un passo nuovo con predefinito che non cambia nulla per chi c'è già. Ogni passo lascia il suo esito nel dettaglio del PdV (`verifica.passi[]`), riportato nel RdV.

### 8.8 Un nuovo regolamento è un nuovo modello, mai un ramo di codice
Profili di metadati per tipologia (documento informatico, documento amministrativo informatico, aggregazione documentale, fattura, registro di protocollo…) sono **dati versionati** (schema + mappatura), non classi Python. Un Allegato 5 aggiornato = un profilo `v2` con data di decorrenza. Vietato `if tipologia == "fattura"` fuori dai dati.

### 8.9 Formati di scambio versionati e intercambiabili
IPdA (UNI SInCRO), RdV, indice del PdD, eventi Kafka: ognuno ha una **versione di formato** e un costruttore/validatore registrato per versione (`sincro.builders["2020"]`). Lo schema XSD ufficiale sta nel repository (`libs/sincro/schemas/<versione>/`) e ogni documento generato **si valida contro lo schema prima di essere scritto**. Una nuova versione della norma aggiunge un costruttore, non modifica il vecchio; i pacchetti esistenti restano leggibili con il loro.

### 8.10 Errori sui dati nuovi, avvisi su quelli esistenti
Una regola che entra in vigore blocca i nuovi PdV dalla data di efficacia; i PdA già conservati **non diventano «non conformi» e non si toccano** (sono immutabili): compaiono in un report «conservati prima della regola X» per eventuali azioni (riversamento, integrazione con un nuovo pacchetto). Comando in sola lettura `verumdoc conformita --regola X --cliente Y`.

### 8.11 Prova sui dati reali prima di cambiare una regola
`tools/confronta_regole.py` in sola lettura riapplica versione vecchia e nuova ai PdV esistenti e riporta le differenze («12 PdV sarebbero ora rifiutati per formato non ammesso»). Senza «differenze: 0» o differenze approvate, il rilascio non si applica.

### 8.12 Elenchi di riferimento esterni come dati versionati
Registro formati (PRONOM ↔ Allegato 2), codici IPA, tipologie documentali, elenco dei prestatori qualificati (EU Trusted List), vocabolari dei metadati: tabelle con **periodo di validità** (`valido_dal`, `valido_al`), caricate da una fonte versionata con licenza tramite un comando idempotente. La verifica usa la riga valida alla data rilevante (data della firma, del versamento). Con un dato mancante l'operazione si ferma e dice quale.

### 8.13 Il livello superiore propone, quello inferiore decide (dove la norma lo consente)
Un aggiornamento del profilo del conservatore su parametri **non** imposti dalla norma arriva ai clienti come proposta da accettare o rifiutare con motivo; per quelli imposti, la proposta ha una data di efficacia e diventa vincolante a quella data.

### 8.14 Default di una configurazione nuova = comportamento esistente
Una nuova configurazione su una funzione già in uso ha come predefinito il comportamento di prima. Eccezione più severa del gestionale: per gli **obblighi normativi** il predefinito è il comportamento conforme (ON), mai «spento se il record manca».

### 8.15 Il dato derivabile si calcola, non si chiede
Impronta, dimensione, formato rilevato, data di chiusura: si calcolano. Il formato **si identifica dal contenuto** (firma binaria / PRONOM), mai dall'estensione o dal MIME dichiarato; il dichiarato si conserva come dichiarato.

---

## 9. Una funzione per operazione, una fonte di verità

### 9.1 Una sola implementazione per operazione
Un solo servizio per «versa un PdV», anche con più canali (API REST, portale, cartella SFTP, connettori a sistemi di protocollo): i canali sono adattatori che producono la stessa chiamata. Mai un secondo percorso «semplificato» per il portale.

### 9.2 Una sola funzione per una regola condivisa
Se due flussi prendono la stessa decisione chiamano la stessa funzione: la verifica preventiva dal portale (`POST /pdv/verifica`, dry-run), il versamento vero e la migrazione di formato usano la stessa pipeline. Operazioni inverse condividono le stesse condizioni.

### 9.3 Un concetto, una query
Gli insiemi normativi sono funzioni di repository uniche usate da UI, job, report ed export: `pda_scartabili(cliente, now)`, `pdv_da_elaborare()`, `documenti_vivi()`.

### 9.4 Un calcolo, riusato ovunque
IPdA, impronte, data di scarto prevista, RdV hanno una sola implementazione. Anteprime, PDF, API e simulatore la chiamano; nessuno la reimplementa.

### 9.5 La regola sta nel servizio che scrive
La pagina propone solo ciò che il servizio accetta, e lo riceve dall'API (`GET /accordi/{id}/formati-ammessi`), non lo codifica in TypeScript. Una pagina più severa del servizio è aggirabile; una più permissiva mostra scelte che poi falliscono.

### 9.6 Le API danno le azioni possibili
Ogni risorsa restituisce `azioni_consentite` con, per quelle non consentite, `motivo_blocco`; il frontend non ricalcola permessi né stati (§25.4).

### 9.7 Funzioni «motivo di blocco»
`*_motivo_blocco(...) -> str | None` (None = si può), usate sia per spegnere pulsanti sia dal servizio. I rifiuti sono eccezioni di dominio `RifiutoDominio(codice, messaggio)` (§18).

### 9.8 Ogni record ha un creatore nell'interfaccia
Nessuna configurazione normativa (accordo di versamento, tipologie, piano di conservazione, nomine) esiste solo via SQL, seed o script: ogni entità ha la sua API e la sua pagina, con audit. *Motivo:* oggi contratti e nomine RSC sono solo letti, senza alcun percorso che li crei.

### 9.9 Un campo senza lettore è un difetto
Un parametro esposto in configurazione che nessun servizio applica è una **non conformità del manuale**: si collega o si toglie. Ogni parametro ha un test che ne dimostra l'effetto.

---

## 10. Immutabilità e WORM

### 10.1 Ciò che è conservato non si modifica, non si annulla, non si cancella
Contenuti, IPdA, RdV e metadati di un PdA chiuso sono immutabili: storage S3 con **Object Lock COMPLIANCE** e retention per oggetto, righe DB in sola lettura (trigger o vincolo di stato). Una correzione è un **nuovo PdA** con relazione «sostituisce», mai un update. Il rollback esiste solo per le configurazioni, come nuova versione.

### 10.2 Ogni scrittura su storage conservativo porta la retention
`put_object` sul bucket conservativo **senza** `ObjectLockMode` e `ObjectLockRetainUntilDate` è vietato (test AST + controllo all'avvio, §19.4). La data di retention deriva dalla regola di conservazione fotografata (§8.3).

### 10.3 Impronte calcolate sui byte, mai fidate
Ogni impronta usata in IPdA, RdV o catena si **ricalcola sui byte letti dallo storage**. L'impronta dichiarata dal produttore si confronta, si conserva come dichiarata e non sostituisce mai quella calcolata. Se il file non si legge, il pacchetto **non** avanza di stato.

### 10.4 Nessuna cascata verso dati conservati
Tutte le FK verso PdV, PdA, documenti, eventi di audit sono `RESTRICT`. Le uniche eliminazioni fisiche sono quelle dello **scarto autorizzato**, con verbale (§11.7).

### 10.5 Si annulla o si sostituisce, mai si cancella
Nomine (responsabile della conservazione, delegati), accordi, utenti: periodo di validità `valido_dal/valido_al` + `sostituito_da`. Un documento versato per errore si annulla con un evento, mai con DELETE.

### 10.6 Le migrazioni non toccano contenuti conservati
Le migrazioni agiscono su tabelle di indice e di stato; mai su oggetti conservati né sui loro metadati fotografati.

---

## 11. Stati, transizioni e cancelli

### 11.1 Macchine a stati esplicite
PdV, PdA, PdD/esibizione, proposta di scarto, accordo di versamento, cliente: le transizioni ammesse sono un dato (`TRANSIZIONI`), con un solo punto `transizione(obj, verso, ctx)`. Nessun `stato = …` fuori da lì. Le transizioni automatiche chiamano lo stesso metodo delle manuali.

### 11.2 Ogni stato raggiungibile ha un percorso che lo raggiunge
Uno stato dichiarato nell'enum ma mai raggiunto da nessun servizio è un difetto (*motivo:* oggi nessun servizio porta PdV o AIP in `CONSERVATO`, quindi la RdV non si ottiene mai). Test permanente: per ogni stato non iniziale esiste una transizione nel codice.

### 11.3 Il cancello sta nel servizio
`assicura_pda_aperto(pda)`, `assicura_cliente_attivo(cliente)`, `assicura_accordo_valido(accordo, now)` in testa a ogni servizio che scrive in quella fase; le viste non lo replicano, mostrano l'errore.

### 11.4 Requisiti di stato come registro
I requisiti per attivare un cliente o un accordo (responsabile nominato, accordo firmato, tipologie definite, piano di conservazione caricato…) sono voci di un registro con `bloccante`, dipendenze, pagina di correzione; checklist, «cosa fare ora» e pulsanti leggono lo stesso registro.

### 11.5 Ogni stato ha un'uscita; la pagina dice la verità
Un PdV in attesa di correzione ha «Rifiuta definitivamente», «Riapri verifica», «Proroga»; un PdV in errore tecnico ha «Rielabora» (idempotente). Un esito non annuncia un effetto futuro come avvenuto.

### 11.6 L'esito si decide al calcolo e si salva
Esito della verifica (formato riconosciuto, firma valida alla data, versione delle regole) si salva sul documento/PdV; RdV, esibizione e report lo leggono da lì e non rivalutano con le regole correnti.

### 11.7 Scarto: proposta, autorizzazione, esecuzione, verbale
Nessun passaggio diretto a «eliminazione». Lo scarto è: proposta (calcolata dal piano di conservazione, anteprima con elementi bloccati: contenzioso, esibizione aperta, blocco legale) → approvazioni previste (per la PA, autorizzazione della Soprintendenza archivistica ai sensi del Codice dei beni culturali) → esecuzione per PdA con report per elemento → verbale di scarto conservato. Ogni passo in audit.

### 11.8 Derivati vecchi si marcano, non si ricalcolano in silenzio
Se cambia il piano di conservazione, le date di scarto previste dei PdA esistenti si marcano «da ricalcolare» e la proposta di scarto si blocca finché il responsabile non ricalcola e conferma.

### 11.9 Scadenze controllate a ogni richiesta
Termine di versamento e scadenza dell'accordo si controllano a ogni richiesta anche se il job di chiusura non è girato; un job programmato che non gira è un allarme in dashboard.

---

## 12. Concorrenza, idempotenza, eventi

### 12.1 Versamento idempotente
Header `Idempotency-Key` obbligatorio su `POST /pdv` e vincolo unico `(cliente, identificativo_pdv_produttore)`. Stessa chiave + contenuto diverso → 409. Ogni risposta d'errore ha un `codice` stabile e documentato.

### 12.2 Consumer idempotenti, offset dopo l'effetto
Kafka consegna almeno una volta: ogni consumer registra `messaggio_elaborato(consumer, id_messaggio)` **nella stessa transazione** degli effetti e conferma l'offset **solo dopo** il commit. Vietato confermare prima, vietato svuotare un lotto prima di averlo sigillato. *Motivo:* l'hashchain oggi conferma l'offset prima del sigillo e perde pacchetti dalla catena a ogni crash.

### 12.3 Outbox transazionale
Gli eventi si scrivono in tabella `outbox` nella stessa transazione del cambio di stato; un relay li pubblica. Mai `producer.send()` dentro la transazione di dominio, mai una scrittura senza il suo evento.

### 12.4 Ritentativi e DLQ
Ritardi come lista costante (`RITARDI = [60, 300, 1500, 7200]` secondi); dopo l'ultimo, DLQ con motivo strutturato. Una DLQ deve essere **consumata da qualcosa** (pannello di gestione con «Rielabora»): una DLQ che nessuno legge è un buco nero.

### 12.5 Comandi con lo stato atteso
Chiusura PdA, approvazione scarto, accettazione PdV mandano lo stato (o la versione) visti dal client; se è cambiato → 409 `STATO_CAMBIATO` con lo stato attuale.

### 12.6 «Controlla poi crea» sotto lock del padre
Transazione + lock del padre + rilettura dello stato dopo il lock: la seconda richiesta trova il lavoro fatto. I vincoli unici sono la protezione, non il middleware.

### 12.7 Lock sempre nello stesso ordine
Ordine canonico: cliente → accordo → PdA → PdV → documento; a parità di livello, `id` crescente. `with_for_update(of=Modello)` sempre quando la query ha join; lock solo dentro una transazione aperta (test AST).

### 12.8 Ordine totale dove l'ordine influenza il risultato
L'ordine dei documenti nell'IPdA, delle foglie dell'albero di Merkle e di ogni lettura che entra in un'impronta è totale e documentato (ultimo criterio la chiave primaria). A parità di contenuto, stesso IPdA e stessa radice.

### 12.9 Operazioni lunghe uniche: lease con scadenza
Scarto, verifica periodica, riversamento, rinnovo marche: `lease_job(tipo, cliente, detentore, scade_il)` con vincolo «una attiva»; lease scaduto = tentativo fallito.

### 12.10 Numerazioni progressive
Numeri di RdV e PdA per cliente e anno: assegnati una volta in transazione, mai riusati, buchi ammessi e spiegati. L'URN segue lo schema documentato.

### 12.11 Effetti esterni dopo il commit
Marca temporale, firma remota, invio a sistemi esterni: dopo il commit dello stato «da marcare/da firmare», con ripresa idempotente. Le letture esterne si ritentano; le scritture esterne no (si riprendono con gli id salvati).

---

## 13. Anteprima e applicazione (plan/apply)

### 13.1 Pattern obbligatorio per operazioni massive o normative
Scarto, riversamento/migrazione formati, cambio del piano di conservazione, cambio dei formati ammessi di un accordo, presa in carico da altro conservatore, trasferimento fra AOO:
- `POST …/anteprima` non scrive (test che lo verifica) e restituisce il piano completo: cosa si farà, elementi **bloccati con motivo**, avvisi, chi verrà avvisato, `impronta_piano`;
- `POST …/applica {impronta_piano}` è atomico, ricalcola il piano dentro il lock e, se l'impronta non coincide, non scrive e risponde 409 `PIANO_CAMBIATO` con il piano aggiornato;
- una sola voce di registro con i conteggi.

### 13.2 Tre gruppi di controlli
`invalicabili` (mai forzabili: formato vietato, firma non verificabile, virus), `blocchi` (forzabili con motivo dal responsabile), `avvisi` (registrati sul pacchetto).

### 13.3 Bozze versionate
Accordo di versamento, configurazione cliente, piano di conservazione si compongono in una bozza (`bozza(risorsa, dati, versione, in_modifica_da)`); nulla si scrive fuori dalla bozza fino alla conferma; ogni salvataggio porta la versione letta (409 se cambiata da altri).

### 13.4 Impatto sui dati reali prima di cambiare un dato usato
Cambiare una configurazione già usata mostra l'impatto calcolato con gli stessi servizi del salvataggio («N PdV in lavorazione diventerebbero non conformi»), poi applica alla conferma.

---

## 14. Multi-tenant e permessi: fail-closed

### 14.1 Un cambio di permessi è una decisione
Se una correzione cambierebbe chi può fare cosa rispetto alla guida o ai test, non si applica: si registra «in attesa di decisione» e si chiede. Allineare il codice alla guida invece è un fix.

### 14.2 Perimetro vuoto = nessun dato
Ogni funzione di visibilità termina con «se il perimetro è vuoto, nessun risultato». Test obbligatorio: utente senza ruoli vede zero record.

### 14.3 Il cliente non si ricava? Negato
Ogni risorsa ha `tenant_id NOT NULL`. Se il cliente non è determinabile il motore di autorizzazione nega e logga. Difesa in profondità proposta: **Row Level Security** di PostgreSQL con `SET app.tenant_id` per transazione (policy che nega se non impostato) — decisione di architettura da confermare (vedi STATO_PROGETTO).

### 14.4 404, non 403, fra clienti diversi
Una risorsa di un altro cliente risponde 404 anche in GET: l'indirizzo non dice se esiste.

### 14.5 Permesso sull'oggetto
`autorizza(utente, azione, risorsa)` sempre con la risorsa. Senza risorsa solo per azioni di livello cliente, con il cliente come risorsa.

### 14.6 Figli verificati dentro il padre
`GET /pda/{pda_id}/documenti/{doc_id}` cerca il documento con `pda_id` **e** cliente; mai `session.get(Documento, doc_id)`. Ogni id nell'URL si risolve nel contesto dell'URL.

### 14.7 L'elenco mostrato è l'elenco accettato
La funzione che costruisce le scelte valida anche l'invio: una tipologia fuori dall'accordo si rifiuta anche se il client la manda.

### 14.8 Il ruolo dà il perimetro, il permesso dà l'azione
Ruoli = perimetro (conservatore, cliente, AOO). Permessi = azioni (`pdv:versa`, `scarto:approva`, `pdd:richiedi`, `audit:leggi`…). Ruoli da prevedere (Linee guida AgID): titolare dell'oggetto di conservazione, **responsabile della conservazione** del cliente e suoi delegati, produttore/operatore di versamento, utente abilitato all'esibizione; lato conservatore: **responsabile del servizio di conservazione**, responsabile della funzione archivistica, del trattamento dei dati, della sicurezza, dei sistemi informativi, dello sviluppo e manutenzione; super amministratore tecnico; auditor in sola lettura. La matrice ruoli × azioni sta nel manuale ed è generata (§28).

### 14.9 Helper di perimetro unici
Chi può fare cosa si decide solo nel modulo di autorizzazione (`can_operate_on`, `managers_of`); mai confronti scritti a mano nei router, mai `ruolo == "SUPER_ADMIN"` come scorciatoia.

### 14.10 Interfaccia coerente con i permessi
Menu e pulsanti si calcolano con la stessa funzione della risorsa di destinazione (`azioni_consentite` dall'API). Un pulsante che porta a 403 e un ruolo autorizzato senza pulsante sono lo stesso difetto. Test permanente: per ogni ruolo, ogni voce di menu risponde 200.

### 14.11 Azioni che cambiano stato: mai in GET
Ogni effetto passa da POST/PUT/PATCH/DELETE.

### 14.12 Più clienti per lo stesso utente
Un utente che opera per più clienti (intermediario) sceglie il cliente esplicitamente; il contesto viaggia nel token; mai «il primo».

### 14.13 Filtri automatici dichiarati
Se una lista applica un perimetro che l'utente non ha scelto, la pagina lo dice e offre di allargarlo se consentito.

### 14.14 Privacy nei messaggi
Errori e messaggi non rivelano dati di altri clienti («identificativo già usato» senza dire da chi).

---

## 15. Registro attività (audit) e log di conservazione

### 15.1 Ogni scrittura è tracciata, automaticamente
Un listener SQLAlchemy registra ogni creazione, modifica, eliminazione fatta in una richiesta, nella stessa transazione (outbox → servizio audit). Un modello nuovo è coperto senza fare nulla; per escluderlo serve un motivo scritto in `AUDIT_ESCLUSI`. Test che scorre i modelli e verifica copertura o esclusione.

### 15.2 Un'azione = una voce leggibile
`audit.registra(ctx, azione="pdv.accettato", risorsa, riepilogo="PdV 123 accettato: 45 documenti", dettagli)`; le altre modifiche si aggregano come «collegate». Vietato importare il modello di audit fuori dal modulo audit (`import-linter`).

### 15.3 Il registro non fallisce in silenzio
Se la voce di audit non si scrive, l'operazione non si conferma (stessa transazione). *Motivo:* oggi `log_audit` ingoia gli errori.

### 15.4 Registro a prova di manomissione
Voci append-only con impronta concatenata (`impronta_precedente`, `impronta`), sigillo periodico con **marca temporale qualificata**, verifica schedulata. Chi ha accesso al DB non deve poter riscrivere la storia senza che la verifica lo rilevi.

### 15.5 Prima e dopo dal database
Per UPDATE/DELETE lo stato precedente si rilegge dal database prima di applicare la modifica; mai dall'oggetto già mutato.

### 15.6 Anche le letture sensibili
Ogni esibizione, download di documento, export di metadati, accesso al registro → voce con soggetto, oggetto e motivo dichiarato quando richiesto. Ogni export registra il numero di righe.

### 15.7 Il motivo è un dato
Ogni forzatura (accettazione con avvisi, riapertura, scarto anticipato, intervento del conservatore su una richiesta del cliente) richiede un motivo (≥ 10 caratteri) salvato nella voce.

### 15.8 Esecuzioni dei job append-only
Ogni esecuzione di job (verifica integrità, rinnovo marche, scarto, riversamento, prova di ripristino) crea una riga con avvio, fine, esito, tentativo, conteggi: è l'**evidenza** per l'auditor che i controlli periodici sono stati eseguiti.

### 15.9 Visibilità
Una sola funzione decide chi legge cosa del registro; il cliente vede descrizioni leggibili, il personale tecnico del conservatore i dettagli, mai i contenuti dei documenti.

---

# PARTE C — TECNICA

## 16. Database e migrazioni

### 16.1 Decisione di architettura (v4.2, confermata)
Un solo schema PostgreSQL (`public`) per tutte le tabelle; isolamento per `tenant_id`. Nessuno schema per cliente. `tenants.schema_name` resta solo come identificativo. *Nota:* `docs/adr/ADR-001` (schema per tenant, Celery) è superato e va riscritto.

### 16.2 Una sola fonte dello schema: Alembic
Le tabelle si creano **solo** con le migrazioni Alembic (in `services/gateway/alembic/`). Vietati `Base.metadata.create_all()` a runtime e `infra/postgres/init.sql` con DDL o enum: in sviluppo e produzione lo schema deve essere identico. Test: database vuoto → `alembic upgrade head` → confronto con i modelli (`alembic check` / autogenerate vuoto).

### 16.3 Regole ORM
| Operazione | Usa |
|---|---|
| Insert, select per chiave o cliente, update di stato | ORM |
| Aggregati complessi, join su 3+ tabelle | SQL con commento che spiega il join; parametri con `bindparam`, **mai** `:param::uuid` (usare `CAST(:param AS uuid)`) |
| DDL | Solo nelle migrazioni |
| Sequenze (URN) | `SELECT nextval(...)` |
Serializzazione: `model_dump(mode="json")`, mai `json.dumps` su date senza conversione.

### 16.4 Modelli
`__table_args__` con `{"schema": "public"}`; UUID `PG_UUID(as_uuid=True)`; date `DateTime(timezone=True)`; JSON → `JSONB`; `created_at` con `server_default=func.now()`; `tenant_id` NOT NULL su ogni tabella con dati di un cliente; FK con `ondelete` esplicito (§10.4); docstring con lo scopo.

### 16.5 Migrazioni
- Nome: `AAAAMMGG_NNN_descrizione.py`; `upgrade()` e `downgrade()` funzionanti (eccezione: `ALTER TYPE ADD VALUE`).
- Mai modificare una migrazione già su `main`: sempre una nuova.
- Colonna NOT NULL su tabella con dati: nullable → riempimento → NOT NULL.
- Vincolo nuovo su dati esistenti: prima un controllo che si ferma con l'elenco dei doppioni.
- UUID nuovo su tabella esistente: tre passi (colonna senza default → riempimento con `gen_random_uuid()` → vincolo).
- Indici su tabelle grandi con `CONCURRENTLY` dove possibile; migrazioni bloccanti in finestra di manutenzione annunciata (SLA).
- Enum Python e PostgreSQL allineati (test).
- Le migrazioni si eseguono all'avvio del gateway (`entrypoint.sh`), mai a mano in produzione; prima, backup verificato.

### 16.6 Seed e dati di riferimento
Seed idempotenti con chiave stabile (mai il nome visibile); una riga trovata non si aggiorna (le modifiche dell'utente restano); test dei due lanci. I dati di sviluppo restano separati dai dati di riferimento normativi (§8.12).

### 16.7 Mai il database di produzione a mano
Nessun `psql`/`ALTER TABLE` diretto. Ogni modifica di schema in una migrazione, ogni modifica di dati in una migrazione dati o in un comando versionato con modalità di prova.

---

## 17. Standard Python

### 17.1 File
Docstring di modulo (scopo, servizio, riferimento al piano, **riferimento normativo** se applicabile); import nell'ordine standard → terze parti → `libs` → interni; costanti in `UPPER_SNAKE_CASE` (solo quelle non normative: le normative vanno nel registro, §8.1).

### 17.2 Tipi
Type hints completi; `mypy --strict` obbligatorio su `libs/` e su `domain/` di ogni servizio, gradualmente esteso agli altri moduli con un elenco del debito che si accorcia soltanto (§29.4). `TYPE_CHECKING` per gli import solo-hint; mai `# noqa` o `type: ignore` senza codice e motivo.

### 17.3 Errori
Mai `except: pass`, mai `except Exception: return False`. Ogni `except` logga con contesto, rilancia o restituisce un esito esplicito. Ruff `BLE001`, `S110` attive.

### 17.4 Log
`structlog` JSON con `tenant_id`, `request_id`, id della risorsa; mai `print`, mai f-string come messaggio; impronte e dati personali troncati o mascherati.

### 17.5 Pydantic v2
Ogni input e output pubblico è un modello Pydantic con tipi stretti (`UUID`, interi con limiti, `StrictBool`); `Field(description=…)` con regola e riferimento normativo (finisce in OpenAPI). Nomi file validati contro path traversal e caratteri di controllo.

### 17.6 Tempo
Persistenza in UTC, presentazione Europe/Rome. Le regole che dipendono dal tempo ricevono `now` come argomento; `datetime.now()` vietato nel dominio (test).

---

## 18. Errori: mai un 500

### 18.1 Rifiuti di dominio
`RifiutoDominio(codice, messaggio, dettagli)` mappato in un unico handler a 409/422 con `{"codice", "messaggio", "dettagli"}`. `RequestValidationError` → 422 con messaggi in italiano. Un 500 è sempre un bug: si logga con `request_id` e si apre una criticità.

### 18.2 Catalogo dei codici d'errore
`CODICI_ERRORE` versionato e pubblicato (i produttori integrano su quei codici); generato in `docs/catalogo/`.

### 18.3 Mai scartare in silenzio
Un valore non valido produce un errore visibile e non sovrascrive quello salvato; un file non conforme in un PdV multi-file produce un errore per file nel RdV.

### 18.4 Errore del server ≠ assenza di rete
Il client frontend distingue `ApiError{status, codice, messaggio}` da `NetworkError` e mostra messaggi diversi.

---

## 19. Sicurezza

### 19.1 Autenticazione sempre verificata
Ogni token si verifica con firma, emittente, pubblico e scadenza (RS256 con chiave pubblica). Vietato decodificare un JWT senza verificarne la firma, anche «solo per leggere il ruolo». Test permanente: ogni endpoint (escluso un elenco esplicito di pubblici: health, metadati SPID) risponde 401 senza token e con token firmato da un'altra chiave.

### 19.2 Login reale prima della produzione
Il login di sviluppo («dev-token», profili demo) esiste solo con `ENV=development` **e** flag esplicito, mai in staging esposto; non entra nel bundle di produzione del frontend. Produzione: SPID/CIE (per la PA), OIDC aziendale, API key / OAuth2 client credentials / mTLS per i sistemi versanti. MFA per ruoli privilegiati.

### 19.3 Segreti
Mai nel repository (né chiavi private «di sviluppo»): `.env` locale non versionato, secret manager in produzione. Una chiave committata per errore si considera compromessa: si ruota. Campi `SecretStr`; redazione nei log e nell'audit.

### 19.4 Impostazioni pericolose fermano l'avvio
In `ENV=production` il servizio **non parte** se: chiavi o password d'esempio, provider di firma/marca `stub`, bucket conservativo senza Object Lock COMPLIANCE e retention predefinita, CORS aperto, endpoint S3 locale. Controlli puri e testati.

### 19.5 File caricati
Dimensione, firma del contenuto (magic bytes / PRONOM), antivirus; mai fidarsi dell'estensione o del MIME dichiarato. Nomi file solo come metadato, mai come chiave di storage.

### 19.6 Download
S3 senza accesso pubblico; download solo tramite servizio autorizzato (URL prefirmati brevissimi o streaming), `Content-Disposition: attachment`, `nosniff`, `no-store`; 404 fra clienti.

### 19.7 Sessioni
Token in cookie HttpOnly + SameSite (non in `localStorage`); scadenza per inattività; il polling automatico non rinnova la sessione; timeout più breve per i ruoli privilegiati.

### 19.8 API macchina-macchina
Chiave per sistema versante, mostrata una volta, salvata come impronta con le ultime cifre in chiaro; rotazione e revoca immediata; scope per cliente e azione; rate limit per cliente e IP (`429` con `Retry-After`); `client_ip()` unico che considera i proxy fidati.

### 19.9 Minimizzazione
Il personale del conservatore vede metadati tecnici, non i contenuti; un ruolo nuovo nasce senza accesso ai contenuti; gli eventi Kafka non portano dati personali.

---

## 20. Integrazioni esterne (TSA, firma, OCSP/CRL, IPA, PEC)

### 20.1 Un sistema esterno, un solo client
Un modulo client per ciascuno (TSA RFC 3161, firma remota/HSM, OCSP/CRL, IPA, PEC); trasporto iniettabile (`httpx.MockTransport` nei test); i test non chiamano la rete.

### 20.2 Uno stub dichiara se stesso
Un adattatore non implementato solleva `NotImplementedError` **e** il servizio riporta `production_ready=false`; l'interfaccia mostra «Firma non disponibile», mai «eIDAS attiva». Il provider `stub` in produzione blocca l'avvio (§19.4).

### 20.3 Firma e marca reali
Firma CAdES/XAdES/PAdES con librerie standard (es. `pyhanko`, `asn1crypto`); marca RFC 3161 da TSA qualificata con verifica del token; la validazione delle firme dei documenti versati usa la Trusted List alla data rilevante e conserva le evidenze (OCSP/CRL) per la validazione a lungo termine.

### 20.4 Ogni chiamata lascia una riga senza segreti
`chiamata_integrazione(servizio, operazione, esito, durata_ms, riepilogo)`; i token di marca e le risposte OCSP/CRL si conservano perché sono prova.

### 20.5 Letture si ritentano, scritture no
Vedi §12.11.

### 20.6 Prima di usare una fonte, verificarne la validità
Accordo attivo e produttore abilitato prima di accettare; certificati della TSA validi alla data.

### 20.7 Collegamenti per codici forti
Abbinamenti (cliente ↔ codice IPA ↔ codice fiscale; PdA ricevuto ↔ fascicolo) solo per codici o impronte, mai per nome o oggetto.

### 20.8 Licenze
Licenza del progetto: **EUPL-1.2** (VD-P-190, R18). Nessun codice GPL/AGPL copiato; strumenti esterni (DROID, Siegfried, librerie XAdES) e ogni dipendenza con licenza verificata, compatibile con la EUPL-1.2 e annotata in `THIRD_PARTY_NOTICES.md`; mai licenze sconosciute, proprietarie, SSPL, BUSL o simili «source available». Ogni file sorgente porta l'intestazione SPDX (§31, R18); `tools/opensource/controlla_pubblicazione.py` (in `make verifiche`) la controlla insieme a segreti evidenti e riferimenti vietati.

---

## 21. Notifiche

### 21.1 L'operazione principale non dipende dalla posta
I servizi di dominio pubblicano eventi; solo `notification` parla con SMTP/PEC/webhook. Il RdV è disponibile via API anche se la notifica fallisce.

### 21.2 Destinatari veri
Destinatari calcolati da una funzione unica (`destinatari(cliente, evento)`) sui dati reali del cliente; vietati indirizzi segnaposto. Una configurazione d'esempio = posta «non configurata», e la dashboard lo dice.

### 21.3 Coda con esito vero
`messaggio_uscita(tipo, oggetto, destinatario, stato, tentativi, prossimo_tentativo)`; ritentativi solo per errori temporanei; deduplica per `(tipo, oggetto, destinatario)`. Stati: «in coda», «inviata», «consegnata» (ricevuta PEC), «non partita» con motivo. Le comunicazioni con valore legale avanzano di stato solo alla ricevuta di consegna.

### 21.4 Mai allegare documenti conservati
Si invia un link autenticato a scadenza alla PdD.

### 21.5 Categorie e riepiloghi
Preferenze per categoria (critiche, operative, promemoria); riepilogo giornaliero invece di N email; solo account attivi.

---

## 22. Job periodici e consumer

### 22.1 Job della conservazione
Verifica periodica d'integrità e leggibilità (campionaria e completa, con periodo da registro), rinnovo delle marche prima della scadenza, verifica della copia nel sito secondario, proposta di scarto, prove di ripristino, sigillo periodico del registro: ognuno con definizione versionata, comando CLI (`verumdoc job esegui <nome>`), lease (§12.9) ed esecuzioni registrate (§15.8).

### 22.2 Nessun lotto resta aperto
Un consumer che accumula a lotti ha anche un sigillo a tempo: con traffico basso il lotto si chiude comunque entro un intervallo massimo.

### 22.3 Rete di sicurezza
Oltre al job, un controllo idempotente chiude ciò che è scaduto; un job che non gira da più del suo periodo è un allarme.

---

## 23. Test

### 23.1 I test eseguono il codice
Vietati i test che verificano la presenza di stringhe nel sorgente: non provano il comportamento. Ogni funzione pubblica: caso felice, errore atteso, caso limite. Nomi `test_<funzione>_<scenario>_<esito>`, docstring in italiano con lo scenario e, se fissa una decisione, il codice `VD-N`/`VD-C`.

### 23.2 Cosa si lancia
Di norma: test nuovi + servizi toccati + `tests/integrity`. Suite intera per modifiche a librerie condivise, modelli, migrazioni, autenticazione/tenant, schemi degli eventi. I numeri si copiano dall'esecuzione.

### 23.3 Infrastruttura vera dove conta
PostgreSQL, MinIO (con Object Lock) e Kafka via testcontainers per lock, letture in blocco, consumer, storage. Test di concorrenza con **due connessioni reali** (due versamenti dello stesso PdV, due chiusure dello stesso PdA). Bucket di test effimeri, mai quelli reali.

### 23.4 Suite permanente di integrità (`tests/integrity`, mai disattivata)
- ogni route FastAPI ha `response_model`, `summary`, tag e dipendenza di autenticazione; nessun path duplicato;
- ogni endpoint risponde 401 senza token e 404 su risorse di un altro cliente;
- parametri non validi (`abc`, `-1`, `²`, UUID inesistente) → 404/422, mai 500;
- ogni topic prodotto ha un consumer e uno schema; ogni stato dell'enum è raggiungibile;
- ogni URL chiamato dal frontend esiste nel backend con il metodo giusto (test di contratto generato da OpenAPI);
- nessun `put_object` sul bucket conservativo senza retention;
- modelli coperti dall'audit; enum Python = enum PostgreSQL; schema da Alembic = modelli.

### 23.5 Oracoli esterni
Conformità dei formati provata contro gli schemi ufficiali (XSD UNI SInCRO) e contro esempi reali anonimizzati; verifiche crittografiche con strumenti indipendenti (`openssl ts -verify`, validatori di firma). Fixture di metadati da esempi reali, non inventate da chi scrive il validatore.

### 23.6 End-to-end per ogni flusso normativo
Versamento → RdV; esibizione → PdD; proposta di scarto → approvazione → esecuzione con verbale: chiamando l'API HTTP con il payload reale prodotto dal frontend. Playwright per i flussi UI principali.

### 23.7 Un test per caso di policy
Un test nominato per ogni caso (firma valida, scaduta con marca valida, revocata, OCSP irraggiungibile…), non un parametrico unico.

### 23.8 Refactoring puri
In un rilascio di solo refactoring nessun test esistente si modifica; quando si sposta una regola si congela la versione vecchia come oracolo e si confrontano vecchia e nuova su molti casi generati con seme fisso.

### 23.9 Query costanti
Ogni endpoint di lista ha un test con 2 e 50 righe: stesso numero di query.

### 23.10 Coverage
≥ 80% sulle righe dei moduli toccati, ma la coverage non sostituisce i test di integrazione: un modulo coperto da mock che cercano stringhe vale zero.

---

## 24. Qualità del codice e pulizia tecnica

### 24.1 Ruff per primo, regole nel repository
`ruff.toml` unico, versione fissata in `requirements-dev.txt` e pre-commit. Ogni pulizia parte dalle statistiche per codice.

### 24.2 Codice morto
Quando si toglie una funzione si toglie tutto ciò che ne dipende (router, servizio, topic, pagina, chiavi di query, test, documentazione). Strumenti: `vulture`, `ruff F401/F841`, ricerca dei riferimenti. Librerie e moduli vuoti (solo commenti TODO) non restano nel repository.

### 24.3 Dipendenze
Ogni libreria importata è dichiarata nel `pyproject.toml` del servizio che la usa; versioni bloccate con lock file; dev separato e mai nell'immagine; SBOM a ogni build; cambio di versione = modifica voluta, provata e nel changelog. *Motivo:* oggi `fastapi>=0.111` fa sì che onboarding non parta con alcune versioni e PyJWT non è dichiarato.

### 24.4 File generati fuori dal repository
`__pycache__`, `*.egg-info`, `dist/`, `node_modules/`, `.pytest_cache/` mai committati (già in `.gitignore`; lo script di pulizia li rimuove dal repository).

### 24.5 Bonus fix separati
I bug incrociati durante una funzionalità si correggono nello stesso ciclo ma in una sezione «Correzioni» distinta del changelog.

---

# PARTE D — FRONTEND

## 25. Frontend: architettura e regole

### 25.1 Stack
React 18 + TypeScript strict + Vite + TanStack Query + React Router. `tsconfig.json` strict obbligatorio; `npm run build` = `tsc --noEmit && vite build` verde nella checklist. Tailwind **configurato davvero** (PostCSS) o rimosso: dichiarato e non attivo è vietato.

### 25.2 Zero inline
Vietato `style={{…}}` salvo valori calcolati dinamici passati come variabili CSS (larghezza di una barra di avanzamento), con regola ESLint e allowlist motivata. Vietati gestori `onMouseEnter` per gli stati hover: si usano le pseudo-classi CSS. Il debito inline esistente è congelato da un test che si accorcia soltanto (§29.4).

### 25.3 Componenti condivisi
Un pattern ripetuto ha un componente condiviso con contratto dichiarato in `src/ui/`: `AppShell`, `DataTable` (con modalità a schede sul telefono), `StatusBadge`, `EmptyState`, `ErrorState`, `Skeleton`, `ConfirmDialog` (unico, accessibile), `toast()` (unico), `Drawer`, `FileDropzone`, `Autocomplete` remoto, `DateField`, `ScopeNotice`, `GuidePanel`. Una pagina non ridefinisce gli stili di un componente condiviso.

### 25.4 Frontend «stupido»
Nessuna logica di permessi o di stato nel frontend: si usano `azioni_consentite` e `motivo_blocco` dall'API. Le liste di scelte (formati, tipologie, classi) arrivano dall'API.

### 25.5 Client API generato
Tipi e client generati da OpenAPI (`openapi-typescript`); nessun URL scritto a mano nelle pagine; test di contratto (§23.4). Dati finti, «demo» o grafici casuali vietati nel bundle di produzione: un grafico senza dati mostra lo stato vuoto.

### 25.6 Accessibilità: obbligo di legge
Per un prodotto venduto alla PA si applicano L. 4/2004, le Linee guida AgID sull'accessibilità e WCAG 2.1 AA (dichiarazione di accessibilità). Obbligatori: skip-link, `:focus-visible` visibile ovunque (mai `outline: none` senza sostituto), etichette su tutti i controlli, `button` per le azioni (mai `div onClick`), tabelle con `<th scope>`, icone decorative `aria-hidden`, contrasto testo ≥ 4,5:1 e bordi/controlli ≥ 3:1, bersagli ≥ 44 px su touch, `eslint-plugin-jsx-a11y`, `@axe-core/playwright` su ogni pagina in CI con zero violazioni serious/critical.

### 25.7 Responsive
Nessuno scorrimento orizzontale della pagina a 390 px; navigazione che collassa su schermi stretti; tabelle utili che diventano schede con etichette sotto i 600 px invece di nascondere colonne; breakpoint fissi del sistema grafico.

### 25.8 Stati obbligatori
Ogni vista con dati ha caricamento (skeleton della forma finale), vuoto (con l'azione per riempirlo), errore (con cosa fare), parziale. Errori e avvisi non si chiudono da soli.

### 25.9 Testi per le persone
Niente gergo tecnico nell'interfaccia (`pending`, `null`, `payload`, `AIP`, `PdV` senza spiegazione la prima volta, «Blockchain» per indicare la catena delle impronte): glossario controllato basato sulla terminologia delle Linee guida (pacchetto di versamento, pacchetto di archiviazione, pacchetto di distribuzione, rapporto di versamento), sentence case, verbi sui pulsanti, plurali corretti. Test che cerca le parole vietate.

### 25.10 Risorse locali
Font e librerie nel bundle; la pagina non carica nulla da domini esterni (CSP); niente `@import` di Google Fonts.

### 25.11 Prestazioni
Code splitting per route (`React.lazy`); librerie pesanti (grafici) caricate solo dove servono; budget di peso per route in CI. Il polling è unico per pagina, sospeso a pagina nascosta e quando un dialog è aperto, con intervallo deciso dal server.

### 25.12 Guide in pagina
Ogni pagina ha una guida (per ruolo) o il motivo in un elenco di esclusioni; test di completezza. Le guide sono anche materiale del manuale utente richiesto dai clienti.

### 25.13 Verifica visiva prima del rilascio
Foto Playwright delle pagine toccate a 390 e 1366 px, nei due temi se previsti, guardate una per una; regressione visiva sulle PR che toccano l'interfaccia.
**Le foto di verifica grafica non si conservano nel repository: si generano a richiesta in `docs/design/foto/` (ignorata da git) e non si producono di default** (`FOTO=1 npm run e2e -- foto`). Rapporti e registri citano il test end-to-end, non le foto. Foto e cache rimaste da rilasci precedenti le toglie `tools/pulizia/pulisci_progetto.py` (R06).

---

## 26. Sistema grafico

> **Stato al 20/10/2026:** il sistema grafico attuale (navy + oro, DM Sans + Instrument Serif, stili inline) è **dismesso** per decisione del committente («cambiare totalmente personalità»). La nuova direzione è oggetto della decisione `VD-DR-01` (vedi `docs/design/AUDIT_GRAFICO_2026-10-20.md`). Le regole qui sotto valgono per qualsiasi direzione scelta.

### 26.1 Solo token
Nessun colore, dimensione di carattere, raggio, ombra, z-index o durata scritti a mano nei componenti. Token come variabili CSS in `src/styles/tokens.css`, mappati nella configurazione di Tailwind con tema chiuso (niente colori predefiniti non mappati, niente valori arbitrari `text-[#…]`). Un colore nuovo si aggiunge solo nei token, in entrambi i temi, con il contrasto misurato scritto accanto.

### 26.2 Un colore = un significato
Semantica fissa: `azione` (link, pulsante principale, selezione), `in-lavorazione` (pacchetto in elaborazione adesso), `conservato` (completato, integro), `attenzione` (da correggere, in scadenza), `errore` (rifiutato, distruttivo), `neutro`. Il colore del marchio è solo marchio, mai uno stato.

### 26.3 Lo stato non è mai solo colore
Ogni stato passa da `<StatusBadge>` con parola + icona di forma diversa + colore; mappa centrale tipizzata con unione esaustiva (uno stato nuovo senza voce non compila).

### 26.4 Una sola azione principale per vista
Al massimo un pulsante primario; le distruttive piene solo nel dialog di conferma; un'azione principale spenta ha accanto il motivo; azioni irreversibili mai come primarie a lavoro non finito.

### 26.5 Tipografia
Una scala tipografica definita; numeri, impronte e identificativi in cifre tabellari o monospazio; niente maiuscole spaziate come etichette; sentence case.

### 26.6 Movimento
Solo `transform`, `opacity` e colori; vietato `transition: all`; durate sotto 300 ms per l'interfaccia; nessuna animazione su azioni da tastiera ripetute; `prefers-reduced-motion` gestito con alternative (colori/opacità), non con un azzeramento globale. Le animazioni infinite solo per stati realmente vivi.

### 26.7 Icone
Una sola libreria (decisione nella `VD-DR-01`), tratto uniforme, sempre accanto a una parola nella navigazione. Niente emoji.

### 26.8 Documentazione del sistema
`frontend/DESIGN.md` descrive token, componenti e pattern; ogni pagina ridisegnata ha la sua scheda; le decisioni di design hanno codice `VD-DR-nn`.

---

# PARTE E — ESERCIZIO E DOCUMENTAZIONE

## 27. Distribuzione, backup, ambienti

### 27.1 Immagini
Un Dockerfile base comune; un'immagine per servizio; utente non root; dipendenze bloccate; immagini scansionate e firmate.

### 27.2 Avvio
Migrazioni in un passo dedicato prima dei servizi; readiness reale (DB, Kafka, S3 raggiungibili e **bucket con Object Lock**); consumer dopo le migrazioni.

### 27.3 Rete
Solo l'ingress pubblica porte; PostgreSQL, Kafka, Redis, MinIO su rete privata. Limiti di upload del proxy allineati a quelli applicativi; file grandi via upload multipart diretto su area di staging.

### 27.4 Configurazioni coerenti
Docker Compose, Kubernetes/Helm e Terraform usano gli stessi nomi di variabili letti dalle `Settings`; un test confronta le chiavi delle ConfigMap/values con i campi delle `Settings` di ogni servizio. Terraform `validate` e `plan` nella CI.

### 27.5 Installazione da zero provata
Job CI: volumi vuoti → avvio → cliente di prova → versamento di un PdV d'esempio fino a RdV, esibizione e verifica d'integrità. Se fallisce, il README è sbagliato.

### 27.6 Un backup vale quando è stato ripristinato
Backup PostgreSQL (WAL + base), replica dello storage conservativo in sito separato con Object Lock; **prove di ripristino periodiche** in ambiente isolato con verifica a campione delle impronte, registrate come esecuzioni di job (§15.8). Comandi di ripristino con `ON_ERROR_STOP` e transazione singola.

### 27.7 Ambienti
`development` (locale), `staging` (mai esposto con login di sviluppo), `production`. Script operativi versionati in `ops/`, mai incollati dalla guida.

---

## 28. Documentazione generata e cataloghi

### 28.1 Cataloghi generati, mai scritti a mano
`tools/docs/genera_cataloghi.py` produce in `docs/catalogo/`: endpoint (servizio, metodo, percorso, permesso, ruoli, pagina che lo chiama), route frontend (ruoli, raggiungibile da), topic Kafka (produttore, consumer, schema), codici d'errore, registro impostazioni (con riferimento normativo), catalogo regole, matrice ruoli × permessi, classificazione dei modelli. Zero endpoint orfani, zero route irraggiungibili.

### 28.2 `--controlla`
Ogni tabella generata ha la modalità `--controlla` che non scrive ed esce con errore se non allineata; la CI la lancia.

### 28.3 Allegati del manuale generati
Le tabelle tecniche del Manuale di conservazione (formati ammessi, metadati per tipologia, impostazioni con riferimento normativo, ruoli e permessi, algoritmi) sono generate dai registri: il manuale non può dire una cosa diversa dal sistema.

### 28.4 Verifica coerenza documentazione
`tools/docs/verifica_documentazione.py`: ogni parametro citato nel manuale esiste nel registro, ogni codice d'errore documentato esiste, ogni file citato esiste, ogni «✅» in STATO_PROGETTO cita un test esistente, sezioni numerate in ordine. Deve terminare con 0 problemi.

### 28.5 Simulatore sui servizi veri
`verumdoc simula --cliente demo --pdv 1000` versa PdV realistici tramite l'API vera, per demo, prove di carico e collaudo; ogni funzionalità nuova che cambia cosa esiste in uno stato si aggiunge al simulatore.

---

## 29. Registri: criticità, decisioni, debito

### 29.1 Registro criticità
`docs/registro/criticita.md` con codici `VD-C-nnn` (mai riusati): gravità P0–P3, area, file:riga, stato, rilascio di chiusura, risoluzione. Le criticità dell'audit del 20/10/2026 sono il punto di partenza. Regole, test e changelog citano i codici.

### 29.2 Registro decisioni
`docs/registro/decisioni.md`, tre famiglie:
- `VD-N-nn` **normative**: interpretazione delle Linee guida e del regolamento; firmate dal responsabile del servizio di conservazione; data, norma, decisione, impatto, rilascio;
- `VD-P-nn` di **prodotto e architettura**;
- `VD-DR-nn` di **design**.
Una decisione che supera una precedente la annota come «superata da». Le scelte ovvie fatte dallo sviluppo si dichiarano in «Scelte di sviluppo da confermare».

### 29.3 Una divergenza trovata si conserva; cambiarla è una decisione
Se due servizi applicano diversamente la stessa regola, si conserva il comportamento che ha prodotto i pacchetti esistenti e si apre una decisione.

### 29.4 Elenchi del debito che si accorciano soltanto
Un controllo architetturale introdotto su codice esistente ha un elenco del debito (`percorso::funzione → conteggio`); il test fallisce sia per una lacuna nuova sia per una voce non più vera; non si aggiungono voci per codice nuovo. Si usa per: stili inline, `mypy --strict`, modelli senza `tenant_id`, servizi che pubblicano su Kafka senza outbox.

### 29.5 Audit come rilasci di correzione
Un audit produce rilievi codificati; un piano di rilasci di correzione approvato; ogni rilascio dichiara rilievi corretti, test nuovi e limiti noti.

---

## 30. Naming

| Cosa | Convenzione | Esempio |
|---|---|---|
| Servizio | kebab-case | `aip-builder` |
| Package Python | snake_case | `verumdoc_rules` |
| Modello SQLAlchemy | PascalCase singolare | `ClasseDocumentale` |
| Tabella | snake_case | `classi_documentali` |
| Campo | snake_case | `created_at`, `tenant_id` |
| Endpoint REST | kebab-case, plurali, versionati | `/api/v1/piani-conservazione` |
| Topic Kafka | `dominio.entità.evento.vN` | `pdv.ricevuto.v1` |
| Codice errore | MAIUSCOLO_SNAKE con prefisso | `PDV_FORMATO_NON_AMMESSO` |
| Impostazione | MAIUSCOLO_SNAKE | `TERMINE_VERSAMENTO_GIORNI` |
| Componente React | PascalCase, un file | `StatusBadge.tsx` |
| Hook | `useX` | `usePdvList` |
| Test Python | `tests/test_<dominio>.py`, `test_<funzione>_<scenario>_<esito>` | `test_versa_pdv_formato_vietato_rifiutato` |
| Decisioni / criticità | `VD-N-nn`, `VD-P-nn`, `VD-DR-nn`, `VD-C-nnn` | `VD-N-03` |

Lingua: codice di dominio in italiano dove il concetto è normativo italiano (PdV, PdA, PdD, RdV, classe documentale), inglese per i concetti tecnici generici; testi dell'interfaccia sempre in italiano. Un concetto ha un solo nome in tutto il sistema; se la norma rinomina un concetto si cambia il testo visibile, non lo schema.

---

## 31. Storico regole

| Data | Regola | Contesto |
|---|---|---|
| 2026-03 | Regole di sviluppo v1 (ciclo PLAN→BUILD→VERIFY→DOCUMENT, Git, standard Python, STATO_PROGETTO, criteri di fase, regole per chi sviluppa, consegna ZIP, imprevisti, checklist) | `Regole_Sviluppo_VerumDoc.docx`, assorbite nei §1–§6, §17, §23 |
| 2026-03-20 | Linee guida database v4.2: schema unico `public`, regole ORM, migrazioni, modelli, enum, file deprecati | `Regole_Sviluppo_VerumDoc_v4_2.docx`, assorbite nel §16 |
| 2026-10-20 | **Prima stesura unificata** con le regole trasferibili da un progetto precedente: regola prioritaria di allineamento documentazione (§0) estesa al manuale; checklist di rilascio ordinata; ZIP differenziale con changelog e script di eliminazione (§3); regole come dati e catena di risoluzione (§8); immutabilità e WORM (§10); stati e cancelli (§11); idempotenza e outbox (§12); plan/apply (§13); perimetri fail-closed (§14); audit automatico a prova di manomissione (§15); schema solo da Alembic (§16.2); stub che si dichiarano (§2.4, §20.2); test che eseguono il codice e suite permanente (§23); frontend a token, accessibilità come obbligo di legge (§25–§26); registri `VD-C`, `VD-N`, `VD-P`, `VD-DR` (§29) | Audit funzionale e grafico del 20/10/2026 |
| 2026-10-21 | **R03**: un solo caso d'uso per ogni canale (i canali traducono, non decidono, §9); nessun file intero in memoria (streaming con impronte durante il trasferimento); errori solo dal catalogo `libs/shared/errori` in problem+json (supera la forma del §18 di R02, VD-P-30); regole e quote dal motore `libs/verumdoc_rules` (§8); elenchi solo a cursore, mai `COUNT(*)`/`OFFSET` su tabelle grandi; documentazione API generata (`make docs-api`) e controllata in `make verifiche` | Piano R03–R05, revisione indipendente R03 |
| 2026-10-23 | **R04**: nessuno stato «conservato» senza prova verificabile (checkpoint controfirmato); registri append-only protetti anche dal proprietario (helper `registri_modificabili` per le migrazioni, runbook `migrazioni_audit.md`); servizi con login non proprietario; lock e `SET` di sessione solo su connessione diretta (PgBouncer); permessi minimi per servizio (IAM, ACL Kafka per topic) controllati da `verifica_manifest`; ogni correzione con test che fallisce prima e prova di mutazione | Revisione indipendente R04 in quattro giri |
| 2026-10-24 | **R05**: Row Level Security per ente su ogni tabella con `tenant_id` (esclusioni motivate in `docs/architettura/sicurezza.md`), ambito impostato per transazione da un solo punto e test dei servizi con il login non proprietario; guardia all'avvio su ogni URL del database (nessun login privilegiato, `idle_in_transaction_session_timeout` obbligatorio); un processo esposto non riceve credenziali né permessi che usa solo un lavoratore (Secret, ServiceAccount, IAM e login DB separati); un fatto che autorizza un'azione distruttiva si scrive con un ruolo diverso da quello dell'API e il lavoratore lo riverifica prima di agire; «nulla distrutto» si decide dallo storage, mai da un contatore; migrazioni riprendibili (una transazione per revisione, `VALIDATE` e indici fuori dal lock esclusivo, `lock_timeout`); nessun upload di dimensione non limitata dentro una transazione | Revisione indipendente R05 in quattro giri |
| 2026-10-30 | **R06**: le foto di verifica grafica non si conservano nel repository, si generano a richiesta in `docs/design/foto/` (ignorata da git) e non si producono di default (§25.13); pulizia di foto, cache e script dei rilasci precedenti con `tools/pulizia/pulisci_progetto.py` invece degli script di eliminazione per rilascio (§3); credenziali ambigue (intestazioni ripetute, cookie e Bearer diversi) rifiutate con 400, mai scelta di un'occorrenza (§19); nessuna scrittura sul database per un token non verificato (§19.1); diritti per colonna sulle tabelle di autenticazione e nessun diritto al processo esposto che non le usa (§19); il lavoratore verifica la catena della richiesta prima di ogni fatto che il registro pretende (§10, §19) | Revisione indipendente di R06 (V1–V4); `changelog/CHANGELOG-2026-10-30-rilascio-R06.md` |
| 2026-10-30 | **R07**: ogni parametro portato in console dichiara il verso «più sicuro» (`verso_sicuro`) e, se l'ente lo personalizza, il motore lo fa valere rispetto al tetto del conservatore (valore o limite per ente `LIMITE.<codice>`, riservato): un livello del cliente non supera mai il conservatore, né in console né in risoluzione; righe a livelli non ammessi ignorate con metrica; l'ente non annulla le modifiche del conservatore (anche programmate); un valore che i servizi leggono si risolve con `libs/shared/parametri.py` (una query, stessa catena della console); le operazioni distruttive manuali (riscrittura della cronologia) lavorano su una copia nuova, con copia di sicurezza verificata e conferma digitata | Rilascio R07, revisione indipendente W1–W8 |
| 2026-10-30 | **R08**: (1) strumenti esterni (verifica delle firme, riconoscimento dei formati) sempre dietro un'interfaccia con esito normalizzato e un ripiego dichiarato nell'esito; uno strumento non raggiungibile è un errore TECNICO (si ritenta), mai un esito positivo o negativo; (2) le regole che servono il contenuto intero (fase VERIFICA, o FILE riapplicata col PUID) leggono la fotografia del PdV, mai le impostazioni di oggi; per le fotografie precedenti la regola non si applica (§8.10); (3) un elenco esterno (catalogo dei formati) si porta in console come voci generate dai dati versionati, con verso «più sicuro» per voce, invece di una tabella con bozze proprie; (4) nei test, chiavi e certificati di prova solo generati a runtime (`libs/shared/firme/tests/pki_prova.py`); (5) una correzione di sicurezza non è chiusa finché un secondo giro di revisione non l'ha riverificata contro l'attacco originale e le sue varianti: il limite degli zip valeva solo per DEFLATE (bzip2 e LZMA decomprimono oltre la dimensione dichiarata, quindi si ammettono solo STORED e DEFLATE) e il controllo del firmatario autorizzato era separato dal controllo del contenuto (il riferimento va cercato solo nei byte firmati da chi è autorizzato); i due controlli che proteggono la stessa azione si legano fra loro, non si verificano uno per volta; (6) un limite che protegge un'operazione (dimensione, tempo, metodo) si prova con un test che misura la risorsa (memoria, tempo) e con la mutazione che lo toglie. | Rilascio R08, revisione indipendente X1–X14 e Y1–Y3 |
| 2026-10-30 | **R09**: (1) un lavoro periodico gira nel lavoratore con un lease PER PASSO in tabella (`ciclo_vita_periodico`) e un intervallo, mai un lock di sessione; un errore di un passo non ferma gli altri e il passo si riprova; (2) un'azione automatica del SISTEMA che apre o fa avanzare una pratica usa la STESSA funzione dell'azione manuale (`crea_bozza`, `richiedi_esecuzione`) e lascia un fatto scritto dal solo ruolo del lavoratore che la verifica della catena pretende (creazione «SISTEMA» senza fatto = difetto); (3) quattro occhi veri: chi approva è una persona diversa da chi chiede, controllato nell'API e RIVERIFICATO dal lavoratore sulla cronologia con i parametri ATTUALI dell'ente (fail-closed); (4) le operazioni che estendono una protezione (retention) sono «mai in riduzione» con due guardie indipendenti (filtro del chiamante e rilettura nello storage), ciascuna provata da un test e dalla mutazione che la toglie; (5) un fatto che non invalida il contenuto (chiusura con eccezioni, minimizzazione, rinnovo) entra nel registro delle impronte solo con il riscontro nel database; (6) una verifica incrementale si aggancia all'ultimo elemento già verificato (àncora), non riparte da zero; (7) spostare un'azione dall'API al lavoratore (annullamento, revisione Z3/W1–W3) richiede un'uscita pulita per OGNI stato intermedio (in attesa, approvata, applicata: respinta dal SISTEMA se è cambiato qualcosa prima dell'applicazione, solo controllo d'integrità dopo, blocco con allarme solo per manomissione con un'uscita «Respingi») e un recupero al deploy per il lavoro nato con il codice vecchio fra migrazione e Chart (idempotente, con un confine non falsificabile) | Rilascio R09 |
| 2026-10-30 | **R10**: (1) un parametro a INSIEMI (metodi, permessi, avvisi) dichiara il verso SOTTOINSIEME o SOVRAINSIEME e il motore confronta per INCLUSIONE, mai per numero di elementi (un elenco più corto con un codice nuovo non è «più sicuro»); (2) un'azione mai delegabile si nega a tre livelli indipendenti (formato del parametro, vincolo del database, controllo del servizio sul token) e un token con ruolo personalizzato non la ottiene qualunque cosa contenga; (3) chi assegna o compone permessi non concede ciò che non ha; ogni cambio di permessi alza una versione che la revoca confronta (serve un nuovo accesso); (4) un segreto monouso (invito) si consuma nella STESSA transazione dell'effetto con un UPDATE condizionato sullo stato atteso, e ogni passo intermedio (passaggio in Redis legato al browser) ha un'uscita pulita: scaduto si ricomincia, revocato non scrive nulla; (5) nessuna enumerazione: stesso errore e stesso costo per utente sconosciuto, credenziali errate e blocco; (6) un dato di rete fornito da un intermediario (PROXY protocol, `X-Forwarded-For`) si legge solo dalle sorgenti fidate configurate (le sottoreti reali del bilanciatore, mai la VPC) e lì è obbligatorio: l'IP reale del cliente viene SOLO da proxy fidati, altrimenti è quello della connessione; (7) un limite di frequenza è una RISERVA ATOMICA prima dell'azione (INCR e confronto in un passo, restituzione se l'esito non conta), mai «leggi, esegui, poi conta»: si prova con richieste in parallelo; (8) i controlli di permesso stanno in un punto centrale (`libs/shared/security/permessi.py`) e un test statico, rotta per rotta, fa fallire ogni endpoint che autorizza per ruolo base senza controllare anche il permesso | Rilascio R10, revisione indipendente V1–V4 e U1 |
| 2026-10-31 | **R11**: (1) un dato versionato che ne eredita un altro fissa la VERSIONE della base (`estende_versione`), mai «la più alta»: una versione nuova della base non deve cambiare un profilo già pubblicato; (2) le versioni pubblicate di un dato normativo hanno un'impronta registrata (`impronte.json`) e un test fallisce se cambiano; (3) una versione nuova di un dato normativo che rende più severo il versamento ha **adozione esplicita** per ambito (classe), con anteprima e applicazione programmabile, invece di entrare per data per tutti (§8.4, §8.13, §13); (4) il livello inferiore può solo RESTRINGERE ciò che deriva dalla norma: composizione restrittiva, regole che proteggono un obbligo normativo «solo più severe», piano che blocca la ridefinizione; (5) una conformità a uno schema esterno si dichiara solo con lo schema UFFICIALE registrato con impronta e integro; senza, l'esito dice «non disponibile» (mai «conforme») e i test di conformità si saltano con il motivo, con un controllo indipendente dall'esito del validatore; (6) un dato normativo si prende dalla fonte ufficiale con URL, edizione e data annotati; la parte non letta resta «da riscontrare»; (7) revisione T1–T4: una scelta di profilo ha un verso «più sicuro» a OGNI livello (anche accordo e modifiche programmate), confrontato sugli obblighi; l'impronta di un artefatto esterno copre TUTTI i byte effettivamente caricati (letti una volta, compilati da quei byte, risolutore chiuso sui file registrati); un flag di fiducia («ufficiale») non sta mai in dati modificabili (né nel file che attesta né in un JSON accanto), ma nel codice rivisto; un cambio di semantica su configurazioni esistenti arriva con un rapporto pre-deploy | Rilascio R11, revisione indipendente T1–T4 |
| 2026-10-31 | **R12a**: (1) un documento che autorizza un'azione distruttiva è LEGATO alla pratica (identificativo nel contenuto firmato da chi è autorizzato, mai nel testo libero del file) e lo stesso documento non vale per due pratiche; la transizione delle pratiche aperte ha un confine scritto solo dalla migrazione; (2) un segreto che attraversa code persistenti si SIGILLA per il solo servizio che lo usa, legato al contesto (ente, oggetto, destinatario: cambiare il destinatario non deve far partire il segreto altrove) e si toglie dalla riga dopo l'uso; senza chiave nessun invio e nessuna creazione (controllo PRIMA di ogni scrittura); (3) ogni scrittura di un passo con lease verifica un FENCING TOKEN nella propria transazione (riga del lease bloccata fino al commit), non solo il rinnovo del lease; (4) un lavoro esterno che può non terminare gira in un PROCESSO che si termina (tempo, annullamento, memoria), mai in un thread lasciato a sé; (5) le letture successive di un oggetto si legano alla VERSIONE di cui si sono calcolate le impronte (VersionId/ETag); (6) contro il DNS rebinding la connessione va all'indirizzo già controllato (pinning), non a una seconda risoluzione; (7) un rinnovo «mai in riduzione» vale per OGNI oggetto conservato, compresi quelli del registro, con elenco alimentato dalla scrittura e censimento per l'esistente; (8) revisione: un controllo di riuso per impronta si limita ai documenti che autorizzano e vale per il PRIMO caricato (una copia successiva non deve bloccare la pratica originale); una tabella nuova di un registro protetto si concede al solo gruppo del servizio che la usa, mai al gruppo comune dei servizi; (9) una scadenza complessiva si controlla dentro OGNI lettura bloccante (timeout di lettura = tempo residuo), non solo fra una lettura e l'altra; (10) la pulizia di un segreto fatta dalla migrazione si ripete a ogni giro di un lavoratore, perché i pod vecchi continuano a scriverlo durante il deploy | Rilascio R12a, revisione di sicurezza |
| 2026-10-31 | **R12b**: (1) ogni modifica di un autenticatore (passkey, password, codice a tempo, codici di recupero) richiede una conferma dell'identità RECENTE legata alla SESSIONE che modifica (mai solo alla persona: un'altra sessione della stessa persona non ne approfitta), con finestra parametrica dal motore e fail-closed se lo stato della conferma non si legge; (2) «non lasciare la persona senza accesso» si decide con le righe delle sue credenziali BLOCCATE prima del conteggio e con i metodi ATTUALI dell'ente, e si prova con due revoche in parallelo; (3) un segreto che usa un solo lavoratore non arriva al Deployment esposto e il controllo d'avvio è coerente con il ruolo del processo: il processo esposto NON parte se lo riceve, il lavoratore non parte senza; il controllo dei manifest lo impone (`CREDENZIALI_SOLO_LAVORATORE`); (4) chi scrive davvero su un topic (anche dall'outbox) ne è produttore in `infra/kafka/topics.yaml`, fonte dei permessi IAM: aggiungendo un produttore si ricontrollano gli altri scrittori dello stesso topic; (5) il legame documento-pratica vale per OGNI pratica con un'autorizzazione del produttore nella macchina a stati, con il riuso controllato solo fra pratiche dello stesso tipo e contro il documento caricato prima; una regola nuova su pratiche aperte ha sempre la sua transizione scritta solo dalla migrazione | Rilascio R12b |
| 2026-10-31 | **R12b, revisione**: (1) un contatore di tentativi con scadenza si incrementa e si fa scadere in UN passo atomico (script Lua: INCR e, se manca, EXPIRE), mai INCR ed EXPIRE separati: una chiave rimasta senza scadenza è un blocco permanente; (2) l'audit di un'azione di sicurezza si scrive e si conferma PRIMA dello stato effimero che la rende efficace (chiave in Redis, cache): se l'audit fallisce lo stato non nasce; (3) ogni Deployment riceve solo i segreti che usa (`segretiServizio: false` per i lavoratori con Secret proprio, regola `SOLO_SEGRETI_PROPRI` nel controllo dei manifest); (4) l'OpenAPI si rigenera prima di lanciare la corsa completa, mai durante: una modifica fatta a corsa avviata la rende falsamente rossa | Revisione R12b |
| 2026-10-31 | **R12c**: (1) ogni modifica del frontend si controlla a più larghezze (almeno 390, 768, 1280, 1850 px) in chiaro e scuro e porta con sé prove automatiche obbligatorie di sovrapposizione e taglio della testata, scorrimento orizzontale e contrasto AA (axe-core): una schermata guardata a una sola larghezza non è una verifica; (2) mai dati dimostrativi mescolati ai dati reali in una console vera: righe di esempio solo in sviluppo, dietro un interruttore esplicito e con una fascia che le dichiara; senza dati, uno stato vuoto che spiega e offre l'azione | Rilascio R12c, audit grafico |
| 2026-10-31 | **R13a**: (1) un servizio esterno qualificato (firma, marca) sta dietro un'interfaccia unica con un registro dei tipi (aggiungere un prestatore = un costruttore, nessun chiamante cambia) e si configura dalla console senza segreti (i segreti nel Secret, le librerie caricabili fissate al deploy); (2) ciò che un prestatore restituisce si VERIFICA prima di usarlo (firma prodotta con il verificatore di R08, marca con nonce, impronta, politica, catena e orario) e una risposta che non passa vale come un prestatore irraggiungibile (ripiego), mai come un successo; (3) un fornitore finto è vietato in produzione a tre livelli (console, avvio del servizio, costruzione dell'adattatore); (4) uno stato «non disponibile» esplicito (`NON_FIRMATO`) con allarme, e un parametro che lo rende bloccante (fail-closed) per l'esercizio, con la transizione dei lavori in volo scritta dalla migrazione; (5) ciò che si firma si rilegge dallo storage alla VERSIONE registrata con l'impronta riverificata; (6) al prestatore va solo l'impronta, mai il documento | Rilascio R13a |
| 2026-10-31 | **R13a, revisioni**: (1) una scrittura WORM che precede l'aggiornamento della riga è IDEMPOTENTE: la chiave contiene l'impronta del contenuto (`Sigillo_<nome>.<sha256[:24]>.p7m`), così un crash o un lease perso fra WORM e UPDATE non blocca la ripresa, e un errore per elemento si rinvia con attesa crescente senza fermare il lotto; (2) un segreto destinato a un servizio esterno è LEGATO al suo host (e al pin) nella stessa voce del Secret: un indirizzo configurato in console che esce dagli host non riceve mai il segreto; (3) un trigger che protegge le colonne di stato vale anche su INSERT (BEFORE INSERT e diritti di colonna), non solo su UPDATE: chi accoda non deve poter inserire righe già «fatte» | Revisioni R13a |
| 2026-10-31 | **R13b**: (1) un canale esterno con segreti (PEC, cliente OIDC) segue la regola dei fornitori di R13a: interfaccia unica con registro dei tipi, configurazione in console senza segreti, segreti nel Secret del SOLO processo che li usa e LEGATI agli host fissati al deploy (ricontrollati a ogni connessione); (2) una prova che arriva da un terzo (ricevuta PEC, UserInfo, trust chain) si accetta solo sui dati FIRMATI e verificati contro radici o chiavi fissate in configurazione, mai su intestazioni o campi non firmati, e l'associazione all'oggetto (msgid, mittente, destinatario, `sub`) si controlla sugli stessi dati firmati; (3) uno stato che dipende da una prova esterna si raggiunge solo se la prova è registrata (trigger che pretende la ricevuta associata) e la prova è append-only; (4) un segreto condiviso con il cliente (HMAC dei webhook) si ruota con un periodo di doppia firma misurato dall'orologio di chi firma e un ritiro anticipato; (5) un avviso PERSONALE non passa dai canali dell'ente (eventi, webhook): ha una tabella propria letta solo dalla persona; (6) un'opzione di processo ancora da decidere si predispone come parametro con il predefinito uguale al comportamento esistente, fotografato alla creazione della pratica | Rilascio R13b |
| 2026-10-31 | **R13b, revisione**: (1) di una ricevuta firmata (PEC) si accetta solo la struttura DIRETTA della parte firmata (`daticert.xml` figlio diretto del `multipart/mixed`), mai un contenuto annidato (`message/rfc822`), e le intestazioni che la qualificano sono obbligatorie e uguali ai dati firmati; (2) nella federazione OpenID le chiavi di un'entità si prendono SOLO dalla dichiarazione del livello superiore e si confrontano per impronta (RFC 7638), mai per solo `kid`; gli indirizzi da scaricare stanno negli host ammessi; (3) i dati di prova sono sempre fittizi (domini `.example`/`.invalid`, nessun ente o persona reale) | Revisione R13b |
| 2026-10-31 | **R14**: (1) un lavoro che fallisce sempre non ritenta all'infinito: dopo N tentativi (lease scaduti compresi) va in uno stato di errore ESPLICITO con codice del catalogo, allarme e ripresa manuale documentata in un runbook il cui comando è eseguito da un test; lo stato sospeso conta come «lavoro in corso» per le transizioni e un'esecuzione iniziata resta non respingibile anche da sospesa; (2) un vincolo di concorrenza di un registro append-only sta nel DATABASE (colonna scritta dal trigger, ignorando il chiamante, e indice unico), non solo nel lock applicativo, e si prova con due connessioni dirette senza lock; (3) una retention di dati protetti da DELETE si fa con una funzione SECURITY DEFINER con soglia minima non superabile e un limite per chiamata, con il trigger come seconda guardia indipendente (ognuna con test e mutazione), lease e fencing per lavoro in tabella; (4) un processo senza HTTP non apre porte per le metriche: le calcola dal database chi ha già `/metrics`; (5) ogni metrica ha il prefisso `verumdoc_`, ogni allarme citato in documentazione o runbook esiste e ogni allarme a pagina dice cosa fare, controllato da `tests/integrity`; cruscotti come file versionati; (6) mai URI a nome di un ente terzo (UNI, AgID) per formati propri: namespace del conservatore dichiarato provvisorio; (7) un recupero di arretrato si aggancia a un'impronta già impegnata (manifest verificato contro il registro), mai all'oggetto di oggi, e parte solo quando la risorsa esterna è davvero attiva; (8) le mutazioni di una migrazione si provano applicando l'SQL mutato a una copia del database di prova | Rilascio R14 |
| 2026-10-31 | **R14, revisione**: (1) un confronto di coerenza fra due checkpoint si fa SEMPRE quando entrambi ci sono, anche a pari dimensione (registro biforcato), mai solo nel caso «più grande»; (2) un formato di un artefatto WORM che cambia si fissa nel record della lavorazione al primo tentativo e la ripresa ricostruisce con QUEL formato (stessi byte), con la prova dei byte contro il codice precedente; (3) ogni `CREATE INDEX CONCURRENTLY … IF NOT EXISTS` è preceduto dal DROP dell'indice INVALIDO; (4) uno stato d'errore esplicito ha un'uscita nella console (ruolo fisso del conservatore, permesso mai delegabile, lock, versione attesa, audit, avviso), il comando SQL resta solo per l'emergenza; (5) i tentativi si azzerano quando il lavoro avanza; (6) una versione dello storage si sceglie per impronta, mai «la corrente» | Revisione R14 |
| 2026-11-01 | **R15**: (1) un documento normativo si GENERA dalla configurazione reale, dal database (transazione READ ONLY, solo configurazione, nessun dato personale) e dai registri: un dato che manca è un blocco «DA COMPILARE» con chi lo fornisce e dove, mai un valore predefinito; un requisito non riscontrato sul testo ufficiale è «da verificare sul testo ufficiale», mai inventato; (2) l'unico segnaposto ammesso nei documenti generati è quello dichiarato, controllato da `verifica_documentazione.py`; (3) lo stato di una funzione descritta cita codice e test e si abbassa da sé se una criticità collegata è aperta; (4) le versioni di un documento sono immutabili nel database (trigger anche BEFORE INSERT: versione successiva, impronte ricalcolate dal database, contenuto diverso dal precedente, richiesta in corso) e vanno nel WORM con l'impronta nella chiave; (5) ciò che il servizio in esercizio non ha (repository) arriva come fotografia del rilascio senza date, controllata in `make verifiche` | Rilascio R15 |
| 2026-11-01 | **R16**: (1) un'azione con un segreto MONOUSO della persona (OTP) passa dal servizio esterno con il token della PERSONA, che lo riverifica e usa solo la credenziale associata a quel `user_id` (mai dalla richiesta); l'adattatore usa il segreto una volta sola e non ritenta l'operazione che lo consuma; l'esito è verificato e legato all'identità attesa della persona due volte (adattatore e confronto finale), poi il database lo riverifica (trigger: credenziale ATTIVA della persona e certificato atteso) e chi lo archivia lo riverifica ancora prima del WORM (o lo respinge con allarme); (2) un documento da firmare ha un punto d'aggancio GENERICO (tipo libero, idempotente per impronta) così un rilascio successivo lo usa senza dipendenze; (3) un limite di frequenza di un servizio con più repliche è una riserva atomica in Redis (INCR, scadenza, confronto e restituzione in un solo script), obbligatoria in produzione, e un contatore irraggiungibile ferma l'operazione costosa (503), mai la lascia passare; (4) una consegna «almeno una volta» verso un terzo che dà ricevute si rende «senza doppioni» con un identificativo DETERMINISTICO e una riconciliazione con le ricevute prima del reinvio, che si fa solo dopo una lettura riuscita delle ricevute oltre la soglia; (5) un cambio di un parametro che autorizza (metodi d'accesso, permessi concedibili) alza una VERSIONE per ambito scritta da un trigger SECURITY DEFINER (anche per le modifiche programmate) e la revoca la confronta con il token nella query già esistente: nessun costo in più per richiesta; (6) una politica a livelli di un terzo (metadata_policy della federazione) si combina dall'alto verso il basso e il livello inferiore non la allenta mai; un operatore o claim critico non compreso invalida; un marchio si accetta solo con la firma dell'emittente verificata sulla SUA catena e con l'emittente ammesso sia dalla configurazione sia dall'autorità di vertice; (7) il tracing è spento per impostazione, usa un fornitore proprio (non globale: niente strumentazioni automatiche di terzi con dati effettivi), esporta solo attributi di un elenco chiuso e propaga il contesto solo verso gli host interni; (8) una pulizia del lint su codice esistente si fa con trasformazioni verificate per AST identico, `noqa` motivati dove la regola cambierebbe il comportamento ed esenzioni motivate per configurazione, mai toccando le migrazioni già rilasciate | Rilascio R16 |
| 2026-11-01 | **R17**: (1) una decisione che porta a un effetto irreversibile (scarto, annullamento, rettifica, riversamento in uscita, ripresa) si CONFERMA presso un servizio DISTINTO da quello che la registra: il gateway firma con una chiave solo sua un oggetto canonico {pratica, azione, versione attesa, impronta del contenuto deciso, utente, ente, istante, scadenza breve, nonce} dopo una conferma dell'identità recente della sessione, che CONSUMA (una conferma dell'identità = una decisione); chi registra la conferma non sceglie istante né versione (trigger, diritti di colonna) e la registra nella stessa transazione della decisione; chi esegue la riverifica con le chiavi pubbliche della PROPRIA configurazione (mai dal database scrivibile da chi registra), ricalcola il contenuto da ciò che sta per eseguire e consuma il nonce in un registro solo suo; (2) un'opzione di processo decisa dal committente con una deroga per un singolo ente concessa SOLO dal conservatore si fa con una voce riservata al livello ENTE (non con un limite che l'ente dovrebbe poi usare), con la migrazione che toglie i valori che diventerebbero deroghe non concesse; (3) un test non dipende dalla data della corsa: i dati di prova datati hanno una fixture che ne crea le partizioni (con la funzione e il ruolo di esercizio), e un test su una risorsa condivisa (partizioni di una tabella) guarda SOLO la risorsa che ha creato, mai i contatori complessivi; (4) all'unione di rami paralleli si rinumera ciò che collide (migrazione successiva con la down della prima, registri dal primo numero libero) con tutti i riferimenti, e una forma canonica usata da due linguaggi si prova con gli stessi vettori nei due | Rilascio R17 (unione R15 + R16, collaudo finale) |
| 2026-11-01 | **R17, revisione e audit della console**: (1) una conferma consumata vale solo per il GIRO di esecuzione in cui è registrata: chi esegue scrive un fatto di fine giro (`GIRO_CHIUSO`) e rifiuta le conferme precedenti; un'azione automatica del SISTEMA si riconosce da un fatto scritto dal SOLO ruolo che la compie (`RIPRESA_SISTEMA` del lavoro periodico), mai da campi della pratica scrivibili dall'API; (2) un'esenzione concessa dalla migrazione alle pratiche aperte al deploy vale solo fino alla fine del giro in corso; (3) un contesto di tracciamento esterno non decide il campionamento (rapporto configurato anche per i genitori remoti) e si accoglie solo il `traceparent`, mai `tracestate`; (4) un documento normativo si firma sulla versione LEGGIBILE (HTML stampabile, servito con CSP `default-src 'none'` e mostrato in una cornice isolata) e solo dal ruolo che la norma indica, mai sui dati da cui si genera; (5) il downgrade di una migrazione che distruggerebbe righe immutabili si rifiuta; (6) la console ricava pagine, voci e comandi dai permessi EFFETTIVI della sessione (un solo modello, copia verificata da un test contro i modelli del servizio), nessun rinvio muto: una pagina non permessa dice perché; il servizio rifiuta comunque, anche ai ruoli fissi; la guida generata fallisce se pagina e menu divergono; (7) il backend finto degli e2e copre i dati di ogni pagina centrale e le azioni dipendenti dal ruolo, altrimenti una perdita di permessi nell'interfaccia non si vede | Revisione di sicurezza R15–R17, audit finale della console (`docs/audit/AUDIT_FINALE_2026-11-01.md`) |
| 2026-11-14 | **R18, apertura open source** (VD-P-190): (1) ogni file sorgente NUOVO (`.py`, `.ts`, `.tsx`, `.js`, `.mjs`, `.java`, `.sql`, `.sh`, `.ps1`, `.tf`, Dockerfile, YAML di infra e CI) porta in testa, dopo shebang e direttive, le righe `SPDX-FileCopyrightText: 2026 Contributori di VerumDoc` e `SPDX-License-Identifier: EUPL-1.2` nel commento del suo linguaggio (nei template Helm un commento di template); un generatore scrive l'intestazione nei file che genera; `python tools/opensource/controlla_pubblicazione.py --applica-intestazioni` la aggiunge; (2) nel repository non entrano segreti nemmeno di prova (chiavi generate nei test), percorsi locali, caselle personali, nomi di persone reali, documenti d'ufficio, immagini né documentazione riservata di terzi: il controllo è in `make verifiche`, i termini riservati stanno nel file locale ignorato `tools/opensource/pubblicazione.locale.toml`; (3) una dipendenza nuova ha licenza compatibile con la EUPL-1.2, annotata in `THIRD_PARTY_NOTICES.md` (§20.8) | Apertura open source, `docs/sviluppo/PUBBLICAZIONE_OPEN_SOURCE.md` |
