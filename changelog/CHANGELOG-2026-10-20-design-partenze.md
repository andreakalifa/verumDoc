> **Assorbito da `changelog/CHANGELOG-2026-10-21-rilascio-R02.md`** (rilascio unico R02). Questo file resta come storico del primo passo grafico; lo stato descritto sotto («Le altre 16 pagine non sono ridisegnate», «`tsconfig` manca») è superato.

# CHANGELOG — R02 — Nuovo sistema grafico «Partenze» (2026-10-20)

Decisione: VD-DR-01 (`docs/registro/decisioni.md`). Metodo: impeccable (PRODUCT.md, sorteggio della direzione, contratto di direzione, costruzione, revisione indipendente, documentazione).

## Nuovo
- Sistema grafico «Partenze» (orario ferroviario): testata grafite, giallo segnaletico, Archivo a larghezza variabile servito dal progetto (`@fontsource-variable/archivo`), tabelloni con filetti neri, stati come parole colorate.
- `frontend/src/pages/OverviewPage.tsx` — «Quadro del servizio»: fascia di stato a cinque esiti onesti (integro, compromesso, mai verificato, non disponibile, in lettura), tabellone Partenze (scadenze), tabellone Arrivi (ultimi versamenti, aggiornati ogni 30 s), situazione dell'archivio dell'ente.
- `frontend/src/pages/VersamentoLivePage.tsx` — «Versa un documento»: modulo in tre tappe con etichette vere, errori per campo, trascinamento del file; tabellone «Corsa del pacchetto» con l'ora di ogni fase.
- `frontend/src/pages/DaRealizzarePage.tsx` — pagina che dichiara le funzioni non ancora realizzate (rinnovo marche, scarto) invece di simularle.
- `frontend/src/data/scadenzeDimostrative.ts` — scadenze dimostrative, etichettate come tali in pagina.
- `frontend/DESIGN.md`, `frontend/.impeccable/design.json` — il sistema documentato; `frontend/PRODUCT.md`; `frontend/.impeccable/surfaces/` — contratto di direzione.
- `tools/design/foto_visive/partenze.js` — foto delle due pagine e dei loro stati.

## Modificato
- `frontend/src/components/Layout.tsx` — la barra laterale diventa testata con aree (Servizio, Versamento, Archivio, Integrità, Esibizioni, Conformità, Enti) e seconda riga di pagine; filtri per ruolo e scope invariati; link «Vai al contenuto»; ricerca nella testata.
- `frontend/src/index.css` — token del nuovo sistema; nessun font da Google; le vecchie variabili restano come ponte chiaro per le pagine non ancora ridisegnate.
- `frontend/src/pages/SearchPage.tsx` — accetta `?q=` dalla ricerca in testata.
- `frontend/src/App.tsx` — rotta `/da-realizzare/:funzione`.
- `frontend/package.json`, `package-lock.json` — dipendenza `@fontsource-variable/archivo`.

## Corretto
- Versamento: un rifiuto con messaggio che non conteneva la parola «Errore» veniva ignorato e l'interrogazione continuava fino al timeout.
- Accessibilità: anello di fuoco a 3:1 su ogni fondo; campi con `<label>` associate; contrasti del testo tutti ≥ 4,5:1.

## Verifiche
- `vite build` riuscita; controllo dei tipi sui file modificati senza errori (il `tsconfig` del progetto manca ancora: criticità nota).
- Rilevatore impeccable: nessun rilievo.
- Revisione indipendente: 8 correzioni richieste, tutte risolte in due giri; esito **ship**.

## Non incluso
- Le altre 16 pagine non sono ridisegnate: ricevono solo i colori del ponte (leggibili, non ancora nello stile).
- Restano idee del revisore non obbligatorie: riga «adesso» nel tabellone, conteggio degli arrivi del giorno, animazione all'arrivo di un versamento.
