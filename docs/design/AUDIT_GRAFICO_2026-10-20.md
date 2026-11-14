# Audit grafico di VerumDoc — critica e direzioni per la nuova personalità

> Data: 20/10/2026 · Target: `frontend/src` (React 18 + TS + Vite), 19 route.
> Metodo: doppia valutazione indipendente secondo il metodo *impeccable critique + audit*: **A** revisione di design su 92 screenshot reali (desktop 1440×900 e telefono 390×844, stati di caricamento, errore, vuoto, dati peggiori, flussi di versamento, esibizione, verifica, navigazione da tastiera) con API simulate realistiche; **B** detector automatico (statico e iniettato in pagina), axe-core, misure di contrasto, overflow e build. Le due valutazioni non si sono viste prima della sintesi.
> Metodi di revisione: critica di design, audit di accessibilità e di resa, prove con dati estremi, revisione delle animazioni.
> Richiesta del committente: **cambiare totalmente personalità** (redesign-overhaul). L'aspetto attuale è trattato come evidenza e anti-riferimento. **La direzione non è scelta in questo documento**: sono proposte tre direzioni (§9) per la decisione `VD-DR-01`.
> I rapporti di lavoro completi erano allegati interni e non sono pubblicati (R18).

---

## 1. Punteggi

### 1.1 Euristiche di Nielsen (valutazione A): **12/40 — Scarso**
| # | Euristica | Voto | Problema chiave |
|---|---|---|---|
| 1 | Visibilità dello stato | 1 | «Sistema operativo» sempre verde scritto nel codice; «Live» su un grafico con dati finti; spinner fermi (manca `@keyframes spin`); versamento in errore che resta «Validazione…» |
| 2 | Corrispondenza col mondo reale | 1 | AIP, PdV, PdD, Tenant, Gateway, Hashchain, Merkle, enum grezzi (`IN_CATENA`), «10y», `48213`, date ISO |
| 3 | Controllo e libertà | 1 | Drawer senza Esc; dopo un timeout il versamento resta bloccato fino al ricaricamento; nessun annulla |
| 4 | Coerenza e standard | 2 | Token aggirati da colori inline; 3 paginazioni diverse; `tag-gold` usata e mai definita; bottoni in font di sistema |
| 5 | Prevenzione degli errori | 1 | Classe predefinita `fattura` inesistente; disattivazioni e revoche con un clic senza conferma |
| 6 | Riconoscere invece di ricordare | 1 | Esibizione, verifica e filtri chiedono di incollare UUID |
| 7 | Flessibilità ed efficienza | 1 | Un documento alla volta, 7 campi ridigitati, nessun export, nessuna scorciatoia |
| 8 | Estetica e minimalismo | 2 | Ordinato ma rumoroso: 4 chip di download per riga, tipo ripetuto 3 volte, micro-etichette in maiuscolo ovunque |
| 9 | Diagnosi e recupero dagli errori | 1 | Un 500 diventa «Nessun AIP trovato» o «Nessun RSC nominato… obbligatoria» |
| 10 | Aiuto e documentazione | 1 | Nessun glossario, nessun aiuto contestuale |

### 1.2 Audit tecnico (valutazione B): **6/20 — Scarso**
| # | Dimensione | Voto | Evidenza principale |
|---|---|---|---|
| 1 | Accessibilità | 1 | axe: 134 nodi con contrasto insufficiente; violazioni *critical* su select-name, label, button-name; `--text-3` #4a5a7a a 2,29–2,78:1 usato 245 volte; focus a 1,07:1 |
| 2 | Prestazioni | 2 | Bundle unico da 830 KB (recharts ~611 KB usato in 2 pagine); Google Fonts con `@import` bloccante |
| 3 | Theming | 2 | 17 variabili CSS ma 738 `style={{}}` inline e hex duplicati |
| 4 | Responsive | 0 | Zero media query; sidebar fissa da 216 px; a 390 px la pagina è larga 793–1083 px; 97–100% dei bersagli sotto 44 px |
| 5 | Integrità dell'implementazione | 1 | Build rotta (manca `tsconfig.json`); Tailwind dichiarato e inattivo; favicon mancante; classi mancanti; pagine duplicate |

