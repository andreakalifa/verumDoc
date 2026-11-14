---
version: 1
slug: "src-pages-overviewpage-tsx"
primary_target: "src/pages/OverviewPage.tsx"
related_targets: ["src/pages/VersamentoLivePage.tsx","src/components/Layout.tsx"]
---

# Home del servizio e Versamento

Scope: home del responsabile del servizio (`/overview`) e flusso di versamento (`/versamento`). Modo: Operate.
Pubblico: responsabile del servizio di conservazione (primario), operatori che versano. Compito: sapere in un colpo d'occhio se l'archivio è integro, cosa scade, cosa è arrivato e cosa è stato rifiutato; versare un pacchetto e leggerne l'esito.
Prova e contenuti: dati dimostrativi etichettati; nessuna funzione inesistente presentata come attiva.
Vincoli: React 18 + TS + Vite, italiano, WCAG 2.1 AA, nessun Designers Italia.
Direzione scelta dal committente il 20/10/2026 (VD-DR-01): Partenze. Momento memorabile: la fascia di stato gialla e il tabellone delle scadenze.
Non deciso: logo e nome definitivo.

## Direction contract

THESIS: il servizio di conservazione come orario ferroviario. Il tempo e la puntualità sono il contenuto: scadenze in partenza, versamenti in arrivo, ogni riga ha un orario e uno stato. Rifiuta la home da kit con sidebar, griglia di card metriche e grafico.

OWN-WORLD: grafite #1D1F22 per la testata, giallo segnaletico #FFC20E che possiede la fascia di stato e le etichette di sezione, bianco puro per i tabelloni. Archivo a larghezza variabile: condensata e pesante per orari e titoli, normale per il testo. Filetti neri di 2px sotto le intestazioni, righe sottili grigie, stati come parole colorate in grassetto, mai pillole. Angoli vivi, nessuna ombra.

STORY: il responsabile capisce subito se l'archivio è integro, vede la prossima scadenza a rischio e gli arrivi rifiutati, e agisce aprendo la riga.

FIRST VIEWPORT: testata grafite con schede orizzontali e ricerca; fascia gialla a tutta larghezza con «Archivio integro» a 28px e la riga di verifica; sotto, a sinistra il tabellone Partenze (2/3), a destra Arrivi (1/3). Azione primaria: la riga a rischio e il link all'attestazione nella fascia.

FORM: orario ferroviario / tabellone partenze e arrivi, prima della lista ordinata, seed 85561cb1.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
