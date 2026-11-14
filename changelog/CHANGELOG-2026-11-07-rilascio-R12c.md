# CHANGELOG — R12c — Revisione grafica della console (31/10/2026)

Rilascio solo frontend, nessuna migrazione, nessun secret, nessun cambiamento delle API. Origine: prova del committente sulla console vera (docker): grafica peggiore della proposta «Partenze» di R02, testata con voci coperte dalla ricerca, comandi pesanti, nessun comportamento responsive, righe «Esempio» nel quadro del servizio, accesso senza identità. Sistema grafico invariato (VD-DR-01, VD-DR-05): stessi colori e carattere, correzioni di sistema documentate in `frontend/DESIGN.md` («Revisione grafica della console (R12c)»).

## Sintesi
La console torna alla proposta «Partenze» di R02 e la supera dove la console vera aveva accumulato difetti: testata a una riga (o due, misurate) con menu a scomparsa sotto 1024 px, un solo menu della persona, ente in un solo punto, testa di pagina dentro la colonna, quadro del servizio con Partenze calcolate dai dati veri, accesso a due colonne, motivi ferroviari (binario, passaggio a livello, corsa del pacchetto a fermate). Audit su 11 viste a 390, 768, 1280 e 1850 px in chiaro e scuro: 27 difetti (11 alta, 9 media, 7 bassa), tutti corretti; prove automatiche nuove di sovrapposizione, scorrimento, tastiera e contrasto. Completamento invariato (~87%).

## Prima di usarlo
```bash
cd frontend; npm ci      # nuova dipendenza di sviluppo @axe-core/playwright 4.13.0
npm run dev
```
Nessuna migrazione, nessun secret, nessun cambiamento delle API o dei servizi Python e Java. Non tocca `seed.py`, `versamento_service.py` né `docker-compose.yml` (correzioni R12b.1/R12b.2 del committente restano intatte).

## Per chi usa la console
- **Testata**: le aree non finiscono più sotto la ricerca. Sugli schermi larghi stanno su una riga se ci entrano, altrimenti su una seconda riga grafite; sotto 1024 px sono nel **Menu** a scomparsa, con tutte le pagine e, su telefono, la ricerca.
- **Menu della persona** (il nome del ruolo, in alto a destra): tema (automatico, chiaro, scuro), Preferenze, Le mie credenziali, Esci. «Da fare» mostra il numero in un riquadro giallo.
- **Ente su cui lavori**: una sola scelta, nella riga delle pagine sotto la testata; anche il quadro del servizio la segue.
- **Quadro del servizio**: le Partenze sono calcolate dai dati del servizio (verifica della catena delle impronte, pratiche che aspettano te, rinnovo dei blocchi WORM). Niente più righe «Esempio» inventate; il calendario completo (firma del manuale, rinnovo delle marche) resta da realizzare ed è dichiarato. Arrivi vuoti spiegano che cosa significa e offrono «Versa un documento».
- **Accesso**: pagina a due colonne con l'identità del servizio; i profili di sviluppo (solo `npm run dev`) sono in un'area tratteggiata separata.
- Testa di ogni pagina dentro la colonna, con titolo e spiegazione allineati; «Versa un documento» usa la stessa testa.

## Motivi ferroviari (seconda parte, richiesta del committente)
- **Binario**: due rotaie con traversine chiudono la testa di ogni pagina e separano i blocchi maggiori del quadro; le righe dei tabelloni restano filetti.
- **Passaggio a livello**: strisce diagonali giallo/grafite sottili sul bordo superiore delle fasce di attenzione (integrità mai verificata, stato non disponibile, compromessa, avviso dei dati dimostrativi). «Archivio integro» resta giallo pieno.
- **Corsa del pacchetto** come linea ferroviaria verticale: fermate con pallino pieno (raggiunta), giallo (in corso), vuoto (in attesa), rosso (interrotta), sempre con la parola di stato; niente più «--:--».
- **Quadro**: il rinnovo dei blocchi WORM mostra la scadenza solo se cade entro 90 giorni, altrimenti «Nessun rinnovo necessario entro 90 giorni».