### 1.3 Detector
- **Statico**: 3 segnalazioni in `index.css` (font abusato, testo a gradiente non usato). Lo zero sui componenti non significa pulizia: lo stile inline sfugge all'analisi statica.
- **In pagina** (iniezione riuscita 5/5): 17 problemi su /overview, 35 su /versamento, 71 su /aip, 48 su /audit, 16 su /login — contrasto basso, testo sotto misura (corpo a 8–11 px in 189 punti), tracking largo, card annidate, icona sopra il titolo. Tutti i campioni verificati sono veri positivi.
- **Dove concordano A e B**: contrasto, focus invisibile, mobile inutilizzabile, componenti mancanti, Tailwind inattivo. **Cosa ha trovato solo A**: la menzogna degli stati (errori come vuoto, dati finti, «verificato» su un semplice lookup). **Cosa ha trovato solo B**: build rotta, upload non raggiungibile da tastiera, profili demo e `dev-token` nel bundle di produzione.

---

## 2. Verdetto di specificità

**Intercambiabile.** Coprendo il nome, la home potrebbe essere un wallet crypto o la console di un SaaS di analytics: 4 KPI con tile-icona e barra a gradiente, area chart dorata, ciambella monocolore, navy quasi nero con un solo accento oro «premium», titoli in Instrument Serif condensato, e la voce «Blockchain Explorer» (per un compratore PA anche un rischio reputazionale: le Linee guida non la chiedono e una catena di impronte non è una blockchain). È il «kit SaaS a card» e la «cromatura da template» che le skill elencano come segnali tipici del design generato.

La conoscenza del dominio (Allegato 5, SInCRO, DI/DAI/AGG, PdD di migrazione) vive solo nei testi. Gli oggetti veri del mestiere **non hanno forma**: la segnatura e il rapporto di versamento, il tempo di conservazione e lo scarto, l'esibizione davanti a un ispettore, la prova d'integrità leggibile da un non tecnico, la classe documentale come contratto che genera il modulo di versamento.

**Impressione generale.** Un buon prototipo da sprint che **simula fiducia invece di costruirla** (oro, scudo, «Live», «immutabilità garantita», «verificato») e che, quando qualcosa va storto, mente o tace. Per un conservatore la fiducia è il prodotto: la nuova personalità deve nascere dalla **verità sullo stato** (versato, conservato, integro, esibito, in errore e perché).

**Cosa funziona** (da preservare): il modello di dominio è giusto (7 metadati obbligatori, modalità di formazione, tipi di PdD, conservazione permanente); l'impronta SHA-256 calcolata nel browser prima dell'invio con un registro dei passaggi (istinto corretto: mostrare la catena di custodia); API key mostrata una sola volta; navigazione filtrata per ruolo con `aria-current`; cifre tabellari nelle card.

---

## 3. Problemi prioritari

