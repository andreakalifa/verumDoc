# CHANGELOG — R18 — Apertura open source (14/11/2026)

> Base: R15–R17 (`5dbebf3`). Decisione VD-P-190 «Licenza EUPL-1.2 e storia nuova», confermata dal committente il 14/11/2026. **Nessuna funzionalità cambiata**: cambiano licenza, intestazioni dei sorgenti, documentazione e strumenti di controllo. La pubblicazione la fa il committente seguendo `docs/sviluppo/PUBBLICAZIONE_OPEN_SOURCE.md`.

## Funzionalità

- **Licenza EUPL-1.2**: `LICENSE` con il testo ufficiale integrale in inglese (dall'elenco SPDX, `license-list-data`, identico al testo della Commissione); `NOTICE` con copyright, licenza e **politica del marchio** (nome e logo «VerumDoc» non concessi in licenza, EUPL art. 5); campo licenza in tutti i `pyproject.toml` dei servizi e dell'SDK (`license = { text = "EUPL-1.2" }` e classificatore), in `frontend/package.json` (e nel lock), `tools/design/foto_visive/package.json`, `services/verifica-firme/pom.xml` (`<licenses>`). Non c'è un `go.mod`.
- **Intestazioni SPDX** (`SPDX-FileCopyrightText: 2026 Contributori di VerumDoc`, `SPDX-License-Identifier: EUPL-1.2`) in **1.320 sorgenti** tracciati (Python, TypeScript, JavaScript, Java, SQL, shell, PowerShell, Terraform, Dockerfile, YAML di infra e CI, template Mako delle migrazioni) più i generati: i generatori `infra/k8s/genera_manifest.py` e `tools/docs/genera_catalogo_errori.py` scrivono l'intestazione e i loro file sono rigenerati. Nei template Helm l'intestazione è un commento di template (`{{- /* … */ -}}`), quindi il rendering non cambia; dopo shebang, `# syntax=` dei Dockerfile, BOM e CRLF restano dove sono. Le migrazioni non sono confrontate con impronte (verificato): hanno l'intestazione in commento, senza cambiare il codice.
- **Componenti di terze parti**: `THIRD_PARTY.md` diventa `THIRD_PARTY_NOTICES.md` con l'elenco completo (chiusura transitiva Python dai metadati dei pacchetti: 79 di esercizio e 52 di sviluppo; npm dal lock: 73 nella console e 232 di sviluppo; Java dal `pom.xml`; binari, dati, font, vettori di prova di terzi, immagini dei container), licenze e compatibilità con la EUPL-1.2. **Nessuna dipendenza del codice incompatibile** né con licenza sconosciuta.
- **File per la comunità**: `README.md` riscritto in testa (cos'è, stato, contribuire, licenza, sezione in inglese), `CONTRIBUTING.md` (DCO consigliato, stile, test, revisione), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, testo ufficiale inglese), `SECURITY.md`, `GOVERNANCE.md`, `MAINTAINERS`, `CHANGELOG.md` pubblico sintetico, `publiccode.yml`, modelli di issue e pull request e `dependabot.yml` in `.github/`.
- **Controllo della pubblicazione** `tools/opensource/controlla_pubblicazione.py` (in `make verifiche`, test in `tools/opensource/tests`, in `make test-libs`): fallisce per sorgenti senza intestazione SPDX nelle cartelle di codice, file con nomi o tipi vietati (chiavi, `.env`, documenti d'ufficio, immagini: solo il nome, mai il contenuto), segreti evidenti (PEM con contenuto, JWT completi, chiavi di servizi cloud), riferimenti vietati (percorsi locali, caselle personali, cartelle di lavoro temporanee e termini del file locale ignorato `tools/opensource/pubblicazione.locale.toml`; il testo trovato non si stampa), file tracciati che `.gitignore` esclude, `LICENSE` assente. `--applica-intestazioni` aggiunge le intestazioni mancanti. Configurazione pubblica in `tools/opensource/pubblicazione.toml`.
- **Istruzioni di pubblicazione con storia nuova**: `docs/sviluppo/PUBBLICAZIONE_OPEN_SOURCE.md` (PowerShell): rotazione delle chiavi di sviluppo, ramo orfano `pubblico`, controlli dell'indice, commit unico «Prima pubblicazione open source» firmato DCO, repository GitHub vuoto, push del solo ramo come `main`, protezione del ramo, segreti di CI in un Environment, secret scanning con push protection, Dependabot, segnalazione privata delle vulnerabilità; la chiave JWT compromessa non va mai riusata.

## Pulizia per la pubblicazione

- **Rimossi** (materiale di lavoro interno): `docs/audit/allegati/audit_A_versamento_validazione.md`, `audit_B_conservazione.md`, `audit_C_servizi_infrastruttura.md` (percorsi della macchina di lavoro, riferimenti alle sessioni), `docs/design/allegati/design_A_review.md`, `design_B_detector.md`, `docs/storico/Piano_Implementazione_Tecnico_Completo_v5.docx`.
- **Ripuliti**: `docs/audit/allegati/ricerca_normativa.md` (tolta la panoramica dei servizi di altri conservatori, le citazioni dei loro manuali e le relative fonti; resta la parte normativa, usata dalla documentazione generata), `docs/architettura/PIANO_R03_R05.md` (tolti il confronto con altri conservatori e la fonte relativa), `docs/registro/decisioni.md` e `docs/audit/AUDIT_FUNZIONALE_2026-10-20.md` (VD-P-05 senza nomi di fornitori), `docs/registro/criticita.md` (titolo della sezione 091–100), `docs/design/AUDIT_GRAFICO_2026-10-20.md` (strumenti di lavoro e allegati), `REGOLE_SVILUPPO.md` (§2 «Regole per chi sviluppa» al posto delle istruzioni per l'assistente e del prompt d'inizio sessione; riferimento a un progetto precedente reso generico), `GUIDA_TEST_COMPLETA.md` (percorso locale di Windows), `STATO_PROGETTO.md` e changelog R11, R12b, R13a, due test del gateway (cartella di lavoro temporanea nei percorsi), `changelog/CHANGELOG-2026-10-20-pulizia-regole-audit.md`, `tools/design/foto_visive/mocks.js` (nome di una persona reale nei dati finti).
- **Segreti**: scansione dell'albero tracciato con detect-secrets 1.5.0 e con il controllo nuovo: **nessuna chiave reale**. Sono valori di sviluppo dichiarati (`*_dev_only` in `docker-compose.yml`, `infra/pgbouncer/userlist.dev.txt`), segnaposto della CI, impronte SHA-256 dei binari, vettori di prova pubblici (Go `sumdb/note`, RFC 6962, RFC 7638) e chiavi generate durante i test. Nessun file `infra/dev_*.env` è tracciato (non letto).
- `.gitignore` completato (file locale dei termini riservati, altri formati di chiavi, `node_modules/`, ambienti e risultati locali).

## File eliminati

| File | Sostituito da |
|---|---|
| `THIRD_PARTY.md` | `THIRD_PARTY_NOTICES.md` |
| `docs/audit/allegati/audit_A_versamento_validazione.md`, `audit_B_conservazione.md`, `audit_C_servizi_infrastruttura.md` | sintesi in `docs/audit/AUDIT_FUNZIONALE_2026-10-20.md` e registro delle criticità |
| `docs/design/allegati/design_A_review.md`, `design_B_detector.md` | sintesi in `docs/design/AUDIT_GRAFICO_2026-10-20.md` |
| `docs/storico/Piano_Implementazione_Tecnico_Completo_v5.docx` | superato dall'audit e da `STATO_PROGETTO.md` |

Tolti anche dallo script di pulizia (`tools/pulizia/pulisci_progetto.py`, con test).

## Conformità normativa

Nessun cambiamento di comportamento. I documenti generati (manuale, piano della sicurezza, guida) cambiano solo nelle impronte delle fonti e nel rilascio citato (`libs/conformita/dati/fonti_repository.json` rigenerata).

## Test eseguiti

Corsa completa dopo tutte le modifiche (tutti i sorgenti hanno cambiato byte per le intestazioni): R18 (14/11/2026, dopo le intestazioni SPDX in 1.320 sorgenti; PostgreSQL 16 reale, `r18_integ` NUOVO migrato da zero a `20261112_059`, login non proprietario dei servizi, Siegfried reale, oracolo Go, versitygw): servizi gateway 839, validation 175, aip-builder 196 (+3 saltati XSD), hashchain 371, notification 337, distribution 165, dashboard-api 274, signature 167, sftp-ingest 154, ciclo-vita 276, onboarding 305, testimone 61; librerie shared 499, rules 261, catena 105, sincro 18, conformita 21; strumenti docs 30, dev 21, verificatore 62, validatore 7, sincro 4, profili 2, carico 6, pulizia 4, manutenzione 7, **opensource 27 (nuovi)**; infra kafka 5, tests 13, k8s 108; sdk 93; integrity 11: **4.624 test Python passati, 0 falliti**; frontend typecheck, lint, build, e2e **152** con 2 lavoratori; `make verifiche` 0 (compreso `controlla_pubblicazione.py`: nessun problema); `ruff check .` 0; rendering di Helm identico a `5dbebf3` per default, staging, prod e testimone.

## Decisioni realizzate

- **VD-P-190** «Licenza EUPL-1.2 e storia nuova» (confermata dal committente il 14/11/2026).

## Scelte di sviluppo da confermare

- **SDK con EUPL-1.2** (prima dichiarato MIT, mai pubblicato): allineato al resto del progetto; un SDK permissivo (MIT o Apache-2.0) faciliterebbe l'integrazione nei gestionali proprietari: da decidere prima della pubblicazione su PyPI.
- **Titolare del copyright** «Contributori di VerumDoc», senza nome di persona o società.
- **Codice di comportamento** in inglese (testo ufficiale), con il collegamento alla traduzione italiana non ufficiale.

## Limiti noti

- `LICENSE.it` (versione italiana ufficiale della EUPL-1.2) **non inclusa**: i siti della Commissione (Joinup, EUR-Lex) non erano raggiungibili dall'ambiente di lavoro; non è stata scritta a memoria. Da scaricare dalla Gazzetta ufficiale dell'UE (Decisione (UE) 2017/863) se la si vuole aggiungere.
- Dipendenze Java transitive non elencate con lo strumento (VD-C-189), immagini di sviluppo con licenze da rivedere (VD-C-188), provenienza delle interfacce degli adattatori di compatibilità da confermare (VD-C-187).
- `publiccode.yml` non validato con il validatore ufficiale (non installabile nell'ambiente); `url` e contatti sono «DA COMPILARE».