## Per chi sviluppa
- Righe dimostrative del quadro solo in sviluppo con `?dimostrativi=1` (fascia «Dati dimostrativi (solo sviluppo)»).
- Componente nuovo `components/Vuoto.tsx` per gli stati vuoti; token di disposizione `--vd-gutter`, `--vd-testata-h`, `--vd-sotto-h`, `--vd-sezione`, `--vd-corpo-alto`, `--vd-corpo-basso`.
- Prove nuove `frontend/e2e/r12c.spec.ts`: sovrapposizione e taglio delle aree a 1280 e 1850 px, scorrimento orizzontale a 390 px in chiaro e scuro, menu da tastiera, fuoco visibile, contrasto AA con axe-core (dipendenza di sviluppo `@axe-core/playwright` 4.13.0), niente «Esempio» nel quadro. Aggiornati i selettori di r07 (Preferenze nel menu) e r10 (conteggio di «Da fare»).
- API simulata degli e2e: aggiunta la situazione dell'archivio (`/dashboard/overview`), così le schermate si prendono a dati caricati.
- Corretto: etichetta bordata illeggibile in tema scuro (contrasto 1,03:1, trovato da axe); esito e firme incollati nei Pacchetti in arrivo.

## Difetti trovati (audit grafico, prima delle correzioni)
Metodo: `vite dev` con l'API simulata degli e2e, 11 viste (accesso, quadro come responsabile e come amministrazione, versa, pacchetti in arrivo e conservati, catena, impostazioni, pratiche, utenti, code di scarto) a 390, 768, 1280 e 1850 px in chiaro e scuro (88 schermate per giro) con misure automatiche di sovrapposizione, taglio e scorrimento. Le schermate restano fuori dal repository (§25.13). **27 difetti: alta 11, media 9, bassa 7.**

### Priorità alta (11)
| # | Difetto | Correzione |
|---|---|---|
| T1 | Aree della testata tagliate e sfumate sotto la ricerca a 1280 e 1850 px (88/88 schermate) | Testata ricostruita: una riga se le aree ci stanno, altrimenti seconda riga grafite (misurata) |
| T2 | A 390 px il marchio coperto da «Tema» e dal selettore dell'ente | Comandi nel menu della persona e nel menu a scomparsa |
| T3 | Nessun comportamento responsive: aree in una riga nascosta a 390 e 768 px | Menu a scomparsa sotto 1024 px con tutte le aree e le pagine (sotto 640 anche la ricerca) |
| T4 | Comandi di testata come cinque riquadri bordati diversi; «Preferenze» link allineato in alto | Un solo menu della persona (Tema, Preferenze, Credenziali, Esci), testo su grafite |
| P1/V1 | «Versa un documento»: filetto a tutta finestra, titolo fuori colonna | Testa di pagina comune dentro la colonna |
| Q1 | Righe «Esempio» inventate nelle Partenze, con date scadute | Partenze dai dati veri; dimostrativi solo con `?dimostrativi=1` in sviluppo, dichiarati |
| Q2 | Due scelte dell'ente con stati diversi | Ente solo nella sotto-navigazione; il quadro usa `useEnte` |
| L1 | Accesso in una colonna stretta, metà schermo vuota | Due colonne con l'identità del servizio |
| L2/X1 | Etichetta «Profili di sviluppo» illeggibile in scuro (contrasto 1,03:1, axe) | Inchiostro del tema |

