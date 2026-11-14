# CHANGELOG — R05 — Configurazione, ciclo di vita e sicurezza (24/10/2026)

Terza e ultima tappa del piano R03–R05 (`docs/architettura/PIANO_R03_R05.md`). Prerequisito: R04 applicato (migrazioni fino a `20261023_035`, ruoli database separati, testimone).

Contenuto:
- la configurazione con bozze, anteprima e applica (motore delle regole completo: accordi di versamento, override del PdV, profili per tipologia, validatore preventivo);
- il ciclo di vita in un servizio nuovo `ciclo-vita`: rettifica, annullamento, scarto con iter e nulla osta, riversamento in uscita e in ingresso, restituzione a fine contratto;
- il registro delle impronte esteso al ciclo di vita (foglie v2, tombstone di scarto, attestazione firmata) e all'audit (export orario nel WORM);
- la Row Level Security per ente su tutte le tabelle dei dati;
- la sessione del portale in cookie HttpOnly con protezione CSRF e uscita lato server;
- le console del conservatore e del cliente (configurazione, canali, registro, pratiche, fascicoli) con tema scuro e test end-to-end.

Esito sul registro (`docs/registro/criticita.md`):
- chiuse: VD-C-087, 111, 113, 115, 117, 118;
- chiuse con riserve: VD-C-072, 079, 080, 088, 112;
- parzialmente chiusa: VD-C-082 (test end-to-end del frontend);
- nuove: VD-C-119–140 (residui dichiarati dai rapporti e dalla revisione).

Fonti: rapporti degli agenti (regole, ciclo di vita, sicurezza, catena, console, integrazione) e revisione indipendente in quattro giri. I primi tre giri hanno dato «non approvabile»; il quarto «approvabile con riserve», con quattro riserve (U1–U4) corrette prima del rilascio. Ogni correzione ha un test permanente che fallisce senza di essa: 15 + 21 + 29 prove di mutazione nei giri di correzione 2–4, tutte uccise, più quelle di sviluppo e del primo giro.

---

## Funzionalità

### Configurazione (contratto Q1) — onboarding, `libs/verumdoc_rules`
- **Catena di risoluzione** CONSERVATORE → ENTE → CLASSE → **ACCORDO** (nuovo) → **PDV** (override motivato). Ogni valore riporta l'origine, `locked` con il riferimento normativo e le azioni consentite con il motivo del blocco.
- **Bozze** versionate (`/api/v1/configurazione/bozze`), **anteprima** che non scrive (sessione READ ONLY) con prima/dopo, impatto su un campione dichiarato degli ultimi 30 giorni, bloccati e avvisi; **applica** atomica con l'impronta del piano (409 `CFG-409` con `PIANO_CAMBIATO` e il piano nuovo). Ogni applicazione crea versioni nuove e una voce d'audit.
- **Proposte** del conservatore agli enti, accettate o rifiutate dall'ente con motivo.
- **Profili di metadati per tipologia** come dati versionati (documento informatico, documento amministrativo informatico, aggregazione, fattura, registro di protocollo, contratto), attivati per classe con `PROFILO_TIPOLOGIA`; i campi da riscontrare sul testo ufficiale sono marcati.
- **Accordi di versamento** (append-only) con elenco a cursore; campi facoltativi `accordo` e `override` nell'indice del PdV (RGL-110/111/112); fotografia delle regole con `catalogo_regole: "2"`.
- **Validatore preventivo**: `POST /api/v1/configurazione/valida` (anche con chiave API) e CLI senza rete `tools/validatore/valida_pdv.py` con il profilo esportato.
- Documento: `docs/architettura/regole.md`.

### Ciclo di vita (contratto Q2) — servizio nuovo `services/ciclo-vita`
- **Pratiche** con macchina a stati come dati, allegati nel WORM con impronta, autorizzazioni per ruolo, stato atteso (409 `LC-409-STATO-CAMBIATO`), anteprima e applica con l'impronta del piano, audit nella transazione, eventi in outbox.
- **Rettifica** (append-only): PdV nuovo con l'atto di rettifica, che segue la pipeline normale tramite il canale `CICLO_VITA` del gateway; l'originale resta immutato.
- **Annullamento logico** prima o dopo la conservazione secondo regole configurabili; evento `versamento.annullato`.
- **Scarto**: iter BOZZA → PROPOSTA → AUTORIZZATA_PRODUTTORE → NULLA_OSTA (enti PA) → ESEGUIBILE → ESEGUITA | ESEGUITA_CON_ECCEZIONI | RESPINTA.
  - Ente PA o privato da `tenants.natura` (NULL = PA), dichiarata dal SUPER_ADMIN con motivo; il codice IPA non decide nulla.
  - Prima di ogni cancellazione: retention Object Lock scaduta e nessun legal hold su **ogni** versione, PdA bloccato `FOR UPDATE`, ricontrollo dei blocchi del DB (classe permanente, scadenza, fascicolo aperto, altra pratica, esibizione in corso).
  - Il lavoratore verifica da sé la **catena delle autorizzazioni** (iter continuo, allegati riletti dal WORM, impronta del piano); se non regge, la pratica si blocca con `LC-409-AUTORIZZAZIONI` prima di qualunque cancellazione.
  - Inventario delle versioni registrato prima della prima cancellazione: «nulla distrutto» si decide dallo storage, mai da un contatore.
  - Cancellazione interrotta da un blocco: PdA `ELIMINAZIONE_INCOMPLETA`, oggetto `NON_COMPLETATO`, pratica `ESEGUITA_CON_ECCEZIONI` con verbale, allarme e ripresa del conservatore (runbook `docs/runbooks/scarto_incompleto.md`).
  - Verbale XML nel WORM e **tombstone** nel registro per ogni PdA cancellato per intero; evento `scarto.eseguito`.
