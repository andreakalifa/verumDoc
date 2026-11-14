# VerumDoc — conservazione digitale a norma

VerumDoc è un **sistema di conservazione digitale** dei documenti informatici progettato sulle Linee guida AgID sulla formazione, gestione e conservazione dei documenti informatici (in vigore dal 1/1/2022) e sui loro allegati, sul Regolamento dei servizi di conservazione (Det. AgID 455/2021), sul CAD (D.Lgs. 82/2005), su UNI 11386:2020 (SInCRO), ISO 14721 (OAIS), eIDAS e GDPR. È costruito per **cambiare quando cambiano le norme**: regole, profili dei metadati, formati e parametri sono dati versionati, non costanti nel codice (`REGOLE_SVILUPPO.md` §8).

Licenza: **EUPL-1.2** (`LICENSE`). Il nome e il logo «VerumDoc» non sono concessi in licenza (`NOTICE`). *English summary at the end of this page.*

## Cos'è

- **Versamento** dei pacchetti di versamento (PdV) da API REST, sessioni di caricamento per file grandi, SFTP e PDND, con idempotenza obbligatoria e un solo caso d'uso per tutti i canali.
- **Verifica** dei PdV con regole configurabili per classe documentale: formato riconosciuto (PRONOM), firme, metadati dell'Allegato 5, rapporto di versamento.
- **Conservazione** in pacchetti di archiviazione (PdA) su storage WORM (S3 Object Lock COMPLIANCE) con indice SInCRO, **catena delle impronte** verificabile da terzi (RFC 6962, checkpoint firmati, testimone indipendente, verificatore pubblico).
- **Distribuzione ed esibizione** (PdD), ricerca, **ciclo di vita** (scarto con autorizzazioni, rettifiche, annullamenti, riversamento) con decisioni irreversibili confermate e firmate.
- **Console** web per conservatore, enti e auditor, con accesso SPID/CIE (predisposto), password con codice a tempo e passkey; documentazione di conformità (manuale di conservazione, piano della sicurezza) **generata dalla configurazione reale**.

## Stato del progetto

> Aggiornato il 14/11/2026 con il rilascio R18 (apertura open source). Stato dettagliato e motivato per area: `STATO_PROGETTO.md`; registri pubblici: `docs/registro/criticita.md` e `docs/registro/decisioni.md`; regole: `REGOLE_SVILUPPO.md`.

| Voce | Valore |
|------|--------|
| Fase | Codice interno completo e collaudato (R17); R18 prepara la pubblicazione con licenza EUPL-1.2 |
| Completamento reale | ~90% rispetto a un servizio di conservazione a norma e qualificabile; il resto dipende da risorse esterne (prestatore qualificato di firma e marca, aggregatore SPID/CIE, gestore PEC, schema UNI a pagamento, infrastruttura) |
| Criticità | 186 registrate, 22 aperte, nessuna P0 aperta — `docs/registro/criticita.md` |
| Test | 4.582 test Python con PostgreSQL reale, 152 end-to-end della console, controlli statici (`make verifiche`) |

**Il sistema non è ancora utilizzabile per conservare documenti a norma**: firma e marca non sono qualificate (manca il contratto con un prestatore qualificato), l'IPdA non è verificato contro lo XSD UNI 11386 (norma a pagamento), e in produzione nessuno può entrare finché non è collegato un aggregatore SPID/CIE. Non è mai stato distribuito su un'infrastruttura reale.

## Stack tecnologico

