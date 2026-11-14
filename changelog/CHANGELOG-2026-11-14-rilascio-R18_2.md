# CHANGELOG — R18.2 — Rimozione degli adattatori di compatibilità (14/11/2026)

> Base: R18 (`537c568`). Criticità **VD-C-187** chiusa; decisione **VD-P-191** «Niente adattatori di compatibilità con interfacce di terzi senza specifica pubblica verificata», confermata dal committente il 14/11/2026. Motivo: il committente non può confermare che le specifiche usate per gli adattatori «stile ParER» e «stile Packer» (R03) fossero pubbliche; un repository pubblico non deve riprodurre interfacce, nomi di campi, schemi o esempi di documentazione forse riservata. Gli adattatori sono quindi tolti del tutto, non solo nascosti.

## Cosa cambia per chi integra

- Le rotte `/api/compat/...` del gateway non esistono più (`404`). I canali restano REST multipart, sessioni di caricamento con versamento per riferimento, SFTP e PDND (`docs/integrazione/README.md`, nuovo §12). I PdV già versati da quei canali restano conservati e consultabili come gli altri.
- Codici d'errore `CMP-*` **dismessi** (restano nel catalogo, un codice pubblicato non si riusa): titolo «Dismesso: adattatori di compatibilità tolti in R18.2», descrizione e azione neutre; `docs/catalogo/errori.md` e `libs/shared/errori/_catalogo_dati.py` rigenerati da `tools/docs/genera_catalogo_errori.py`.
- Impostazioni del gateway tolte: i due limiti degli adattatori e `NOTIFICATION_INTERNAL_URL` (usata solo dalle loro callback). Il gateway non chiama più notification: tolti il flusso di rete gateway → notification (Helm `egress.services`, `callers`) e le rotte dell'ingress `/api/compat`; manifest Kubernetes rigenerati da `infra/k8s/genera_manifest.py` (ConfigMap, Ingress, NetworkPolicy, overlay staging).

## Codice