- **Riversamento in uscita** e **restituzione**: PdD MIGRAZIONE completi tramite distribution (token di servizio), manifesto, export dei registri dell'ente, evento `riversamento.pronto`.
- **Riversamento in ingresso**: PdD SInCRO di un altro conservatore caricato con le sessioni esistenti, indice verificato (impronte, firma rilevata), versamento dei documenti; avvio solo dall'ADMIN_TENANT dell'ente.
- **Fascicoli** (distribution): elenco a cursore `GET /api/v2/fascicoli` e chiusura `POST /api/v2/fascicoli/{id}/chiudi`; lo scarto blocca i PdA dei fascicoli aperti e calcola il termine dalla chiusura.
- Gateway: `POST /api/v1/lifecycle/{aip_id}/avvia` risponde 410 `LC-410` con il percorso sostitutivo; `GET /api/v1/versamento/{id}` riporta `annullamento` e `rettifica` solo se valorizzati.
- **Separazione API / lavoratore**: due Deployment, due ServiceAccount e ruoli IAM (`scarto` solo al lavoratore, solo sui prefissi dei PdA), due Secret, due login DB; la tabella `esiti_esecuzione` la scrive solo il lavoratore.
- Documento: `docs/architettura/ciclo_vita.md`.

### Registro delle impronte — hashchain, `libs/catena`, verificatore
- **Foglie v2** con `tipo` (`scarto`, `annullamento`, `rettifica`, `audit`) nello stesso registro e nello stesso ordine totale dei PdA. API interna `POST/GET /interno/v1/registro/foglie` (fuori da `/api/`, non instradata dall'ingress, non pubblicata), token di servizio `registro:aggiungi`, idempotente per (tipo, PdA, pratica), con **riscontro nel DB** prima di accodare (per lo scarto la riga `esiti_esecuzione` del lavoratore).
- **Prova d'inclusione** con le foglie del ciclo di vita (scarti e annullamenti sempre tutti), `ciclo_vita_totale`/`ciclo_vita_completo`, `stato_pda` e **attestazione v2** firmata dal registro, rilasciata solo per l'ultimo checkpoint.
- **Verificatore** (CLI e pagina web offline): «DOCUMENTO SCARTATO / ANNULLATO / SCARTATO IN PARTE» (codice 2), «stato al checkpoint N del <data>», confronto con il checkpoint corrente indicato (più vecchio o biforcato = fallito) e consistenza facoltativa (`--checkpoint-corrente`, `--consistenza-corrente`, passo 5 della pagina).
- **Export dell'audit nel WORM** (VD-C-112): segmenti orari JSON Lines concatenati, radice di Merkle come foglia `audit`, orizzonte delle transazioni aperte (`vd_transazioni_aperte()`), `verifica-audit` e `adotta-segmento-audit`. Runbook `docs/runbooks/audit_worm.md`.
- **Ricostruzione dal WORM** (VD-C-111): `ricostruisci-da-worm --fino-a … [--prova] [--scarta-coda-non-coperta]`, solo foglie coperte da checkpoint firmato **e** controfirmato, mai automatica.
- **Coerenza** (VD-C-118): i segmenti WORM devono coprire le foglie dichiarate esportate.
- Hashchain con legal hold solo sui RdV e scrittura solo su `registro/` e RdV.

### Sicurezza e isolamento
- **Row Level Security per ente** (VD-C-080) su tutte le tabelle con `tenant_id` dei dati, delle pratiche e della configurazione; un solo punto che imposta l'ambito per transazione (`SET LOCAL`, compatibile con PgBouncer); ambito conservatore solo da ruolo verificato o con motivo dichiarato; `tenant_id` proprio su `documenti`, `metadati_allegato5`, `lifecycle_events`, `fascicoli_aip`. Le suite di tutti i servizi girano ora con il login dei servizi, non con il superutente. Documento: `docs/architettura/sicurezza.md`.
- **Guardia all'avvio** (VD-C-113) su tutte le URL del database: nessun superutente, `BYPASSRLS`, `rds_superuser` o proprietario di tabelle; `idle_in_transaction_session_timeout` obbligatorio (≤ 30 min) fuori da sviluppo e test.
- **Sessione in cookie** (VD-C-079): `vd_sessione` HttpOnly/Secure/SameSite=Strict, CSRF a doppio invio con controllo dell'origine, `GET`/`DELETE /api/v1/auth/sessione`, uscita che revoca il token (`sessioni_revocate`, per `jti`); chiavi API invariate.
- **Migrazioni senza `PYTHONPATH`** (VD-C-117).

### Console (frontend)
- Configurazione: impostazioni e regole con origine, profili, classi e accordi, quote, bozze con anteprima e applica, proposte, validatore.
- Canali: account SFTP, webhook (tipi letti da `GET /api/v1/webhook/tipi`).
- Registro: stato dell'ultimo checkpoint, verifica continua, registro delle verifiche.
- Pratiche: «in attesa di te», elenco, corsa della pratica, nuova pratica con anteprima.
- Fascicoli: elenco, apertura, chiusura.
- Tema scuro automatico o scelto in testata (VD-DR-05, `frontend/DESIGN.md`); 80 foto in `docs/design/R05/`.
- Test end-to-end Playwright: 27.

### Integrazione e infrastruttura
- Migrazione di unione `20261023_040` e RLS sulle tabelle dei rami; canale `CICLO_VITA` nel gateway con chiave pubblica dedicata; API di migrazione di distribution con token di servizio; notification consuma i 5 eventi del ciclo di vita.
- SDK 3.2.0 (`StatoVersamento.annullamento`, `.rettifica`), compatibile con la 3.1.
- Storage di sviluppo **versitygw 1.9.0** (VD-C-115) costruito dal binario della release con SHA-256 verificato; stesso server in CI.
- Regole Prometheus `infra/observability/regole/verumdoc-r05.yml`.
- OpenAPI: 164 operazioni pubbliche, tutte instradate dall'ingress (ciclo-vita 11; hashchain 14 → 12 perché l'API interna non è più pubblica).

