---
name: VerumDoc
description: Servizio di conservazione digitale a norma, letto come un orario ferroviario (sistema grafico «Partenze», VD-DR-01).
colors:
  grafite: "#1D1F22"
  grafite-2: "#2A2D31"
  giallo: "#FFC20E"
  giallo-velato: "#FFF6D6"
  carta: "#FFFFFF"
  binario: "#F2F3F4"
  testo: "#16181B"
  testo-2: "#575C63"
  su-grafite: "#D7D9DC"
  filetto: "#E1E3E6"
  bordo-campo: "#767B82"
  bordo-su-grafite: "#8A9097"
  ok: "#0E7A46"
  rischio: "#A63D00"
  critico: "#B42318"
  info: "#1F5FAD"
  inchiostro-pieno: "#000000"
  segnaposto-su-grafite: "#B5B9BE"
typography:
  display:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "28px"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 85"
  titolo-pagina:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "32px"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 85"
  headline:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    lineHeight: 1.25
    fontVariation: "'wdth' 85"
  orario:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.2
    fontVariation: "'wdth' 75"
    fontFeature: "'tnum' 1"
  marchio:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "20px"
    fontWeight: 800
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 112"
  motto:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(34px, 3.4vw, 52px)"
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "-0.015em"
    fontVariation: "'wdth' 85"
  body:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
    fontVariation: "'wdth' 100"
  body-lg:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 500
    lineHeight: 1.45
  esito:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.45
  stato:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 700
  label:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    fontVariation: "'wdth' 75"
  nota:
    fontFamily: "Archivo Variable, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "13px"
rounded:
  vivo: "0px"
  controllo: "2px"
spacing:
  gutter: "32px"
  gutter-mobile: "16px"
  cella-y: "12px"
  cella-x: "14px"
  testa-y: "8px"
  pagina-alto: "28px"
  pagina-basso: "48px"
  colonne: "36px"
  sezione: "40px"
  pagina-max: "1360px"
components:
  bottone-primario:
    backgroundColor: "{colors.testo}"
    textColor: "{colors.carta}"
    typography: "{typography.body-lg}"
    rounded: "{rounded.controllo}"
    padding: "0 20px"
    height: "44px"
  bottone-primario-hover:
    backgroundColor: "{colors.inchiostro-pieno}"
    textColor: "{colors.carta}"
  bottone-primario-disabilitato:
    backgroundColor: "{colors.filetto}"
    textColor: "{colors.testo-2}"
  bottone-secondario:
    backgroundColor: "transparent"
    textColor: "{colors.testo}"
    rounded: "{rounded.controllo}"
    padding: "0 20px"
    height: "44px"
  bottone-secondario-hover:
    backgroundColor: "{colors.binario}"
    textColor: "{colors.testo}"
  campo:
    backgroundColor: "{colors.carta}"
    textColor: "{colors.testo}"
    rounded: "{rounded.controllo}"
    padding: "0 12px"
    height: "44px"
  fascia-stato:
    backgroundColor: "{colors.giallo}"
    textColor: "{colors.testo}"
    typography: "{typography.display}"
    rounded: "{rounded.vivo}"
    padding: "18px 32px"
  fascia-stato-critica:
    backgroundColor: "{colors.critico}"
    textColor: "{colors.carta}"
  fascia-stato-neutra:
    backgroundColor: "{colors.filetto}"
    textColor: "{colors.testo}"
  etichetta-sezione:
    backgroundColor: "{colors.giallo}"
    textColor: "{colors.testo}"
    typography: "{typography.headline}"
    rounded: "{rounded.vivo}"
    padding: "2px 10px"
  testata:
    backgroundColor: "{colors.grafite}"
    textColor: "{colors.su-grafite}"
    height: "56px"
  ricerca-testata:
    backgroundColor: "{colors.grafite-2}"
    textColor: "{colors.carta}"
    rounded: "{rounded.controllo}"
    height: "40px"
    width: "clamp(180px, 16vw, 260px)"
  riga-tabellone-hover:
    backgroundColor: "{colors.binario}"
  riga-tabellone-in-corso:
    backgroundColor: "{colors.giallo-velato}"
  testa-pagina:
    textColor: "{colors.testo}"
    typography: "{typography.titolo-pagina}"
    padding: "0 0 16px"
  esito:
    backgroundColor: "transparent"
    textColor: "{colors.testo}"
    typography: "{typography.esito}"
    padding: "10px 0 0"
  avviso:
    backgroundColor: "transparent"
    textColor: "{colors.testo}"
    typography: "{typography.body}"
    padding: "8px 0 0"
  comando-testo:
    backgroundColor: "transparent"
    textColor: "{colors.testo}"
    typography: "{typography.body}"
    height: "44px"
  dialogo:
    backgroundColor: "{colors.carta}"
    textColor: "{colors.testo}"
    rounded: "{rounded.vivo}"
    width: "580px"
  chiave-una-volta:
    backgroundColor: "{colors.carta}"
    textColor: "{colors.testo}"
    typography: "{typography.mono}"
    rounded: "{rounded.vivo}"
    padding: "12px 14px"
  voce-apribile:
    backgroundColor: "{colors.carta}"
    textColor: "{colors.testo}"
    typography: "{typography.body-lg}"
    padding: "12px 14px"
    height: "52px"
---

# Design System: VerumDoc

## Overview

**Creative North Star: "Il tabellone delle partenze"**

Il servizio di conservazione si legge come un orario ferroviario. Il tempo e la puntualità sono il contenuto: le scadenze del servizio sono Partenze, i versamenti sono Arrivi, il percorso di un pacchetto è una Corsa. Ogni riga ha un orario, un'attività e uno stato, e lo stato è una parola, non un'icona. La testata è grafite come una pensilina, la fascia di stato è gialla come la segnaletica, i tabelloni sono carta bianca con filetti neri.

La densità è quella di un tabellone: molte righe, nessuna decorazione, gerarchia affidata alla larghezza del carattere (condensato e pesante per orari e titoli, normale per il testo) e a un unico colore segnaletico. Le superfici sono piatte, gli angoli vivi, la profondità non esiste: si separa con filetti, non con ombre. Rifiutato in modo esplicito dal committente: la home da kit (barra laterale, griglia di card metriche, grafico), le palette tenui e prudenti, la tipografia anonima, l'aspetto da servizio dello Stato (nessun Designers Italia / Bootstrap Italia).

Il sistema dice sempre la verità sul proprio stato: la fascia cambia tono quando lo stato è sconosciuto o compromesso, e i dati dimostrativi lo dichiarano nella riga stessa.