| Gravità | Problema | Perché conta | Rimedio |
|---|---|---|---|
| **P0** | **Gli errori diventano «vuoto» e lo stato globale è finto.** Con un 500, /aip dice «Nessun AIP trovato», /nomine-rsc dice «Nessun RSC nominato… obbligatoria ai sensi dell'art. 44 del CAD»; contratti e fascicoli fanno lo stesso; l'header dice «Sistema operativo» in verde, fisso | A un responsabile della conservazione si dice che l'archivio è vuoto o che manca una nomina obbligatoria: escalation e perdita di fiducia irreversibile | Componente unico di stato dati (caricamento con la forma reale, errore con causa, ora e «Riprova», vuoto con l'azione); divieto di `catch → vuoto`; indicatore di servizio dal vero health check, «Servizio attivo / degradato» |
| **P0** | **Il versamento fallito resta «in corso»**; la classe predefinita `fattura` non esiste; a successo si vede solo un UUID; l'upload non è raggiungibile da tastiera (`div onClick` su input nascosto) | È il momento a più alto rischio: l'operatore non sa se il documento è conservato | Macchina a stati esplicita (inviato → in verifica → conservato / rifiutato con codice, motivo e campo / sospeso); select senza valore predefinito; esito con segnatura, rapporto di versamento e «Versa un altro» |
| **P0** | **L'esibizione non consegna nulla**: il PdD di esibizione non mostra link, copia o QR; per arrivarci si incolla un UUID; il richiedente è fisso «dashboard-user» | Esibire a un ispettore è uno dei tre momenti per cui il cliente paga: fallisce davanti a un terzo, e il registro di chi ha esibito è falso | «Esibisci» come azione sul documento: destinatario e motivo, durata, poi link, QR e attestazione PDF con impronta e prova; storico delle esibizioni |
| **P0** | **Inutilizzabile sotto i ~900 px** (sidebar fissa, nessuna media query) e **build rotta** (`npm run build` fallisce) | Non si può rilasciare né usare da tablet o portatili piccoli | Layout responsive strutturale; `tsconfig.json` strict |
| **P1** | **Dati inventati presentati come reali**: trend settimanale finto con «Live», +12% fisso, SLA 99,95% «conforme» quando il backend non risponde, «verificato» dopo una semplice ricerca, legenda che nasconde gli AIP in errore | Un KPI «conforme» senza dati mostrato a chi firma report verso AgID è un problema di responsabilità | Zero valori di ripiego; «Dato non disponibile» esplicito; la verifica mostra la prova; le eccezioni sempre in cima |
| **P1** | **Accessibilità sotto la soglia di un fornitore PA**: zero `aria`, una sola label associata, righe cliccabili non raggiungibili da tastiera, drawer non dialog, bottoni di sole icone senza nome, nessuno skip link, focus 1,07:1, testo secondario 2,56:1 | Obbligo di legge negli acquisti PA (L. 4/2004, Linee guida AgID accessibilità, EN 301 549) | Componenti accessibili, `:focus-visible` a ≥ 3:1, label vere, testo minimo 13–14 px, contrasto ≥ 4,5:1 |
| **P1** | **Gergo e architettura informativa per tecnologia**: 18 voci di menu, un gruppo «Blockchain» che contiene la ricerca, «Fascicoli» sotto «Amministrazione», due pagine quasi identiche sulla catena | Il responsabile del Comune deve «trovare, dimostrare, esibire, firmare», non tradurre OAIS | Navigazione per compiti e ruolo (§5), glossario in linea, sigle come metadato |
| **P1** | **Il modulo di versamento è fisso** (7 campi nel codice) mentre le classi configurano fino a 16 metadati; un documento alla volta; riferimenti normativi sparsi nei sottotitoli | L'obiettivo «predisposto al cambio di norma» richiede che una modifica all'Allegato 5 tocchi zero righe di interfaccia | Modulo **generato dal profilo della classe**; registro normativo centrale; versamento a lotti |
| **P2** | **Fondamenta**: Tailwind inattivo, 738 stili inline, hex fuori dai token, classi mancanti, date con `slice(0,10)`, nessun `Intl`, favicon mancante, azioni distruttive senza conferma | Ogni pagina reinventa tutto e diverge | Un solo sistema di stile, token a tre livelli, kit di componenti (§6) |

**Carico cognitivo**: 6 su 8 voci della checklist falliscono (alto). Punti di decisione con più di 4 opzioni: navigazione (18), azioni per riga AIP (5), filtri di ricerca, tipo di PdD.

**Momenti ad alto rischio** — l'emozione da progettare è *sollievo e certezza*, non entusiasmo:
| Momento | Oggi | Cosa serve |
|---|---|---|
| Versare | Stepper tecnico; se va male attesa infinita in oro; se va bene un UUID | Fine forte: una **segnatura di conservazione** stampabile e il rapporto di versamento; in caso di rifiuto causa, campo e rimedio |
| Verificare l'integrità | Tabella di hash a 64 caratteri; «verificato» su un lookup | Una frase umana con prova: «Identico a quello versato il 14/10/2026 alle 9:41. Ultimo controllo: oggi 9:41. Blocco 4.815» |
| Esibire | Modulo con UUID, risultato senza link | Un gesto e un artefatto: link, QR e attestazione in pochi secondi |
| Errore di sistema | «Archivio vuoto», «nessun RSC nominato» | Onestà: «Non riusciamo a raggiungere l'archivio. I documenti non sono stati toccati.» |