| Componente | Tecnologia | Note |
|-----------|-----------|------|
| Backend | Python 3.12, FastAPI (`>=0.117,<0.157`), Pydantic v2 | 12 servizi (9 microservizi + `sftp-ingest` + `testimone` + `ciclo-vita`) |
| Autenticazione | JWT RS256 con PyJWT, libreria unica `libs/shared/security` | python-jose eliminato; audience ed emittente obbligatori |
| Eventi | Kafka (aiokafka) + outbox transazionale | Topic in `infra/kafka/topics.yaml` (unica fonte) |
| Database | PostgreSQL ≥ 15 (16 in sviluppo), asyncpg + SQLAlchemy 2.0, Alembic | Schema `public.*`, multi-tenant per `tenant_id` con Row Level Security per ente; servizi con login non proprietario; migrazioni solo nel gateway |
| Storage | S3 con Object Lock COMPLIANCE (versitygw 1.9.0 in sviluppo e CI) | Retention per oggetto dalla classe documentale; cancellazione solo dal lavoratore di ciclo-vita, dopo la scadenza |
| Cache | Redis 7 | State e codici monouso SPID, limiti di richieste e quote per piano ed ente |
| Ricerca | OpenSearch | Indicizzazione a lotti dal lavoratore di distribution, metadati sensibili esclusi; 503 esplicito se non risponde |
| Catena delle impronte | Registro a due livelli RFC 6962, checkpoint C2SP su nota firmata Ed25519, testimone indipendente, verificatore pubblico, foglie del ciclo di vita e dell'audit (`libs/catena`, `services/testimone`, `tools/verificatore`) | Sigillo qualificato sui checkpoint quando c'è il fornitore (VD-P-05); `docs/architettura/catena.md` |
| Firma e marca | pyHanko (CAdES/PAdES), CSC API v2, RFC 3161 | Adattatore di sviluppo non qualificato; fornitore qualificato da scegliere |
| Notifiche | aiosmtplib (Mailpit in sviluppo), webhook firmati HMAC, elenco eventi a cursore | PEC non disponibile |
| Frontend | React 18, TypeScript strict, Vite 5, TanStack Query, eslint 9, Playwright (e2e) | Sistema grafico «Partenze» con tema scuro (`frontend/DESIGN.md`); sessione in cookie HttpOnly |
| Infra | Docker, Kubernetes/Helm, Terraform (validato con OpenTofu 1.8.5), GitHub Actions | Unica fonte `infra/helm/values.yaml`; mai distribuito su un cluster |

## Architettura microservizi

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│   Gateway    │────▶│  Validation  │
│  React/Vite  │     │  JWT RS256   │     │  9 passi     │
└─────────────┘     │  SPID/CIE    │     └──────┬───────┘
                    │  PDND        │            │ Kafka (outbox)
                    │  Outbox      │            ▼
                    └──────┬───────┘     ┌──────────────┐
                           │             │ AIP Builder  │
                    ┌──────┴───────┐     │ IPdA, WORM   │
                    │ Dashboard API│     └──────┬───────┘
                    │ Audit/SLA    │            │ Kafka
                    │ Manuale      │            ▼
                    │ Piano Cess.  │     ┌──────────────┐     ┌──────────────┐
                    └──────────────┘     │  Hashchain   │────▶│  Signature   │
                                         │ RFC 6962     │     │ CSC / RFC3161│
                    ┌──────────────┐     │ RdV Defin.   │     └──────────────┘
                    │ Distribution │     └──────┬───────┘
                    │ PdD, fascic. │            │ Kafka
                    └──────────────┘            ▼
                    ┌──────────────┐     ┌──────────────┐
                    │  Onboarding  │     │ Notification │
                    │ Enti, utenti │     │ SMTP, eventi │
                    │ Configuraz.  │     │ webhook      │
                    └──────────────┘     └──────────────┘
                    ┌──────────────┐  canale CICLO_VITA → Gateway
                    │  Ciclo vita  │  foglie /interno → Hashchain
                    │ API+lavorat. │  PdD migrazione  → Distribution
                    │ scarto, rett.│  eventi (outbox) → Notification
                    └──────────────┘
```
Il ciclo di vita ha un'API esposta (pratiche, allegati, transizioni) e un lavoratore separato, l'unico che cancella nel WORM e chiede le foglie al registro (`docs/architettura/ciclo_vita.md`).

## Avvio rapido (sviluppo locale)

```bash
# 1. Segreti di sviluppo (una volta; file esclusi da git)
bash tools/dev/genera_chiavi_dev.sh                 # chiavi JWT e pepper delle chiavi API
bash infra/scripts/genera_segreti_dev_r03.sh        # canale SFTP e cifratura dei webhook
python tools/dev/genera_segreti_dev.py              # R04–R05: catena, testimone, ciclo di vita
# Windows: basta `python tools/dev/genera_segreti_dev.py` (serve solo il pacchetto
#   cryptography; niente PowerShell 7, openssl né script .ps1): genera TUTTI i file,
#   compresa la coppia della posta (dev_posta_priv.env, dev_posta_pub.env);
#   non sovrascrive i file esistenti, --forza per rigenerare