## Correzioni (revisione indipendente)
- **Primo giro — «non approvabile»** (1 bloccante, 5 alte):
  - R1 scarto respinto durante l'esecuzione: PdA distrutti senza verbale né tombstone;
  - R2 PdA senza oggetti nel WORM dichiarato eliminato;
  - R3 pod API esposto con cancellazione e legal hold su tutto il bucket;
  - R4 prova d'inclusione troncata a 50 foglie (scarto invisibile);
  - R5 ricostruzione che accettava foglie non coperte da firma;
  - R6 `VALIDATE` sotto lock esclusivo nelle migrazioni 038/039;
  - poi: export dell'audit che perdeva righe di transazioni lunghe (R7), riversamento in ingresso «eseguito» senza versare (R8), nulla osta dal codice IPA facoltativo (R9), assenza di lock fra ricontrollo e cancellazione (R10), lease non rinnovato nel passo (R11), uscita che non revocava il token (R12), guardia solo sull'engine primario (R13), tabelle con `tenant_id` fuori dalla RLS (R14).
- **Secondo giro — «non approvabile»** (3 alte):
  - S1 upgrade non riprendibile dopo un `lock_timeout` (ora una transazione per revisione e segmenti idempotenti);
  - S2 credenziali di servizio nel pod API esposto e foglie «scarto» accettate senza riscontro;
  - S3 scarto bloccato per sempre su una versione non cancellabile (stati nuovi `ELIMINAZIONE_INCOMPLETA`, `NON_COMPLETATO`, `ESEGUITA_CON_ECCEZIONI`);
  - basse: legal hold e scrittura della hashchain su tutto il bucket (S4), natura PRIVATO senza motivo (S5), versione nata dopo l'elenco (S6), orologio dell'applicazione nell'audit e transazioni dimenticate (S7), assenza di scarto non dimostrabile (S8, attestazione firmata).
- **Terzo giro — «non approvabile»** (1 alta):
  - T1 interruzione e poi blocco: PdA mutilato dichiarato CONSERVATO (ora inventario e decisione dallo storage);
  - medie: attestazione aggirabile con un checkpoint vecchio (T2), PdA incompleto dichiarato conservato dalla prova (T3), verifica continua su PdA incompleti (T4), riscontro basato su righe scrivibili dall'API (T5: `esiti_esecuzione` e login del lavoratore);
  - basse: ripresa senza ricontrollo dei blocchi del DB (T6), upload WORM dentro transazioni (T7), riallineamento del nulla osta prima dei controlli d'accesso (T8).
- **Quarto giro — «approvabile con riserve»**, riserve corrette:
  - U1 storage illeggibile alla chiusura: `TypeError` e oggetto mai chiuso (ora «non determinabile» dichiarato e chiusura reale);
  - U2 pratica di scarto fabbricata dal login dell'API ed eseguita dal lavoratore (ora verifica della catena delle autorizzazioni e migrazione 044);
  - U3 checkpoint corrente più vecchio o biforcato accettato dal verificatore;
  - U4 versione nata dopo l'inventario cancellata e poi persa da un crash.
- **Integrazione**: payload Kafka di ciclo-vita senza `tenant_id` (notification avrebbe scartato tutti gli eventi); middleware di sessione e ambito assenti in ciclo-vita; rotta d'ingresso della configurazione mancante.

## Conformità normativa
- Scarto con autorizzazione del produttore e nulla osta della Soprintendenza per gli enti PA (D.Lgs. 42/2004 art. 21; Linee guida §4.11), verbale nel WORM e prova nel registro; nessuna cancellazione forzata prima della scadenza della retention.
- Rettifica e annullamento senza modificare il conservato (append-only); riversamento e restituzione con PdD SInCRO e prove della catena.
- Audit esportato nel WORM e legato al registro: il superutente del database non lo altera più senza traccia (finestra residua dichiarata).
- Limiti invariati: firma e marca non qualificate (VD-C-068); firme dei documenti versati e degli allegati rilevate, non verificate (VD-C-073); IPdA non verificato contro lo XSD UNI (VD-C-071); campi dei profili di tipologia da riscontrare sul testo ufficiale (VD-C-136).

