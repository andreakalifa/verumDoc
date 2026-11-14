# CHANGELOG — R10 — Accesso, ruoli e notifiche (30/10/2026)

Quarto rilascio della roadmap R07–R13 (decisioni D08, D12, D30, D32, opzione consigliata). Prerequisito: R09 applicato (migrazioni fino a `20261101_048`). Migrazione nuova: `20261102_049` (riprendibile, `lock_timeout`, vincoli e chiave esterna `NOT VALID` convalidati in autocommit, indice su `users` CONCURRENTLY fuori dalla transazione e senza CONCURRENTLY nello script offline). Criticità chiusa con riserve: VD-C-107 (prova sull'NLB reale: VD-C-154). VD-C-069 resta aperta (contratto con l'aggregatore: decisione commerciale) con il codice collegato alla console. Nuove: VD-C-152…156. Scelte da confermare: VD-P-93…104.

> **Avviso operativo**
> - **Da fare prima del deploy**: segreto `CREDENZIALI_CHIAVE_CIFRATURA` del gateway (32 byte casuali in base64; senza, l'accesso con password e codice a tempo risponde 503 ACC-503) e `GRANT verumdoc_credenziali_accesso TO <login del gateway>` (il gruppo lo crea la migrazione 049 se il proprietario ha CREATEROLE; il login del gateway è l'unico membro).
> - **SFTP: bilanciatore e sftp-ingest nello stesso deploy**: l'NLB ha ora l'annotazione `aws-load-balancer-proxy-protocol: "*"` e sftp-ingest pretende l'intestazione PROXY dalle reti `SFTP_PROXY_SORGENTI_FIDATE` (values: le sole sottoreti dell'NLB, prod `10.0.0.0/20`, `10.0.16.0/20`, `10.0.32.0/20`, staging `10.1.x`, uguali a `network.nlbSourceCidrs`). Vanno applicati INSIEME (Chart unico): con uno solo dei due il canale SFTP non accetta connessioni.
> - **Reti fidate da allineare alle sottoreti reali**: `PROXY_FIDATI` del gateway (= `network.albSourceCidrs`, le sottoreti dell'ALB) e i CIDR dell'NLB (`nlbSourceCidrs` = `SFTP_PROXY_SORGENTI_FIDATE`) vanno controllati contro le sottoreti vere dell'account AWS prima del deploy; si correggono a coppie (il controllo dei manifest le vuole uguali). Il gateway non parte con reti più ampie di /8, sftp-ingest con reti più ampie di /20 (/48 in IPv6). Senza `PROXY_FIDATI` fuori da sviluppo e test il limite per IP è spento (log d'avvio `accesso_limite_ip_disattivato`).
> - **Rinomina**: `ACCESSO_TENTATIVI_IP_15_MINUTI` (nome della prima versione di R10) diventa `ACCESSO_FALLIMENTI_IP_15_MINUTI` (30, contano solo i tentativi sbagliati); nuovo `ACCESSO_OPZIONI_PASSKEY_IP_15_MINUTI` (300). Chi aveva sovrascritto il vecchio nome lo aggiorni: il vecchio è ignorato.
> - **Limite per IP e accessi contemporanei**: ogni richiesta d'accesso riserva un tentativo prima di essere eseguita (revisione U1), quindi con il limite a 30 si hanno al più **30 accessi contemporanei dallo stesso IP reale**; oltre si riceve 429 `ACC-429` finché le richieste in corso non rispondono. Un ente con molte persone dietro un unico NAT che entrano insieme può richiedere di alzare il valore.
> - **Funzioni nuove spente per impostazione predefinita** (i cambi visibili sono in «Cambi incompatibili»): metodo d'accesso predefinito SPID_CIE, nessun ruolo personalizzato, nessun avviso obbligatorio, elenco SFTP facoltativo e vuoto, posta al referente come prima.

