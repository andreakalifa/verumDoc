<!-- Leggi CONTRIBUTING.md. Per una vulnerabilità non aprire una pull request pubblica: segui SECURITY.md. -->

## Cosa cambia e perché

<!-- Descrizione breve; issue collegata (es. «Chiude #123»); criticità VD-C-… o decisione VD-P-… toccate. -->

## Tipo di modifica

- [ ] Correzione di un errore
- [ ] Funzione nuova
- [ ] Cambiamento di comportamento (decisione registrata in `docs/registro/decisioni.md`)
- [ ] Migrazione del database (solo in avanti, `services/gateway/alembic/versions/`)
- [ ] Documentazione

## Controlli

- [ ] Test nuovi o aggiornati che eseguono il codice; numeri della corsa qui sotto
- [ ] `make verifiche` verde (compreso `tools/opensource/controlla_pubblicazione.py`)
- [ ] `ruff check .` senza errori; frontend: `npm run typecheck`, `npm run lint`, `npm run build` (ed e2e se toccata la console)
- [ ] Intestazione SPDX nei file sorgente nuovi
- [ ] Cataloghi e documenti generati rigenerati, se toccati
- [ ] Documentazione allineata (REGOLE_SVILUPPO §0.2) e changelog del rilascio aggiornato
- [ ] Dipendenze nuove motivate, con licenza compatibile annotata in `THIRD_PARTY_NOTICES.md`
- [ ] Nessun segreto, dato personale reale o documento riservato di terzi
- [ ] Commit firmati DCO (`git commit -s`)

## Test eseguiti

```
<!-- comandi e risultati copiati dall'esecuzione -->
```