**Key Characteristics:**
- Testata grafite a schede orizzontali, scheda attiva sottolineata da 4px di giallo.
- Fascia di stato a tutta larghezza sotto la testata: gialla se l'archivio è integro, rossa se compromesso o illeggibile, grigia se mai verificato o in lettura.
- Tabelloni su carta bianca: intestazione condensata con filetto nero da 2px, righe separate da filetti grigi da 1px, orari condensati in cifre tabellari.
- Etichette di sezione come cartelli gialli pieni («Partenze», «Arrivi», «Corsa del pacchetto»).
- Stati come parole colorate in grassetto, mai pillole né badge.
- Un solo carattere, Archivo variabile (asse wdth 62–125), servito dal progetto.

## Colors

Una palette da segnaletica: grafite e carta come materiali, un giallo che possiede la scena, quattro inchiostri di stato a contrasto pieno.

### Primary
- **Giallo segnaletico** (giallo): il colore del sistema. Possiede la fascia di stato «Archivio integro», le etichette di sezione, il numero delle tappe del modulo, la sottolineatura della scheda attiva in testata, l'alone del focus e la selezione del testo. Sempre con testo grafite sopra, mai come colore del testo.
- **Giallo velato** (giallo-velato): solo come fondo temporaneo di stato: la riga «in corso» del tabellone di versamento e l'area di trascinamento mentre un file vi passa sopra.