## Dismissioni
- `LC-501` e `LC-503` del gateway (sostituiti da `LC-410`).
- Console MinIO sulla porta 9001 in sviluppo (versitygw non ha console); volume `minio_data` (nuovo volume `storage_data`).
- `POST /api/v1/auth/spid/scambio` non restituisce più `access_token` e `token_type`.

## File eliminati
Nessuno: il rilascio aggiunge 258 file e ne modifica 199 rispetto a R04 (più i documenti di questo rilascio). Non c'è uno script `elimina_R05`.

## Test eseguiti (24/10/2026, numeri copiati dall'esecuzione)

PostgreSQL 16 reale, template `r05_integ` creato da zero con `alembic upgrade head` (testa `20261024_044`); S3 simulato (moto) e, per `infra/tests` e il registro, versitygw 1.9.0 reale; tutti i servizi con il login non proprietario `vd_prova_servizio` (RLS attiva nei test).

| Suite | Passati (0 falliti) |
|---|---|
| gateway | 750 |
| validation | 159 |
| aip-builder | 182 |
| hashchain | 312 |
| notification | 268 |
| distribution | 160 |
| dashboard-api | 235 |
| signature | 91 |
| sftp-ingest | 147 |
| ciclo-vita (nuovo) | 139 |
| onboarding | 249 |
| testimone | 61 |
| libs/shared | 307 |
| libs/verumdoc_rules | 156 |
| libs/catena | 104 (+1 saltato: oracolo Go facoltativo) |
| tools/docs, tools/dev, tools/verificatore (con Playwright), tools/validatore, tools/carico | 30, 18, 54, 7, 5 |
| infra/kafka, infra/tests (versitygw reale), infra/k8s (con `tofu test`) | 5, 11, 99 |
| sdk | 93 |
| **Totale** | **3.642** (R04: 3.153) |
| frontend end-to-end (Playwright) | 27 |

Inoltre:
- `make docs-api` e `make verifiche` con uscita 0;
- frontend typecheck, lint e build con uscita 0;
- OpenTofu `fmt -check`, `validate` (modulo `iam_servizi`, prod, staging), `tofu test` del modulo IAM 18/18;
- migrazioni: da vuoto alla testa, downgrade a 035 e nuovo upgrade, ripresa dopo un guasto iniettato all'inizio di ogni segmento e su ogni comando in autocommit;
- tutte le prove dei revisori dei quattro giri rieseguite: nessuna riproduce più il difetto (tranne `test_rev_038_lock.py`, che riproduce DDL scritti a mano e prova PostgreSQL, non il codice).

**Non provati qui:**
- Kafka e MSK reali, OpenSearch reale, S3 con permessi IAM veri;
- immagini Docker (compresa quella di versitygw), deploy, k6 e CI su un ambiente reale;
- PowerShell.

