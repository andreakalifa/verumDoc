# CHANGELOG — R01 — Pulizia, regole di sviluppo, audit funzionale e grafico (2026-10-20)

## Funzionalità
Nessuna funzionalità applicativa nuova: rilascio di fondamenta e documentazione.

## Documentazione
- `REGOLE_SVILUPPO.md` — nuove regole unificate: regole VerumDoc esistenti + regole trasferibili da un progetto precedente, adattate a FastAPI, Kafka, PostgreSQL, S3 WORM e React. Sostituisce `Regole_Sviluppo_VerumDoc.docx` e `Regole_Sviluppo_VerumDoc_v4_2.docx`.
- `docs/audit/AUDIT_FUNZIONALE_2026-10-20.md` + 4 allegati — stato reale (~15–20%), dichiarato contro reale, gap normativo, mappa del servizio completo, piano in fasi, decisioni.
- `docs/design/AUDIT_GRAFICO_2026-10-20.md` + 2 allegati + 12 screenshot — Nielsen 12/40, audit tecnico 6/20, tre direzioni di personalità da scegliere.
- `docs/registro/criticita.md` (40 voci) e `docs/registro/decisioni.md`.
- `STATO_PROGETTO.md` riscritto con lo stato verificato.
- `README.md` — sezioni di stato, conformità, sicurezza, documentazione e produzione rese veritiere; generazione delle chiavi nell'avvio rapido.

## Conformità normativa
- Mappa requisito per requisito rispetto a Linee guida AgID, Regolamento conservatori (Det. 455/2021, Allegati A e B), D.Lgs. 42/2004 art. 21, DM 17/6/2014, ETSI TS 119 511/512 (audit funzionale §5).

## Correzioni
- Rimossa dal repository la chiave privata RSA di sviluppo (VD-C-004, mitigata): nuovo generatore locale `tools/dev/genera_chiavi_dev.{sh,ps1}`, file escluso da `.gitignore`. **La vecchia chiave è da considerare compromessa.**

## Dismissioni — File eliminati (con `tools/pulizia/elimina_R01_pulizia.{sh,ps1}`)
| Percorso | Motivo | Sostituito da |
|---|---|---|
| `services/{dashboard-api…` (albero) | Cartelle vuote da un `mkdir` con brace expansion non espansa | — |
| `services/*/verumdoc_*.egg-info` (9) | Artefatti di build | — |
| `__pycache__`, `.pytest_cache` (tutte) | Artefatti | — |
| `services/gateway/app/services/ingestion.py` | Mai importato, incompatibile col modello | `routers/versamento.py` |
| `services/gateway/app/services/kafka_producer.py` | Mai importato, import rotto | `libs/shared/kafka` |
| `services/gateway/app/models/requests.py` + `tests/test_models.py` | IndiceUD v1 duplicato e il suo test | `models/versamento.py` (IndiceUD in uso) |
| `libs/shared/kafka/producer.py` | Producer duplicato mai inizializzato | `libs/shared/kafka/__init__.py` |
| `libs/shared/db/tests/` | Importa una funzione inesistente e blocca la suite | — |
| `libs/shared/crypto/`, `libs/shared/s3/` | Stub vuoti | `libs/verumdoc_rules` e servizio signature (da realizzare) |
| `services/aip-builder/app/services/tiered_storage.py` + test | Mai invocato, dipendenza mancante | Da riprogettare (politiche di storage) |
| 22 package vuoti `app/{auth,middleware,routers,models}` | Mai importati | — |
| `infra/k8s/base/gateway-deployment.yaml` | Duplicato non incluso in kustomization | `infra/k8s/base/deployments/gateway-deployment.yaml` |
| `infra/terraform/environments/dev/main.tf` | Solo commenti | — |
| `Regole_Sviluppo_VerumDoc.docx`, `Regole_Sviluppo_VerumDoc_v4_2.docx` | Superati | `REGOLE_SVILUPPO.md` |
| `infra/dev_jwt.env` | Segreto nel repository | Generatore locale |

**Spostato:** `Piano_Implementazione_Tecnico_Completo_v5.docx` → `docs/storico/` (superato dall'audit).

**Non eliminati di proposito** (servono correzioni, non cancellazioni): `infra/postgres/init.sql` (referenziato da docker-compose, R03), script dei topic Kafka (VD-C-035, R04), `aggregator.py` (test misti, VD-C-040), `libs/policy-engine` (target del Makefile, sostituzione in R05), `lifecycle_manager.py` (VD-C-036), `libs/shared/observability` e `connectors` (test esistenti in `tests/test_fase4`).

## Strumenti
- `tools/pulizia/elimina_R01_pulizia.{sh,ps1}` — modalità prova predefinita, `--applica`/`-Applica` per eseguire; idempotente; `git rm` per i file tracciati.
- `tools/dev/genera_chiavi_dev.{sh,ps1}` — chiavi RS256 locali.
- `tools/design/foto_visive/` — foto Playwright del frontend con API simulate (stati normale, caricamento, errore, vuoto, dati peggiori).

## Test eseguiti
- Script di pulizia su una copia Git del repository: 48 voci fisse + 46 cartelle `__pycache__` della copia (il numero degli artefatti dipende dalla macchina); seconda esecuzione: 0 da eliminare. Provato anche fuori da un repository Git.
- Dopo la pulizia, raccolta dei test senza errori: gateway 121, aip-builder 88, validation 80.
- Chiave generata: firma e verifica RS256 riuscite con `python-jose`.
- Stato delle suite esistenti (non modificate): vedi `STATO_PROGETTO.md` §4.

## Decisioni realizzate
- VD-P-09 (regole unificate), VD-DR-00 (dismissione del sistema grafico attuale).

## Scelte di sviluppo da confermare
- Il Piano v5 è conservato in `docs/storico/` invece di essere eliminato.
- Le criticità hanno un rilascio previsto indicativo secondo il piano dell'audit funzionale §7.

## Limiti noti
- La vecchia chiave privata resta nella cronologia Git finché non si riscrive la cronologia.
- Il testo definitivo delle ultime sezioni del cap. 4 delle Linee guida non è stato letto per intero (vedi `allegati/ricerca_normativa.md` §9): i riferimenti puntuali vanno ricontrollati.

## Regole
- Regole: prima stesura unificata (storico §31).