## Per chi usa la console
- **Campanella «Da fare»** in testata: pratiche da approvare (annullamento come seconda persona, scarto da autorizzare), proposte di scarto, annullamenti non applicati, quote vicine al limite, versamenti rifiutati in scadenza, allarmi d'integrità; ognuno vede solo ciò che riguarda il suo ruolo. «Segna tutto come letto», «Scegli cosa ricevere».
- **Preferenze → «Cosa ricevere»** (`/preferenze/notifiche`): per categoria, nella console e/o per e-mail; gli avvisi resi obbligatori dall'ente sono spuntati e non si tolgono.
- **Enti → «Accessi e inviti»** (`/utenti/inviti`): metodi d'accesso ammessi per l'ente; inviti per e-mail con stato (in attesa, attivato, revocato, scaduto), «Invia di nuovo», «Revoca». Il collegamento arriva solo alla persona.
- **Enti → «Ruoli personalizzati»** (`/utenti/ruoli`): ruoli che partono da «Operatore» o «Consultazione» con i permessi concedibili; le azioni mai delegabili sono spente con il motivo. Il ruolo si assegna da «Utenti e accessi» o nell'invito.
- **Configurazione → «Accesso e notifiche»** (`/configurazione/accesso`): schede «Metodi d'accesso», «Ruoli personalizzati», «Avvisi obbligatori», «Indirizzi SFTP» (stessa console delle impostazioni: bozza, anteprima, applicazione, verso «più sicuro»); per il conservatore, **«Fornitori d'identità»** (SPID/CIE con aggregatore, passkey, password e codice a tempo; nessun segreto in console).
- **Canali → Account SFTP**: «Indirizzi ammessi» per account.
- **Pagina d'accesso**: «Accesso senza SPID» (password e codice a tempo, o codice di recupero; «Entra con una passkey») se il conservatore lo abilita. **Attivazione** (`/attiva`): dal collegamento dell'e-mail, indirizzo e-mail, metodo, password e codice a tempo con i codici di recupero da conservare, o passkey, o SPID/CIE.

## Per chi gestisce il servizio
- Parametri nuovi del motore (gruppi `accesso`, `ruoli`, `notifiche`, `sftp`): `ACCESSO_METODI_AMMESSI` (SOTTOINSIEME, LIMITE per ente), `INVITO_VALIDITA_ORE` (72), `ACCESSO_TENTATIVI_MASSIMI` (5), `ACCESSO_BLOCCO_MINUTI` (15), `PASSWORD_LUNGHEZZA_MINIMA` (12), `RUOLI_PERMESSI_CONCEDIBILI` (riservato), `NOTIFICHE_OBBLIGATORIE` (SOVRAINSIEME, nuovo verso), `SFTP_ALLOWLIST_VOCI_MASSIME` (20), `SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4/IPV6` (16/48), `SFTP_ALLOWLIST_OBBLIGATORIA` (FACOLTATIVA).
- Motore: i versi SOTTOINSIEME (ora anche per le voci del cliente) e SOVRAINSIEME confrontano per inclusione e risolvono con intersezione/unione.
- Impostazioni d'ambiente nuove: gateway `CREDENZIALI_CHIAVE_CIFRATURA` (+ `_ID`, `_PRECEDENTI` per la rotazione), `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NOME`, `WEBAUTHN_ORIGINI`, `BASE_URL_CONSOLE`, `PROXY_FIDATI`, `ACCESSO_FALLIMENTI_IP_15_MINUTI`, `ACCESSO_OPZIONI_PASSKEY_IP_15_MINUTI`; onboarding `BASE_URL_CONSOLE`; sftp-ingest `SFTP_PROXY_SORGENTI_FIDATE`, `SFTP_PROXY_ATTESA_SECONDI`.
- Ingress: `/api/v1/notifiche` → notification. Nessun topic Kafka nuovo: inviti sul topic esistente `notification.send`; la posta degli avvisi legge anche `integrita.anomalia` (solo per chi la sceglie).
- Dipendenze del gateway: `pyotp==2.10.0` (MIT), `webauthn==3.0.1` (BSD-3-Clause; cbor2, pyOpenSSL, pyasn1). La cifratura AES-GCM dei segreti è ora in `libs/shared/security/cifratura.py` (una per webhook e TOTP).