# 2. Tutto con docker compose (ENV=development esplicito; il gateway esegue le migrazioni e il seed)
docker compose up -d --build
make kafka-topics                                   # Windows: .\CREA_TOPIC_KAFKA.ps1

# 3. Frontend
cd frontend && npm ci && npm run dev          # http://localhost:5173, profili di sviluppo (dev-token)

# 4. Test (serve un PostgreSQL migrato a head usato come template)
cd services/gateway && DATABASE_URL=postgresql+asyncpg://… alembic upgrade head && cd ../..
VD_TEST_DATABASE_URL=postgresql+asyncpg://… make test
make verifiche && make lint-critico
cd frontend && npm run typecheck && npm run lint && npm run build
```

Senza `ENV` un servizio si comporta da **produzione** (chiavi obbligatorie, nessun login di sviluppo, guardia sul ruolo del database). Passando da R04 a R05 lo storage di sviluppo cambia: serve `docker compose down -v`. Procedura completa di aggiornamento: `README_RILASCIO.md`.

## Porte (docker-compose)

| Servizio | Porta host | Salute |
|---------|-------|-------------|
| PostgreSQL | 5434 | `pg_isready -p 5434` |
| Redis | 6379 | `redis-cli ping` |
| Kafka | 9092 | `make health-kafka` |
| Storage S3 (versitygw, servizio `minio`) | 9000 (API, nessuna console) | `/health` |
| Mailpit | 1025 (SMTP), 8025 (interfaccia) | — |
| OpenSearch | 9200 | — |
| Gateway | 8000 | `/api/v1/health/ready` |
| Validation | 8001 | `/api/v1/health/ready` |
| Signature | 8003 | `/api/v1/health/ready` |
| Hashchain | 8004 | `/api/v1/health/ready` |
| AIP Builder | 8005 | `/api/v1/health/ready` |
| Notification | 8006 | `/api/v1/health/ready` |
| Onboarding | 8007 | `/api/v1/health/ready` |
| Distribution | 8008 | `/api/v2/health/ready` |
| SFTP ingest | 2222 (SFTP), 8010 (salute) | `/api/v1/health/live` |
| Testimone | 8011 | `/api/v1/health/ready` |
| Ciclo di vita | 8012 | `/api/v1/health/ready` |
| Dashboard API | 8009 | `/api/health/ready` |
| Frontend (dev) | 5173 | — |

Le readiness rispondono 503 se una dipendenza bloccante (DB, Kafka, S3) non risponde. `make health` controlla tutti i servizi. Ogni servizio espone `/metrics`.

## Autenticazione

**Persone — SPID/CIE** tramite aggregatore OIDC: id_token verificato (JWKS, iss, aud, nonce), livello minimo L2 letto dall'`acr`, codice fiscale associato all'utente (unico per ente; con più enti la persona sceglie), codice monouso nel frammento dell'URL scambiato con il token. Senza aggregatore configurato la pagina d'accesso mostra SPID-001. In sviluppo locale: dev-token solo con `VERUMDOC_DEV_AUTH=1` ed ENV development/test.

**Sessione del portale**: cookie `vd_sessione` HttpOnly, Secure, SameSite=Strict, con token anti-CSRF (`X-CSRF-Token`) e controllo dell'origine sulle modifiche; «Esci» revoca il token lato server. Nessun token nel browser.

**Token**: JWT RS256 firmati solo dal gateway, con `iss`, `aud`, `exp`, `jti`, ente e ruolo; verificati da tutti i servizi con la stessa libreria. Utente disattivato o ente sospeso: token rifiutati entro 30 s (AUTH-003).

**Sistemi — chiavi API** `vd_<identificativo>_<segreto>`, generate da «Utenti e accessi» e mostrate una volta; impronta HMAC-SHA256 con pepper; intestazione `X-Api-Key` oppure `X-Api-Key-Id` + `X-Api-Key-Secret` (SDK: `sdk/README.md`).

**Canali**: REST multipart e sessioni di caricamento a parti, SFTP (chiave pubblica), PDND; ogni canale chiama lo stesso caso d'uso di versamento con `Idempotency-Key`. Guida per chi integra: `docs/integrazione/README.md`; contratto: `docs/catalogo/openapi.yaml`; errori: `docs/catalogo/errori.md`.

**Servizi**: firma, registro (foglie del ciclo di vita) e migrazione di distribution accettano solo token di servizio con audience e scope dedicati (`python -m libs.shared.security.emetti_token_servizio`).

## Ruoli

| Ruolo | Descrizione | Note |
|-------|-------------|------|
| SUPER_ADMIN | Amministrazione del servizio | Unico che registra contratti e nomine e cambia il CF di un utente esistente |
| RSC_CONSERVATORE | Responsabile del servizio di conservazione | Metadati di ogni ente; contenuti solo del proprio; esegue scarti e riversamenti |
| ADMIN_TENANT | Amministratore dell'ente | Gestisce solo OPERATORE e VIEWER del proprio ente; configura ente, classi e accordi; autorizza scarti e restituzioni come produttore |
| OPERATORE | Versamento e consultazione | |
| VIEWER | Consultazione | Non scrive fascicoli |
| API_SYSTEM | Sistemi versanti (chiavi API) | Scope `versamento:ud`, `versamento:doc`, `read` |

## Pipeline di conservazione (provata da `services/gateway/tests/test_e2e_pipeline.py`)

```
1. VERSAMENTO   POST /api/v1/versamento/ud  (Idempotency-Key facoltativa)
   ↓            impronte ricalcolate, tipo reale del file, classe dell'ente, duplicati
   ↓            file su S3 + PdV + evento in outbox nella stessa transazione → pdv.received
