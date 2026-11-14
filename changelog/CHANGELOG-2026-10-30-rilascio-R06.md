# CHANGELOG — R06 — Sessione, credenziali e pulizia (30/10/2026)

Rilascio di consolidamento dopo il piano R03–R05. Prerequisito: R05 applicato (migrazioni fino a `20261024_044`, login separato del lavoratore di ciclo-vita, sessione in cookie).

Contenuto:
- **Pulizia**: `tools/pulizia/pulisci_progetto.py` cancella foto di prova, cache e script dei rilasci precedenti; da ora le foto non si conservano nel repository (REGOLE §25.13).
- **Sessione del portale con scadenza per inattività** (VD-C-127): 30 minuti, 15 per SUPER_ADMIN e RSC_CONSERVATORE, fissata all'accesso in `sessioni_attive`, valida subito in ogni servizio; avviso un minuto prima nel portale.
- **Credenziali ambigue rifiutate** (revisione V1): più intestazioni `Authorization`/`X-Api-Key`, più cookie di sessione diversi o cookie e Bearer diversi → 400 `AUTH-041` in ogni servizio.
- **Fatti del lavoratore per annullamento e rettifica** (VD-C-119): il riscontro della hashchain li pretende; il lavoratore li scrive solo dopo aver verificato la catena della richiesta (revisione V2).
- **Chiave dei token SFTP solo al lavoratore** (VD-C-126): il server SFTP esposto non la riceve più.
- **CronJob giornaliero `hashchain-verifica-audit`** con allarmi (VD-C-130).
- Console: «In attesa di te» con una richiesta (VD-C-128), allarmi del registro (VD-C-129), natura dell'ente (VD-C-138), elenco degli accordi (VD-C-139); dati di prova senza nomi di enti reali (VD-C-140).

Esito sul registro (`docs/registro/criticita.md`): chiuse 126, 127, 128, 129, 130, 138, 139, 140; chiusa con riserve 119; nuove 141–142. Totale 142: 26 aperte, 7 parzialmente chiuse, 24 chiuse con riserve, 85 chiuse.

Fonti: rapporto di sviluppo R06 e revisione indipendente («non approvabile»: 2 rilievi alti, 2 bassi), corretti prima del rilascio con test permanenti che falliscono senza la correzione; 13 prove di mutazione nello sviluppo e 9 nella correzione, tutte uccise.

---

## Revisione indipendente: rilievi e correzioni

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| **V1** Scadenza per inattività e «Esci» aggirabili con due intestazioni `Authorization` (il middleware leggeva l'ultima, le rotte la prima) | Alta | `libs/shared/security/credenziali_uniche.py`: intestazioni di credenziali ripetute, più cookie `vd_sessione` diversi, cookie e Bearer diversi → 400 `AUTH-041` (catalogo), in `MiddlewareSessioneCookie` e in signature e testimone (`MiddlewareCredenzialiUniche`) | `gateway/tests/test_r06_revisione_credenziali_sessione.py` (gateway e servizio con il solo middleware, dopo l'inattività e dopo «Esci»), `test_r05_sessione_cookie.py` |
| **V2** L'API compromessa faceva scrivere al lavoratore il fatto di annullamento o rettifica (anche sul PdA di un altro ente) e la foglia passava il riscontro | Alta | Prima di atto, versamento e fatto il lavoratore verifica la catena della richiesta (`verifica_puntuale`, stesso approccio dello scarto): richiedente presente, attivo e dell'ente, ruolo ammesso, cronologia coerente, voce d'audit, PdV e PdA della pratica. Altrimenti pratica bloccata `LC-409-AUTORIZZAZIONI`, allarme `PuntualeAutorizzazioniNonVerificate`. Rettifica valida con richiedente poi disattivato → RESPINTA | `ciclo-vita/tests/test_r06_revisione_puntuali_autorizzazioni.py`: lavoratore e riscontro **vero** della hashchain, compresa la prova del revisore |
| **V3** Allarme del CronJob su tentativi intermedi; Job con segreti in eccesso | Bassa | Allarme sull'ultimo Job con condizione Failed più recente dell'ultima verifica riuscita (`kube_job_failed`); `backoffLimit: 0`; Secret proprio `hashchain-verifica-audit-secrets` (solo S3); nessun token del ServiceAccount; manifest e chart rigenerati | `infra/k8s/test_verifica_manifest.py::test_r06_revisione_cronjob_verifica_audit_minimo`, `infra/tests/test_regole_prometheus.py` |
| **V4** `sessioni_attive` modificabile in ogni colonna da tutti i login (anche SFTP); UPDATE per un JWT non firmato | Bassa | Migrazione 045: diritti solo al gruppo `verumdoc_sessioni_portale` (SELECT, INSERT, DELETE, UPDATE della sola `ultima_attivita`), nessuno al server SFTP; trigger: `ultima_attivita` mai nel futuro; limite fra 60 s e 2 ore. Il middleware verifica firma e scadenza prima di toccare il DB; rinnovo al più ogni 30 s | `gateway/tests/test_r06_revisione_credenziali_sessione.py` (`test_v4_*`) |

