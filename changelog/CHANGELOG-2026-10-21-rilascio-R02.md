# CHANGELOG — R02 — Sistema grafico «Partenze» e correzione delle criticità del registro (20-21/10/2026)

Rilascio unico. Prerequisito: R01 applicato. Assorbe `changelog/CHANGELOG-2026-10-20-design-partenze.md` (primo passo grafico: testata, home, versamento), che resta come storico.
Contenuto: nuovo sistema grafico su **tutto** il frontend (decisione VD-DR-01) e correzione delle criticità dell'audit del 20/10/2026, più i difetti trovati durante il lavoro e dalla revisione indipendente di sicurezza.
Esito sul registro (`docs/registro/criticita.md`): delle 40 criticità dell'audit 19 chiuse, 17 chiuse con riserve, 3 parzialmente chiuse, 1 aperta; 29 criticità nuove trovate e chiuse (041–067, 089–090); 21 limiti aperti dichiarati (068–088).
Fonti: rapporti degli agenti (be-*, fe-*, g2-*, g3-*, g4-*, g5-sicurezza, g6-ultime) e revisione indipendente di sicurezza. Ogni affermazione importante è stata riscontrata sul codice e con `make test`.

---

## CAMBI INCOMPATIBILI (leggere prima di applicare)

| # | Cambio | Chi è toccato | Cosa fare |
|---|---|---|---|
| 1 | **Modalità di formazione solo `a`, `b`, `c`, `d`** (LG §2.1.1, VD-N-02). I vecchi valori `INFORMATICA_AB_ORIGINE`, `SCANSIONE`, … ricevono **422**; un IPdA senza modalità valida è un errore `AIP-METADATI-001` (DLQ, PdV in ERRORE), mai un valore predefinito | Sistemi versanti, SDK, integrazioni | Inviare la lettera. Corrispondenza indicativa in `METADATI_AGID_VERSAMENTO.md` |
| 2 | **Chiavi API vecchie disattivate** (migrazione 012): non avevano mai funzionato. Formato nuovo `vd_<id>_<segreto>`, intestazione `X-Api-Key` oppure `X-Api-Key-Id` + `X-Api-Key-Secret` | Ogni sistema versante | Generare una chiave nuova da «Utenti e accessi» e consegnarla al versatore |
| 3 | **Token con `aud` e `iss` obbligatori** in tutti i servizi; chiavi JWT obbligatorie anche in development | Tutti gli utenti e i servizi | Al rilascio **tutti sono disconnessi** e rifanno l'accesso |
| 4 | **Accesso solo con SPID/CIE** (o dev-token in sviluppo locale). Serve il codice fiscale sull'utente; il livello minimo è L2 letto dall'`acr` dell'id_token | Utenti del portale | Inserire il CF degli utenti esistenti prima del rilascio; aggregatore qualificato configurato |
| 5 | **Errori SPID via redirect**: SPID-002/010/011 (e gli altri) tornano come 303 a `BASE_URL/login#spid_errore=<codice>`, non più JSON 400/422; `BASE_URL` del gateway è l'host del **frontend** | Aggregatore, configurazione | Registrare la callback `BASE_URL/api/v1/auth/spid/callback` |
| 6 | **Readiness vera** negli 8 servizi + gateway: 503 se DB/Kafka/S3 bloccanti non rispondono; probe di signature su `/api/v1/health/ready` | Kubernetes, monitoraggio | Atteso: un pod con una dipendenza giù esce dal Service |
| 7 | **Verifica completa della catena asincrona**: `POST …/verifica-completa` → **202** `{verifica_id, stato, avviata_il}`, poi `GET …/verifica-completa/{verifica_id}`; 409 con l'id della verifica in corso. Avvio e stato solo SUPER_ADMIN e RSC | Client della dashboard, script | Fare polling sullo stato |
| 8 | **Endpoint eliminati**: `POST /api/dashboard/auth/api-key` (le chiavi si usano direttamente sul gateway) e `GET /api/v1/hashchain/integrity` (sostituito dalla verifica completa) | Script e guide | `GUIDA_TEST_COMPLETA.md` aggiornata |
| 9 | **`richiedente_id` ignorato** nella richiesta di esibizione: il richiedente è l'utente del token; `hash_sha256` e `dimensione_bytes` dei documenti del PdD possono essere `null` | Client di distribution | Togliere il campo |
| 10 | **Ambiente predefinito `production`** in tutti i servizi: senza `ENV` valgono le regole di produzione (chiavi obbligatorie, nessun dev-token, CORS ristretto) | Sviluppo locale, CI, test | Impostare `ENV=development` (docker-compose lo fa) o `ENV=test` |
| 11 | **Firma e marca solo con token di servizio** (audience `verumdoc-signature`, scope `firma:servizio`): un token utente su `/firma` e `/marca` riceve 403; signature non parte se `JWT_AUDIENCE_SERVIZIO` = `JWT_AUDIENCE` | hashchain, chiunque chiamasse signature | **Emettere e ruotare `SIGNATURE_SERVICE_TOKEN`** di hashchain |
| 12 | **Isolamento dei contenuti**: anche SUPER_ADMIN e RSC ottengono PdD, esibizioni e ZIP completo solo del proprio ente (metadati di ogni ente con `?tenant_id=`) | Profili globali | VD-P-10 |
| 13 | **Revoca dei token**: utente disattivato, ente SOSPESO/DISATTIVATO, **ruolo cambiato** o utente spostato in un altro ente → 401 `AUTH-003` entro 30 s | Tutti | Dopo un cambio di ruolo l'utente deve rientrare |
| 14 | **`/api/dashboard/audit`** vietato a OPERATORE, VIEWER, API_SYSTEM; **`/distribution/search`** 503 se OpenSearch non risponde (non più elenco vuoto); **migrazione** 422 per id non validi e `/status/{job_id}` con token | Client | — |
| 15 | **Database di sviluppo da ricreare** (`docker compose down -v`): quelli creati con `create_all` non sono migrabili; il bucket MinIO dei PdA deve avere Object Lock | Sviluppatori | Vedi `README_RILASCIO.md` |
| 16 | **Variabili d'ambiente** nuove obbligatorie (`API_KEY_PEPPER`, `JWT_PUBLIC_KEY` di hashchain, `RETENTION_PERMANENTE_ORIZZONTE_ANNI`…), rinominate o rimosse | Esercizio | Tabella in fondo |
| 17 | **Topic Kafka**: eliminato `aip.building`; nuovi `pdv.received.dlq`, `pdv.validated.dlq`, `notification.dlq` | Broker | `make kafka-topics` |
| 18 | **PDND**: voucher con `exp`, `iat`, `aud`, `iss` obbligatori; con audience o emittente mancanti o segnaposto le rotte PDND rispondono **503 PDND-503** (il gateway parte comunque); `anno` obbligatorio nel versamento; tolto il campo fisso `conservatore`; fruitori da registrare in `public.pdnd_fruitori` | Enti via PDND | Registrare i fruitori |