2. VERIFICA     9 passi fail-closed, stato VALIDATO | ERRORE con i motivi → pdv.validated / pdv.invalid
3. PdA          impronte ricalcolate sui byte, IPdA SInCRO, scrittura WORM COMPLIANCE → aip.conserved
4. CATENA       blocco Merkle RFC 6962 (anche a tempo), firma via signature, RdV definitivo in WORM
   ↓            PdA e PdV → CONSERVATO;  GET /api/v1/versamento/{id}/rdv
5. NOTIFICA     e-mail al referente dell'ente; scarti in DLQ con il motivo
```
Errori ripetuti → DLQ (`pdv.received.dlq`, `pdv.validated.dlq`, `notification.dlq`) e PdV in ERRORE con il motivo, consultabili e rielaborabili dalla console «Code di scarto». Limiti: checkpoint senza sigillo qualificato; IPdA non verificato contro lo XSD.

## Ciclo di vita (servizio `ciclo-vita`, `/api/v1/ciclo-vita`)

```
RETTIFICA      richiesta → PdV di rettifica nella pipeline normale → foglia «rettifica»   (originale immutato)
ANNULLAMENTO   annullamento logico → foglia «annullamento» → versamento.annullato
SCARTO         BOZZA → PROPOSTA → AUTORIZZATA_PRODUTTORE → NULLA_OSTA (enti PA) → ESEGUIBILE
               → lavoratore: verifica delle autorizzazioni, retention scaduta su ogni versione,
                 cancellazione nel WORM, verbale XML, tombstone nel registro
               → ESEGUITA | ESEGUITA_CON_ECCEZIONI (cancellazione incompleta dichiarata) | RESPINTA