## Cambi incompatibili
- **Gateway e servizi del portale**: una richiesta con credenziali ambigue riceve 400 `AUTH-041` (prima ne veniva scelta una). Un client che manda cookie di sessione **e** un Bearer diverso va corretto.
- **Login dei servizi del portale** membri di `verumdoc_sessioni_portale`, altrimenti le richieste del portale ricevono 503 `AUTH-503` (vedi sotto).
- **Sessioni del portale anteriori a R06** (token senza claim `ses`): valgono come prima fino alla scadenza; i nuovi accessi hanno la scadenza per inattività.
- **Codice fiscale del login di sviluppo** cambiato (`FTZPRV00A01Z999V`): riallineare i database di sviluppo.
- Le pratiche di rettifica e annullamento in corso senza fatto non ottengono la foglia (R05 non è in esercizio: nessun recupero).
- `SESSIONE_INATTIVITA_*` del gateway fra 1 e 120 minuti (il gateway non parte fuori da questo intervallo).

## Variabili, Secret, database
| Dove | Cosa | Note |
|---|---|---|
| Secret nuovo `sftp-ingest-lavoratore-secrets` | `SFTP_TOKEN_CHIAVE_PRIVATA` | Toglierla da `sftp-ingest-secrets` (il server con la chiave non parte in produzione) e **ruotarla**: in R05 era nel Secret del server esposto |
| Secret nuovo `hashchain-verifica-audit-secrets` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Credenziali in sola lettura, vuote con IRSA |
| ConfigMap del gateway | `SESSIONE_INATTIVITA_MINUTI` (30), `SESSIONE_INATTIVITA_PRIVILEGIATI_MINUTI` (15), `SESSIONE_RUOLI_PRIVILEGIATI` | Facoltative |
| Database | gruppo `verumdoc_sessioni_portale` (lo crea la 045 con CREATEROLE) | `GRANT verumdoc_sessioni_portale TO <login>` per i login di gateway, onboarding, dashboard-api, distribution, notification, hashchain e API di ciclo-vita; **non** al server SFTP |
| Cluster | kube-state-metrics | Richiesto dagli allarmi `VerificaAuditFallita` e `VerificaAuditNonEseguita` |

Codici d'errore nuovi: `AUTH-004` (sessione scaduta), `AUTH-041` (credenziali ambigue), `CAT-400-CURSORE`. `GET /auth/sessione` riporta `inattivita_secondi`.

## Migrazione `20261030_045` (head, dopo la 044)
- `esiti_esecuzione`: fatti `ANNULLAMENTO`/`ATTO_ARCHIVIATO` e `RETTIFICA`/`PDV_VERSATO`, vincoli su tipi, coppie, PdA e dettagli (NOT VALID, convalidati in autocommit).
- Tabella `sessioni_attive` con privilegi minimi, trigger `trg_sessioni_attive_ora_server` e vincolo sul limite.
- Riprendibile, `lock_timeout` sui DDL. Il downgrade si rifiuta se esistono fatti di annullamento o rettifica.

## Allarmi (`infra/observability/regole/verumdoc-r06.yml`)
`VerificaAuditFallita` (ultimo Job fallito), `VerificaAuditNonEseguita` (nessuna verifica riuscita da 26 ore), `PuntualeAutorizzazioniNonVerificate` (pagina).

## Scelte da confermare
VD-P-51…56 in `docs/registro/decisioni.md`: limiti e rinnovo della sessione (ADMIN_TENANT a 30 minuti), 400 per credenziali ambigue, gruppo dei servizi del portale per `sessioni_attive`, verifica della richiesta di annullamento e rettifica, CronJob a tentativo unico con kube-state-metrics, dettagli (motivo anche per PA, foto non conservate).

## Residui
- **VD-C-141** (P2): un'API compromessa che agisce come un utente legittimo può scrivere anche cronologia e audit coerenti e ottenere fatto e foglia di annullamento o rettifica per un PdV di quell'ente (come per lo scarto); resta la traccia permanente.
- **VD-C-142** (P3): il CronJob usa ancora il ruolo IAM e il login DB della hashchain, non credenziali in sola lettura proprie.
- Già aperti da R05: firma qualificata (VD-C-068), verifica delle firme (VD-C-073) e gli altri del registro.