---

## Nuovo

**Frontend — sistema «Partenze» su tutte le pagine (VD-DR-01)**
- Tutte le 20 pagine e la testata nel sistema grafico: grafite, giallo segnaletico, Archivo a larghezza variabile servito dal progetto, tabelloni con filetti, stati come parole colorate. Unico foglio `frontend/src/index.css` (token `--vd-*`, classi `vd-*`).
- Componenti condivisi: `api/errori.ts` (lettura unica degli errori, traduzione italiana degli errori di validazione), `components/EsitoCritico.tsx`, `components/SceltaEnte.tsx`, `components/Modulo.tsx`, `auth/ruoli.ts` (`NOME_RUOLO`), `data/integrita.ts` (regola unica della fascia d'integrità e della qualità della firma), `data/allegato5.ts`, `data/modalitaFormazione.ts`, `pages/DaRealizzarePage.tsx` (funzioni non realizzate dichiarate, non simulate).
- Accesso con SPID/CIE (`LoginPage.tsx`, `AuthContext.tsx`): scelta dell'ente, errori spiegati per codice (SPID-001…012, AUTH-003), profili di sviluppo solo in `npm run dev`.
- Moduli di registrazione di contratti e nomine (solo SUPER_ADMIN), scelta dell'ente per i profili globali, verifica completa della catena con avvio, polling ed esiti.
- `frontend/tsconfig.json` (strict), `eslint.config.mjs`, `public/favicon.svg`, `Dockerfile` e `nginx.conf` del frontend, `.dockerignore`; pagine caricate con `React.lazy` e pezzi separati (`grafici`, `base`).
- Documentazione del sistema: `frontend/DESIGN.md`, `frontend/.impeccable/design.json`, `frontend/PRODUCT.md`.

**Sicurezza e accesso**
- `libs/shared/security/jwt_rs256.py`: verifica unica RS256 (iss, aud, exp, iat, tenant e ruolo obbligatori), token di servizio (`firma_token_servizio`, `verifica_token_servizio`), emissione `python -m libs.shared.security.emetti_token_servizio`.
- `libs/shared/security/chiavi_api.py` (HMAC-SHA256 con pepper), `codice_fiscale.py` (normalizzazione e controllo con omocodia), `revoca.py` (revoca con cache).
- Gateway: accesso SPID/CIE reale (`/api/v1/auth/spid/login`, `/callback`, nuovo `POST /api/v1/auth/spid/scambio`), `app/auth/ruoli.py`.
- hashchain: autenticazione JWT delle API (`app/auth/jwt_handler.py`).

**Pipeline di conservazione (prova end-to-end: `gateway/tests/test_e2e_pipeline.py`, PdV → CONSERVATO → `/rdv`)**
- Gateway: outbox transazionale, idempotenza (`Idempotency-Key`, VRS-120/121), riconoscimento del formato reale, pulizia degli orfani (`python -m app.services.pulizia_orfani [--prova]`), `/rdv` che restituisce l'XML con impronta verificata.
- `libs/shared/outbox.py` (outbox condivisa con onboarding e distribution), `libs/shared/kafka/consumatore_affidabile.py` (ritentativi, DLQ, offset dopo l'esito), `libs/shared/impronte.py`, `libs/shared/storage/worm.py` (scrittura WORM unica).
- hashchain: Merkle RFC 6962, blocco formato 2, sigillo a tempo, firma dei blocchi via signature (`firma_blocchi.py`), RdV definitivo in WORM (`rdv_definitiva.py`), verifica completa asincrona (`verifica_asincrona.py`, `routers/catena.py`).
- Migrazioni `20261020_010` (allineamento modelli, `metadati_allegato5`, `pdnd_fruitori`, FK RESTRICT), `011` (outbox, impronte del versamento, catena append-only), `012` (codice fiscale degli utenti, chiavi API), `013` (deduplica delle notifiche).

**Firma e marca (signature)**
- CAdES (attached e detached) e PAdES con pyHanko, livello B-T; firma remota **CSC API v2**; marca **RFC 3161** con verifica della catena; adattatore «sviluppo» con chiavi autogenerate dichiarate NON QUALIFICATE e vietato in produzione.
- Endpoint `POST /api/v1/signature/firma`, `/marca`, `/verifica`, `/marca/verifica`, `GET /status` (stato onesto), `/health/ready`, `/info`.

**Notifiche**: invio SMTP reale (aiosmtplib, STARTTLS obbligatorio), coda con esiti `IN_CODA`/`IN_INVIO`/`INVIATA`/`NON_PARTITA`, destinatari veri dall'ente, DLQ con motivo; Mailpit nel docker-compose (http://localhost:8025).

**Governo e consultazione (dashboard-api, distribution)**
- `POST /api/dashboard/contratti`, `POST /api/dashboard/nomine-rsc` (solo SUPER_ADMIN); `POST /api/dashboard/hashchain/verifica-completa` e `GET /api/dashboard/hashchain/verifica-completa/{verifica_id}`.
- Ricerca con testo, classe, anno di versamento e stato; elenco versamenti con motivi del rifiuto; elenco pacchetti con i metadati dell'Allegato 5; storico delle esibizioni con canale e motivo; notifiche con tentativi ed errore; SLA con la sola misura reale (p99 di conservazione).
- Manuale di conservazione e piano di cessazione generati da configurazione (`CONSERVATORE_*`) e DB, con lo stato di ogni sezione.

**Esercizio e qualità**
- `.github/workflows/ci.yml`: PostgreSQL 16 di servizio, `make test` con `VD_TEST_DATABASE_URL` (fallisce anche su un solo test saltato), `make verifiche`, `make lint-critico`, frontend (`npm ci`, `typecheck`, `lint`, `build`), OpenTofu (`fmt -check`, `validate`), actionlint.
- `Makefile`: `make test` che non maschera i fallimenti (servizi + `test-libs`), ambienti per servizio (`make venv-all`), `make verifiche`, `make lint-critico`, `make kafka-topics`.
- `infra/kafka/topics.yaml` + `verifica_topic.py`; `infra/k8s/genera_manifest.py` + `verifica_manifest.py`; `tools/dev/verifica_dockerfile.py`; template dei Secret in `infra/k8s/secrets/`.
- Readiness comune `libs/shared/observability/prontezza*.py`; metriche `/metrics` (`metriche.py`) nei 9 servizi.

## Modificato
- `tools/dev/genera_chiavi_dev.{sh,ps1}`: generano anche `API_KEY_PEPPER`; su un file esistente senza pepper aggiungono solo quello.
- Dipendenze dei 9 servizi rigenerate dagli import (VD-C-031): PyJWT al posto di python-jose ovunque, FastAPI `>=0.117,<0.157`, tolte celery/redis/alembic/lxml/bcrypt dove non usate.
- Frontend: dipendenze tolte `tailwindcss`, `postcss`, `autoprefixer`, `lucide-react`, `clsx`; aggiunte (dev) `eslint`, `@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `globals`; `@fontsource-variable/archivo` già dal primo passo grafico. Script `typecheck` e `lint`.
- `entrypoint.sh` del gateway: `alembic upgrade head` (saltabile con `VD_SALTA_MIGRAZIONI=1`) poi seed; nessun `create_all`. `infra/postgres/init.sql` crea solo `pgcrypto`.
- `seed.py`: cliente dimostrativo solo in development/test con dati fittizi; bucket PdA creato con Object Lock.
- Classi documentali: base solo SUPER_ADMIN, classi del cliente isolate e prevalenti.
- Infrastruttura: unica fonte `infra/helm/values.yaml` (servizi, probe, ConfigMap, Secret, rete, Ingress per prefisso), manifest rigenerati; Terraform corretto (10 moduli + 2 ambienti validi, Object Lock alla creazione dei bucket); Dockerfile con HEALTHCHECK su libreria standard.
- `docker-compose.yml`: `ENV: development` esplicito, `VERUMDOC_DEV_AUTH: "1"` sul gateway, Mailpit, healthcheck senza curl, dipendenze da MinIO sano.
- `METADATI_AGID_VERSAMENTO.md`, `GUIDA_TEST_COMPLETA.md`: modalità a–d, chiavi API, verifica completa al posto di `/integrity`.
- `docs/adr/ADR-001-stack-tecnologico.md`: «Superato in parte».

## Corretto
Criticità dell'audit (dettaglio e test in `docs/registro/criticita.md`):
- **P0**: VD-C-001, 002, 003, 007, 008, 009, 012, 014 chiuse; VD-C-004, 005, 006, 010, 011, 013 chiuse con riserve.
- **P1**: VD-C-018, 020, 022, 023, 025, 029 chiuse; VD-C-015, 016, 017, 019, 021, 024, 026, 027, 028 chiuse con riserve.
- **P2/P3**: VD-C-030, 031, 035, 039, 040 chiuse; VD-C-036, 037 chiuse con riserve; VD-C-032, 033, 038 parzialmente chiuse; VD-C-034 aperta.

Difetti trovati durante il rilascio (VD-C-041…067), fra cui: chiavi API che non avevano mai autenticato (041); ogni richiesta di PdD in 500 (043); ricerca su tutti gli enti con un token senza ente (044); home che mostrava «Integrità compromessa» su una catena mai verificata (046); immagini Docker non avviabili (047); readiness che dicevano «ready» senza controlli (049); ZIP con file mancanti sostituiti da `*_ERROR.txt` (067); creazione utente che rispondeva 201 senza creare (042).

## Sicurezza
Revisione indipendente (rapporto `revisione-sicurezza`) e correzioni (g5-sicurezza, g6-ultime):
- **A1 → VD-C-052 (P0)**: un amministratore d'ente poteva scrivere il proprio codice fiscale su un utente SUPER_ADMIN ed entrare con SPID come SUPER_ADMIN. Ora: ruoli modificabili e assegnabili in tabella, CF di un utente esistente solo da SUPER_ADMIN, audit nella stessa transazione (VD-P-14).
- **M1 → VD-C-054**: voucher PDND senza `aud` accettato; ora PyJWT con claim obbligatori; python-jose eliminato.
- **M2 → VD-C-053**: il servizio di firma firmava contenuti arbitrari per qualsiasi utente; ora solo token di servizio con audience dedicata (VD-P-15).
- **M3, M4 → VD-C-048**: catena senza autenticazione e token non inoltrato; `/integrity` eliminato.
- **M5 → VD-C-055**: ambiente predefinito `production` nei 9 servizi e nel seed del gateway (VD-P-18).
- **M6 → VD-C-056**: migrazione con testo d'eccezione e stato senza token.
- **B1–B13 → VD-C-051, 057–064**: fughe di informazioni, token non revocabili (revoca entro 30 s, VD-P-17), livello SPID dall'`acr`, responsabili di altri enti, zip-slip, ricerca fail-open, connettori con SSRF, `/audit` aperto a tutti.
- Criticità P0 dell'audit: dev-token, onboarding senza verifica, lifecycle senza autenticazione, IDOR dell'esibizione, id_token SPID non verificato, chiave privata nel repository (rotazione: `README_RILASCIO.md`).
- **N1 → VD-C-089**, **N2 → VD-C-090**, R1–R3: vedi «Ultime correzioni di sicurezza» più sotto. Esito della revisione dopo le correzioni: **approvabile**.

## Rimosso

**File eliminati** — si applicano con `tools/pulizia/elimina_R02.{sh,ps1}` (il pacchetto contiene solo file aggiunti o modificati):

| Percorso | Motivo | Sostituito da |
|---|---|---|
| `frontend/tailwind.config.js` | Tailwind dichiarato e inattivo | `frontend/src/index.css` |
| `frontend/src/components/StatCard.tsx` | Mai usato | — |
| `services/signature/app/services/signer.py` + `tests/test_signer.py`, `tests/test_sprint4a_adapter.py` | Firma finta (VD-C-010) | `fornitori.py`, `csc_client.py`, `tsa_client.py`, `firma_cms.py`, `sviluppo.py`, `registro.py`; test in signature e `dashboard-api/tests/test_manuale_piano.py` |
| `services/aip-builder/app/services/lifecycle_manager.py` + `tests/test_lifecycle.py`, `tests/test_bugfix_review.py` | Irraggiungibile e rotto (VD-C-036) | Servizio `lifecycle` da progettare (VD-P-06) |
| `services/notification/app/services/dispatcher.py` + `tests/test_dispatcher.py`, `test_consumer.py`, `test_sprint3a_dlq.py`, `test_bugfix_review.py` | Invio finto, test per stringhe | `smtp.py`, `coda.py`, `invio.py`, `elaborazione.py`; `test_smtp.py`, `test_coda_*.py` |
| `services/dashboard-api/app/services/aggregator.py` + `tests/test_aggregator.py`, `test_bugfix_review.py` | Codice morto (VD-C-040) | `tests/test_configurazione.py` |
| `services/gateway/alembic/__init__.py`, `alembic/versions/__init__.py` | Oscuravano la libreria alembic nel container | — |
| `tests/` alla radice (9 file: `e2e/`, `test_fase4/`, `test_deepreview_env.py`) | Mai eseguiti da `make test` | `dashboard-api/tests/test_g4_elenco_aip.py`, `libs/shared/security/tests/test_input_validation.py`, `libs/shared/connectors/tests/test_gedoc.py`, test dei servizi |
| `infra/terraform/main.tf` | Radice vuota di TODO | Moduli e ambienti |
| `infra/k8s/base/network-policies/allow-opa.yaml` | OPA non distribuito | — |
| `infra/k8s/base/hpa/hashchain-hpa.yaml` | min = max = 1 | hashchain a 1 replica (lock di sessione) |
| `infra/k8s/overlays/staging/patch-replicas.yaml` | Repliche nella fonte unica | `infra/helm/values.staging.yaml` |
| `tools/pulizia/elimina_R01_pulizia.{sh,ps1}` | Già applicati con R01 (§3.3) | `tools/pulizia/elimina_R02.{sh,ps1}` |

**Endpoint dismessi (20/10/2026)**: `POST /api/dashboard/auth/api-key` (sostituto: chiave usata direttamente sul gateway); `GET /api/v1/hashchain/integrity` (sostituto: `POST /api/dashboard/hashchain/verifica-completa`); `POST /api/v1/auth/dev-token` esiste solo con `VERUMDOC_DEV_AUTH=1` e ENV development/test.
**Topic Kafka dismessi**: `aip.building` (nessun consumatore), `audit.event` (mai usato), `notification.sent`, `notification.failed` (stato nella coda). **Make**: target `test-e2e`.
**Ripieghi eliminati**: voucher PDND non firmato `sub=dev-pdnd-test`; chiavi JWT effimere fuori da `ENV=test`; classe documentale creata dal versamento; interruttore `S3_VERIFY_HASH`; «inviata» finta delle notifiche in sviluppo; foglio di ponte e fogli di area del frontend.

## Variabili d'ambiente nuove, cambiate o rimosse

| Variabile | Servizi | Obbligatoria | Predefinito | Note |
|---|---|---|---|---|
| `ENV` | tutti | sì | **`production`** (prima `development`) | Nella ConfigMap; `development` esplicito in docker-compose; `test` abilita le chiavi JWT effimere (solo test). Lo staging gira con `production` |
| `JWT_PRIVATE_KEY` | gateway | sì (Secret) | — | Mai nel repository; la vecchia è compromessa: rotazione |
| `JWT_PUBLIC_KEY` | onboarding, distribution, dashboard-api, signature, **hashchain**; gateway facoltativa | sì (Secret), anche in development | — | Senza, il servizio non parte; il gateway la ricava dalla privata |
| `JWT_ISSUER` / `JWT_AUDIENCE` | gateway, onboarding, distribution, dashboard-api, signature, hashchain | no | `verumdoc.it` / `verumdoc-api` | Uguali ovunque (controllo in `verifica_manifest.py`); i token precedenti non valgono più |
| `JWT_AUDIENCE_SERVIZIO` | signature | no | `verumdoc-signature` | Audience del token di servizio; deve essere diversa da `JWT_AUDIENCE` |
| `SIGNATURE_SERVICE_TOKEN` | hashchain | sì per firmare i blocchi (Secret) | `""` | **Rotazione obbligatoria al rilascio**: `python -m libs.shared.security.emetti_token_servizio --servizio hashchain --giorni 30`; durata massima 90 giorni. Senza o scaduto: blocchi `DA_FIRMARE`, firmati dopo il rinnovo |
| `API_KEY_PEPPER` | gateway, onboarding | sì fuori da development/test (Secret) | — | ≥32 byte casuali, stesso valore nei due servizi; cambiarlo invalida tutte le chiavi. In development senza pepper: 503 AUTH-030/ONB-530 |
| `VD_REVOCA_CACHE_SECONDI` | gateway, onboarding, dashboard-api, distribution, hashchain | no | `30` | Ritardo massimo della revoca (disattivazione, sospensione, cambio di ruolo o di ente); `0` = controllo a ogni richiesta |
| `VERUMDOC_DEV_AUTH` | gateway | no | `false` | Solo sviluppo locale con ENV development/test; mai in staging/produzione |
| `SPID_AGGREGATOR_URL`, `SPID_CLIENT_ID`, `SPID_ISSUER`, `SPID_JWKS_URL` | gateway | sì per SPID/CIE | `""` | Senza: SPID-001 esplicito |
| `SPID_CLIENT_SECRET` | gateway | sì per SPID/CIE (Secret) | `""` | Ora `SecretStr` |
| `SPID_JWKS_CACHE_SECONDS` | gateway | no | `3600` | |
| `SPID_ALLOWED_REDIRECTS` | gateway | no | `[]` (JSON) | Ritorni oltre a `BASE_URL`; staging `["https://app.staging.verumdoc.it/login"]` |
| `SPID_LIVELLO_MINIMO` | gateway | no | `2` (ammessi 2 o 3) | Letto dall'`acr` dell'id_token; sotto il minimo o senza `acr`: SPID-009 (senza `acr` log `spid_acr_assente`: l'aggregatore deve fornirlo) |
| `SPID_MOCK_ENABLED` | gateway | no | `true` nel codice, `false` nei values | Mock attivo solo se è attivo anche il login di sviluppo |
| `BASE_URL` (cambiata) | gateway | sì in esercizio | `https://api.verumdoc.it` | Ora host del **frontend** (`https://app.verumdoc.it`; staging `https://app.staging.verumdoc.it`) |
| `PDND_PUBLIC_KEY` | gateway | sì per PDND (Secret) | `""` | Senza: le rotte PDND rispondono 503 |
| `PDND_AUDIENCE`, `PDND_ISSUER` | gateway | sì se c'è la chiave | `""` | Audience dell'e-service; `interop.pagopa.it` (collaudo `uat.interop.pagopa.it`, da confermare). Vuote o `DA_IMPOSTARE:…` con la chiave presente: il gateway parte, log `pdnd_non_configurato`, rotte PDND a 503 PDND-503 (oggi il segnaposto è in base e staging) |
| `VD_SALTA_MIGRAZIONI` | gateway (entrypoint) | no | `0` | `1` quando le migrazioni girano in un job dedicato |
| `OUTBOX_RELAY_ATTIVO` | gateway | no | `true` | Il relay pubblica gli eventi di gateway, onboarding e distribution; `false` solo con `python -m app.services.outbox` |
| `OUTBOX_INTERVALLO_SECONDI` | gateway | no | `2` | |
| `RETENTION_PERMANENTE_ORIZZONTE_ANNI` | aip-builder, hashchain | sì se esistono classi permanenti | nessuno | Stesso valore nei due (àncora YAML + controllo). Nei values il segnaposto blocca l'avvio finché non si decide (VD-N-03) |
| `HASHCHAIN_VERIFICA_TIMEOUT_SECONDS` | **hashchain** (spostata da dashboard-api) | no | `300` | Durata massima della verifica asincrona; in dashboard-api non è più letta |
| `SIGNATURE_URL` | hashchain | no | `http://signature:8000` | |
| `SIGNATURE_TIMEOUT_SECONDS`, `POLL_TIMEOUT_MS`, `RITARDI_SIGILLO_SECONDI`, `INTERVALLO_RIPRESA_SECONDI` | hashchain | no | `15`, `1000`, `[5,30,120,600]`, `60` | |
| `CONSERVATORE_NOME` | hashchain, aip-builder | no | `VerumDoc S.r.l.` | Tolta da signature |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_AIP`, `S3_REGION` | hashchain (nuove) | sì | sviluppo MinIO | RdV definitivo in WORM nel bucket dei PdA |
| `S3_BUCKET_AIP` (vincolata) | aip-builder, hashchain | sì | — | In staging/produzione deve essere il bucket Terraform `verumdoc-<ambiente>-aip-prod` con Object Lock COMPLIANCE |
| `RITARDI_RITENTATIVI_SECONDI`, `SUFFISSO_DLQ` | validation, aip-builder | no | `[2,10,60]`, `.dlq` | |
| `FIRMA_FORNITORE`, `MARCA_FORNITORE` | signature | no | `non_configurato` | `sviluppo` vietato in produzione (non parte) |
| `CSC_URL_BASE`, `CSC_URL_TOKEN`, `CSC_CLIENT_ID`, `CSC_CREDENTIAL_ID`, `CSC_SIGN_ALGO_OID` | signature | sì con `FIRMA_FORNITORE=csc` | `""` | Configurazione incompleta in produzione: non parte |
| `CSC_CLIENT_SECRET`, `CSC_PIN`, `TSA_PASSWORD` | signature | sì con csc/rfc3161 (Secret) | `""` | |
| `TSA_URL`, `TSA_UTENTE`, `TSA_POLICY_OID`, `TSA_RADICI_PEM`, `FIRMA_RADICI_PEM` | signature | sì con `MARCA_FORNITORE=rfc3161` / per la verifica | `""` | |
| `SVILUPPO_DIR_CHIAVI` | signature | no | `""` | Solo adattatore «sviluppo» |
| `CONSERVATORE_DENOMINAZIONE`, `_CODICE_FISCALE`, `_PARTITA_IVA`, `_SEDE_LEGALE`, `_PEC`, `_EMAIL_CONTATTO`, `_RESPONSABILE_SERVIZIO_NOME`, `_RESPONSABILE_SERVIZIO_CF`, `_RESPONSABILE_FUNZIONE_ARCHIVISTICA_NOME`, `_RESPONSABILE_SICUREZZA_NOME`, `_RESPONSABILE_PROTEZIONE_DATI_NOME`, `_UBICAZIONE_DATA_CENTER`, `_MANUALE_VERSIONE`, `_MANUALE_DATA_ADOZIONE`, `_PIANO_CESSAZIONE_VERSIONE` | dashboard-api | per un manuale presentabile | `""` | Vuote = «da compilare», mai inventate; nei values vuote |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SICUREZZA`, `SMTP_MITTENTE`, `SMTP_NOME_MITTENTE` | notification | sì per inviare | `""`, `587`, `auto`, `""`, `""` | `SMTP_SICUREZZA=nessuna` vietato in produzione |
| `SMTP_UTENTE`, `SMTP_PASSWORD` (Secret), `SMTP_CA_PEM` | notification | secondo il server | `""` | |
| `INVIO_INTERVALLO_SECONDI`, `INVIO_LOTTO`, `INVIO_RITARDI_SECONDI`, `INVIO_RECUPERO_DOPO_SECONDI` | notification | no | `10`, `20`, `[60,300,900,3600]`, `600` | |
| `KAFKA_RITARDI_RITENTATIVI_SECONDI`, `KAFKA_TOPIC_DLQ` | notification | no | `[1,5,30]`, `notification.dlq` | Topic da creare |
| `DATABASE_URL` | notification (ora necessaria) | sì | — | Il servizio usa il DB |
| `GRAFANA_ADMIN_PASSWORD` | stack di osservabilità locale | sì per quello stack | — | |
| `KAFKA_BOOTSTRAP`, `KAFKA_REPLICATION`, `KAFKA_COMMAND_CONFIG`, `KAFKA_CONTAINER` | `infra/scripts/kafka_create_topics.sh` | secondo l'ambiente | — | Topic da `infra/kafka/topics.yaml` |
| `VD_TEST_DATABASE_URL`, `VD_ONBOARDING_PYTHON` | solo test e CI | no | — | Database template migrato; interprete di onboarding per la prova end-to-end delle chiavi |
| `VAULT_URL`, `VAULT_TOKEN` | hashchain | **rimosse** | — | Non usate |
| `S3_VERIFY_HASH` | validation | **rimossa** | — | Verifica sempre attiva |
| `KAFKA_TOPIC_BUILDING` | aip-builder | **rimossa** | — | Topic eliminato |
| `TSA_PROVIDER`, `TSA_API_*`, `TSA_CERTIFICATE_*`, `CONSERVATORE_CF`, `CONSERVATORE_NOME`, DB/Redis/Kafka | signature | **rimosse** | — | |
| `SMTP_FROM`, `SMTP_FROM_NAME`, `SMTP_TLS`, `PEC_*`, `REDIS_URL`, `WEBHOOK_*`, `MAX_RETRIES`, `RETRY_DELAY_SECONDS`, `KAFKA_TOPIC_SENT`, `KAFKA_TOPIC_FAILED` | notification | **rimosse** | — | |

## Migrazioni
`20261020_010` → `011` → `012` → `013` (head `20261020_013`, catena lineare). Verificato su PostgreSQL 16 vuoto: `upgrade head`, `alembic check` senza differenze, downgrade. PostgreSQL ≥ 15 obbligatorio (`NULLS NOT DISTINCT`). La 012 disattiva le chiavi API del vecchio formato; la 013 si ferma con l'elenco se trova notifiche doppie. I database di sviluppo creati con `create_all` vanno ricreati.

## Conformità normativa
- Impronte ricalcolate sui byte; Object Lock COMPLIANCE con retention per oggetto dalla classe documentale (anche per il RdV definitivo); percorso completo fino a CONSERVATO con RdV definitivo.
- Modalità di formazione conforme a LG §2.1.1 (VD-N-02).
- Manuale e piano di cessazione: struttura LG lettere a–l e Allegato B, dati solo da configurazione e DB, sezioni non vere marcate «[DA REALIZZARE]» o «[DA COMPILARE]».
- Non ancora a norma: firma e marca qualificate (VD-C-068), IPdA verificato contro lo XSD UNI 11386 (VD-C-071), scarto e verifiche periodiche (VD-C-072), verifica delle firme dei documenti versati (VD-C-073).

## Test eseguiti
`make test` alla radice, 21/10/2026, PostgreSQL 16 reale (database `doc-rilascio` sul cluster di prova, migrato a `20261020_013`), S3 moto, `VD_TEST_DATABASE_URL` impostata, eseguito dopo le ultime correzioni g6: **uscita 0 — 1.569 passati, 0 falliti, 0 saltati** (audit del 20/10: 659 test, 136 falliti).

| Suite | Audit 20/10 (passati / falliti) | R02 (passati; 0 falliti, 0 saltati) |
|---|---|---|
| gateway | 111 / 20 + 1 errore | 346 |
| validation | 71 / 9 | 96 |
| signature | 41 / 11 | 88 |
| aip-builder | 57 / 37 | 141 |
| hashchain | 20 / 6 | 152 |
| distribution | 41 / 7 | 112 |
| notification | 44 / 2 | 58 |
| onboarding | 49 / 13 | 207 |
| dashboard-api | 72 / 23 | 184 |
| libs/shared + infra/kafka + infra/k8s + tools/dev | 5 / 7 (solo `libs/shared/models`) | 185 |
| sdk (fuori da `make test`, eseguito a parte) | 12 / 0 | 12 |

Altre verifiche (dai rapporti, ripetibili): `make verifiche` uscita 0 (topic, manifest rigenerati senza differenze, Dockerfile, `docker compose config`); `make lint-critico` pulito; `kustomize build` + `kubeconform -strict` 64/64 per base, staging e prod; `tofu fmt -check` e `tofu validate` su 10 moduli e 2 ambienti; frontend `npm run typecheck`, `npm run lint`, `npm run build` verdi; foto di verifica di tutte le 20 rotte a 1440, 1024 e 390 px (nessuno scorrimento orizzontale, nessun errore JS); actionlint OK.
**Non eseguito**: costruzione delle immagini Docker, `helm lint` vero, `nginx -t`, CI su GitHub, Kafka reale (broker simulato a livello di protocollo), MinIO con Object Lock, script `.ps1` (pwsh assente). Passi 3–5 della checklist §0.4 fatti a mano (strumenti non ancora esistenti, VD-C-082).

## Decisioni realizzate
VD-DR-01 (completata su tutto il frontend), VD-DR-02, VD-DR-03, VD-DR-04, VD-N-02, VD-P-00 (schema riallineato), VD-P-10…VD-P-26. Nuove in attesa: VD-N-03, VD-P-27, VD-P-28 (`docs/registro/decisioni.md`).

## Scelte di sviluppo da confermare
- PdV a `CONSERVATO` anche con blocco `NON_FIRMATO` (VD-P-26).
- `Idempotency-Key` facoltativa (VD-P-27).
- Nome del bucket Terraform `-aip-prod` anche in staging (usato dal codice): rinominarlo richiede di aggiornare anche `s3_uploader`.
- `PDND_ISSUER` (`interop.pagopa.it` / `uat.interop.pagopa.it`) preso dalla documentazione del codice: confermarlo con l'e-service registrato.
- Gestore d'ente che indica il codice fiscale alla creazione di OPERATORE e VIEWER (VD-P-14).
- Un token senza ente è sempre rifiutato (401), anche per i ruoli globali: il gateway emette sempre l'ente del conservatore.
- Il ripiego «token del gateway al posto del voucher PDND» resta solo in development/test.

## Limiti noti
Registro `docs/registro/criticita.md`, voci aperte VD-C-068…088 e riserve. In sintesi: nessun accesso per le persone in produzione finché non c'è un aggregatore SPID qualificato; nessuna firma qualificata finché non si sceglie il fornitore (VD-P-05); orizzonte della retention permanente da decidere; outbox dipendente dal gateway; DLQ senza interfaccia; sessione del portale in `localStorage`; Row Level Security non attiva; motore di distribuzione incompleto; nessun test automatico del frontend.

## Ultime correzioni di sicurezza (g6-ultime)
Seguito della «Verifica delle correzioni» della revisione indipendente. Nessuna migrazione, nessuna variabile nuova.
1. **M5** (VD-C-055): il seed del gateway senza `ENV` assume produzione: mai ente dimostrativo né bucket di sviluppo.
2. **Revoca** (VD-C-057, VD-C-090): «DB non inizializzato» riconosciuto dall'eccezione dedicata `libs.shared.db.DBNonInizializzato`, non dal testo; token revocato (401 AUTH-003, entro 30 s) anche se il ruolo dell'utente è cambiato o l'utente è passato a un altro ente: dopo un cambio di ruolo serve un nuovo accesso. Per le chiavi API conta solo il cambio di ente del responsabile.
3. **N1** (VD-C-089): generando una chiave API, il responsabile indicato dev'essere un utente attivo dello stesso ente con ruolo non superiore a chi genera; altrimenti 403 AUTH-010 (altro ente: 404) e nessuna chiave creata.
4. **PDND** (VD-C-054, rilievo R3): chiave presente ma `PDND_AUDIENCE`/`PDND_ISSUER` mancanti o `DA_IMPOSTARE…`: il gateway **parte**, scrive `pdnd_non_configurato` nel log e le sole rotte `/api/pdnd/v1/*` rispondono 503 PDND-503 «PDND non configurato»; readiness pronta. Senza chiave: 503 come prima.
5. **signature** (VD-C-053, rilievo R2): fuori da `ENV=test` non parte se `JWT_AUDIENCE_SERVIZIO` coincide con `JWT_AUDIENCE`.
6. **SPID** (VD-C-058, rilievo B3): id_token senza `acr` rifiutato con SPID-009 (invariato), con il log esplicito `spid_acr_assente` «acr assente nell'id_token: verificare la configurazione dell'aggregatore».
Test nuovi: `gateway/tests/test_g6_ultime.py`, `onboarding/tests/test_g6_responsabile_chiave.py`, `signature/tests/test_g6_audience_servizio.py`.