RIVERSAMENTO   uscita: PdD MIGRAZIONE SInCRO con manifesto · ingresso: PdD di un altro conservatore verificato e versato
RESTITUZIONE   export completo dell'ente a fine contratto
```
Ogni pratica ha cronologia append-only, allegati nel WORM con impronta, anteprima e applica con l'impronta del piano, audit ed eventi per i clienti. Il verificatore pubblico dice «DOCUMENTO SCARTATO/ANNULLATO» per un documento scartato o annullato. Dettagli: `docs/architettura/ciclo_vita.md`, runbook `docs/runbooks/scarto_incompleto.md`.

## Conformità AgID

Mappa requisito per requisito: `docs/audit/AUDIT_FUNZIONALE_2026-10-20.md` §5, aggiornata dal registro delle criticità. Dopo R02: impronte ricalcolate, immutabilità con Object Lock COMPLIANCE e retention per classe, percorso fino a CONSERVATO con RdV definitivo, modalità di formazione a–d (VD-N-02), manuale e piano di cessazione generati dai dati veri con le parti mancanti dichiarate. Dopo R04–R05: verifica continua dell'archivio, scarto con autorizzazione del produttore e nulla osta della Soprintendenza per gli enti PA, verbale e prova nel registro, rettifica e annullamento senza modificare il conservato, riversamento e restituzione. Dopo R11: profili dell'Allegato 5 riscontrati sul testo ufficiale (versioni 2, adozione per classe) e IPdA predisposto alla verifica contro l'XSD caricabile (schema ufficiale non ancora disponibile). Ancora assenti: firma e marca qualificate, IPdA verificato (UNI 11386), verifica delle firme dei documenti versati e delle autorizzazioni, PEC.

### Profili di metadati dell'Allegato 5 (R11)

I profili di tipologia sono dati versionati in `libs/verumdoc_rules/dati/profili/`. Le versioni 2 sono riscontrate sul testo ufficiale dell'Allegato 5 (fonte, esiti e riferimenti: `docs/conformita/riscontro_allegato5.md`) e hanno **adozione esplicita**: una classe passa alla v2 con una bozza `impostazioni` di livello CLASSE (`PROFILO_TIPOLOGIA` + `PROFILO_TIPOLOGIA_VERSIONE = 2`), con anteprima dell'impatto e applicazione subito o programmata. I versamenti già fatti restano con la versione fotografata. Una classe aggiunge solo campi facoltativi propri; RGL-102 e RGL-106 si possono solo rendere più severe.

### Schema UNI 11386 (IPdA)

L'IPdA prodotto oggi ha una struttura **provvisoria** di VerumDoc e **non è dichiarato conforme**: lo schema XSD della UNI 11386:2020 non è ancora stato acquistato (D03, VD-C-071). Quando il committente lo acquista:

1. copiare i file `.xsd` della norma in `libs/sincro/schemas/uni11386-2020/` (la cartella finisce nell'immagine dell'aip-builder perché il Dockerfile copia `libs/`; verificare prima che la licenza UNI consenta la copia nel repository e nelle immagini);
2. fissare le impronte nel codice: `python tools/sincro/verifica_ipda.py impronte --cartella libs/sincro/schemas/uni11386-2020` e copiarle in `libs/sincro/schemi_ufficiali.py` (modifica rivista e decisione `VD-N`: senza, nessuno schema è «ufficiale», qualunque cosa dica il descrittore);
3. registrarli (scrive `schema.json` con l'impronta SHA-256 di ogni file, di qualsiasi estensione; `--ufficiale` è accettato solo se coincidono con quelle del codice):
   ```bash
   python tools/sincro/verifica_ipda.py registra --cartella libs/sincro/schemas/uni11386-2020 \
       --principale <file-principale>.xsd --versione uni11386-2020 --ufficiale \
       --fonte "UNI 11386:2020, copia acquistata il <data>"
   ```
4. lanciare la verifica: `make conformita-ipda` (stato dello schema e test di conformità parametrici DI, DAI, AGG in `services/aip-builder/tests/test_r11_ipda.py`, che senza XSD sono saltati con il motivo). Un singolo indice si verifica con `python tools/sincro/verifica_ipda.py verifica IndiceAIP_SInCRO.xml` (uscita 0 conforme, 1 non conforme o schema alterato, 2 schema ufficiale non disponibile).

Lo schema si compila dai byte di cui è registrata l'impronta e include o importa solo i file registrati della cartella: un riferimento esterno (anche un import remoto, per esempio la firma XML) dà «schema non utilizzabile» e va sostituito con una copia locale registrata. Finché il costruttore non è adeguato allo schema i test di conformità falliranno con gli errori dello schema: è il lavoro che resta di VD-C-071. Non mettere l'XSD nell'immagine di produzione prima che passino. Senza schema ufficiale l'esito è sempre «schema ufficiale non disponibile», mai «conforme»; un file dello schema cambiato dopo la registrazione dà «schema alterato».

## Sicurezza

**Vulnerabilità**: non aprire una issue pubblica; segui `SECURITY.md` (segnalazione responsabile).

Le criticità P0 dell'audit (verifica dei token, dev-token, endpoint senza autenticazione, IDOR dell'esibizione, SPID, chiave nel repository) sono chiuse e una revisione indipendente ha giudicato il rilascio approvabile (dettaglio in `docs/registro/criticita.md`, VD-C-001…006 e 041–067, 089–090). Chiavi e segreti solo da variabili d'ambiente o secret manager; la vecchia chiave di sviluppo è compromessa: non va mai riusata e va ruotata ovunque sia stata usata; il repository pubblico parte da una storia nuova che non la contiene (`docs/sviluppo/PUBBLICAZIONE_OPEN_SOURCE.md`). Le chiavi di sviluppo si generano in locale (`tools/dev/genera_segreti_dev.py`) e non sono mai tracciate; quelle dei test si generano durante l'esecuzione. Dal R05: Row Level Security per ente su tutte le tabelle dei dati, guardia all'avvio contro login privilegiati, sessione in cookie HttpOnly con CSRF, separazione fra API e lavoratore del ciclo di vita (IAM, Secret, login propri), audit esportato nel WORM. Aperti: scadenza per inattività della sessione e i residui della revisione R05 (VD-C-119–127).

## Struttura monorepo

```
verumdoc/
├── frontend/              # React 18 + TypeScript, sistema «Partenze» (DESIGN.md, PRODUCT.md)
│   ├── src/{api,auth,components,console,data,pages}   # 34 pagine; unico foglio src/index.css
│   └── e2e/               # test end-to-end Playwright
├── services/
│   ├── gateway/           # versamento unico, sessioni di caricamento, stato e RdV, SPID/CIE, PDND, adattatori, outbox, migrazioni
│   ├── validation/        # 9 passi di verifica (consumer Kafka)
│   ├── aip-builder/       # PdA, IPdA SInCRO, scrittura WORM
│   ├── hashchain/         # catena delle impronte, RdV definitivo, verifica completa
│   ├── signature/         # firma e marca (CSC, RFC 3161, sviluppo)
│   ├── distribution/      # esibizioni, PdD, fascicoli, migrazione
│   ├── notification/      # posta, eventi per i clienti, webhook firmati
│   ├── onboarding/        # enti, utenti, classi, chiavi API, configurazione (bozze, anteprima, applica)
│   ├── dashboard-api/     # consultazione, audit, SLA, manuale, piano di cessazione
│   ├── sftp-ingest/       # canale SFTP (server e lavoratore)
│   ├── testimone/         # testimone indipendente del registro (DB e account propri)
│   └── ciclo-vita/        # pratiche: rettifica, annullamento, scarto, riversamento, restituzione (API e lavoratore)
├── libs/shared/           # modelli, DB, Kafka, outbox, storage, sicurezza, errori, limiti di richieste
├── libs/verumdoc_rules/   # motore delle regole: impostazioni, regole, profili, formati, quote
├── libs/catena/           # RFC 6962, note firmate C2SP, checkpoint (usata anche dal verificatore)
├── sdk/                   # SDK Python 3.2 per i versatori
├── infra/                 # helm (unica fonte), k8s (generato), terraform, kafka, observability
├── tools/                 # dev (chiavi, verifiche), verificatore, validatore del PdV, pulizia, design (foto)
├── docs/                  # audit, design, registro (criticità, decisioni), adr, runbooks
└── STATO_PROGETTO.md      # stato corrente
```

## Documentazione

| Documento | Percorso |
|-----------|---------|
| Stato progetto | `STATO_PROGETTO.md` |
| Regole di sviluppo | `REGOLE_SVILUPPO.md` |
| Ultimo rilascio | `README_RILASCIO.md`, `changelog/CHANGELOG-2026-11-14-rilascio-R18.md`; sintesi pubblica in `CHANGELOG.md` |
| Licenza, avvisi, marchio, terze parti | `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` |
| Comunità | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `GOVERNANCE.md`, `MAINTAINERS`, `publiccode.yml` |
| Pubblicazione open source (storia nuova) | `docs/sviluppo/PUBBLICAZIONE_OPEN_SOURCE.md`, `tools/opensource/controlla_pubblicazione.py` |
| Ciclo di vita, configurazione, sicurezza | `docs/architettura/ciclo_vita.md`, `docs/architettura/regole.md`, `docs/architettura/sicurezza.md` |
| Catena, testimone, verifica indipendente | `docs/architettura/catena.md`, `docs/integrazione/verifica_indipendente.md`, `tools/verificatore/` |
| Infrastruttura e runbook | `docs/architettura/infrastruttura.md`, `docs/runbooks/` |
| Piano R03–R05 | `docs/architettura/PIANO_R03_R05.md` |
| Integrazione (canali, webhook, SFTP, PDND) | `docs/integrazione/README.md` |
| Contratto API ed errori (generati) | `docs/catalogo/openapi.yaml`, `docs/catalogo/endpoint.md`, `docs/catalogo/errori.md` |
| Componenti di terze parti e licenze | `THIRD_PARTY_NOTICES.md` |
| Registro criticità / decisioni | `docs/registro/criticita.md`, `docs/registro/decisioni.md` |
| Audit funzionale e grafico | `docs/audit/AUDIT_FUNZIONALE_2026-10-20.md`, `docs/design/AUDIT_GRAFICO_2026-10-20.md` |
| Sistema grafico | `frontend/DESIGN.md` |
| Metadati di versamento | `METADATI_AGID_VERSAMENTO.md` |
| Riscontro dei profili sull'Allegato 5 (R11) | `docs/conformita/riscontro_allegato5.md` |
| Guida ai test manuali | `GUIDA_TEST_COMPLETA.md` |
| SDK | `sdk/README.md` |
| Segreti Kubernetes | `infra/k8s/secrets/README.md` |
| ADR (superato in parte) | `docs/adr/ADR-001-stack-tecnologico.md` |

## Passaggio a produzione

Servono: le decisioni e i contratti esterni (fornitore qualificato di firma e TSA, aggregatore SPID/CIE, UNI 11386, PDND), la conferma delle proposte R05 e la chiusura dei residui (VD-C-119–140), la prova su infrastruttura vera e il percorso di qualificazione (requisiti dell'Allegato A del Regolamento AgID, qualificazione ACN per il SaaS alla PA, certificazioni ISO).

## Contribuire

Le regole per contribuire (segnalazioni, proposte, stile, test, revisione e firma DCO dei commit) sono in `CONTRIBUTING.md`; il codice di comportamento in `CODE_OF_CONDUCT.md`; il governo del progetto in `GOVERNANCE.md`. Ogni file sorgente nuovo porta l'intestazione SPDX (`REGOLE_SVILUPPO.md` §31, R18); `make verifiche` la controlla con `tools/opensource/controlla_pubblicazione.py`.

## Licenza

Copyright 2026 Contributori di VerumDoc. Distribuito con licenza **European Union Public Licence v. 1.2** (EUPL-1.2): testo integrale in `LICENSE`, avvisi in `NOTICE`, componenti di terze parti e loro licenze in `THIRD_PARTY_NOTICES.md`. Il nome e il logo «VerumDoc» sono segni distintivi **non concessi in licenza** dalla EUPL (politica del marchio in `NOTICE`).

## In English

**VerumDoc** is a digital preservation system for electronic documents, designed around the Italian AgID Guidelines on electronic documents (2022), the AgID regulation on preservation services, UNI 11386 (SInCRO), ISO 14721 (OAIS), eIDAS and GDPR, and built so that rules, metadata profiles and formats are versioned data that can follow regulatory changes.

- **Status**: internally complete and tested (about 4,600 Python tests against real PostgreSQL, 152 end-to-end UI tests), but **not yet usable for legally compliant preservation**: qualified signatures and timestamps, SPID/CIE login, the paid UNI schema and a real infrastructure are still missing. Never deployed to production.
- **Quick start**: see «Avvio rapido» above (`python tools/dev/genera_segreti_dev.py`, `docker compose up -d --build`, `cd frontend && npm ci && npm run dev`).
- **Architecture**: Python 3.12/FastAPI microservices, Kafka with a transactional outbox, PostgreSQL with row level security, S3 Object Lock storage, an RFC 6962 hash chain with an independent witness, a React/TypeScript console, Kubernetes/Helm and Terraform.
- **Security**: report vulnerabilities privately as described in `SECURITY.md`; do not open public issues.
- **Contributing**: `CONTRIBUTING.md` (DCO sign-off recommended). Documentation is in Italian.
- **License**: EUPL-1.2 (`LICENSE`). The «VerumDoc» name and logo are not licensed under the EUPL (`NOTICE`).