- **Tolti**: `services/gateway/app/compat/` (pacchetto degli adattatori), `services/gateway/app/routers/compat_*.py`, i loro test `services/gateway/tests/test_compat_*.py` e le guide `docs/integrazione/compat_*.md`; registrazione delle rotte in `main.py`; valori di canale degli adattatori nell'enum `Canale`; modello ORM della tabella delle richieste (`libs/shared/models`); dipendenza `defusedxml` del gateway (nessun altro uso nel servizio; resta in ciclo-vita e notification, `THIRD_PARTY_NOTICES.md` aggiornato).
- **Spostato**: `app/compat/corpo.py` → `services/gateway/app/services/corpo.py` (lettura limitata del corpo, JSON con file base64 in streaming, budget del disco temporaneo: usati da REST e PDND). Tolte le funzioni base64 usate solo dagli adattatori (`valida_base64`, `dimensione_base64`, `blocchi_base64`) e il loro test in `test_pdnd.py`; `caricamenti.apri` non accetta più `dimensione_parte` (la passavano solo gli adattatori).
- **Test adattati**: `test_f1_correzioni_canali.py`, `test_f3_correzioni_revisione.py`, `test_r03_quote_canali.py` (tolte le prove sui soli adattatori, restano REST, sessioni, SFTP e PDND; la prova del tetto dei multipart contemporanei resta sul REST), `test_b4_accesso_canali_quote.py`, `test_caricamenti.py` (canali di confronto PDND e CICLO_VITA), `test_r05_rls_ente.py`, testa delle migrazioni in `test_r04_unione_migrazioni.py`, `test_r04_correzioni_registri_ruoli.py`, `test_r05_unione_migrazioni.py`, `test_r05_revisione_migrazioni_lock.py`, `test_r05_alembic_senza_pythonpath.py`; **nuovo** `test_r18_2_migrazione_060.py` (tabella vuota eliminata; con righe l'upgrade si ferma e la tabella resta; con la conferma eliminata; downgrade con la politica per ente); `infra/k8s/verifica_manifest.py` (limiti del corpo) e il suo test; `tools/docs/genera_openapi.py` (`INOLTRI` vuoto, il meccanismo resta con i suoi test) e il suo test.
- **Documentazione ripulita**: `README.md`, `publiccode.yml`, `GUIDA_TEST_COMPLETA.md`, `METADATI_AGID_VERSAMENTO.md`, `STATO_PROGETTO.md`, `docs/integrazione/README.md`, `docs/architettura/PIANO_R03_R05.md`, `docs/architettura/sicurezza.md`, `sdk/README.md`, `changelog/CHANGELOG-2026-10-21-rilascio-R03.md` (descrizioni degli adattatori rese neutre, con il rinvio a questa rimozione), commenti di `services/ciclo-vita/app/domain/indice_pdd.py`, `libs/shared/security`, rotte e servizi del gateway. Rigenerati con i loro strumenti: `docs/catalogo/openapi.yaml`, `endpoint.md`, `metriche.md`, `docs/manuale/manuale_conservazione.md`, `docs/manuale/piano_sicurezza.md`, `libs/conformita/dati/fonti_repository.json`.
- Frontend e SDK: nessun riferimento nel codice (solo la tabella degli errori in `sdk/README.md`).

## Database

- **Migrazione nuova `20261114_060_rimozione_adattatori`** (testa): elimina la tabella `public.compat_packer_richieste` (con la sua politica per ente). È necessaria perché, tolto il modello ORM, `alembic check` (schema identico ai modelli, `tests/test_schema_alembic.py`) segnalerebbe la tabella e i nomi delle sue colonne riprendono l'interfaccia tolta. **Protezione dei dati**: se la tabella ha righe l'upgrade si ferma con un messaggio, a meno di `VD_MIGRAZIONE_060_ELIMINA_RICHIESTE=1` (esportarle prima se servono; PdV, documenti e RdV collegati restano). Funziona anche in modalità offline (`--sql`). Downgrade: tabella ricreata vuota con colonne, vincoli e politica di prima.
- **Migrazioni storiche invariate** (il database di produzione le ha applicate): `20261021_015_sessioni_caricamento`, `20261021_021_correzioni_canali`, `20261021_023_unione_correzioni_r03`, `20261023_038_rls_per_ente` creano, modificano o elencano quella tabella; nessuna modifica, nemmeno ai commenti (le impronte delle migrazioni non sono controllate, ma toccarle non serve).
- **Valori di canale**: nessun vincolo `CHECK` né tipo enum del database elenca i canali (il canale è nel `metadata_json` del PdV): i PdV già versati dagli adattatori mantengono il loro valore come dato storico, nessuna migrazione per questo.

## File eliminati

| File | Sostituito da |
|---|---|
| `services/gateway/app/compat/__init__.py`, `packer.py`, `parer.py` | nessuno (adattatori tolti) |
| `services/gateway/app/compat/corpo.py` | `services/gateway/app/services/corpo.py` |
| `services/gateway/app/routers/compat_packer.py`, `compat_parer.py` | nessuno |
| `services/gateway/tests/test_compat_packer.py`, `test_compat_parer.py` | nessuno |
| `docs/integrazione/compat_packer.md`, `compat_parer.md` | `docs/integrazione/README.md` §12 |

Dieci file. Tolti anche dallo script di pulizia (`tools/pulizia/pulisci_progetto.py`: la cartella `services/gateway/app/compat` e i sei file singoli, con test).

## Test eseguiti

R18.2 (14/11/2026; PostgreSQL 16 reale, `r182_fin` NUOVO migrato da zero a `20261114_060`, login non proprietario dei servizi): gateway **786 passati, 0 falliti** (R18: 839; tolti i test dei soli adattatori, aggiunto `test_r18_2_migrazione_060.py`); librerie e strumenti toccati (libs/shared, libs/conformita, tools/docs, tools/pulizia, tools/opensource, tests/integrity) **591 passati**, 1 saltato; sdk 93; infra/k8s 107 (+1 saltato); `make verifiche` 0 (compreso `controlla_pubblicazione.py`: nessun problema); `ruff check .` 0 (ruff 0.17.1). Frontend non toccato (nessun riferimento). Gli altri servizi non cambiano codice (ciclo-vita: solo un commento) e non sono stati rieseguiti.

## Decisioni

- **VD-P-191** (confermata il 14/11/2026). Annotata VD-P-29 (piano R03–R05: adattatori tolti in R18.2).
- **VD-C-187** chiusa: adattatori rimossi in R18.2 perché la provenienza delle specifiche non è confermabile.

## Conformità normativa

Nessun requisito delle Linee guida AgID dipendeva dagli adattatori: il versamento resta disponibile da REST, SFTP e PDND con le stesse regole, idempotenza e quote. Il manuale di conservazione generato non elenca più gli adattatori fra i canali.