---

## 4. Persone: dove si rompe

- **Alex, amministratore esperto del conservatore**: nessuna scorciatoia né ricerca globale per URN o protocollo; tre cambi di pagina per esibire; nessun export; animazioni di ingresso a ogni cambio pagina.
- **Sam, screen reader e tastiera**: focus invisibile; righe non attivabili; input senza label («edit text, es. FT-2026-001»); bottoni senza nome; stati solo colore; drawer che non trattiene il focus; zoom 200% = layout rotto; `prefers-reduced-motion` ignorato.
- **Rita, responsabile della gestione documentale di un Comune, 58 anni, non tecnica**: accede con «API Key ID / Secret» invece che con SPID o credenziali personali; la home non risponde a «siamo a posto?»; il manuale si scarica in JSON mentre il testo dice di firmarlo; emoji in un documento ufficiale; parole sostituite da sigle; testo da 10–12 px a 2,5:1; un errore di rete le dice che manca una nomina obbligatoria.
- **Davide, operatore di protocollo, 200 documenti al giorno**: un file per volta, 7 campi ridigitati, classe predefinita invalida con rifiuti invisibili, nessuna coda «i miei versamenti di oggi» con gli esiti.

---

## 5. Linguaggio e architettura informativa proposti

**Regola: l'etichetta è il compito, la sigla è il dettaglio.** Esempi: «AIP conservati» → **Documenti conservati**; «Pipeline PdV» → **Versamenti in corso**; «Blockchain / Explorer» → **Integrità**; «Distribuzione / PdD» → **Esibizioni ed esportazioni**; «Tenant» → **Enti e aziende clienti**; «Audit Log» → **Registro delle attività**; «Sistema operativo» → **Servizio attivo**; «10y / ∞» → «10 anni / Illimitata»; «IN_CATENA, SU_STORAGE» → «In conservazione, Archiviato».

```
Home (stato e cose da fare)                  tutti
ARCHIVIO
  Versa documenti                            operatore, amministratore ente
  Versamenti in corso ed esiti               operatore, amministratore ente, responsabile
  Documenti conservati (ricerca)             tutti
  Fascicoli                                  tutti tranne sistemi
ESIBIZIONI ED ESPORTAZIONI                   amministratore ente, consultazione, responsabile
INTEGRITÀ E REGISTRI
  Verifica un documento · Catena delle impronte · Registro delle attività
CONFORMITÀ
  Manuale di conservazione · Nomine · Contratti · Livelli di servizio
CONFIGURAZIONE
  Classi documentali · Utenti e chiavi di accesso · Enti clienti (solo piattaforma)
```
Al primo livello non più di 5 gruppi; ricerca globale nell'intestazione (oggetto, protocollo, numero).

---

## 6. Fondamenta tecniche (valgono per qualunque direzione)

1. **Un solo sistema di stile**: Tailwind attivato davvero con tema chiuso sui token, oppure CSS Modules con token; mai stili inline per gli stati.
2. **Token a tre livelli**: primitivi → semantici (`--surface`, `--text-muted`, `--status-error-fg`, `--focus-ring`) → componenti. I colori dei grafici passano dai token.
3. **Kit di componenti accessibili** su primitive headless (Radix, React Aria o Ark) oppure il kit PA (§8): Button, IconButton con nome, Table responsive, StatusBadge, PageHeader, DataState, Pagination, Dialog/Drawer, ConfirmDialog, Toast, FormField, DateInput it-IT, Combobox.
4. **Moduli generati dallo schema** della classe documentale (requisito architetturale per la predisposizione normativa).
5. **Registro normativo centrale** consumato dall'interfaccia (riferimento, versione, data di efficacia).
6. **Code-splitting per route**, font self-hosted, nessuna risorsa esterna.
7. **Formati italiani** con `Intl.DateTimeFormat`, `Intl.NumberFormat`, `Intl.PluralRules`.
8. **Profili demo e login di sviluppo fuori dal bundle di produzione**; sessione in cookie HttpOnly.

