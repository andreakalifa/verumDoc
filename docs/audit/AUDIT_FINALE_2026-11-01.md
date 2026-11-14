# Audit funzionale e grafico finale della console — R17 (collaudo)

> Data: 01/11/2026 · Repository VerumDoc, commit `a4976ba` (R17 prima delle correzioni) · Sola lettura: nessun file del repository modificato durante l'audit.
> Copia nel repository (solo testo): i percorsi delle schermate e dei dati grezzi dell'audit, che restano fuori dal repository, sono stati tolti. Lo stato dopo le correzioni è nel §7.
> Riferimenti: `docs/audit/AUDIT_FUNZIONALE_2026-10-20.md`, `docs/design/AUDIT_GRAFICO_2026-10-20.md`, `frontend/DESIGN.md` («Partenze» + motivi ferroviari R12c), `frontend/PRODUCT.md`, `docs/registro/criticita.md`, `GUIDA_FUNZIONALITA.md` (R15).

## 1. Metodo

- **Prove esistenti**: `npx playwright test` (123 prove): **123 passate**. `tsc --noEmit` e `eslint src`: puliti.
- **Giro automatico** (giro Playwright scritto per l'audit, backend finto `e2e/finto.ts` importato senza modifiche): 5 profili (Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore, Consultazione; **nessun profilo «auditor»** esiste nel codice: `src/auth/ruoli.ts`) × 48 rotte (tutte quelle di `App.tsx`, più due pratiche, una bozza, una pagina «da realizzare») = 240 combinazioni. Per ciascuna: voce nel menu sì/no, rotta finale, h1, comandi visibili, errori di lettura, stati vuoti, codici grezzi nel testo, date ISO, scorrimento orizzontale a 390/768/1280/1850 px (390 anche in scuro), allineamento alla colonna, bersagli piccoli, fuoco visibile sui primi 20 Tab, **axe-core completo (WCAG 2.0/2.1 A e AA) in chiaro e in scuro a 1280 px**. 960 schermate (fuori dal repository).
- **Supplemento** (prova di supporto): dati per le pagine che il finto non copre (Pacchetti conservati, Ricerca, Classi documentali), classe del versamento, scheda Esibizioni dell'operatore. Una terza prova per sotto-navigazione e fuoco.
- **Lettura del codice** per le incoerenze ruolo/permesso (`App.tsx`, `Layout.tsx`, `libs/verumdoc_rules/permessi.py`, servizi).
- Limiti: 768/1850/390 misurati ridimensionando la pagina già caricata; il finto non dà dati per 14 endpoint (vedi R17-18), per cui archivio, SLA, registro delle operazioni, manuale, contratti e nomine sono stati visti solo nello stato d'errore (che è corretto) o con il supplemento.

## 2. Sintesi

La console è **solida sul piano grafico e dell'accessibilità**: zero violazioni axe (A/AA) su 240 combinazioni in chiaro e in scuro; nessuno scorrimento orizzontale tranne una pagina; testata, sotto-navigazione, testa di pagina e contenuto allineati alla stessa colonna a ogni larghezza; fuoco sempre visibile; errori di lettura onesti (`EsitoCritico`), passaggio a livello e binario usati come prescrive DESIGN.md. I problemi residui sono soprattutto **funzionali e di coerenza ruolo ↔ permesso ↔ pagina**: l'esibizione è aperta a chi non può chiederla e chiusa a chi può; la firma del manuale porta a firmare un JSON mentre la documentazione promette la firma sulla versione stampabile; il versamento resta a modulo fisso con classe predefinita scritta nel codice.

| Gravità | Rilievi |
|---|---|
| Alta | 4 |
| Media | 14 |
| Bassa | 14 |
| **Totale** | **32** |

Rilievi del 20/10 riferiti alla console: **34**; risolti **23 (68%)**, parziali 6, ancora aperti 5 (§5).

## 3. Rilievi

Legenda ruoli: SA Amministrazione del servizio, RSC Responsabile del servizio, AE Amministrazione dell'ente, OP Operatore, CO Consultazione.

| ID | Pagina | Ruolo | Larghezza / tema | Descrizione | Gravità | File probabile | Correzione suggerita |
|---|---|---|---|---|---|---|---|
| R17-01 | /distribution | OP | tutte | La scheda «Esibizioni» è nel menu dell'operatore (`TUTTI`) e il ruolo ha il permesso `esibizione:richiedi` (`permessi.py:97`), ma la rotta esclude OPERATORE con `redirect="/overview"`: un clic sulla scheda riporta in silenzio al quadro. L'operatore non può chiedere un'esibizione dalla console. | Alta | `src/App.tsx:164-172`, `src/components/Layout.tsx:72` | Ruoli della rotta e della voce derivati dal permesso: SA, RSC, AE, OP per la richiesta; CO solo storico. |
| R17-02 | /distribution | CO | tutte | La Consultazione vede il modulo «Prepara l'esibizione» completo, ma non ha `esibizione:richiedi` (`permessi.py:98`) e il servizio lo rifiuta (`distribution.py:160`): compila e riceve un 403. | Alta | `src/pages/DistributionPage.tsx:228-330` | Senza permesso: niente modulo, un `.vd-avviso` «Sola lettura» e il solo storico. |
| R17-03 | /configurazione/formati | tutti | 390, chiaro e scuro | Il tabellone «Catalogo base del conservatore» impilato collassa la seconda colonna a un carattere (testo verticale «R-i-c-o-n-o-s-c-i-u-t-a…») e la pagina si allarga a 467 px. Causa: `.vd-impila tr { grid-template-columns: minmax(7.5rem, auto) minmax(0,1fr) }` con una prima colonna di testo lungo (non un orario). È l'unico scorrimento orizzontale trovato; la pagina non è nelle prove a 390 di `r12c.spec.ts`. | Alta | `src/pages/ConfFormatiPage.tsx:45`, `src/index.css:221` | `vd-impila--pieno` su questo tabellone; in generale `minmax(7.5rem, 40%)`; aggiungere la pagina a `PAGINE` di `r12c.spec.ts`. |
| R17-04 | /firme-responsabile, /documentazione-servizio | SA, RSC | tutte | Vicolo cieco sulla firma del manuale. «Prepara il manuale da firmare» mette in firma `manuale_conservazione.json` (dati, non il documento leggibile: `documenti_conservatore.py:46`); «Documentazione del servizio» dice «La firma del responsabile si appone sulla versione stampabile» ma non offre alcun comando per mandarla in firma. Il responsabile firma un JSON o non sa come firmare l'HTML. | Alta | `src/pages/DocumentazioneServizioPage.tsx:220`, `src/pages/FirmeResponsabilePage.tsx`, `services/dashboard-api/app/routers/documenti_conservatore.py:46` | Comando «Metti in firma questa versione» sulla versione stampabile (HTML o PDF/A) che crea la richiesta in «Firme del responsabile»; togliere la preparazione dal JSON. |
| R17-05 | /versamento | OP, AE, SA, RSC | tutte | Classe predefinita `useState("fattura")` scritta nel codice. Se le classi dell'ente non hanno quel codice (es. `FATTURA`) il select mostra la prima opzione ma lo stato invia `fattura` (VRS-105, confronto sensibile alle maiuscole in `regole_db.py:127`). Le opzioni mostrano il codice e non il nome della classe. | Media | `src/pages/VersamentoLivePage.tsx:98, 312-316` | Nessun valore predefinito («Scegli la classe»), `value=codice`, etichetta = `nome`. |
| R17-06 | /versamento | OP, AE | tutte | Modulo fisso a 7 metadati dell'Allegato 5 e un solo documento per volta: i metadati obbligatori della classe (es. `numero_fattura`) non compaiono e arrivano come rifiuto. Residuo P1 del 20/10. | Media | `src/pages/VersamentoLivePage.tsx:103-140` | Campi generati dal profilo della classe (`/v1/configurazione/profili`); versamento a lotti in seguito. |
| R17-07 | /distribution, /aip | tutti | tutte | L'esibizione chiede ancora di incollare l'UUID di 36 caratteri; dalla scheda del pacchetto non c'è «Esibisci» né un indirizzo precompilato. | Media | `src/pages/DistributionPage.tsx:166, 236-242`, `src/pages/AIPPage.tsx` | Comando «Esibisci» nel dettaglio del pacchetto verso `/distribution?aip=<id>`, campo precompilato. |
| R17-08 | 17 pagine (Pacchetti conservati, Esibizioni, Impostazioni, Profili, Bozze, Proposte, Contratti, Nomine, Notifiche, Pratiche, Nuova pratica, SFTP, Inviti, Classi e accordi, Quadro…) | SA, RSC | tutte | Due selettori dell'ente nella stessa vista: «Ente su cui lavori» in sotto-navigazione e un secondo «Ente» nei filtri, con opzioni diverse («Tutti gli enti» contro «Scegli un ente», «(ente del tuo profilo)» solo nel secondo). Entrambi scrivono la preferenza persistente: cambiare un filtro cambia l'ente di tutta la console. DESIGN.md (R12c): «la scelta dell'ente è una sola, in sotto-navigazione». | Media | `src/components/SceltaEnte.tsx:58-85` e le pagine che usano `<SceltaEnte>` | Togliere `SceltaEnte` dalle pagine sotto la testata; dove serve «Tutti gli enti» gestirlo nel selettore unico. |
| R17-09 | Configurazione (12 pagine), Conformità (7) | SA, RSC | 768, 1280 e anche 1850 | La sotto-navigazione non sta nella colonna (1360 px) nemmeno a 1850: da 1 a 10 pagine restano nascoste, indicate solo dalla sfumatura (es. a 1280 su «Quote degli enti» non si vedono Impostazioni, Profili, Firme, Fornitori, Ciclo di vita). | Media | `src/components/Layout.tsx:74-103` | Dividere Configurazione in due aree (Regole / Servizio) o andare a capo su due righe sopra i 1024 px. |
| R17-10 | sotto-navigazione e schede di testata | tutti | 1280, 1850 | L'alone del fuoco sulle schede che scorrono è tagliato dall'`overflow` dell'elenco: restano due barre gialle verticali. Il fuoco resta percepibile ma non è quello di DESIGN.md. | Media | `src/index.css` (`.vd-schede ul`, `.vd-sotto-schede`) | `padding-block` sull'elenco pari all'alone, oppure fuoco interno (sottolineatura gialla 4 px + contorno inset) per le schede. |
| R17-11 | 13 rotte con `redirect="/overview"` (Esibizioni, Enti serviti, Utenti, Notifiche, Livelli di servizio, Manuale, Registro delle operazioni, Fascicoli, Contratti, Nomine, Code di scarto) | OP, CO, AE | tutte | Reindirizzamento muto al quadro, mentre le altre rotte mostrano «Pagina non accessibile con il tuo profilo» con i ruoli ammessi. Un indirizzo salvato o condiviso porta al quadro senza spiegazione. | Media | `src/App.tsx` (props `redirect`) | Togliere `redirect` ovunque: una sola risposta, `AccessoNegato`. |
| R17-12 | /versamento | CO | tutte | La rotta è protetta solo dallo scope `versamento:ud`: dall'indirizzo la Consultazione apre il modulo completo (il permesso `versamento:esegui` non ce l'ha). La guida generata lo elenca fra le pagine della Consultazione. | Media | `src/App.tsx:131-136` | `RoleGuard` OPERATIVI insieme a `ScopeGuard`. |
| R17-13 | GUIDA_FUNZIONALITA.md | OP, CO | — | La guida generata eredita le incoerenze: OP senza «Esibizioni e copie» pur avendo il permesso, CO con «Versa un documento». | Media | `tools/conformita/genera_documenti.py`, `GUIDA_FUNZIONALITA.md` | Rigenerare dopo R17-01/02/12; far fallire la generazione quando pagina e permesso divergono. |
| R17-14 | Impostazioni, Firme, Formati, Accesso, Firma qualificata, Esibizioni, Pratica, Registro | tutti | tutte | Valori enum mostrati come valori, non come parole: `AMMESSO`, `DA_CONVERTIRE`, `CON_REVOCHE`, `NON_FIRMATO`, `SPID_CIE`, `PASSWORD_TOTP`, «canale PORTALE», «classi FATTURA», `FIRMA_NON_VALIDA`. Il codice della voce in mono sotto la descrizione è previsto da DESIGN.md; il valore no. | Media | `src/console/CampoValore.tsx`, `src/console/parole.ts`, `DistributionPage.tsx`, `PraticaPage.tsx`, `RegistroPage.tsx` | Vocabolario delle scelte in `parole.ts` (es. «Ammesso», «Da convertire», «Con controllo delle revoche»); codice solo come dettaglio. |
| R17-15 | /pratiche/:id | tutti i ruoli che la vedono | tutte | «Dati della pratica» mostra l'ente come UUID; i criteri come codici («classi FATTURA»). | Media | `src/pages/PraticaPage.tsx` | Ragione sociale dall'elenco degli enti; nome della classe. |
| R17-16 | /code-scarto (area Servizio) | SA, RSC | — | «Code di scarto» indica i messaggi non elaborabili (dead letter) ma nella stessa console «scarto» è lo scarto archivistico autorizzato (D.Lgs. 42/2004, pratiche): ambiguità terminologica per un responsabile della conservazione. | Media | `src/components/Layout.tsx:55`, `src/pages/CodeScartoPage.tsx:403` | «Messaggi non elaborati» o «Code dei messaggi in errore». |
| R17-17 | /manuale | SA, RSC | tutte | L'unico comando è «Scarica i dati del manuale (JSON)»; la versione leggibile e firmabile sta in «Documentazione del servizio», senza collegamento fra le due pagine (residuo 20/10). | Media | `src/pages/ManualePage.tsx:174-182` | Collegare alla versione stampabile più recente; valutare l'unione delle due pagine. |
| R17-18 | prove e2e | — | — | Il backend finto non simula 14 endpoint delle pagine centrali (`/dashboard/aip`, `/dashboard/classi-documentali`, notifiche, scarti, manuale, SLA, registro, contratti, nomine, api-keys…) e ignora il ruolo in `azioni_consentite` (Consultazione e Operatore vedono «Modifica» e «Respingi…»). Archivio, ricerca, SLA, registro, manuale, contratti e nomine non sono mai provati con dati né con axe, e una perdita di permessi nell'interfaccia non verrebbe colta. | Media | `frontend/e2e/finto.ts` | Dati per gli endpoint mancanti; `azioni_consentite` dipendenti dal ruolo come nel servizio vero. |
| R17-19 | /configurazione/formati | tutti | tutte | Data ISO visibile: «in vigore dal 2026-10-31». | Bassa | `src/pages/ConfFormatiPage.tsx` | `dataIt`. |
| R17-20 | Manuale, Nomine, Enti, Utenti, Contratti, Quadro | — | — | Date ricavate con `slice(0,10)`: giorno UTC, non Europe/Rome (sbaglia vicino alla mezzanotte). | Bassa | `ManualePage.tsx:29`, `NomineRSCPage.tsx:23`, `TenantsPage.tsx:27`, `UtentiPage.tsx:27`, `ContrattiPage.tsx:22`, `OverviewPage.tsx:168` | Una sola funzione `Intl.DateTimeFormat("it-IT", {timeZone: "Europe/Rome"})`. |
| R17-21 | /aip | tutti | tutte | Conservazione permanente (scadenza nulla) mostrata come «non indicata» invece di «Illimitata». | Bassa | `src/pages/AIPPage.tsx:246, 435` | «Illimitata» quando la classe è permanente. |
| R17-22 | /configurazione/profili | tutti | 1280 | Bottoni-link dei profili alti 22 px (sotto i 24 px di WCAG 2.5.8 e i 44 px di DESIGN.md). | Bassa | `src/pages/ConfProfiliPage.tsx` | `.vd-link-bottone` con area minima 44 px. |
| R17-23 | /pratiche/:id | tutti | tutte | Nella sotto-navigazione «Pratiche» non è attiva sul dettaglio (`end` forzato da «/pratiche/nuova»). | Bassa | `src/components/Layout.tsx:117` | Attiva su `/pratiche/:id` escludendo solo `/pratiche/nuova`. |
| R17-24 | /pratiche/:id | — | tutte | La «Corsa della pratica» è un tabellone con «--:--», la «Corsa del pacchetto» è la linea ferroviaria che vieta «--:--»: due forme per lo stesso concetto. | Bassa | `src/pages/PraticaPage.tsx`, `DESIGN.md` | Linea della corsa anche per le pratiche, o motivare la differenza in DESIGN.md. |
| R17-25 | /pratiche/:id, quadro | — | 1280+ | Note di sezione allineate al bordo destro della colonna (1248 px) mentre il contenuto della sezione si ferma a 852 px («Allegati», «Situazione dell'archivio»). | Bassa | `PraticaPage.tsx`, `OverviewPage.tsx` | Stessa larghezza massima per testa di sezione e contenuto. |
| R17-26 | /aip, /configurazione | — | — | Codici interni nel testo per l'utente: «(VD-P-27)», «VD-C-005». | Bassa | `AIPPage.tsx:188`, descrizioni del registro impostazioni | Togliere i codici di decisione dai testi. |
| R17-27 | /configurazione | SA, RSC | tutte | «Imposto dalla norma» accanto a «Modifica» sulla stessa voce (Idempotency-Key obbligatoria, livello conservatore): messaggio contraddittorio. | Bassa | `src/pages/ConfImpostazioniPage.tsx` (colonna «Chi decide») | «Imposto dalla norma, si stabilisce solo qui» quando la modifica è consentita. |
| R17-28 | area Enti | SA, AE | — | Due voci quasi uguali: «Utenti e accessi» e «Accessi e inviti». | Bassa | `src/components/Layout.tsx:85-86` | «Utenti e chiavi API» / «Inviti». |
| R17-29 | /overview (Arrivi) | tutti | tutte | Prima riga con l'identificativo tecnico «pdv00000» invece dell'oggetto (domanda 5 del 20/10). | Bassa | `src/pages/OverviewPage.tsx` | Oggetto in prima riga, identificativo in sottoriga. |
| R17-30 | /aip (dettaglio) | tutti | — | «Identificativo AIP»: sigla inglese, altrove «PdA». | Bassa | `src/pages/AIPPage.tsx:240` | «Identificativo del pacchetto (PdA)». |
| R17-31 | /documentazione-servizio | SA, RSC | tutte | Stati vuoti senza comando (il comando «Genera» è solo nella testa): DESIGN.md chiede che `Vuoto` porti l'azione. | Bassa | `src/pages/DocumentazioneServizioPage.tsx` | Bottone «Genera la documentazione» anche nel vuoto. |
| R17-32 | tutte | — | — | 483 attributi `style={{}}` fra pagine e componenti (erano 738; debito VD-C-027). | Bassa | `src/pages/*`, `src/components/*` | Continuare la riduzione con classi `vd-*`. |

## 4. Che cosa funziona (da non rompere)

- axe-core A/AA: **0 violazioni** su 240 combinazioni ruolo × pagina, chiaro e scuro (1280 px), e sulle pagine con dati del supplemento a 390 e 1280.
- Nessuno scorrimento orizzontale a 390, 768, 1280, 1850 px tranne R17-03; nessuna sovrapposizione reale (quelle segnalate dallo strumento erano intestazioni nascoste dei tabelloni impilati o righe a capo).
- Colonna unica: marchio, sotto-navigazione, h1 e testa di pagina allineati al pixel a tutte le larghezze.
- Fuoco visibile su tutti i primi 20 Tab di ogni pagina; «Vai al contenuto»; menu a scomparsa da tastiera (prove r12c).
- Testa di pagina con binario su tutte le pagine interne; passaggio a livello solo su fasce neutre/critiche; «Archivio integro» giallo pieno; stati come parole; tabelloni con orario.
- Errori di lettura sempre con `EsitoCritico` («Non significa che l'archivio sia vuoto», codice, riferimento, «Riprova»).
- 123/123 prove e2e, `tsc` e `eslint` puliti; bundle diviso per rotta.

## 5. Confronto con gli audit del 20/10/2026

| # | Rilievo del 20/10 | Stato R17 |
|---|---|---|
| 1 | Errori mostrati come «vuoto» (P0) | Risolto (`EsitoCritico`) |
| 2 | Stato globale finto «Sistema operativo» (P0) | Risolto (fascia con regola di verità) |
| 3 | Versamento fallito che resta «in corso» (P0) | Risolto (linea della corsa con fermata interrotta) |
| 4 | Classe predefinita `fattura` (P0) | **Aperto** → R17-05 |
| 5 | A successo solo un UUID | Risolto (segnatura nella corsa e nel registro) |
| 6 | Upload non raggiungibile da tastiera | Risolto |
| 7 | L'esibizione non consegna nulla (link, QR, attestazione) | Parziale: link e rigenerazione; scarico dal portale «da realizzare» |
| 8 | Esibizione con UUID incollato | **Aperto** → R17-07 |
| 9 | Richiedente fisso «dashboard-user» | Risolto (dal token) |
| 10 | Inutilizzabile sotto 900 px (P0) | Risolto (salvo R17-03) |
| 11 | Build rotta (P0) | Risolto |
| 12 | Dati inventati (Live, SLA conforme, «verificato») | Risolto |
| 13 | Accessibilità (contrasto, label, focus, dialog, skip link) | Risolto (axe 0; residuo R17-10) |
| 14 | Gergo e IA per tecnologia, 18 voci | Parziale: aree per compiti, ma 10 aree, 12 pagine in Configurazione, enum e «Code di scarto» (R17-09, 14, 16) |
| 15 | Modulo di versamento fisso, un documento per volta | **Aperto** → R17-06 |
| 16 | 738 stili in linea, Tailwind inattivo | Parziale: Tailwind tolto, 483 stili (R17-32) |
| 17 | Date con `slice`, nessun `Intl` | Parziale (R17-19, 20) |
| 18 | Azioni distruttive senza conferma | Risolto (conferma in riga) |
| 19 | Nessuna ricerca globale | Risolto |
| 20 | Nessun glossario o aiuto contestuale | **Aperto** |
| 21 | Manuale scaricato in JSON | Parziale: HTML in Documentazione, JSON resta in Manuale e in firma (R17-04, 17) |
| 22 | Accesso con API key | Risolto (SPID/CIE, credenziali personali) |
| 23 | Movimento: ingressi animati, niente reduced-motion | Risolto |
| 24 | Tre paginazioni diverse | Risolto (`Paginazione` a cursore) |
| 25 | Spinner e skeleton fermi | Risolto |
| 26 | Profili demo e dev-token nel bundle | Risolto (`import.meta.env.DEV`) |
| 27 | La home non risponde a «siamo a posto?» | Risolto |
| 28 | Nessuna coda «i miei versamenti» per l'operatore | Parziale (Pacchetti in arrivo, senza filtro «miei di oggi») |
| 29 | Bundle unico 830 KB | Risolto (lazy per rotta) |
| 30 | Google Fonts con `@import` | Risolto (Archivo servito dal progetto) |
| 31 | Identificativi e nomi lunghi che spingono le colonne | Risolto (`vd-a-capo`, verificato con oggetto di 170 caratteri) |
| 32 | Titolo/oggetto in prima riga nelle liste | Risolto in Pacchetti conservati (residuo R17-29 nel quadro) |
| 33 | Nessun export | **Aperto** |
| 34 | Personalità intercambiabile «kit SaaS» | Risolto (sistema «Partenze») |

**Risolti 23 su 34 (68%)**; parziali 6; aperti 5 (n. 4, 8, 15, 20, 33). Contando i parziali a metà: 76%.
Nuovi rilievi di R17: 27 dei 32; gli altri 5 sono residui del 20/10 riformulati (n. 4 → R17-05, n. 8 → R17-07, n. 15 → R17-06, n. 21 → R17-17, n. 16 → R17-32).

## 6. Schermate chiave

Le schermate dell'audit (390 e 1280 px, chiaro e scuro) e i dati grezzi del giro per ruolo non sono nel repository (REGOLE §25.13: nessuna immagine nel repository; le foto si rigenerano a richiesta con `FOTO=1 npm run e2e -- foto`). Le viste di riferimento erano:
1. Configurazione → Formati, Amministrazione del servizio, 390 px chiaro: R17-03, colonna collassata e pagina larga 467 px.
2. Firme del responsabile, Responsabile del servizio, 1280 px chiaro (con il fuoco sulla sotto-navigazione): R17-04 (firma di `piano_cessazione.json`), R17-09 (sotto-navigazione tagliata a 1280), R17-10 (alone del fuoco tagliato).
3. Esibizioni, Amministrazione del servizio, 390 px scuro: R17-07 (UUID da incollare), R17-08 (due selettori dell'ente), R17-14 («canale PORTALE»).
4. Quadro, Responsabile del servizio, 1280 px scuro: riferimento positivo (fascia neutra con passaggio a livello, Partenze/Arrivi, binario, tema scuro).

## 7. Stato dopo le correzioni di R17 (commit «wip: R17 correzioni»)

Fonte: `changelog/CHANGELOG-2026-11-12-rilascio-R17.md` (sezione «Revisione di sicurezza e audit della console»), `docs/registro/criticita.md` (VD-C-186).

| Gravità | Rilievi | Corretti | Parzialmente | Non corretti (VD-C-186) |
|---|---|---|---|---|
| Alta | 4 | 4 (R17-01…04) | — | — |
| Media | 14 | 14 (R17-05…18; di R17-06 resta il versamento a lotti, funzione nuova) | — | — |
| Bassa | 14 | 9 (R17-19, 20, 21, 23, 27, 28, 29, 30, 31) | 2 (R17-22 a 24 px invece di 44; R17-26 solo nei testi della console) | 3 (R17-24, 25, 32) |
| **Totale** | **32** | **27** | **2** | **3** |

Prove dopo le correzioni: e2e **152/152** con 2 lavoratori (nuove `e2e/r17_console.spec.ts`; backend finto con i dati di 14 endpoint, scope, permessi e azioni dipendenti dal ruolo; axe A/AA su 12 pagine in chiaro e scuro); typecheck, lint e build verdi.

Rilievi del 20/10 riferiti alla console, ricontati dopo le correzioni (stessa tabella del §5):

| Stato | Prima delle correzioni | Dopo le correzioni |
|---|---|---|
| Risolti | 23 (68%) | **28 (82%)**: in più n. 4 (R17-05), 8 (R17-07), 14 (R17-09, 14, 16), 17 (R17-19, 20), 21 (R17-04, 17) |
| Parziali | 6 | **4**: n. 7 (scarico dal portale), 15 (metadati della classe nel modulo, lotti da fare), 16 (483 stili, R17-32), 28 (filtro «miei di oggi») |
| Aperti | 5 | **2**: n. 20 (glossario), 33 (export) |

Contando i parziali a metà: 88%.