### Neutral
- **Grafite pensilina** (grafite): fondo della testata. Nessun altro uso di superficie scura.
- **Grafite rialzata** (grafite-2): fondo del campo di ricerca dentro la testata.
- **Carta** (carta): fondo di pagina e di ogni tabellone. Bianco puro, non avorio.
- **Binario** (binario): passaggio del puntatore sulle righe e sui bottoni secondari.
- **Inchiostro** (testo): testo, filetti d'intestazione da 2px, bottone primario, bordo di focus.
- **Inchiostro attenuato** (testo-2): note, sottorighe, stati neutri, orari non ancora raggiunti (contrasto su carta circa 6,6:1).
- **Testo su grafite** (su-grafite): voci di testata non attive e nome del ruolo.
- **Filetto** (filetto): divisori di riga da 1px, bordo inferiore della sotto-navigazione, fascia neutra, bottone disabilitato.
- **Bordo campo** (bordo-campo): bordo da 1px dei campi di inserimento su carta; bordo tratteggiato dell'area di trascinamento vuota.
- **Bordo su grafite** (bordo-su-grafite): bordo da 1px dei comandi dentro la testata (ricerca, «Esci»).
- **Inchiostro pieno** (inchiostro-pieno, #000000): solo il passaggio del puntatore sul bottone primario e sui link (`a:hover`).
- **Segnaposto su grafite** (segnaposto-su-grafite, #B5B9BE): solo il testo segnaposto della ricerca in testata (contrasto sufficiente sulla grafite rialzata).

### Stati
- **Verde in orario** (ok): «Conservato», «Fatto», «In orario»; filetto superiore da 4px dell'esito positivo.
- **Arancio rischio** (rischio): «Rischio ritardo», quota oltre l'80%, avvisi di classi di riserva.
- **Rosso critico** (critico): «Rifiutato», «Interrotta», errori di campo, fascia «Integrità compromessa» o «Stato non disponibile» (testo bianco).
- **Blu informazione** (info): stati in corso («In verifica», «In conservazione», «In corso»).

### Named Rules
**La regola del giallo unico.** Il giallo segnala, non decora: fascia di stato, etichette di sezione, scheda attiva, focus. Mai su grandi superfici oltre la fascia, mai come colore del testo, mai sfumato.

**La regola della verità di stato.** La fascia è gialla solo quando la verifica d'integrità è riuscita. Errore o integrità compromessa: fondo critico con testo bianco. In lettura o mai verificato: fondo filetto. Uno stato sconosciuto non si mostra mai come «tutto bene».

**La regola del foglio unico.** Tutte le pagine sono nel sistema: `src/index.css` è l'unico foglio di stile e contiene solo token `--vd-*` e classi `vd-*`, un nome per componente. Il vecchio ponte (`--bg-*`, `--text-*`, `--gold*`, `.card`, `.btn-gold`, `.tag-*`, `.fade-in*`…) e le direttive Tailwind sono stati eliminati il 20/10/2026: non vanno reintrodotti. Una classe nuova si aggiunge a `index.css` e a questo documento, non in un foglio di area. Anche `src/styles/fe-enti.css`, l'ultimo foglio di area, è stato cancellato (g3-frontend): non esistono più fogli oltre a `index.css`.

## Typography

**Display Font:** Archivo Variable (con Helvetica Neue, Arial)
**Body Font:** Archivo Variable, a larghezza normale
**Label/Mono Font:** Archivo condensato per le intestazioni; ui-monospace di sistema solo per identificativi e registro tecnico

**Character:** un solo grottesco a larghezza variabile che fa due voci: condensato e pesante come i numeri di un tabellone, normale e piano per le frasi. Archivo è installato da `@fontsource-variable/archivo` (file `wdth.css`, asse 62–125): nessun servizio di caratteri esterno.

### Hierarchy
- **Display** (800, 28px, larghezza 85%, interlinea 1,2, -0,01em): il titolo della fascia di stato (22px su telefono).
- **Titolo di pagina** (800, 32px, larghezza 85%, interlinea 1,2, -0,01em): l'h1 di ogni pagina interna (`.vd-testa-pagina h1`), 28px su telefono.
- **Headline** (800, 22px, larghezza 85%, interlinea 1,25): etichette di sezione, titoli delle tappe, titolo «Situazione dell'archivio».
- **Orario** (700, 20px, larghezza 75%, cifre tabellari): la prima colonna di ogni tabellone (data, ora) e i numeri della situazione dell'archivio.
- **Marchio** (800, 20px, larghezza 112%, -0,01em): solo la parola «VerumDoc» in testata, l'unico uso espanso.
- **Body** (400, 15px, larghezza 100%, interlinea 1,45): celle dei tabelloni e voci di navigazione. 16px per campi, bottoni e riga della fascia; 17px nei testi descrittivi a colonna stretta (massimo 720px).
- **Esito** (700, 17px): il titolo di un esito o di un avviso importante (`.vd-esito-titolo`) e i titoletti dentro un dialogo.
- **Stato** (700, 14px): le parole di stato colorate.
- **Label** (700, 13px, larghezza 75%): intestazioni di colonna dei tabelloni. Mai in maiuscolo.
- **Nota** (400, 14px, inchiostro attenuato): note sotto i tabelloni, testi d'aiuto (13px), sottorighe di cella (13px).
- **Mono** (13px): identificativi dei pacchetti (primi 8 caratteri, intero nel title), segnature, righe del registro tecnico.

### Named Rules
**La regola della larghezza che parla.** La gerarchia si fa con la larghezza dell'asse wdth prima che con la dimensione: 75% per orari e intestazioni, 85% per titoli, 100% per il testo, 112% solo per il marchio. Nessun maiuscoletto, nessuna spaziatura larga.

**La regola delle cifre in colonna.** Ogni tabellone usa cifre tabellari, così gli orari si allineano come su un pannello.

## Layout

Colonna di pagina larga al massimo 1360px, centrata, con margine laterale di 32px che scende a 16px sotto i 640px. Testata e fascia di stato corrono a tutta larghezza; il loro contenuto rispetta la stessa colonna. La testata è alta 56px; la sotto-navigazione dell'area attiva è alta 44px e chiusa da un filetto.

Sotto la fascia il contenuto parte a 28px e chiude a 48px dal fondo; le sezioni maggiori distano 40px. La home affianca due tabelloni con flex che va a capo: Partenze circa due terzi (base 620px), Arrivi un terzo (base 340px), separati da 36px. Il versamento affianca il modulo (base 560px) e il tabellone della Corsa (base 340px) a 48px. Le griglie di campi usano colonne automatiche da almeno 200 o 260px con interspazio 16px × 20px.

Le pagine interne sono avvolte dalla testata in `.vd-pagina` con 28px sopra e 48px sotto; solo le rotte elencate in `RIDISEGNATE` (`Layout.tsx`: home e versamento, che hanno fascia o testa a tutta larghezza) gestiscono da sole la colonna. Le sezioni di una pagina si impilano in `.vd-pila` a 40px.

Celle dei tabelloni: 12px × 14px; intestazioni 8px × 14px. Sotto i 640px un tabellone marcato come «impilabile» perde l'intestazione visiva e ogni riga diventa un blocco: orario a sinistra (almeno 84px) e dettagli impilati a destra. I tabelloni stretti restano tabelle con scorrimento orizzontale nel proprio contenitore, mai la pagina.

## Elevation & Depth

Il sistema è piatto. Nessuna ombra di superficie, nessuna sovrapposizione, nessun fondo sfumato. La profondità si costruisce con tre materiali (grafite, giallo, carta) e con i filetti: 2px di inchiostro sotto un'intestazione o sopra un'area d'azione, 1px di filetto tra le righe, 4px di colore di stato sopra un esito.

Due eccezioni funzionali, non decorative: il velo del dialogo (`::backdrop` grafite al 62%), che separa il dialogo dalla pagina, e la sfumatura di 48px sul bordo delle schede di testata quando scorrono (`mask-image`), che segnala che altre aree sono nascoste.

L'unico `box-shadow` del sistema è l'alone del focus: contorno di 3px inchiostro a 2px di distanza più un anello giallo di 7px (`box-shadow: 0 0 0 7px #FFC20E`). Sulla fascia gialla l'anello diventa bianco; in testata il contorno diventa giallo senza anello.

### Named Rules
**La regola del filetto.** Si separa con una linea, mai con un'ombra o con una card. Filetto spesso (2px inchiostro) per chiudere un'intestazione o aprire la zona dei comandi; filetto sottile (1px) tra righe dello stesso elenco.

## Shapes

Angoli vivi ovunque: fascia, etichette, tabelloni, area di trascinamento (0px). I soli controlli interattivi (bottoni, campi, ricerca, «Esci») hanno un raggio quasi impercettibile di 2px. Le forme ricorrenti sono rettangoli pieni (il cartello giallo dell'etichetta, il numero giallo della tappa) e linee orizzontali. Nessun cerchio, nessuna pillola, nessun puntino di stato, con una sola eccezione: le fermate della linea della corsa (vedi «Motivi ferroviari»), sempre accompagnate dalla parola di stato.

## Components

### Buttons
Pesanti e diretti, come un comando di banchina.
- **Shape:** quasi vivo (2px), bordo pieno da 2px, altezza minima 44px.
- **Primario:** fondo inchiostro, testo bianco 16px/700, padding orizzontale 20px. Un solo primario per vista («Versa in conservazione»).
- **Hover / Focus:** il fondo passa a nero pieno; focus con l'alone giallo descritto in Elevation & Depth.
- **Secondario:** fondo trasparente, bordo e testo inchiostro; al passaggio fondo binario.
- **Disabilitato:** fondo e bordo filetto, testo inchiostro attenuato, cursore non consentito.
- **Testuali:** le azioni minori sono link sottolineati in grassetto 15px («Dettaglio della verifica», «Aggiungi metadati facoltativi»), non bottoni fantasma.

### Inputs / Fields
- **Style:** fondo carta, bordo 1px bordo-campo, raggio 2px, altezza 44px, testo 16px. Etichetta sopra (14px/600), aiuto sotto (13px, inchiostro attenuato); l'obbligo è un asterisco con testo nascosto «(obbligatorio)».
- **Focus:** il bordo diventa inchiostro, più l'alone giallo.
- **Errore:** bordo critico (`aria-invalid`) e messaggio come parola di stato critica sotto il gruppo o accanto al bottone.
- **Ricerca in testata:** fondo grafite rialzata, bordo bordo-su-grafite, testo bianco, alta 40px, larga tra 180 e 260px (16vw) sugli schermi larghi per lasciare una riga alle schede, 280px quando la testata va a capo (sotto 900px).
- **Filtri** (`.vd-filtri`): riga di campi larghi 280px che va a capo, allineati in basso, 28px sopra il tabellone.
- **Scelta esclusiva** (`.vd-scelte` + `.vd-scelta`): radio nativi in `fieldset`/`legend`, una riga da almeno 44px per opzione con titolo 16px/700 e spiegazione 13px, separate da filetti.
- **Scelta multipla** (`.vd-caselle` + `.vd-casella`): caselle native 20px con area cliccabile di 44px, in colonne automatiche da 240px.
- **Testo lungo** (`.vd-input.vd-textarea`): almeno 88px, ridimensionabile in verticale.
- **Modulo aperto sopra un tabellone** (`.vd-modulo`): aperto da 2px d'inchiostro, titolo headline, campi in `.vd-griglia` (colonne da 240px), comandi in `.vd-comandi`.
- **Campo con errore proprio** (`components/Modulo.tsx`, `Campo`): etichetta, campo, aiuto e messaggio d'errore del campo (parola di stato critica 13px) collegati con `aria-describedby`; `aria-invalid` sul campo. Gli errori arrivano dai controlli della pagina (gli stessi del servizio) e dal 422 del servizio, letto per campo da `leggiErrore(e).campi`. Accanto al bottone resta solo il conteggio («2 campi da correggere») e l'errore generale.
- **Riepilogo da confermare** (`components/Modulo.tsx`, `Riepilogo`): una registrazione che non si cancella (contratto, nomina) passa da «Controlla e registra» a un `.vd-esito--info` con i dati in `.vd-dati`, l'effetto collaterale in un `.vd-avviso` («La nomina in carica sarà revocata») e due comandi: «Conferma e registra» (primario, riceve il focus) e «Correggi i dati». Un rifiuto del servizio riporta al modulo con gli errori sui campi.
- **Scelta dell'ente** (`components/SceltaEnte.tsx`, `useEnte` + `SceltaEnte`): solo per amministrazione e responsabile del servizio, un `select` «Ente» nei filtri della pagina; l'ente del profilo è indicato «(ente del tuo profilo)». Le pagine mandano `?tenant_id=` con l'ente scelto. Se l'elenco degli enti non si legge il campo diventa una parola di stato critica con il motivo. Una pagina che consegna contenuti (Pacchetti conservati) dichiara con un `.vd-avviso` che il contenuto di un altro ente non si consegna.

### Navigation
- **Testata:** fondo grafite, marchio a sinistra, aree come schede orizzontali a 20px di distanza (15px; 500 non attive in su-grafite, 700 attiva in bianco con 4px di giallo sotto, `aria-current` su tutte le pagine dell'area), ricerca e ruolo a destra. Le schede stanno sempre su una riga: se non ci stanno scorrono senza barra, il bordo che nasconde altre aree sfuma (`.vd-schede[data-oltre]`) e la scheda attiva viene portata in vista. Su telefono ricerca e ruolo vanno a capo.
- **Sotto-navigazione:** fondo carta, pagine dell'area attiva in inchiostro 15px; la pagina attiva è in grassetto con 3px di inchiostro sotto. Una voce non abilitata resta visibile in inchiostro attenuato con «(non abilitato)».
- Collegamento «Vai al contenuto» come primo elemento focalizzabile.

### Fascia di stato (componente firma)
Banda a tutta larghezza subito sotto la navigazione, padding verticale 18px. Titolo display 28px («Archivio integro»), una riga di verifica 16px/500 e, allineati a destra, il selettore dell'ente e il link all'attestazione. Il tono segue la regola della verità di stato; la sezione è `aria-live="polite"`.

### Tabellone (componente firma)
Tabella su carta, larghezza piena. Intestazione: label condensata 13px con filetto inchiostro da 2px. Righe: filetto da 1px, fondo binario al passaggio. Prima colonna: orario condensato 20px, con eventuale sottoriga 13px a larghezza normale («tra 2 gg»). L'attività è un link sottolineato da 1px che al passaggio diventa una sottolineatura gialla da 3px. Ultima colonna: parola di stato. Le righe del tabellone Partenze della home sono calcolate dai dati del servizio (R12c: verifica della catena, pratiche che aspettano chi guarda, blocchi WORM). Le righe dimostrative esistono solo in sviluppo con `?dimostrativi=1`, dichiarate da `.vd-avviso-sviluppo` e nella cella («Esempio: in orario»), in stato neutro.

### Etichetta di sezione
Cartello giallo pieno, headline 22px, padding 2px × 10px, angoli vivi; accanto, allineata sulla linea di base, una nota o una dichiarazione. Esiste una variante bordata (trasparente con bordo inchiostro da 2px) definita nel foglio di stile ma non ancora usata.

### Tappa del modulo
Legenda con il numero in un rettangolo giallo (22px, larghezza 75%) seguito dal titolo headline e da una nota facoltativa. Le tappe distano 36px; la zona d'invio è aperta da un filetto inchiostro da 2px.

### Testa di pagina
`.vd-testa-pagina` (R12c, griglia): h1 titolo di pagina 32px e, sotto, una riga di spiegazione (`p` o `.vd-nota`, 15px inchiostro attenuato, al massimo 72 caratteri); comandi della pagina a destra in `.vd-testa-comandi`, allineati in basso; chiusa da 2px d'inchiostro dentro la colonna (mai a tutta larghezza), `--vd-corpo-alto` sopra il contenuto. Sotto 760px tutto si impila. Anche «Versa un documento» usa questa testa.

### Fascia di colonna
`.vd-fascia` + `--giallo | --critico | --neutro`: la fascia di stato dentro la colonna di una pagina interna (Catena delle impronte), con le stesse tre tonalità e le stesse parole della home; titolo display 28px, riga 16px, comandi a destra (`.vd-fascia-comandi`, bottoni secondari nel colore del testo). Le parole e i toni della home e della Catena delle impronte vengono da un'unica regola, `src/data/integrita.ts` (`fasciaIntegrita`), che legge l'ultima verifica COMPLETA (`integrita.esito`, `verificata_il`, `blocchi_aggiunti_dopo`): la riga dice data, blocchi verificati e quanti blocchi sigillati dopo non sono coperti.

**Comando «Avvia verifica completa».** Nella fascia della Catena delle impronte, solo per amministrazione e responsabile del servizio, un bottone primario (`.vd-bottone`) prima di «Rileggi lo stato». La verifica è asincrona: l'avvio risponde subito con l'identificativo (o 409 con quello della verifica già in corso, che la pagina aggancia invece di avviarne un'altra); lo stato si rilegge ogni 5 secondi solo con la scheda in primo piano. Finché è in corso il bottone dice «Verifica in corso…» ed è disabilitato e sotto la fascia c'è un `.vd-esito--info` «Verifica in corso, avviata alle HH:MM». Poi: esito (`--ok` integra, `--critico` compromessa, `--info` nessun blocco), «Verifica interrotta» (`--rischio`) o «Verifica fallita» (`--critico`), entrambe senza esito sulla catena; un avvio rifiutato è un `EsitoCritico` «Verifica non avviata». Alla chiusura la fascia, la home e i Blocchi rileggono lo stato. «Compromessa» è un esito, non un errore. La firma dei blocchi si dice con la sua qualità: «4.790 firmati e marcati · firma non qualificata» (`qualitaFirma` in `src/data/integrita.ts`), mai un numero che contraddica «senza firma qualificata».

### Esito
`.vd-esito` + `--ok | --critico | --rischio | --info`: blocco con filetto superiore da 4px nel colore di stato, titolo `.vd-esito-titolo` 17px/700 nello stesso colore, al massimo 820px; il neutro ha il filetto in inchiostro attenuato. Si usa per il risultato di un comando (versamento, esibizione), per gli errori di caricamento (con il motivo restituito dal servizio, il riferimento per la segnalazione e «Riprova») e per gli stati vuoti che vanno spiegati. Un errore non è mai «nessun dato».

**Un solo trattamento per gli errori di lettura: `components/EsitoCritico.tsx`.** Titolo «X non disponibile», il motivo in parole (dal servizio, via `motivoDaLetto`), che cosa l'errore NON significa («Non significa che l'archivio sia vuoto»), su una riga a parte codice e riferimento per la segnalazione, e «Riprova» quando la pagina può rileggere. Nessuna pagina scrive più a mano un errore di caricamento in rosso.

**Tabellone impilato con etichette.** Su telefono ogni cella di un `.vd-impila` porta `data-label` con l'intestazione della sua colonna e la mostra sopra il valore (13px, inchiostro attenuato); le sole celle senza etichetta sono quelle di comandi (bottoni) e le righe di dettaglio. La prima colonna è larga almeno 7,5rem e il suo testo secondario va a capo. Contatori di paginazione: l'intervallo è calcolato dalle righe ricevute («Versamenti 51–62»), mai dal limite; da R04 gli elenchi sono a cursore e non mostrano il totale.

**Testata (R12c).** Una sola colonna (`--vd-pagina` 1360px, margine `--vd-gutter` 32/24/16px) per testata, sotto-navigazione, fascia e contenuto. ≥1024px: marchio, aree, ricerca, «Da fare» e menu della persona su una riga se le aree ci stanno, altrimenti le aree passano su una seconda riga grafite a tutta colonna (`data-righe="2"`, misurato in `Layout.tsx`: mai sovrapposizione con la ricerca). <1024px: le aree escono dalla barra ed entrano nel menu a scomparsa («Menu», `.vd-menu-pannello` a tutta larghezza); sotto 640px anche la ricerca sta nel menu. Le azioni della persona (tema, preferenze, credenziali, esci) sono in un solo menu (`.vd-menu`, disclosure con Esc, clic fuori e cambio pagina che chiudono; il fuoco torna al bottone). I comandi di testata (`.vd-comando-testata`) sono testo su grafite senza riquadro, con fondo grafite rialzata al passaggio o quando aperti; il conteggio di «Da fare» è un rettangolo giallo (`.vd-conta`) come il numero della tappa. L'ente su cui si lavora sta nella sotto-navigazione, a destra delle pagine (`.vd-testata-ente`, etichetta visibile «Ente su cui lavori»). La scheda attiva si porta in vista solo se la riga scorre. Segnaposto della ricerca: «Cerca nell'archivio».

### Avviso di limite
`.vd-avviso` (filetto superiore arancio da 2px, 15px, al massimo 820px; `--neutro` in inchiostro attenuato): dichiara in modo permanente il limite di una funzione («Contenuto non consegnato per questo ente», «Sola lettura»), con il link «Che cosa manca» alla pagina `/da-realizzare/<chiave>`.

### Comandi minori
- **Comando testuale** (`.vd-comando-testo`): azione minore come link sottolineato in grassetto 15px, anche quando è un `<button>`, alto 44px; al passaggio sottolineatura gialla da 3px.
- **Bottone-link** (`.vd-link-bottone`): un bottone dentro un testo o una cella che apre qualcosa (la segnatura che apre il dettaglio), sottolineato da 1px, eventualmente in mono.
- **Selettore** (`.vd-selettore`): gruppo di bottoni con `aria-pressed`; il premuto è primario, gli altri secondari (periodo dei livelli di servizio).
- **Conferma in riga**: un'azione distruttiva (disattiva, revoca) non apre una finestra: la cella dei comandi (`.vd-comandi-cella`) mostra la domanda, il bottone che conferma e «Annulla»; l'esito resta nella riga.
- **Paginazione** (`.vd-paginazione`): «Precedente/Successiva» secondari ai lati e la posizione al centro in cifre tabellari; su telefono la posizione va sopra.

### Dati
- **Scheda termine–valore** (`.vd-dati`, `<dl>`): termine 14px/600 in una colonna di 128–240px, valore 15px, righe a filetto; numeri `.vd-numero` condensati 20px; su telefono il termine sta sopra il valore.
- **Tabellone termine–valore** (`.vd-tabellone.vd-impila.vd-coppie`): intestazione di riga `th.vd-riga`; su telefono l'intestazione va sopra il valore. Numeri a destra con `td.vd-num`.
- **Riga di dettaglio** (`tr.vd-dettaglio` con `.vd-dettaglio-griglia`): si apre sotto una riga del tabellone da un bottone con `aria-expanded`, chiusa da 2px d'inchiostro; elenchi interni in `.vd-elenco`.
- **Identificativi lunghi** (`.vd-a-capo`): vanno a capo dentro la loro cella, mai scorrimento della pagina.

### Dialogo laterale
`.vd-dialogo`: `<dialog>` nativo aperto con `showModal()`, largo 580px a destra e alto quanto la finestra (tutta la larghezza su telefono), filetto d'inchiostro a sinistra, velo grafite. Testa fissa (`.vd-dialogo-testa`) con titolo e «Chiudi» da 44px, corpo (`.vd-dialogo-corpo`) a sezioni con titoletti 17px chiusi da 2px. Esc e clic sul velo chiudono; il focus torna al comando che l'ha aperto.

### Chiave mostrata una volta
`.vd-chiave`: riquadro con bordo d'inchiostro da 2px, mono 15px, selezionabile con un clic (`user-select: all`), che riceve il focus appena generata. La pagina dice che è l'unico momento in cui la chiave è leggibile.

### Sezioni apribili
`.vd-voci` + `.vd-voce` (`<details>` nativo): una riga da almeno 52px per sezione con numero condensato, titolo 16px/700, parola di stato e «+»/«−» a destra; il corpo `.vd-voce-corpo` è a colonna di lettura (72 caratteri). Su telefono lo stato va sotto il titolo.

### Pagina «da realizzare»
`/da-realizzare/<chiave>` (`DaRealizzarePage.tsx`): titolo, «Funzione da realizzare» in stato neutro, che cosa farà, che cosa manca e il riferimento. Chiavi: `marche`, `firma-manuale`, `scarico-pdd`, `accordi`, `allarmi-registro` (`scarto` e `fascicoli` tolte in R05) (`verifica-catena` e `accesso-spid` tolte da g3-frontend: le funzioni ora esistono). Una funzione inesistente si collega qui o si dichiara nel testo, mai si finge.

### Pagina di accesso
`.vd-accesso-pagina` (R12c): due colonne ≥1024px. A sinistra `.vd-accesso-identita` su grafite, ferma mentre la destra scorre: marchio, cartello giallo «Servizio di conservazione digitale», motto (`motto`, 52→34px, larghezza 85%) e un piccolo orario delle tre azioni vere (Versare, Verificare, Consultare) con filetti; nessun dato inventato. A destra `.vd-accesso` (al più 600px, centrata nello spazio): testa con titolo e filetto, poi accesso con identità digitale, accesso senza SPID e, solo in sviluppo, i profili in un'area tratteggiata (`.vd-accesso-sviluppo`, elenco `.vd-profili`). Sotto 1024px l'identità diventa una banda grafite in alto. Due comandi primari «Entra con SPID» e «Entra con CIE»; al ritorno il frammento `#spid_codice=` o `#spid_errore=` si legge e si toglie subito dall'indirizzo. Con più enti la persona sceglie l'ente con radio (`.vd-scelte`). Ogni errore (SPID-001…012, SPID-008-ENTE, AUTH-003) è un `.vd-esito--critico` con che cosa è successo, che cosa fare e il codice. I profili di sviluppo esistono solo con `npm run dev` (`import.meta.env.DEV`).

### Code di scarto e paginazione a cursore (R04, 23/10/2026)
- **Pagina «Code di scarto»** (`/code-scarto`, `pages/CodeScartoPage.tsx`, area Servizio, solo amministrazione e responsabile del servizio): etichetta «Code» con un tabellone termine–valore (coda in parole del mestiere, nome tecnico in mono, chi scarta, messaggi da gestire in cifre condensate); il nome della coda è un bottone-link (`aria-pressed`) che filtra l'elenco. Etichetta «Messaggi» con filtri (stato, coda, ente, versamento), comandi in blocco sopra il tabellone (`.vd-comandi-elenco`) e tabellone con caselle di selezione (`.vd-cella-scelta`: prima colonna da 44px; su telefono la casella va in alto a destra e l'orario resta la prima colonna). Foto: a richiesta con `FOTO=1 npm run e2e -- foto` (cartella `docs/design/foto/`, ignorata da git).
- **Piano prima di un'azione massiva** (REGOLE §13): «Rielabora» apre un dialogo laterale con «Che cosa succederà» (`.vd-dati`: da rielaborare, bloccati, chi sarà avvisato), il tabellone dei messaggi con esito per riga («Si rielabora» in verde con l'azione, «Bloccato» in rosso con il motivo, avvisi in arancio) e la zona «Conferma» (`.vd-modulo`) con il motivo obbligatorio (almeno 10 caratteri) e un solo bottone primario «Conferma e rielabora N messaggi». Se il servizio risponde «piano cambiato» il dialogo mostra un `.vd-esito--rischio` e il piano aggiornato da confermare di nuovo. Titolo del dialogo: `.vd-dialogo-titolo` (headline 22px).
- **Conferma in riga con motivo** (`.vd-conferma-riga`): un'azione che chiede un motivo (Archivia) mostra nella cella dei comandi la domanda, il campo di testo, il bottone e «Annulla».
- **Paginazione a cursore** (`components/Paginazione.tsx`, `useCursori` + `Paginazione`): gli elenchi del servizio non danno più il totale (VD-C-102); la posizione al centro è «Notifiche 21–40», senza «di N». Usata da Code di scarto, Notifiche, Registro delle operazioni, storico delle esibizioni.
- Classi nuove in `index.css`: `.vd-dialogo-titolo`, `.vd-cella-scelta`, `.vd-sotto-normale`, `.vd-forte`, `.vd-cella-larga`, `.vd-comandi-elenco`, `.vd-conferma-riga`.

### Console di configurazione, ciclo di vita e tema scuro (VD-DR-05, R05, 23/10/2026)
Decisione VD-DR-05: le console del conservatore e del cliente, le pratiche del ciclo di vita e il tema scuro entrano nel sistema «Partenze» **senza componenti né colori nuovi**: stessi tabelloni, stesse parole di stato, `EsitoCritico` per ogni errore di lettura. Foto a 1440 e 390 px, chiaro e scuro: a richiesta con `FOTO=1 npm run e2e -- foto` in `docs/design/foto/`, ignorata da git (dal rilascio R06 le foto non si conservano nel repository).

**Tema scuro.** «La stessa segnaletica di notte»: la testata resta grafite e il giallo resta il giallo; cambiano solo i materiali e gli inchiostri, ridefinendo i token su `:root` (nessuna classe in più).
- Automatico con `prefers-color-scheme: dark`, oppure scelto dalla persona col comando «Tema» in testata (`components/Tema.tsx`: automatico → chiaro → scuro, `<html data-tema>`, ricordato nel browser; senza memoria disponibile vale per la visita). Applicato prima del primo disegno (`main.tsx`).
- Valori scuri: carta `#121417`, binario `#1D2024`, testo `#ECEDEF`, testo-2 `#A8ADB4`, filetto `#2E3237`, bordo-campo `#8A9097`, giallo-velato `#3A3112`; stati schiariti oltre 7:1 sul fondo: ok `#5CC995`, rischio `#F29A55`, critico `#FF8A7E`, info `#86B6F5`.
- Token nuovi d'inchiostro «sopra un fondo pieno», uguali o invertiti fra i temi: `--vd-su-testo` (testo del bottone primario), `--vd-su-giallo` (sempre grafite: etichette, fascia gialla, numero di tappa, selezione), `--vd-su-grafite-forte` (voce attiva e comandi in testata), `--vd-fondo-critico` (`#B42318` in ogni tema: la fascia critica resta rossa con testo bianco), `--vd-velo` (velo del dialogo). Promossi a token i quattro colori che erano scritti in linea: `--vd-giallo-velato`, `--vd-bordo-su-grafite`, `--vd-inchiostro-pieno`, `--vd-segnaposto-su-grafite`. Sulla fascia gialla e dentro `.vd-su-giallo` i token d'inchiostro tornano quelli del giorno (il testo su giallo è sempre grafite).
- Pagine esistenti allineate ai token: `Layout.tsx` (testata), `OverviewPage.tsx` (fascia), `VersamentoLivePage.tsx` (giallo velato, numero della tappa), `SLAPage.tsx` (colori del grafico come `var(--vd-*)`).

**Console di configurazione** (area «Configurazione»). Una riga per voce: *Voce* (descrizione e codice in mono) · *Valore* (in grassetto; un elenco di codici in mono a peso normale) con sotto l'**origine** («ereditato da: conservatore», «stabilito a livello ente», «predefinito normativo») · *Chi decide* («Imposto dalla norma» neutro con il riferimento normativo sotto; «Non modificabile qui» con il motivo del servizio; «Modificabile», con i livelli solo se la voce non vale ovunque) · *Comandi*. Le severità delle regole sono parole di stato: Blocca (critico), Avvisa (rischio), Ignora (neutro).
- **Modifica in riga** (`.vd-modifica-riga`): il campo del tipo giusto (`console/CampoValore.tsx`: intero, sì/no, scelta, elenco di codici, data, durata) e «Metti nella bozza», «Torna al valore ereditato», «Annulla» nella cella dei comandi, come la conferma in riga. Nessuna modifica si applica da qui.
- **Bozza in preparazione**: `.vd-modulo` sopra i tabelloni con le modifiche raccolte, il motivo obbligatorio e un solo primario «Salva la bozza (N modifiche)». Finché esiste, il livello e l'ente restano fermi (una bozza = un ambito) e la pagina lo dice.
- **Pagina della bozza** (anteprima e applica, REGOLE §13): «Che cosa cambia» (*Prima* attenuato `.vd-prima`, *Dopo* in grassetto `.vd-dopo`, ognuno con la sua origine), poi «Impatto sui versamenti» con la frase in parole del mestiere («Con questa modifica 12 versamenti degli ultimi 30 giorni sarebbero stati rifiutati»), la scheda dei numeri e il **campione dichiarato** (limite raggiunto, calcolo troncato: parole di rischio), gli esempi come tabellone con orario. Bloccati: `.vd-esito--critico` con l'elenco. Zona «Applica» (`.vd-modulo`) con l'impronta del piano in mono e «Conferma e applica N modifiche». **409 «piano cambiato»**: `.vd-esito--rischio` «Il piano è cambiato… Nulla è stato scritto», il piano nuovo al posto del vecchio e l'impatto precedente dichiarato superato («Ricalcola l'impatto»).
- Proposte agli enti: tabellone con Accetta/Rifiuta come conferme in riga con motivo; per il conservatore lo stato per ente.

**Ciclo di vita** (area «Pratiche»).
- **Corsa della pratica**: l'iter completo del tipo come il tabellone «Corsa del pacchetto» del versamento: ora in cui ogni tappa è stata raggiunta (le tappe future `--:--` in inchiostro attenuato, `.vd-ora--attesa`), parola di stato sotto la tappa (Fatto, Qui adesso, In attesa, Respinta), chi e perché a destra; la tappa attuale è la riga in corso (`tr.vd-riga-in-corso`, giallo velato). Il nulla osta compare solo per gli enti pubblici.
- **Prossimo passo**: un bottone per ogni azione consentita dal servizio («Proponi…», «Autorizza come produttore…», «Esegui…»; «Respingi…» secondario) e, sotto, le azioni non consentite con il motivo del servizio. Ogni azione apre un `.vd-modulo` con gli allegati obbligatori (radio fra quelli caricati o «Carica un file»; firma «rilevata, non verificata» in rischio) e il motivo. **409 «stato cambiato»**: `.vd-esito--rischio` con lo stato attuale e «Rileggi la pratica».
- **Nuova pratica**: tappe numerate come il versamento (`.vd-tappa`, promossa a classe): 1 tipo (scelta esclusiva, filtrata per ruolo), 2 criteri, 3 anteprima (inclusi in verde, bloccati in rosso, tabellone termine–valore «Perché alcuni pacchetti restano fuori», esempi), poi «Conferma»; le pratiche puntuali passano dal riepilogo da confermare.
- «In attesa di te»: le pratiche che aspettano il ruolo di chi guarda, dalla più vecchia.

**Registro e verifiche** (area «Integrità»). Fascia di colonna con la regola della verità di stato applicata al checkpoint: gialla solo se sigillato **e** controfirmato; critica se il testimone ha rifiutato o lo stato non si legge; neutra se in attesa (con l'età dell'attesa) o testimone non configurato. Allarmi e blocchi del registro non esposti dal servizio: `.vd-avviso` con «Che cosa manca».

**Scalare con gli utenti.** Ogni elenco è a cursore (`useCursori`/`Paginazione`), con filtri lato servizio; nessun totale calcolato nel browser; nessun elenco caricato per intero (l'elenco degli account SFTP di un ente è piccolo e il servizio lo dà intero). Le liste «in attesa» leggono al più 10 righe per stato e lo dichiarano.

**Classi nuove in `index.css`** (sezione «R05 — VD-DR-05»): `.vd-riga-in-corso`, `.vd-ora--attesa`, `.vd-tappa` (+ `-n`, `-titolo`), `.vd-area-file` (+ `--scelto`, `--sopra`, `-nome`), `.vd-comando-testata`, `.vd-prima`, `.vd-dopo`, `.vd-cella-valore`, `.vd-modifica-riga`, `.vd-impila--pieno` (tabellone senza colonna d'orario: su telefono ogni cella su una riga a tutta larghezza). Il comando «Esci» usa ora `.vd-comando-testata` come «Tema».

**Pagina «da realizzare»**: tolte `scarto` e `fascicoli` (le funzioni ora esistono); aggiunte `accordi` (elenco degli accordi non offerto dal servizio) e `allarmi-registro`.

### Revisione grafica della console (R12c, 31/10/2026)
Correzioni di sistema dopo la prova sulla console vera: niente componenti né colori nuovi.
- **Token di disposizione** su `:root`: `--vd-gutter` (32px, 24px sotto 1024, 16px sotto 640), `--vd-testata-h` 56px, `--vd-sotto-h` 48px, `--vd-sezione` 40px (32px su telefono), `--vd-corpo-alto` 28px (20px), `--vd-corpo-basso` 56px. `.vd-corpo-pagina` applica alto e basso.
- **Fascia della home** (`.vd-banda` + `--giallo | --critico | --neutro`, `.vd-banda-corpo`): titolo e riga a sinistra, comandi a destra in griglia; la scelta dell'ente non sta più nella fascia (è una sola, in sotto-navigazione).
- **Stato vuoto** (`components/Vuoto.tsx`, `.vd-vuoto`): filetto d'inchiostro da 2px, titolo 17px/700, che cosa significa (15px attenuato) e l'azione da fare (bottone o comando testuale). Mai per gli errori di lettura (`EsitoCritico`).
- **Affiancati** (`.vd-affianca`, `--home`, `-principale`, `-laterale`): modulo e tabellone, Partenze e Arrivi, che vanno a capo.
- Classi nuove: `.vd-console`, `.vd-salta`, `.vd-testata-fondo`, `.vd-testata-azioni`, `.vd-scheda-area`, `.vd-cerca`, `.vd-conta`, `.vd-menu` (+ `-bottone`, `-pannello`, `-cerca`, `-aree`, `-area`, `-voce`, `-persona`, `-chi`, `-comandi`), `.vd-scelta-tema`, `.vd-sotto-testata` (+ `-corpo`), `.vd-sotto-schede`, `.vd-scheda-pagina`, `.vd-titolo-sezione`, `.vd-dopo-tabellone`, `.vd-in-lettura`, `.vd-banda*`, `.vd-avviso-sviluppo`, `.vd-vuoto*`, `.vd-accesso-*`, `.vd-profili`.
- **Prove** (`e2e/r12c.spec.ts`): aree mai sotto ricerca, azioni o marchio e mai tagliate a 1280 e 1850px; nessuno scorrimento orizzontale a 390px in chiaro e scuro; menu a scomparsa da tastiera; contrasto AA con axe-core (`@axe-core/playwright`, sola prova); nessuna riga «Esempio» nel quadro senza l'indicatore di sviluppo.

### Motivi ferroviari (R12c, richiesta del committente)
Riferimenti ai binari, con misura: tre usi soli, nessuna animazione, colori dai token (corretti in chiaro e scuro perché le rotaie usano `--vd-testo` e le traversine `--vd-bordo-campo`), disegnati con gradienti CSS a passi in pixel (nitidi a ogni zoom).
- **Binario** (`--vd-binario-sfondo`, `<hr class="vd-binario" aria-hidden="true">`): due rotaie da 2px e traversine da 3px ogni 14px, alto 12px. **Uso consentito**: chiusura della testa di pagina (`.vd-testa-pagina::after`, `.vd-accesso-testa::after`, al posto del filetto da 2px) e separazione fra blocchi maggiori di una pagina (nel quadro fra Partenze/Arrivi e la situazione dell'archivio). **Vietato**: fra le righe dei tabelloni, sotto le intestazioni di colonna, dentro moduli e dialoghi (lì restano i filetti), più di due volte per pagina.
- **Passaggio a livello** (`--vd-passaggio`): strisce diagonali giallo/grafite di 8px, alte 7px, sul bordo superiore delle fasce di attenzione: `.vd-banda--neutro` e `--critico` (mai verificata, in lettura, non disponibile, compromessa), `.vd-fascia--neutro` e `--critico`, `.vd-avviso-sviluppo`. **Mai** sulla fascia «Archivio integro» (resta giallo pieno), mai a tutta superficie, mai su bottoni o tabelloni.
- **Linea della corsa** (`.vd-linea` + `.vd-fermata--fatta | --attuale | --attesa | --fallita`): la «Corsa del pacchetto» come binario verticale; a sinistra l'ora (vuota finché la fermata non è raggiunta, mai «--:--»), al centro il pallino da 18px sul binario (pieno d'inchiostro = raggiunta, giallo = in corso, vuoto = in attesa, rosso = interrotta), a destra la fase con la parola di stato. Il binario già percorso è d'inchiostro, quello da percorrere in bordo campo. La fermata in corso ha il nome su giallo velato e `aria-current="step"`. È l'unico posto dove compare un cerchio.

### Canali esterni (R13b, 31/10/2026)
Nessun componente né colore nuovo.
- **Canali PEC** (`/canali/pec`, `pages/CanaliPecPage.tsx`, area «Canali»; conservatore e amministratore dell'ente): testa e impostazioni del gruppo «pec» dalla console delle impostazioni; per il conservatore tabellone dei canali con l'ora dell'ultimo invio in prima colonna, stato in parole («Pronto», «Non utilizzabile», «Mai visto dal lavoratore», «Disattivato») con i problemi sotto, comandi «Prova d'invio» (conferma in riga con l'indirizzo di prova) e «Modifica» (dialogo laterale con il modulo, mai campi per i segreti: la nota dice dove vanno); tabellone dei messaggi con filtri, parola di stato (Consegnato in verde, Accettato e Inviato in blu, Mancata consegna, Non accettato, Non partito in rosso), avviso «consegna non confermata» in arancio, «Ricevute» nel dialogo laterale (tabellone delle ricevute, «Scarica il messaggio»); «Ricevute non associate» come evidenza.
- **Webhook**: «Ruota il segreto» come conferma in riga con la durata della transizione; il nuovo segreto nella `.vd-chiave` mostrata una volta; durante la transizione la riga dice «doppia firma» in arancio con «Ritira il precedente».
- **Campanella**: sezione «Il tuo account» (voci personali) fra «Da fare per te» e «Avvisi».
- Classe nuova: `.vd-pila--dopo` (distanza di sezione dopo la console delle impostazioni).
- Prove: `e2e/r13b.spec.ts` (390 e 1280 px, chiaro e scuro, nessuno scorrimento orizzontale, axe-core AA); schermate solo con `R13B_FOTO` verso una cartella fuori dal repository.

### Scarti noti fra questo documento e il codice (aggiornato il 23/10/2026)
- Risolti con VD-DR-05: i quattro colori scritti in linea sono token `--vd-*`; il colore `#F1F1EE` della testata è sostituito da `--vd-su-grafite`.
- Le pagine contengono ancora circa 400 attributi `style=` (disposizione, e in pochi casi colori): la regola «solo classi `vd-*`» vale per i colori e i componenti; il debito di disposizione si riduce con l'elenco del §29.4 delle regole (VD-C-027, riserva).

## Do's and Don'ts

### Do:
- **Do** dare a ogni riga un orario in prima colonna, condensato al 75% e in cifre tabellari.
- **Do** scrivere gli stati come parole in grassetto 14px nei quattro inchiostri di stato (#0E7A46, #A63D00, #B42318, #1F5FAD) o in inchiostro attenuato per il neutro.
- **Do** aprire ogni tabellone con un'etichetta gialla piena e chiudere l'intestazione di colonna con 2px di inchiostro.
- **Do** cambiare il tono della fascia quando lo stato è sconosciuto, in lettura o compromesso.
- **Do** dichiarare i dati dimostrativi nella sezione e nella riga stessa, non solo in una nota a piè di pagina.
- **Do** usare i token `--vd-*` e le classi `vd-*` di `src/index.css` per ogni nuova schermata; il motivo di un errore si legge con `motivoErrore`/`leggiErrore` di `src/api/errori.ts`, i nomi dei ruoli con `NOME_RUOLO` di `src/auth/ruoli.ts`.
- **Do** mantenere comandi e campi alti almeno 44px e il focus con l'alone giallo.

### Don't:
- **Don't** usare pillole, badge, puntini o icone come portatori di stato.
- **Don't** usare ombre, card sollevate, sfumature o angoli arrotondati oltre i 2px dei controlli.
- **Don't** costruire la home da kit: barra laterale, griglia di card metriche, grafici decorativi.
- **Don't** usare il giallo come colore del testo o su superfici diverse da fascia, etichette, scheda attiva, numero di tappa e focus.
- **Don't** introdurre un secondo carattere di titolazione o caricare caratteri da servizi esterni.
- **Don't** reintrodurre le variabili e le classi del vecchio ponte (`--bg-*`, `--text-*`, `--gold*`, `.card`, `.btn-gold`, `.tag-*`…), fogli di stile per area o doppioni di una classe con un altro prefisso.
- **Don't** riprendere navy e oro, Instrument Serif o lo stile Designers Italia: il sistema precedente è dismesso.