### 6.1 Movimento (review-animations: **BLOCCATO**)
Ingressi animati a ogni cambio pagina (400 ms + sfalsamento fino a 250 ms); `transition: all` diffuso; animazione di `width`; hover via JavaScript; spinner fermi; skeleton fermi; nessun `prefers-reduced-motion`; grafici con animazione di 1,5 s a ogni montaggio. Il prodotto deve sentirsi affidabile con risposte istantanee: nessuna animazione sulle azioni frequenti, un solo gesto significativo al completamento di un versamento. Tabella prima/dopo completa in `allegati/design_A_review.md` §15.

### 6.2 Dati peggiori (break-ui): 12 rotture
Uno stato d'errore sconosciuto appare **grigio** e sborda; la legenda nasconde gli errori; quota al 102% senza messaggio; `12873441` illeggibile; nomi di ente da 118 caratteri su 5 righe; URN lunghi che spingono le colonne; URN nullo che mostra l'UUID due volte; «1 risultati». Dettaglio in `allegati/design_A_review.md` §16.

### 6.3 Grafici (dataviz)
La ciambella con un'unica categoria al 99,6% non comunica nulla: serve un **numero principale** («48.213 documenti conservati») e una **lista di eccezioni** («12 in errore, 41 in preparazione»). I colori di stato usati come categorici e assegnati per posizione violano due regole base. Le soglie SLA vanno disegnate come linee di riferimento; i conteggi giornalieri come barre, non curve morbide.

---

## 7. Cosa dicono le skill (sintesi)

| Skill | Rilievo principale |
|---|---|
| impeccable | Modalità *Operate*: l'interfaccia deve sparire nel compito; ogni componente deve avere tutti gli stati; il «mondo» presta tipo, palette, densità e **una** mossa firma, mai i controlli |
| redesign-existing-projects | Card generiche, sidebar scontata, raggio uniforme, nessun anello di focus, nessuno stato di caricamento/vuoto/errore, favicon mancante, stili inline, maiuscole ovunque; grigio freddo + oro caldo estranei al dominio |
| design-taste-frontend | Dichiara le dashboard fuori dal proprio ambito e rimanda ai design system ufficiali → valutazione di Designers Italia (§8) |
| frontend-design | L'aspetto attuale coincide con il «kit SaaS a card» e la «cromatura da template»; la materia del mestiere è assente dalla forma; il coraggio va speso in un solo punto |
| emil-design-eng | I dettagli invisibili rotti (spinner, badge invisibili, font dei bottoni) erodono la fiducia più di qualunque palette |
| apple-design | Feedback di completamento ed errore rotti; nessuna via d'uscita dopo il timeout; etichette vaghe («Overview», «Operazioni», «Explorer»); conferme assenti proprio sulle azioni distruttive |
| review-animations | Bloccato (§6.1) |
| break-ui | 12 rotture (§6.2) |
| dataviz | Forma, colore e dati finti sbagliati (§6.3) |

---

## 8. Opzione: Designers Italia / Bootstrap Italia

| Opzione | Pro | Contro | Quando |
|---|---|---|---|
| **A. Adozione piena** (Bootstrap Italia / design-react-kit, Titillium, palette PA) | Familiare ai dipendenti PA; componenti già accessibili; argomento nelle gare | VerumDoc è un **fornitore privato**: l'identità «Italia» può far sembrare il prodotto un servizio dello Stato; kit pensato per servizi al cittadino più che per strumenti densi; i clienti privati non lo riconoscono; **azzera la personalità richiesta** | Mercato quasi solo PA, vendita su «si integra col vostro ecosistema» |
| **B. Adozione dei pattern, non dell'identità** (form, tabelle, notifiche, checklist di accessibilità, linguaggio di Designers Italia; marchio proprio) | Si prende il valore senza impersonare la PA; compatibile con le tre direzioni | Richiede disciplina per non scivolare nell'imitazione | Mercato misto PA + privati |
| **C. Nessun riferimento** (sistema proprietario su primitive headless) | Massima libertà | Si rifà da zero ciò che il kit PA ha già risolto | Mercato prevalentemente privato |

