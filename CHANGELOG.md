# Changelog

Sintesi pubblica dei rilasci di VerumDoc. Il dettaglio di ogni rilascio (funzionalità, correzioni, test eseguiti, decisioni, file eliminati) è in [`changelog/`](changelog/); lo stato per area in [`STATO_PROGETTO.md`](STATO_PROGETTO.md). Le date sono quelle di consegna.

Il progetto non ha ancora una versione stabile: i rilasci si identificano con il numero progressivo `Rnn`.

## R18.2 — Rimozione degli adattatori di compatibilità (14/11/2026)
- Tolti gli adattatori di compatibilità del gateway (rotte `/api/compat/...`): VerumDoc espone solo interfacce proprie (REST, sessioni di caricamento, SFTP, PDND). Decisione VD-P-191.
- Codici d'errore `CMP-*` dismessi; migrazione `20261114_060` che elimina la tabella delle richieste degli adattatori (si ferma se contiene righe, salvo conferma esplicita).

## R18 — Apertura open source (14/11/2026)
- Licenza **EUPL-1.2** (`LICENSE`), avvisi e politica del marchio (`NOTICE`), intestazioni SPDX in tutti i sorgenti, campo licenza in `pyproject.toml`, `package.json` e `pom.xml`.
- Elenco dei componenti di terze parti con licenze e compatibilità (`THIRD_PARTY_NOTICES.md`).
- File per la comunità: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `GOVERNANCE.md`, `MAINTAINERS`, modelli di issue e pull request, `publiccode.yml`.
- Controllo automatico prima della pubblicazione (`tools/opensource/controlla_pubblicazione.py`, in `make verifiche`) e istruzioni per pubblicare con una storia git nuova (`docs/sviluppo/PUBBLICAZIONE_OPEN_SOURCE.md`).
- Pulizia del repository dal materiale di lavoro interno. Nessuna funzionalità cambiata.

## R15–R17 — Documentazione di conformità, residui interni, collaudo finale (01/11/2026)
- Manuale di conservazione, piano della sicurezza e guida generati dalla configurazione reale.
- Firma personale con codice a tempo del responsabile; federazione OpenID; conferma firmata delle decisioni irreversibili del ciclo di vita.
- Revisione di sicurezza e audit della console, con correzioni.

## R14 — Debito interno (31/10/2026)
- Cataloghi generati e controllati, pratiche sospese con ripresa dalla console, vincoli di concorrenza nel database.

## R13a–R13b — Firma qualificata e canali esterni predisposti (31/10/2026)
- Adattatori di firma e marca qualificate configurabili (CSC, PKCS#11, RFC 3161), PEC con verifica delle ricevute firmate, accesso SPID/CIE con OpenID Connect.

## R12a–R12c — Criticità, credenziali, revisione grafica (31/10/2026)
- Preparazione dello staging, credenziali degli utenti con conferma dell'identità, posta isolata, revisione grafica della console.

## R11 — Conformità dei pacchetti (31/10/2026)
- Profili dei metadati dell'Allegato 5, validatore dell'IPdA predisposto per lo schema UNI 11386.

## R10 — Accesso, ruoli, notifiche (30/10/2026)
- Password con codice a tempo e passkey, ruoli personalizzati, inviti, notifiche.

## R09 — Ciclo di vita automatico (30/10/2026)
- Lavori periodici del ciclo di vita, scadenze e verifiche continue.

## R08 — Firme e formati (30/10/2026)
- Riconoscimento dei formati (PRONOM), verifica delle firme, catalogo dei formati.

## R07 — Parametri in console (30/10/2026)
- Parametri del servizio configurabili dalla console con storico.

## R06 — Sessione, credenziali, pulizia (30/10/2026)
- Sessione in cookie HttpOnly con CSRF, pulizia del codice.

## R05 — Configurazione, ciclo di vita, sicurezza (24/10/2026)
- Row Level Security per ente, ciclo di vita (scarto, rettifiche), storage WORM di sviluppo con Object Lock.

## R04 — Scala e catena (23/10/2026)
- Catena delle impronte RFC 6962 con testimone indipendente e verificatore pubblico, obiettivo di un milione di documenti al giorno.

## R03 — Canali e contratto delle API (21/10/2026)
- Canali SFTP e PDND, sessioni di caricamento, adattatori di compatibilità (tolti in R18.2), catalogo degli errori e OpenAPI.

## R02 — Sistema grafico e criticità del registro (20–21/10/2026)
- Chiusura delle criticità gravi dell'audit funzionale, sistema grafico «Partenze».

## R01 — Pulizia, regole, audit (20/10/2026)
- Regole di sviluppo unificate, audit funzionale e grafico.
