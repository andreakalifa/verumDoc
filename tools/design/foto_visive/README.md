# Foto visive del frontend

Strumento usato per l'audit grafico del 20/10/2026 (`docs/design/AUDIT_GRAFICO_2026-10-20.md`).
Fotografa tutte le route a 1440×900 e 390×844 con le API simulate, negli stati normale, caricamento,
errore, vuoto e dati peggiori, più i flussi principali (versamento ok/ko, esibizione, verifica, focus).

- `mocks.js` — risposte simulate realistiche per ogni endpoint chiamato dal frontend; dalla R05
  anche la sessione (`GET /api/v1/auth/sessione`, `instradaSessione`): il portale non legge più
  `localStorage`, la sessione è un cookie HttpOnly
- `shoot.js` — foto (si aspetta il frontend servito su http://localhost:4173 con `npx vite preview`)
- `contrast.js` — rapporti di contrasto WCAG dei colori del tema
- `overflow.js` — misura dello scorrimento orizzontale sul telefono

Uso:
```bash
cd frontend && npm run build && npx vite preview --port 4173 &
cd tools/design/foto_visive && npm install && node shoot.js
```
Le foto si scrivono nella cartella dello strumento (non committarle). I font del tema attuale sono serviti
da @fontsource perché lo strumento non dipende da Google Fonts; quando il nuovo sistema grafico userà
font locali, aggiornare `ctxFor()` in `shoot.js`.