### Priorità media (9)
| # | Difetto | Correzione |
|---|---|---|
| T5 | Ente in testata come select su grafite, ripetuto nel quadro | Vedi Q2 |
| T6 | «Da fare (1)» solo testo tra parentesi | Conteggio in rettangolo giallo `.vd-conta` |
| P2 | Titolo e spiegazione affiancati: spiegazione in colonna stretta, comandi su una terza riga | Testa a griglia |
| Q3 | Fascia: ente e «Dettaglio della verifica» su una seconda riga staccata | Fascia a griglia |
| Q4 | Arrivi vuoti spogli, senza azione | Componente `Vuoto` con «Versa un documento» |
| A1 | «Conservato» e «Firme: …» incollati nei Pacchetti in arrivo | Esito su riga propria |
| L3 | Profili di sviluppo con lo stesso peso dell'accesso vero | Area tratteggiata separata |
| G1 | Sotto-navigazione di Configurazione (11 voci) senza alternativa su telefono | Pagine nel menu a scomparsa |
| X2 | Menu principale non raggiungibile su telefono se non scorrendo | Menu a scomparsa da tastiera |

### Priorità bassa (7)
| # | Difetto | Correzione |
|---|---|---|
| T7 | «Vai al contenuto» gestito da JavaScript | `.vd-salta` solo CSS |
| P3 | Margini 32→16 px con un solo salto a 640 | Token `--vd-gutter` 32/24/16 |
| Q5 | Situazione dell'archivio senza didascalia; numeri con stile in linea | Didascalia e `.vd-num` |
| V2 | Tappa del modulo con stili in linea | Classe `.vd-tappa` |
| V3 | «Versamento non abilitato» con colonna propria e senza azione | Componente `Vuoto` |
| A2 | «Versa un documento» isolato su una terza riga della testa | Vedi P2 |
| X3 | Nessuna prova automatica di contrasto | axe-core negli e2e |

Verificato senza difetto: nessuno scorrimento orizzontale a 390 px nelle altre pagine (tabelle già impilate con `.vd-impila`). Misure dopo le correzioni: 0 aree tagliate a 1280/1850, 0 sovrapposizioni, 0 scorrimenti a 390.

## Modifiche per file
- `frontend/src/components/Layout.tsx`, `frontend/src/index.css`: testata, menu della persona, menu a scomparsa, testa di pagina, motivi ferroviari, token di disposizione.
- `frontend/src/components/Vuoto.tsx` (nuovo), `Campanella.tsx`, `SceltaEnte.tsx`, `Tema.tsx`: stati vuoti, conteggio, ente nella sotto-navigazione, tema nel menu.
- `frontend/src/pages/OverviewPage.tsx`, `LoginPage.tsx`, `VersamentPage.tsx`, `VersamentoLivePage.tsx`: quadro dai dati veri, accesso a due colonne, testa comune, corsa del pacchetto a fermate.
- `frontend/DESIGN.md`: sezione «Revisione grafica della console (R12c)».
- `frontend/e2e/r12c.spec.ts` (nuovo), `finto.ts`, `r07.spec.ts`, `r10.spec.ts`; `frontend/package.json` e `package-lock.json` (`@axe-core/playwright`).
- `README_RILASCIO.md`, `STATO_PROGETTO.md`, `REGOLE_SVILUPPO.md` (§31), `docs/architettura/ROADMAP_R07_R13.md`.

## Test
`npm run typecheck`, `npm run lint`, `npm run build` senza errori; `npm run e2e` 85 prove verdi (56 esistenti + 29 nuove).

Nuove in `frontend/e2e/r12c.spec.ts` (29 casi): aree della testata non sovrapposte né tagliate a 1280 e 1850 px, nessuno scorrimento orizzontale a 390 px in chiaro e scuro per le viste principali, menu a scomparsa e menu della persona da tastiera (Esc riporta il fuoco), fuoco visibile, contrasto WCAG AA con axe-core in chiaro e scuro, nessuna riga «Esempio» nel quadro senza `?dimostrativi=1`. Backend non toccato: suite Python non rieseguite (nessun file Python o Java modificato); `tools/pulizia` 4 test verdi.

## File eliminati o spostati
Nessuno. `tools/pulizia/pulisci_progetto.py` già rimuove `frontend/test-results` e `frontend/playwright-report` (esiti locali di Playwright, ignorati da git) e non tocca i file nuovi.