In ogni caso i requisiti di accessibilità PA valgono come soglia minima.

---

## 9. Tre direzioni di personalità (da scegliere: decisione `VD-DR-01`)

**Vincoli comuni.** Navigazione e controlli standard (il «mondo» presta solo tipo, palette, densità e una mossa firma). Stati con una palette dedicata, sempre con icona ed etichetta. Nessun nero puro, nessun crema, nessun terracotta, nessun verde acido; niente etichette in maiuscolo spaziato, niente «·» come separatore, niente «→» nei bottoni; monospazio solo per impronte e identificativi. Tema chiaro come principale (la PA lavora su monitor d'ufficio in ambienti illuminati), scuro come variante scelta, non come inversione automatica. Le tre direzioni condividono gli **oggetti di dominio** (segnatura, righello del tempo, sigillo sono tutti veri), ma l'estetica è una sola: la mossa firma resta una.

### 9.1 «Segnatura» — il registro di protocollo, con inchiostro copiativo
**Idea.** Il gesto archivistico italiano per eccellenza è la segnatura: numero, data, ora, classificazione impressi sul documento. Il prodotto ruota attorno alla **ricevuta**: ogni documento conservato ha una segnatura di conservazione visibile, citabile e stampabile. Densità alta, pensata per l'operatore; nessun costume da registro cartaceo (le tabelle restano tabelle).

| Ruolo | Hex | Nome |
|---|---|---|
| Superficie | `#F3F5F4` | Grigio ufficio (freddo, leggermente verde) |
| Superficie 2 | `#E3E8E6` | Cartella |
| Testo | `#1E2A2C` | Ardesia |
| Azione | `#4A3B8F` | Viola copiativo |
| Accento tenue | `#E6E2F3` | Velina viola |
| Testo secondario | `#55625F` | Grigio matita (≥ 5:1) |
Stati: successo `#1F7A4D`, avviso `#A86412`, errore `#B3261E`, info `#25638F`.

**Tipografia.** Atkinson Hyperlegible Next per tutta l'interfaccia (progettata per l'ipovisione), Atkinson Hyperlegible Mono solo per impronte e URN; base 15 px, cifre tabellari.
**Mossa firma.** La **segnatura di conservazione**: blocco compatto con bordo viola, n. di conservazione, data e ora, classe, impronta abbreviata e QR di verifica; compare alla fine del versamento, nel dettaglio e nell'esibizione.

```
┌───────────┬──────────────────────────────────────────────────────────┐
│ VerumDoc  │ [Cerca per oggetto, protocollo, numero…        ] Rita M. │
│           ├──────────────────────────────────────────────────────────┤
│ Home      │ Comune di Castelvetro di Modena        Servizio attivo ● │
│ Versa     │ ┌ Da fare oggi ─────────────────────────────────────────┐│
│ Versamenti│ │ ⚠ 12 versamenti rifiutati ieri (classe non abilitata) ││
│ Documenti │ │ ⚠ Archivio all'82%: esaurimento stimato a gennaio      ││
│ Fascicoli │ │ ○ Manuale v3.2.1 da firmare                           ││
│ Esibizioni│ └───────────────────────────────────────────────────────┘│
│ Integrità │  48.213 conservati │ 41 in preparazione │ 12 rifiutati   │
│ Conformità│ ┌ Ultime segnature ──────────────────────────────────────┐│
│ Config.   │ │▌N. 2026/000425  14/10 09:41  Delibera GC 112  …a3f2  ✓ ││
│           │ │▌N. 2026/000424  14/10 09:38  Fattura 418/PA   …91c0  ✓ ││
└───────────┴──────────────────────────────────────────────────────────┘
```
Versamento a lotti: classe e produttore una volta, trascinamento di N file, tabella di revisione per riga con i campi generati dalla classe, «Versa 2 documenti pronti», esiti per riga con segnatura o motivo del rifiuto.
**Movimento.** Quasi nullo: solo la segnatura entra (opacità + 4 px, 180 ms) quando un documento è conservato.
**Rischio.** Scivolare nel costume da registro: tabelle standard, nessuna texture carta.