## Contenuto
- **D08 accesso**: `services/gateway/app/auth/fornitori.py` (fornitori e metodi per ente; SPID/CIE dalla console, `SPID_*` come prima senza riga), `emissione.py` (una sola emissione della sessione per tutti i metodi), `credenziali.py` (bcrypt, TOTP con anti-riuso, codici di recupero, WebAuthn), `accesso_locale.py` (rotte d'accesso e attivazione; passaggi in Redis legati al browser, limiti per account e IP, nessuna enumerazione); scambio SPID/CIE filtrato per i metodi dell'ente. Onboarding: `routers/accesso.py` (fornitori, vista dell'ente), `routers/inviti.py` (inviti sull'outbox, reinvio dopo una disattivazione con sostituzione delle credenziali).
- **D30 ruoli**: catalogo puro `libs/verumdoc_rules/permessi.py` (permessi, modelli, mai delegabili, composizione senza escalation), `libs/shared/security/permessi.py` (permessi del token), revoca con `rp`/`pv` (`libs/shared/security/revoca.py`), `onboarding/app/routers/ruoli.py`, assegnazione in `utenti.py` e negli inviti; controlli in ciclo-vita (annullamento, rettifica, pratiche massive, approvazione), distribution (richiesta di PdD), gateway (scope dai permessi).
- **D32 notifiche**: `notification/app/domain/campanella.py` (categorie e permessi), `api/campanella.py` (campanella, letti, preferenze), posta per persona in `services/destinatari.py`.
- **D12 SFTP**: `libs/shared/security/proxy_protocol.py`, `libs/shared/security/rete.py`, `sftp-ingest` (`AscoltoProxy` con `asyncssh.run_server`, controllo dell'elenco, audit con IP), `onboarding` (`PUT …/ip-ammessi`), Helm (annotazione NLB, reti fidate).
- Migrazione `20261102_049`: `fornitori_identita`, `ruoli_personalizzati` (RLS, vincolo dei mai delegabili), `users.ruolo_personalizzato_id` (chiave esterna composta) e `permessi_versione`, credenziali (solo `verumdoc_credenziali_accesso`), `inviti`, `notifiche_preferenze`, `notifiche_letture` (RLS), `sftp_account.ip_ammessi`.
- Codici d'errore nuovi: famiglie ACC (401, 403-METODO, 400-PASSAGGIO, 404-INVITO, 409-INVITO, 422-PASSWORD, 422-CODICE, 400-PASSKEY, 422-FORNITORE, 429, 503), RUO (403, 404, 409), NTF (409, 422-OBBLIGATORIA, 503), SFTP-422-IP.
- Frontend: `Campanella`, pagine `ConfAccessoPage`, `InvitiPage`, `RuoliPage`, `NotifichePreferenzePage`, `AttivaPage`, accesso locale in `LoginPage`, indirizzi in `CanaliSftpPage`; e2e `frontend/e2e/r10.spec.ts`.

## Test
- Nuovi: `services/gateway/tests/test_r10_accesso_locale.py` (9: invito, password e TOTP, codici di recupero, blocco, anti-riuso, metodi dell'ente, passkey con autenticatore di prova, privilegi delle credenziali, ruolo personalizzato e revoca), `services/onboarding/tests/test_r10_ruoli_inviti.py` (5), `services/notification/tests/test_r10_campanella.py` (2), `services/sftp-ingest/tests/test_r10_proxy_allowlist.py` (5), `services/ciclo-vita/tests/test_r10_ruoli_personalizzati.py` (2), `libs/verumdoc_rules/tests/test_r10_accesso_parametri.py` (11), `libs/shared/security/tests/test_r10_proxy_rete_permessi.py` (17), `frontend/e2e/r10.spec.ts` (6).
- 28 prove di mutazione sui punti di sicurezza (escalation, mai delegabili, token d'invito, TOTP/passkey, elenco IP e PROXY protocol), tutte uccise.

## Correzioni della revisione indipendente (V1–V4) e del secondo giro (U1)
- **V1 (grave)**: limite d'accesso per IP REALE (`libs/shared/security/ip_cliente.py`: `X-Forwarded-For` solo da `PROXY_FIDATI`, primo hop non fidato da destra), contati solo i fallimenti, contatore separato e più largo per la sfida passkey anonima, nessun contatore unico possibile (senza proxy fidati in produzione il limite per IP è spento, con avviso). Stesso IP nel log delle richieste del gateway. VD-P-103.
- **V2**: reinvio e revoca d'invito con le regole della gestione utenti (l'ADMIN dell'ente non tocca un ADMIN; ruolo personalizzato ancora assegnabile).
- **V3**: permessi dei ruoli personalizzati su fascicoli, rigenerazione PdD e «Rielabora» con il controllo centrale `motivo_permesso_mancante`; test statico sugli elenchi di ruoli con OPERATORE. VD-P-104.
- **V4**: reti fidate del PROXY protocol = sottoreti dell'NLB (values di produzione e staging), coerenza con `nlbSourceCidrs` controllata nei manifest, reti più ampie di /20 rifiutate all'avvio di sftp-ingest.
- **U1 (secondo giro)**: il limite per IP riserva il tentativo con un INCR atomico PRIMA della rotta (429 oltre il limite, riserva restituita) e lo restituisce (DECR) se l'esito non è un fallimento: richieste parallele non aggirano più il limite. Test statico V3 anche rotta per rotta (dipendenze e funzioni usate nello stesso modulo).
- 14 prove di mutazione sulle correzioni V1–V3 e U1, tutte uccise.

## Criticità
Chiusa con riserve: VD-C-107 (PROXY protocol, indirizzi ammessi per account e IP reale nei log; la prova con l'NLB AWS reale resta in VD-C-154). VD-C-069 resta aperta: il codice SPID/CIE è collegato alla console, manca il contratto con un aggregatore qualificato (decisione commerciale). Nuove: VD-C-152…156 (vedi Residui). Scelte di dettaglio da confermare: VD-P-93…104.

Totale 156: 26 aperte, 12 parzialmente chiuse, 28 chiuse con riserve, 90 chiuse.

**Fonti**:
- rapporto di sviluppo R10;
- revisione indipendente in due giri: il primo con V1–V4 (1 grave, 3 medi), il secondo mirato sulle correzioni con U1 (1 medio). Tutti i rilievi sono stati corretti prima del rilascio, con test permanenti (le prove del revisore diventano test di regressione);
- prove di mutazione: 28 su 28 uccise nello sviluppo, 14 su 14 nella correzione (V1–V3, U1).

---

## Revisione indipendente: rilievi e correzioni

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| **V1** Limite per IP dell'accesso locale contato sull'IP dell'ALB: in pratica un limite globale (30 errori di chiunque bloccavano tutti) | Grave | IP reale da `libs/shared/security/ip_cliente.py` (`X-Forwarded-For` solo da `PROXY_FIDATI` = sottoreti dell'ALB, primo hop non fidato da destra); contano solo i fallimenti (`ACCESSO_FALLIMENTI_IP_15_MINUTI`, 30); sfida passkey anonima su un contatore separato (`ACCESSO_OPZIONI_PASSKEY_IP_15_MINUTI`, 300); senza proxy fidati in produzione limite per IP spento con errore nel log d'avvio, mai un contatore unico; stesso IP nel log `http_request`. VD-P-103 | `services/gateway/tests/test_r10_accesso_locale.py::test_v1_*` (6, compresa `test_v1_regressione_nessun_blocco_globale`) |
| **V2** Reinvio d'invito: l'ADMIN dell'ente poteva riattivare un altro ADMIN (aggira la regola A1) | Medio | `inviti.py::_persona_gestibile` per reinvio e revoca: l'ADMIN dell'ente solo su OPERATORE/VIEWER (con ruolo personalizzato ancora assegnabile), mai su un ADMIN (404 come `/utenti`); il conservatore sì | `services/onboarding/tests/test_r10_ruoli_inviti.py::test_v2_*` (2) |
| **V3** Permessi del ruolo personalizzato ignorati sugli endpoint che autorizzano per ruolo base OPERATORE | Medio | Controllo centrale `motivo_permesso_mancante` (`libs/shared/security/permessi.py`) su fascicoli (versamento:esegui, `FSC-403-RUOLO`), richiesta e rigenerazione PdD (esibizione:richiedi, `DST-403`), «Rielabora» (versamento:esegui, `AUTH-010`); test statico per modulo e, dopo il secondo giro, rotta per rotta. VD-P-104 | `services/distribution/tests/test_r10_permessi_ruoli.py` (2), `services/dashboard-api/tests/test_r10_permessi_ruoli.py` (1), `libs/shared/security/tests/test_r10_permessi_endpoint.py` (3) |
| **V4** `SFTP_PROXY_SORGENTI_FIDATE` = intera VPC: IP del cliente falsificabile da un pod, elenco degli indirizzi aggirabile | Medio | Reti fidate = sole sottoreti dell'NLB, uguali a `network.nlbSourceCidrs` e alla NetworkPolicy (controllo in `verifica_manifest.py`); sftp-ingest non parte con reti più ampie di /20 (/48 in IPv6). VD-P-103 | `services/sftp-ingest/tests/test_r10_proxy_allowlist.py::test_v4_reti_fidate_troppo_ampie_fermano_l_avvio`, `infra/k8s/test_verifica_manifest.py::test_r10_reti_fidate_uguali_alle_subnet_dei_bilanciatori` |
| **U1** (secondo giro) Limite dei fallimenti per IP aggirabile con richieste in parallelo: controllo (`GET`) e conteggio (`INCR` dopo l'esito) non atomici | Medio | `_riserva_tentativo_ip`: INCR atomico PRIMA della rotta (oltre il limite DECR e 429); la riserva resta solo per un esito in `FALLIMENTI`, altrimenti si restituisce in `finally` (DECR, chiave cancellata se negativa). Le richieste in corso occupano il limite finché non rispondono | `services/gateway/tests/test_r10_accesso_locale.py::test_u1_fallimenti_in_parallelo_non_aggirano_il_limite` (60 richieste parallele, limite 5: esattamente 5×401 e 55×429) |

## Cambi incompatibili
- **SFTP**: dalle reti fidate la connessione senza intestazione PROXY si chiude dopo `SFTP_PROXY_ATTESA_SECONDI`; NLB con proxy protocol e sftp-ingest R10 vanno insieme (un NLB senza annotazione davanti a sftp-ingest R10, o il contrario, blocca il canale).
- **Gateway, avvio**: `PROXY_FIDATI` con reti più ampie di /8 ferma l'avvio; sftp-ingest si ferma con `SFTP_PROXY_SORGENTI_FIDATE` più ampie di /20 o /48.
- **Limite d'accesso per IP**: conta l'IP reale e solo i fallimenti; al più 30 accessi contemporanei dallo stesso IP (429 `ACC-429` oltre); `ACCESSO_TENTATIVI_IP_15_MINUTI` rinominata `ACCESSO_FALLIMENTI_IP_15_MINUTI`.
- **Log delle richieste del gateway**: il campo dell'IP è ora l'IP reale del cliente (prima quello dell'ALB); eventuali filtri o cruscotti sul vecchio valore vanno rivisti.
- **Token**: nuove voci `permessi`, `rp`, `pv`; assegnazione, modifica o tolta di un ruolo personalizzato revocano subito le sessioni della persona (serve un nuovo accesso).
- **Database**: le tabelle delle credenziali non sono leggibili da `verumdoc_applicazione`; serve il gruppo `verumdoc_credenziali_accesso` per il solo login del gateway.

## Cosa è simulato
- **Aggregatore SPID/CIE**: il simulatore esistente (JWKS e token firmati nel test); nessun aggregatore qualificato (VD-C-069).
- **Posta**: outbox e topic `notification.send`; nessun invio SMTP reale degli inviti nei test.
- **Autenticatore passkey**: chiave P-256 generata a runtime (`services/gateway/tests/autenticatore_prova.py`), nessun dispositivo vero.
- **Bilanciatore**: intestazioni PROXY v1/v2 e `X-Forwarded-For` scritte dai test, su socket verso il server asyncssh vero; nessun NLB o ALB AWS reale (VD-C-154).

## Migrazione `20261102_049`
Riprendibile (`lock_timeout`, vincoli e chiave esterna `NOT VALID` convalidati in autocommit, indice su `users` CONCURRENTLY fuori dalla transazione). Contenuto: vedi «Contenuto». Provata in salita sui DB d'integrazione `r10_integ` (template R09 con le correzioni W).

## File eliminati o spostati
Nessuno: in R10 nessun file del progetto diventa superfluo e `tools/pulizia/pulisci_progetto.py` è invariato. La cifratura AES-GCM è stata spostata in `libs/shared/security/cifratura.py`; `services/notification/app/infrastructure/cifratura.py` resta come riesportazione usata dal servizio.

## Scelte da confermare
VD-P-93…104 in `docs/registro/decisioni.md`. VD-P-93…102 vengono dallo sviluppo, VD-P-103 (IP reale e limite per IP, V1, V4) e VD-P-104 (permessi dei ruoli personalizzati sugli endpoint per ruolo base, V3) dalla correzione della revisione.

## Residui
- **VD-C-069** (aperta): nessun aggregatore SPID/CIE qualificato contrattualizzato; il codice è pronto e collegato alla console.
- **VD-C-152**: il collegamento d'invito passa in chiaro da outbox e coda della posta finché l'e-mail non parte.
- **VD-C-153**: riduzione dei permessi concedibili e cambio dei metodi ammessi valgono dal prossimo accesso (limite accettato, VD-P-97).
- **VD-C-154**: PROXY protocol e `X-Forwarded-For` non provati con NLB e ALB AWS reali (R12).
- **VD-C-155**: il lavoratore di ciclo-vita riverifica l'annullamento con il ruolo base, non con i permessi del ruolo personalizzato.
- **VD-C-156**: gestione delle proprie credenziali (seconda passkey, cambio password, nuovi codici di recupero) non ancora in console.
- **VD-C-149** (indicata per R10 nel registro) non è stata affrontata: resta aperta, da pianificare.
- Restano le riserve R08 (EU DSS VD-C-143, veraPDF) e R09 (VD-C-148, 150, 151).

## Test eseguiti
Corsa completa dello sviluppo (migrazione a `20261102_049`, PostgreSQL reale): frontend typecheck, lint, build ed e2e (**53**); `make verifiche` ok; 4 suite con errori (elenchi dei test non aggiornati e uno script di prova sul template sbagliato, nessun difetto di R10) corrette e rieseguite intere. Dopo la correzione V1–V4 rieseguite intere gateway, onboarding, distribution, dashboard-api, sftp-ingest, libs/shared, infra/k8s; dopo U1 gateway e libs/shared; `make verifiche` ok a ogni giro. Totale (ultima esecuzione di ogni suite) **3.941 passati, 0 falliti** (2 saltati facoltativi), più 53 end-to-end. Mutazioni: 28 su 28 nello sviluppo, 14 su 14 nella correzione.