## Scelte da confermare (VD-P-44…50, VD-DR-05)
Elenco completo in `docs/registro/decisioni.md`. Le principali:
1. **Natura dell'ente** (VD-P-45): NULL = PA; PRIVATO dichiarato dal SUPER_ADMIN con motivo; la natura attuale prevale su quella al momento dell'autorizzazione (una pratica di un ente diventato PA si blocca e se ne apre un'altra).
2. **Esecuzione dello scarto** (VD-P-46): stop del PdA alla prima versione non cancellabile, `SCARTO_TENTATIVI_MAX_PDA = 5`, blocco `LC-409-AUTORIZZAZIONI` senza stato nuovo, nulla osta non obbligatoriamente firmato, PdA scartati che restano nel DB con i metadati.
3. **Verificatore e attestazione** (VD-P-48): esiti con codice 2, attestazione v2 solo per l'ultimo checkpoint e firmata dal solo registro, «non dimostrato» (codice 1) senza attestazione o con un checkpoint corrente più recente.
4. **Sicurezza** (VD-P-49): ambito conservatore dal ruolo, guardia anche in staging, `idle_in_transaction_session_timeout` obbligatorio, login distinto del lavoratore, durata della sessione uguale a quella del token.
5. **Configurazione** (VD-P-44): permessi, storico append-only, override solo per RGL-103, impatto su campione fuori dall'impronta del piano.
6. **Ciclo di vita** (VD-P-47): `lifecycle` → 410, rettifica come PdV nuovo con atto XML, regole dell'annullamento, riversamento in ingresso solo dall'ADMIN_TENANT, `ALLEGATI_RETENTION_ANNI = 100` (VD-N-03).
7. **Integrazione e console** (VD-P-50, VD-DR-05): chiave pubblica dedicata del canale, eventi Q2, storage di sviluppo versitygw, tema scuro automatico, pagina Webhook solo ADMIN_TENANT.

## Limiti noti
- **Esercizio mai provato davvero** (VD-C-081 parziale, 109, 110).
- **API compromessa che agisce come un utente legittimo** può ancora percorrere l'iter di scarto con documenti che sembrano firmati (VD-C-120, chiusura con VD-C-073); annullamento e rettifica senza fatto scritto dal solo lavoratore (VD-C-119).
- **Sessione** senza scadenza per inattività (VD-C-127); SFTP con la chiave dei token di canale nel Secret del servizio esposto (VD-C-126).
- **Residui dello scarto**: versioni rimaste «non determinabili» con storage illeggibile o oggetti senza inventario (VD-C-122), PdA incompleti fuori dalla verifica continua (VD-C-135), metadati dei PdA scartati nel DB (VD-C-137).
- **Console**: «in attesa di te» con una richiesta per stato (VD-C-128), nessuna API degli allarmi del registro (VD-C-129), natura dell'ente non gestibile dall'interfaccia (VD-C-138), accordi aperti solo per codice (VD-C-139).
- CronJob `verifica-audit` non creato (VD-C-130).

---

## CAMBI INCOMPATIBILI

1. **Attestazione del ciclo di vita v2**: la v1 è rifiutata da libreria, CLI e pagina web. L'attestazione si rilascia solo per l'ultimo checkpoint (per una `dimensione` vecchia `attestazione_ciclo_vita: null` con `attestazione_assente`); campi nuovi `ultimo_checkpoint`, `stato_pda`. Esito nuovo «SCARTATO IN PARTE» (codice 2). Senza attestazione, o con un checkpoint corrente più recente, più vecchio o biforcato, lo stato è «non dimostrato» (codice 1; prima 0). Opzioni nuove `--checkpoint-corrente` e `--consistenza-corrente`.
2. **API interna del registro** su `/interno/v1/registro/foglie` (fuori da `/api/`, non nell'OpenAPI: hashchain 14 → 12 operazioni pubbliche); 404 con `X-Forwarded-For`; `409 CAT-409-RISCONTRO` senza il fatto nel DB.
3. **SPID/CIE**: `POST /api/v1/auth/spid/scambio` non restituisce più `access_token`/`token_type`; il portale usa il cookie. Ogni modifica dal portale richiede `X-CSRF-Token` e la stessa origine (403 `AUTH-040`). **Frontend e gateway vanno rilasciati insieme.**
4. **Gateway** `POST /api/v1/lifecycle/{aip_id}/avvia` → `410 LC-410` (prima 501/503).
5. **Guardie d'avvio**: fuori da sviluppo e test un servizio non parte con un login privilegiato o proprietario di tabelle (su qualunque URL del database), senza `idle_in_transaction_session_timeout` (o con più di 30 minuti), o con il database irraggiungibile (prima partiva «non pronto»). L'API di ciclo-vita non parte con le credenziali del lavoratore o con INSERT su `esiti_esecuzione`; il lavoratore non parte senza.
6. **Ciclo di vita**: RSC_CONSERVATORE non avvia più il riversamento in ingresso; dopo l'avvio dell'esecuzione nessuna transizione (`LC-409-IN-ESECUZIONE`); stati ed esiti nuovi (`ELIMINAZIONE_INCOMPLETA`, `NON_COMPLETATO`, `ESEGUITA_CON_ECCEZIONI`); verbale con gli attributi dei PdA incompleti (`versioniRimaste="non determinabile"`, `verificaStorage`); manifesto di riversamento e restituzione senza `allegato_id` dell'export; scarto non eseguito senza catena di autorizzazioni verificabile (`LC-409-AUTORIZZAZIONI`) e motivo d'oggetto `FUORI_PIANO`.
7. **Schema**: enum `aip_stato` + `ELIMINAZIONE_INCOMPLETA` (irreversibile); `pratiche_oggetti.esito_piano`/`motivo_piano` congelati; `pratiche.impronta_piano` immutabile; `pratiche_stati` rifiuta righe discontinue; `tenant_id` NOT NULL su `documenti`, `metadati_allegato5`, `lifecycle_events`, `fascicoli_aip`; RLS che nasconde le righe di altri enti a chi non imposta l'ambito.
8. **Distribution**: un PdD chiesto su un PdA in ELIMINAZIONE risponde `DST-101`; `export-batch` con token di servizio richiede `ente_richiedente`.
9. **Sviluppo**: storage su versitygw con volume nuovo (`docker compose down -v`), niente console 9001.
10. Nessun cambio per i versatori: campi dell'indice `accordo` e `override` facoltativi, impronta dell'indice invariata senza di essi; fotografia dei PdV nuovi con `catalogo_regole: "2"`. SDK 3.2.0 compatibile con la 3.1.

## Variabili d'ambiente e Secret nuovi

Fonti: rapporti degli agenti e dei giri di correzione. C = ConfigMap, S = Secret.

| Servizio / processo | Variabile | Nuova / cambiata | Predefinito del codice | Dove in esercizio | Fonte |
|---|---|---|---|---|---|
| **ciclo-vita** (API) | `DATABASE_URL`, `REDIS_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `JWT_PUBLIC_KEY`, `API_KEY_PEPPER` | nuove | — | S: `ciclo-vita-secrets` (login dell'API, **non** membro di `verumdoc_ciclo_vita_lavoratore`) | ciclo, S2, T5 |
| **ciclo-vita-lavoratore** | `DATABASE_URL` | nuova | — | S: `ciclo-vita-lavoratore-secrets` (login `IN ROLE verumdoc_applicazione, verumdoc_ciclo_vita_lavoratore`, prevale su quello del servizio) | T5 |
| ciclo-vita-lavoratore | `HASHCHAIN_TOKEN_REGISTRO` | nuova | "" (pratiche in attesa con l'errore; obbligatoria in produzione) | S lavoratore: token di servizio aud `verumdoc-registro`, scope `registro:aggiungi` | ciclo, catena, S2 |
| ciclo-vita-lavoratore | `CICLO_VITA_TOKEN_CHIAVE_PRIVATA` | nuova | "" (obbligatoria in produzione) | S lavoratore: RSA ≥ 2048 propria, mai la chiave JWT | ciclo, int |
| ciclo-vita-lavoratore | `DISTRIBUTION_TOKEN_SERVIZIO` | nuova | "" (obbligatoria in produzione) | S lavoratore: aud `verumdoc-distribution`, scope `migrazione:servizio`; rotazione entro 90 giorni | ciclo, int |
| ciclo-vita | `ENV`, `KAFKA_BOOTSTRAP`, `JWT_ISSUER`, `JWT_AUDIENCE`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET_AIP`, `S3_BUCKET_PDV`, `HASHCHAIN_URL`, `GATEWAY_URL`, `DISTRIBUTION_URL` e i rispettivi `*_TIMEOUT_SECONDI` | nuove | production, `localhost:9092`, `verumdoc.it`, `verumdoc-api`, `http://minio:9000`, `eu-south-1`, `verumdoc-aip`, `verumdoc-pdv`, URL interni; 15/60/30 s | C: `ciclo-vita-config` | ciclo |
| ciclo-vita | `LAVORATORE_ATTIVO`, `LAVORATORE_INTERVALLO_SECONDI`, `LAVORATORE_LEASE_SECONDI`, `LAVORATORE_RITARDI_SECONDI`, `LAVORATORE_LOTTO`, `LAVORATORE_ATTESA_ESTERNA_SECONDI`, `LAVORATORE_SOGLIA_ALLARME_TENTATIVI`, `METRICHE_INTERVALLO_SECONDI` | nuove | true (false nelle repliche API), 2, 300, [60,300,1500,7200], 200, 30, 5, 60 | C | ciclo, S3 |
| ciclo-vita | `ANNULLAMENTO_DOPO_CONSERVAZIONE`, `ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE`, `ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE`, `RETTIFICA_RUOLI` | nuove | true, [ADMIN_TENANT, OPERATORE, API_SYSTEM], [ADMIN_TENANT], [ADMIN_TENANT, OPERATORE, API_SYSTEM] | non impostate | ciclo |
| ciclo-vita | `SCARTO_MAX_PDA_PER_PRATICA`, `SCARTO_TENTATIVI_MAX_PDA`, `RIVERSAMENTO_MAX_PDA_PER_PRATICA`, `RIVERSAMENTO_LOTTO_DISTRIBUTION`, `ANTEPRIMA_ESEMPI` | nuove | 100000, 5, 1000000, 100, 50 | non impostate | ciclo, S3 |
| ciclo-vita | `ALLEGATI_MAX_MB`, `ALLEGATI_RETENTION_ANNI`, `RIVERSAMENTO_INGRESSO_MAX_MB`, `RIVERSAMENTO_INGRESSO_INDICE_MAX_MB`, `CONSERVATORE_DENOMINAZIONE`, `ELENCO_LIMITE_PREDEFINITO`, `ELENCO_LIMITE_MASSIMO` | nuove | 50, 100 (VD-N-03), 5120, 20, "", 50, 200 | non impostate | ciclo |
| gateway | `CICLO_VITA_TOKEN_CHIAVE_PUBBLICA` | nuova | "" (canale `CICLO_VITA` escluso in produzione) | S `gateway-secrets` | int |
| gateway | `CANALE_SERVIZI_AMMESSI` | valore cambiato | `{"sftp-ingest": ["SFTP"], "ciclo-vita": ["CICLO_VITA"]}` | C | int |
| gateway, onboarding, dashboard-api, distribution, notification, hashchain, ciclo-vita | `SESSIONE_ORIGINI_AMMESSE` | nuova | vuota (stessa origine) | C solo se portale e API hanno origini diverse | sicurezza |
| tutti (`libs/shared/db`) | `VD_DB_IDLE_IN_TRANSACTION_MASSIMO_SECONDI` | nuova | 1800 | non impostata | S7 |
| onboarding | `CONFIGURAZIONE_ANTEPRIMA_CAMPIONE_MAX`, `_TEMPO_MAX_S`, `_FINESTRA_GIORNI`, `_ESEMPI`, `_QUERY_MS` | nuove | 2000, 5, 30, 10, 10000 | non impostate | regole |
| onboarding | `IDEMPOTENZA_OBBLIGATORIA`, `IDEMPOTENZA_TRANSIZIONE_FINO_AL` | lette anche qui | come il gateway | C: **stessi valori del gateway** | regole |
| distribution | `MIGRAZIONE_PUBBLICO_SERVIZIO`, `MIGRAZIONE_SCOPE_SERVIZIO` | nuove | `verumdoc-distribution`, `migrazione:servizio` | non impostate | int |
| hashchain | `CATENA_SCOPE_AGGIUNTA_FOGLIE`, `CATENA_CICLO_VITA_MAX_FOGLIE` | nuove | `registro:aggiungi`, 50 | non impostate | catena |
| hashchain | `AUDIT_EXPORT_ATTIVO`, `AUDIT_EXPORT_PERIODO_SECONDI`, `AUDIT_EXPORT_MARGINE_SECONDI`, `AUDIT_EXPORT_RIGHE_MASSIME`, `AUDIT_EXPORT_INIZIO`, `AUDIT_EXPORT_PERIODI_PER_GIRO` | nuove | true, 3600, 600, 200000, "" (dall'audit più vecchio), 24 | `AUDIT_EXPORT_INIZIO` per non esportare la storia | catena |
| Job delle migrazioni | `VD_MIGRAZIONE_LOCK_TIMEOUT`, `VD_MIGRAZIONE_038_LOTTO_BLOCCHI` | nuove | 5s, 2000 | non impostate | R6, sicurezza |
| sviluppo | `infra/dev_ciclo_vita.env`, `infra/dev_ciclo_vita_pub.env` | nuovi file | — | generati da `tools/dev/genera_segreti_dev.py` (i token di servizio si emettono a mano) | int |

Componenti nuovi:
- servizio `ciclo-vita` (8000, nel compose 8012) e Deployment `ciclo-vita-lavoratore`, con ServiceAccount, ruoli IAM, Secret e NetworkPolicy propri;
- Secret `ciclo-vita-secrets` e `ciclo-vita-lavoratore-secrets` (modelli in `infra/k8s/secrets/`);
- rotte d'ingresso `/api/v1/ciclo-vita` (ciclo-vita) e `/api/v1/configurazione` (onboarding).

## Migrazioni (036 → 044)

| Revisione | down_revision | Contenuto | Note |
|---|---|---|---|
| `20261023_036` | 035 | configurazione: bozze, accordi di versamento e profili di tipologia (append-only), proposte, storico delle impostazioni; indice `idx_pdv_classe_ricezione` | indice CONCURRENTLY; segmento iniziale saltato se già applicato |
| `20261023_037` | 035 | ciclo di vita: `pratiche`, `pratiche_stati` (append-only), `pratiche_allegati`, `pratiche_oggetti`, `pratiche_esecuzioni`, vista `pratiche_canale_versamento` | 3 indici CONCURRENTLY (indici invalidi ricreati) |
| `20261023_038` | 035 | RLS per ente su 28 tabelle; `tenant_id` con trigger su `documenti`, `metadati_allegato5`, `lifecycle_events`, `fascicoli_aip` | **riempimento a lotti** fuori transazione (`VD_MIGRAZIONE_038_LOTTO_BLOCCHI`), `VALIDATE` in autocommit, `SET NOT NULL` in transazioni brevi; minuti su 1 milione di documenti; **si ferma** se ci sono fascicoli con PdA di un altro ente; serve il ruolo proprietario (`verumdoc_manutenzione`) |
| `20261023_039` | 035 | foglie interne del registro, `audit_esportazioni`, `catena_coda`/`catena_registro` estese | DDL di solo catalogo, poi `VALIDATE` in autocommit; downgrade rifiutato se il registro ha foglie interne |
| `20261023_040` | (036, 037, 038, 039) | unione; RLS sulle tabelle dei rami; vista `WITH (security_invoker = true)` | leggera |
| `20261023_041` | 040 | `tenants.natura`, `sessioni_revocate`, RLS su classi/impostazioni/fruitori PDND/nomine, funzione `vd_transazioni_aperte()` SECURITY DEFINER | il proprietario delle migrazioni deve vedere `pg_stat_activity` di tutti (superutente o `pg_read_all_stats`) |
| `20261023_042` | 041 | `aip_stato` + `ELIMINAZIONE_INCOMPLETA`; esito `NON_COMPLETATO` | `ADD VALUE IF NOT EXISTS`; il valore dell'enum non si toglie al downgrade |
| `20261023_043` | 042 | ruolo `verumdoc_ciclo_vita_lavoratore` (NOLOGIN) e tabella `esiti_esecuzione` (append-only, RLS, INSERT solo al lavoratore) | **CREATEROLE** per creare il ruolo; senza, crearlo a mano e rieseguire i GRANT |
| `20261024_044` | 043 | `esito_piano`/`motivo_piano` congelati, `impronta_piano` immutabile, `richiede_nulla_osta` mai da vero a falso, cronologia continua, esito `AUTORIZZAZIONI_VERIFICATE` | vincoli `NOT VALID` + `VALIDATE` in autocommit |

Tutte:
- eseguite dal Job delle migrazioni con un ruolo proprietario (non dai servizi);
- con `SET LOCAL lock_timeout` (`VD_MIGRAZIONE_LOCK_TIMEOUT`, 5 s) sulle DDL esclusive;
- **riprendibili**: una transazione per revisione (`transaction_per_migration=True`) e segmenti idempotenti; dopo un `lock_timeout` basta rilanciare il Job;
- provate con upgrade da zero, downgrade a 035, nuovo upgrade e guasto iniettato in ogni segmento.

Nessuna richiede una finestra di manutenzione come la 028 di R04; la 038 è la più lunga ma non tiene lock esclusivi durante le scansioni.

## Topic Kafka

| Topic | Partizioni | Retention | Produttore | Consumatore | Chiave |
|---|---|---|---|---|---|
| `pratica.cambio_stato` | 6 | 72 h | ciclo-vita | notification | `pratica_id` |
| `versamento.annullato` | 6 | 72 h | ciclo-vita | notification | `pdv_id` |
| `versamento.rettificato` | 6 | 72 h | ciclo-vita | notification | `pdv_id` |
| `scarto.eseguito` | 3 | 72 h | ciclo-vita | notification | `pratica_id` |
| `riversamento.pronto` | 3 | 72 h | ciclo-vita | notification | `pratica_id` |

Totale 243 partizioni (R04: 219). Si creano con `make kafka-topics`; le ACL derivano da `topics.yaml` (modulo `iam_servizi`).

## Ruoli IAM e database

**IAM** (modulo `iam_servizi`, `tofu apply`):
- `verumdoc-<amb>-ciclo-vita` (API): `aip [lettura, scrittura, retention]` solo su `pratiche/`, `staging [lettura]`, nessuna cancellazione né legal hold, Deny su `registro/`;
- `verumdoc-<amb>-ciclo-vita-lavoratore`: permesso `scarto` (`DeleteObjectVersion`) solo su `<uuid-ente>/…`, Deny su `registro/`;
- hashchain: scrittura e retention solo su `registro/` e sui RdV, legal hold solo sui RdV;
- aip-builder: Deny su `registro/` e `pratiche/`;
- `verifica_manifest` rifiuta `scarto` a un servizio esposto o senza prefissi.

**Database**:
- ruolo di gruppo nuovo `verumdoc_ciclo_vita_lavoratore` (043);
- login nuovi: API di ciclo-vita (`IN ROLE verumdoc_applicazione`) e lavoratore (`IN ROLE verumdoc_applicazione, verumdoc_ciclo_vita_lavoratore`);
- `ALTER ROLE <login> SET idle_in_transaction_session_timeout = '15min'` su **ogni** login dei servizi, compresi quelli usati da PgBouncer;
- nessun login di servizio superutente, `BYPASSRLS`, `rds_superuser` o proprietario di tabelle (anche in staging); testimone con un login proprietario del solo schema `testimone`; CronJob `gateway-db-manutenzione` non superutente.

## Allarmi nuovi (`infra/observability/regole/verumdoc-r05.yml`)

| Allarme | Condizione | Gravità |
|---|---|---|
| `AuditNonEsportato` | ritardo dell'export dell'audit > 2 h per 15 min | pagina |
| `AuditWormDivergente` | segmento dell'audit nel WORM diverso dal DB | pagina |
| `AuditExportTrattenuto` | transazione aperta che trattiene l'export > 30 min | avviso |
| `AuditOrizzonteNonVerificabile` | orizzonte delle transazioni non leggibile | pagina |
| `ScartoEliminazioneIncompleta` | PdA in `ELIMINAZIONE_INCOMPLETA` | pagina |
| `ScartoAutorizzazioniNonVerificate` | pratica di scarto bloccata per autorizzazioni non verificabili | pagina |
| `PraticaLavoroInErrore` | lavoro di una pratica in errore oltre la soglia di tentativi per 30 min | avviso |

Suggerito e non creato: CronJob giornaliero `python -m app.registro.operatore verifica-audit` nell'immagine della hashchain, con allarme sul codice d'uscita (VD-C-130).

## Giri di revisione (sintesi)

| Giro | Verdetto | Rilievi | Esito delle correzioni |
|---|---|---|---|
| 1 (R1–R14) | non approvabile | 1 bloccante, 5 alte, 3 medie, 5 basse | tutti corretti; 2 riaperti in parte (R1 → S3, R6 → S1) |
| 2 (S1–S8) | non approvabile | 3 alte, 5 basse | 29 mutazioni su 29 uccise; S3 e S8 parziali al giro 3 |
| 3 (T1–T8) | non approvabile | 1 alta, 4 medie, 3 basse | 21 mutazioni su 21; T5 parziale al giro 4 |
| 4 (U1–U4) | **approvabile con riserve** | 2 medie, 2 basse | 15 mutazioni su 15; riserve corrette; residui nel registro |

## Residui (registro delle criticità)
- **U2** (VD-C-120): un'API compromessa che agisce come un utente legittimo può percorrere l'intero iter con documenti che sembrano firmati (firma rilevata, non verificata: VD-C-073) e far eseguire lo scarto di PdA già scartabili; resta una traccia permanente e attribuita. Chiusura: verifica crittografica delle firme, autorizzazione scritta da un servizio distinto o secondo operatore fuori dall'API.
- **T5** (VD-C-119): annullamento e rettifica senza un fatto scritto dal solo lavoratore.
- **U1/U4/T1** (VD-C-122): versioni rimaste «non determinabili» con storage illeggibile e oggetti avviati senza inventario: controllo manuale prima della ripresa.
- **U3/T2** (VD-C-123): lo stato attuale non si dimostra senza una prova aggiornata; la pagina web non scarica da sola il checkpoint corrente.
- **S8** (VD-C-124): attestazione firmata dal solo registro.
- **R14** (VD-C-125): `catena_pda` e le altre tabelle del registro fuori dalla RLS.
- **S2** (VD-C-126): server SFTP con `SFTP_TOKEN_CHIAVE_PRIVATA` nel Secret del servizio esposto.
- **R7/S7** (VD-C-132): export dell'audit trattenuto da una transazione dimenticata; finestra scoperta fino a 70 minuti.
- **S3** (VD-C-133), **T7** (VD-C-134), **T4** (VD-C-135): nessuno stato terminale automatico per gli altri lavori, documento orfano nel WORM se fallisce la transazione finale, PdA incompleti fuori dalla verifica continua.
- Console e operatività: VD-C-127–131, 136–140.