### 9.2 «Orizzonte» — il tempo della conservazione come materia
**Idea.** Conservare vuol dire **durare**: 10 anni, 40, per sempre, poi lo scarto. L'asse del tempo è l'ossatura di ogni schermata. È il prodotto per chi *risponde* (responsabile del Comune, RSC): calmo, leggibile, densità media, testo più grande.

| Ruolo | Hex | Nome |
|---|---|---|
| Superficie | `#FAFBFA` | Bianco archivio |
| Barra di navigazione | `#1F4B43` | Verde ministeriale |
| Testo | `#1B2422` | Verde inchiostro |
| Azione | `#1F6F5C` | Verde scaffale |
| Tempo / dati | `#7BA7BC` | Azzurro cartellino |
| Testo secondario | `#566360` | Muschio |
Stati: successo `#2E7D32`, avviso `#B26A00`, errore `#C0362C`, info `#2D6A9F`.

**Tipografia.** Source Sans 3 per il sistema; Source Serif 4 **solo per le parole del documento** (oggetto, titolo del fascicolo, testo del manuale): il serif è la voce del documento, il sans quella del sistema. Base 16 px.
**Mossa firma.** Il **righello del tempo** (versamento ●──────── scadenza ◇ / ∞), e in home un'**agenda di conformità** con le prossime scadenze: scarti, firme, rinnovi.

```
┌──────────────────────────────────────────────────────────────────────┐
│█ VerumDoc  Home  Archivio  Esibizioni  Integrità  Conformità   Rita ▾│
├──────────────────────────────────────────────────────────────────────┤
│ Buongiorno. L'archivio del Comune è integro (verificato oggi 09:41). │
│ Prossime scadenze                         2026 ─────┬──── 2027 ──────│
│  ◇ 12/11  Firma manuale v3.2.1 (RSC)       ●────────┼◇               │
│  ◇ 31/12  Proposta di scarto: 1.204 fatture 2016    ┼──────◇         │
│  ◇ 29/02/28 Rinnovo contratto                       ┼─────────────◇  │
│ Attenzione: 12 versamenti rifiutati ieri «classe non abilitata» [Vedi]│
│ Archivio: 48.213 documenti   ▁▂▅▇▆▂▁ ultimi 7 giorni (reali)         │
└──────────────────────────────────────────────────────────────────────┘
```
Versamento guidato in 4 passi con etichette (Classe, Documento, Dati, Verifica e invio); la classe mostra conservazione, formati ammessi e metadati richiesti; esito: «Conservato il 14/10/2026 alle 09:41. Sarà conservato per sempre.»
**Movimento.** Il righello si riempie una volta (240 ms); passi dello stepper con scorrimento di 16 px coerente avanti/indietro.
**Rischio.** Righelli ovunque come decorazione: solo dove c'è davvero una scadenza.

### 9.3 «Sigillo» — la prova d'integrità resa leggibile
**Idea.** Il cliente compra la possibilità di **dimostrare** (a un ispettore, a un giudice, ad AgID). Personalità di strumento di misura notarile: neutra, precisa, ad alto contrasto, con un solo elemento caldo, il **sigillo**, che compare solo quando c'è una prova. Pensata per il responsabile del servizio e per l'esibizione.

| Ruolo | Hex | Nome |
|---|---|---|
| Superficie | `#FFFFFF` | Carta |
| Superficie 2 | `#EEF1F3` | Velina |
| Testo | `#22282D` | Grafite |
| Primario | `#0F4C5C` | Petrolio |
| Sigillo (solo prove) | `#8C2F39` | Ceralacca |
| Testo secondario | `#5A646B` | Grafite chiara |
Stati più luminosi per non confondersi con la ceralacca: successo `#1E7F4F`, avviso `#B86E00`, errore `#D92D20` (sempre con icona ed etichetta), info `#2463A6`.

**Tipografia.** Overpass (derivato dalla segnaletica stradale: istituzionale senza impersonare la PA), Overpass Mono per impronte, URN e numeri di blocco; base 15 px.
**Mossa firma.** Il **sigillo di verifica**: piccolo componente circolare in ceralacca con data, che si apre in un'attestazione leggibile («Identico all'originale versato il 14/10/2026 alle 09:41. Blocco 4.815. Verificato ora.»). La catena delle impronte come striscia di anelli, non come tabella di hash.

```
┌───────────┬──────────────────────────────────────────────────────────┐
│ VerumDoc  │ [Verifica o trova un documento…                 ]  RSC ▾ │
│ (petrolio)├──────────────────────────────────────────────────────────┤
│ Home      │ Stato dell'archivio                    ⬤ sigillo 09:41   │
│ Archivio  │ 48.213 documenti integri. Ultima verifica completa: 09:41│
│ Esibizioni│ ○○○○○○○○○○○○○○○○○○○○○○○●  catena: 4.817 blocchi         │
│ Integrità │ Eccezioni (3)                                            │
│ Registri  │  ✕ 12 versamenti rifiutati                [Apri]         │
│ Conformità│  ! Disponibilità 99,91% sotto il 99,95%   [Report]       │
│ Config.   │ Esibizioni recenti                                       │
│           │  GdF Vignola, Fattura 418/PA, link valido fino al 27/10  │
└───────────┴──────────────────────────────────────────────────────────┘
```
Versamento con una colonna «Prova» che si compone mentre il pacchetto avanza: impronta calcolata sul computer dell'utente, ricevuto, controlli 9/9, pacchetto creato, sigillato alle 09:41, blocco 4.815, rapporto di versamento.
**Movimento.** Il sigillo «si imprime» una volta (scala 0,96 → 1 e opacità, 160 ms, senza rimbalzo).
**Rischio.** Ceralacca confusa con il rosso d'errore: errore più luminoso, sempre con ✕ ed etichetta; sigillo sempre circolare con spunta.

### 9.4 Confronto
| | Segnatura | Orizzonte | Sigillo |
|---|---|---|---|
| Persona servita meglio | Operatore (200 al giorno) | Responsabile del Comune | Responsabile del servizio, ispettore |
| Densità | Alta | Media | Media-alta |
| Navigazione | Barra laterale | Barra in alto colorata | Barra laterale |
| Oggetto centrale | Ricevuta | Scadenza | Prova |
| Rischio | Costume da registro | Righelli decorativi | Ceralacca vs errore |

---

## 10. Domande aperte per il committente

1. **Direzione** (`VD-DR-01`): Segnatura, Orizzonte o Sigillo?
2. **Designers Italia** (`VD-DR-02`): adozione piena, solo pattern, nessun riferimento?
3. **Tema scuro**: serve come variante o si parte solo chiaro?
4. **Persona prioritaria** per il primo rilascio del nuovo frontend: operatore, responsabile del cliente o responsabile del servizio?
5. **Identificativo umano** nelle liste: titolo/oggetto in prima riga con l'URN troncato al centro in seconda (raccomandato) o URN in evidenza?
6. **Liste molto lunghe** (48.000+ documenti): paginazione con ricerca come ingresso principale (raccomandato) o virtualizzazione?

## 11. Comandi di lavoro raccomandati (dopo la decisione)
1. `impeccable init` → PRODUCT.md con le risposte sopra; `new-work` per fissare il mondo scelto in DESIGN.md.
2. `harden` + `clarify`: stati veri, errori onesti, linguaggio (P0, indipendenti dalla direzione: si possono iniziare subito).
3. `adapt` + `audit`: responsive e accessibilità.
4. `shape`: versamento a lotti, esibizione, verifica d'integrità.
5. `extract`: token e componenti nel nuovo sistema.
6. `distill`, `typeset`, `animate` (solo il gesto firma), `polish`.
