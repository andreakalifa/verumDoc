# CHANGELOG — R17 — Collaudo finale (01/11/2026)

Unione dei due rilasci sviluppati in parallelo sulla base R14 (R15 documentazione di
conformità generata, R16 chiusura dei residui interni) e collaudo finale con le decisioni
confermate dal committente il 01/11/2026 («confermo tutte», documento
`DECISIONI_DA_CONFERMARE.md`): conferma firmata dal gateway delle decisioni sensibili
(VD-P-164), autorizzazione del produttore al riversamento in uscita richiesta come valore
generale (VD-P-155), test indipendenti dalla data della corsa.

- **Base**: R14 (`changelog/CHANGELOG-2026-11-10-rilascio-R14.md`), commit `d56979f`, più R16
  (`changelog/CHANGELOG-2026-11-11-rilascio-R16.md`) e R15
  (`changelog/CHANGELOG-2026-11-11-rilascio-R15.md`), mai consegnati prima. Migrazioni:
  `20261110_057_residui` (R16), `20261111_058_documentazione` (R15, nata 057 e rinumerata
  all'unione), `20261112_059_conferme_decisione` (R17).
- **Unione R15 + R16**: criticità di R15 rinumerate 178–180 → **181–183**, scelte di R15
  165–169 → **185–189** (R16 aveva già 165–172); modelli, gruppi del database, ruoli di
  sviluppo, router di dashboard-api, menu e finto della console uniti; cataloghi, OpenAPI,
  manifest e documenti generati rigenerati; `ruff check .` a zero.
- **Decisioni** (`docs/registro/decisioni.md`): 107 VD-P «Da confermare» del documento →
  «Confermata (01/11/2026, committente)», VD-P-118 con la condizione del parere del DPO,
  VD-P-119 con la condizione della prova di ripristino in staging; VD-P-26 e VD-P-40
  confermate nel testo; VD-P-44…50, VD-DR-05 e VD-P-52, 53, 55, 56 annotate come ratificate il
  30/10/2026. VD-P-117 «Decisa: rinviata»; VD-P-155 e VD-P-164 decise (B) e realizzate. Le
  scelte di R15 (185–189), R16 (165–172) e le nuove di R17 (**VD-P-173…176**) restano «Da
  confermare» perché successive al documento.
- **Criticità**: chiusa VD-C-162; chiuse con riserve VD-C-120 e VD-C-141; nuove VD-C-184
  (conferma consumabile da chi possiede il token nella finestra), VD-C-185 (restituzione,
  chiusura con eccezioni e chiavi API) e VD-C-186 (residui dell'audit finale della console).
  186 registrate: 22 aperte, 4 parzialmente chiuse, 54 chiuse con riserve, 106 chiuse; delle
  26 aperte o parziali 12 sono interne (P3, 2 limiti accettati) e 14 dipendono da risorse
  esterne (`STATO_PROGETTO.md` §3).
- **Scelte da confermare**: VD-P-173…176 (sviluppo) e VD-P-177…179 (correzioni della
  revisione e dell'audit).
- **Audit finale della console**: `docs/audit/AUDIT_FINALE_2026-11-01.md` (solo testo, con lo
  stato dopo le correzioni al §7).
- **Documentazione**: `docs/architettura/sicurezza.md` §10, runbook nuovo
  `docs/runbooks/conferme_decisione.md`, `docs/runbooks/pratica_sospesa.md` (ripresa con la
  conferma), `infra/k8s/secrets/README.md` (R15, R17), catalogo errori, impostazioni, OpenAPI e
  documenti generati, REGOLE §31.

> **Avviso operativo**
> - **Migrazioni 057, 058, 059** con il Job `gateway-migrazioni`; controllo `alembic current`
>   = `20261112_059`. GRANT: `verumdoc_firme_responsabile` e `verumdoc_documentazione` al solo
>   login di dashboard-api, `verumdoc_documentazione_archivio` al solo login della hashchain,
>   `verumdoc_auditor` all'auditor (R15, R16) e **`verumdoc_conferme_registra` al SOLO login
>   dell'API di ciclo-vita** (R17). Senza quest'ultimo ogni decisione sensibile risponde 503.
> - **Chiave delle conferme** (R17): generare una coppia Ed25519 (runbook
>   `conferme_decisione.md`); privata in `gateway-secrets` (`CONFERME_CHIAVE_PRIVATA`), kid in
>   `CONFERME_KID`; pubblica in `CONFERME_CHIAVI_PUBBLICHE` di ciclo-vita (valore
>   `DA_IMPOSTARE` nei values). In produzione il gateway non parte senza chiave e ciclo-vita
>   non parte senza le pubbliche; `CONFERMA_DECISIONI=ATTIVO` (DISATTIVO rifiutato).
> - **Decisioni sensibili dalla console con conferma dell'identità**: approvazione ed
>   esecuzione dello scarto, esecuzione del riversamento in uscita, richiesta e approvazione
>   dell'annullamento, rettifica, ripresa di una pratica sospesa chiedono password e codice (o
>   passkey) poco prima; con solo SPID/CIE vale un accesso di pochi minuti prima. **Le
>   integrazioni con chiave API non chiedono più annullamenti né rettifiche** (avvisare gli
>   enti che lo facevano). Le pratiche aperte al deploy proseguono per le azioni già decise.
> - **Riversamento in uscita** (VD-P-155): dopo il deploy serve l'autorizzazione firmata del
>   produttore; la migrazione toglie il valore NON_RICHIESTA del conservatore e degli enti a
>   cui il conservatore non aveva concesso il limite; per un ente il cui contratto regola già il
>   trasferimento, il conservatore imposta la deroga NON_RICHIESTA dalla console al livello di
>   quell'ente. Le pratiche già aperte mantengono il valore fotografato.
> - **Ripresa d'emergenza via SQL** (runbook `pratica_sospesa.md`): in produzione il lavoratore
>   blocca una pratica rimessa così; la ripresa passa dalla console.
> - R15 e R16: vedere i rispettivi avvisi operativi (`CONSERVATORE_*` in dashboard-api, regole
>   `verumdoc-r15.yml` e `verumdoc-r16.yml`, `REDIS_URL` in `signature-secrets`, revoca delle
>   sessioni al cambio dei metodi d'accesso).

## VD-P-164 — Conferma firmata dal gateway delle decisioni sensibili (VD-C-120, VD-C-141)

- `libs/shared/conferme_decisione.py`: forma canonica (JSON con chiavi ordinate), impronta del
  contenuto deciso per azione, firma Ed25519 con `kid`, lettura e verifica PURA (firma, chiave
  nota e non revocata, azione, pratica, ente, utente, contenuto, versione, scadenza, istante,
  durata ≤ 15 minuti, nonce già consumato); vettori condivisi con la console
  (`libs/shared/tests/vettori_conferme.json`, `frontend/e2e/r17.spec.ts`).
- Gateway: `POST /api/v1/auth/conferme-decisione` (sessione del portale): conferma
  dell'identità recente della sessione (R12b, o accesso recente con solo SPID/CIE) consumata
  con `SET NX` (una conferma = una decisione), azione del ruolo fisso e dell'ente di chi
  chiede, audit `decisioni.conferma_firmata` prima della firma; senza chiave 503 (in
  produzione l'avvio si ferma).
- Ciclo-vita API: campo `conferma` nelle transizioni, nell'annullamento, nell'approvazione,
  nella rettifica e nella ripresa; controllo con le chiavi pubbliche, registrazione in
  `conferme_decisione` nella stessa transazione (`LC-403-CONFERMA`, `LC-503-CONFERMA`);
  `GET /api/v1/ciclo-vita/conferme` (attiva? quali azioni).
- Lavoratore: prima di ogni lavoro distruttivo verifica le conferme richieste con le chiavi
  della propria configurazione (mai dal database), consuma i nonce (`conferme_lavoratore`,
  solo suo), registra le sospensioni (la ripresa richiede una conferma successiva); difetti →
  pratica bloccata (`LC-409-AUTORIZZAZIONI`, difetti `CONFERMA_*`), nessun effetto.
- Migrazione 059: `conferme_decisione` (trigger BEFORE INSERT: istante e versione dal database,
  colonne uguali al payload firmato, ente della pratica; append-only; nonce unico; RLS per
  ente), `conferme_lavoratore` (append-only, nonce unico per le verifiche), 
  `conferme_transizione_r17` (scritta solo dalla migrazione), GRANT minimi per colonna,
  `lock_timeout`.
- Console: dialogo laterale «Conferma la tua identità» (password e codice o passkey; con solo
  SPID/CIE spiega di accedere di nuovo), in un portale; la conferma si chiede solo se il
  servizio la dichiara attiva; chiudere il dialogo non registra nulla e non mostra errori.
  Controllo a 390 e 1280 px in chiaro e scuro.

## VD-P-155 — Autorizzazione del produttore al riversamento in uscita (VD-C-162)

- `RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE`: predefinito RICHIESTA, voce riservata al
  conservatore anche al livello ENTE (la deroga per un singolo ente la scrive solo il
  conservatore; l'ente non se la concede né la toglie); niente più limite per ente.
- Migrazione 059: tolti, con la riga di storico, i valori NON_RICHIESTA che diventerebbero
  deroghe non concesse; resta la deroga di un ente a cui il conservatore aveva concesso il
  limite NON_RICHIESTA.

## Test indipendenti dalla data della corsa

- Hashchain: fixture di sessione che crea nel database di prova le partizioni di
  `audit_global` dei mesi dei dati di prova (dal 01/10/2026 a due mesi dopo oggi), con la
  funzione e il ruolo della manutenzione: i 10 test dell'audit nel WORM non falliscono più dal
  1° novembre. Comportamento di produzione invariato.
- Gateway `test_r04_correzioni_partizioni.py::test_consegna_riaccodata_durante_il_distacco_partizione_riagganciata`:
  contava tutte le partizioni staccate della tabella e simulava la «riprova» al primo distacco
  di qualunque partizione; con un template migrato in un mese precedente (o con partizioni
  scadute in più) la manutenzione staccava prima un'altra partizione vuota: staccate = 1 e
  consegna rimessa in coda prima del controllo giusto. Ora la riprova scatta al distacco della
  sola partizione della prova e le asserzioni guardano solo quella.

## Revisione di sicurezza e audit della console (correzioni R17)

Revisione di sicurezza unica di R15, R16 e R17:

| Gravità | Rilievi | Corretti | Prova |
|---|---|---|---|
| Grave | 0 | — | — |
| Media | 1 — conferma consumata riusata per un nuovo giro | 1 (`GIRO_CHIUSO`, `CONFERMA_GIRO_PRECEDENTE`) | `services/ciclo-vita/tests/test_r17_conferme_decisione.py::test_conferma_consumata_non_vale_per_un_nuovo_giro` (due varianti) |
| Bassa | 4 — pratiche sospese prima della 059; `traceparent` esterno che forzava il campionamento; firma di manuale e piano non riservata al responsabile e fatta sui dati JSON; downgrade della 058 con versioni generate | 4 | `test_r17_migrazione_059.py`, `libs/shared/observability/tests/test_r16_tracciamento.py`, `services/dashboard-api/tests/test_r16_firma_responsabile.py`, `services/gateway/tests/test_r15_migrazione_058.py` |
| **Totale** | **5** | **5** | mutazioni della revisione **8/8** uccise (CR4 e CR5 dopo due test aggiunti) |

Audit funzionale e grafico finale della console (`docs/audit/AUDIT_FINALE_2026-11-01.md`):

| Gravità | Rilievi | Corretti | In parte | Residui (VD-C-186) |
|---|---|---|---|---|
| Alta | 4 | 4 | — | — |
| Media | 14 | 14 | — | — (di R17-06 resta il versamento a lotti, funzione nuova) |
| Bassa | 14 | 9 | 2 (R17-22, R17-26) | 3 (R17-24, 25, 32) |
| **Totale** | **32** | **27** | **2** | **3** |

Rilievi del 20/10/2026 riferiti alla console (34): risolti **23** prima delle correzioni,
**28 dopo** (n. 4, 8, 14, 17, 21); parziali 6 → 4; aperti 5 → 2 (glossario, export).

Sicurezza (nessun rilievo grave):
- **Conferma consumata riusata per un nuovo giro (media).** Il lavoratore registra
  `GIRO_CHIUSO` a fine giro e rifiuta le conferme registrate prima
  (`CONFERMA_GIRO_PRECEDENTE`); la ripresa automatica del SISTEMA si riconosce dal fatto
  `RIPRESA_SISTEMA` scritto dal lavoro periodico, non più da `dati.esecuzioni_precedenti`.
  Vale per esecuzione dello scarto, riversamento in uscita e rettifica. Riproduzione della
  revisione portata nei test (`test_conferma_consumata_non_vale_per_un_nuovo_giro`).
- **Pratiche sospese prima della 059 (bassa).** La migrazione scrive per loro il fatto
  `SOSPESA`; l'esenzione della transizione vale solo per il giro in corso (VD-P-177).
- **Campionamento forzato dall'esterno (bassa).** Il flag «campionato» di un `traceparent`
  remoto non forza più il campionamento; `tracestate` non si accoglie né si propaga.
- **Firma di manuale e piani (bassa).** Solo il ruolo RSC_CONSERVATORE; in firma la versione
  stampabile HTML della documentazione generata (`POST /api/dashboard/firme-responsabile/documentazione/{versione_id}`),
  tolta la preparazione dai dati JSON (VD-P-178).
- **Downgrade della 058 (bassa).** Rifiutato se esistono versioni generate.
- In più (audit R17-02): distribution rifiuta richiesta e rigenerazione di un PdD anche a un
  ruolo fisso senza «esibizione:richiedi» (Consultazione). La sessione del gateway restituisce
  i permessi effettivi (`utente.permessi`).

Console (audit finale, 32 rilievi: corretti 4 alti, 14 medi e 9 bassi; residui in VD-C-186):
- Pagine, voci e comandi dai permessi (VD-P-179): l'operatore chiede esibizioni (R17-01), la
  Consultazione vede lo storico in sola lettura (R17-02) e non apre il versamento (R17-12);
  nessun rinvio muto al quadro (R17-11); la guida generata fallisce se pagina e menu
  divergono (R17-13).
- Firma del manuale (R17-04): «Invia alla firma del responsabile» nella Documentazione del
  servizio; la pagina di firma mostra il documento in una cornice isolata; dal Manuale un
  collegamento alla versione stampabile (R17-17).
- Versamento (R17-05/06): nessuna classe predefinita, valore = codice ed etichetta = nome,
  metadati obbligatori della classe nel modulo. Esibizione (R17-07): il pacchetto si cerca e
  si sceglie, o arriva scelto da «Esibisci» nella scheda del pacchetto.
- Un solo selettore dell'ente (R17-08); sotto-navigazione su più righe sopra i 1024 px e fuoco
  interno alle schede (R17-09/10); catalogo dei formati a 390 px (R17-03); scelte in parole
  (R17-14); ente e classi per nome nella pratica (R17-15); «Messaggi non elaborati» (R17-16);
  date in ora italiana (R17-19/20); «Illimitata» (R17-21); bersagli di 24 px (R17-22);
  «Pratiche» attiva sul dettaglio (R17-23); «Imposto dalla norma, si stabilisce solo qui»
  (R17-27); «Utenti e chiavi API» e «Inviti e metodi d'accesso» (R17-28); oggetto in prima
  riga negli arrivi (R17-29); «Identificativo del pacchetto (PdA)» (R17-30); comando nel vuoto
  della documentazione (R17-31).
- Backend finto degli e2e con i dati di archivio, ricerca, notifiche, SLA, registro,
  manuale, contratti, nomine, code, utenti e chiavi; scope, permessi e azioni dipendenti dal
  ruolo (R17-18); nuove prove `e2e/r17_console.spec.ts`.

## Strumenti di sviluppo (confezione del pacchetto R15–R17)

- `tools/dev/genera_segreti_dev.py` genera anche la coppia Ed25519 delle conferme:
  `infra/dev_conferme.env` (`CONFERME_CHIAVE_PRIVATA`, `CONFERME_KID`, al gateway) e
  `infra/dev_conferme_pub.env` (`CONFERME_CHIAVI_PUBBLICHE`, a ciclo-vita); i file esistenti
  non si toccano (`tools/dev/test_genera_segreti_dev.py::test_coppia_delle_conferme_decisione`).
  Nel compose la conferma resta DISATTIVO; `VD_CONFERME=ATTIVO docker compose up -d` la accende.
- Simulatore CSC: `--personale CREDENTIAL_ID[:PIN]` e `--url-pubblico` (credenziale personale
  del responsabile con l'OTP stampato nel terminale,
  `services/signature/tests/test_r16_firma_personale.py::test_simulatore_da_riga_di_comando_con_credenziale_personale`);
  il compose passa `VD_FIRMA_SEGRETI_JSON` al servizio di firma come `FIRMA_SEGRETI_JSON` e
  risolve `host.docker.internal` anche su Linux.

## Prima del deploy (cumulativo R15–R17, base R14)

1. **Migrazioni** `20261110_057_residui`, `20261111_058_documentazione`,
   `20261112_059_conferme_decisione` con il Job `gateway-migrazioni`; controllo
   `alembic current` = `20261112_059`. Pratiche sospese al deploy: la 059 scrive per loro il
   fatto `SOSPESA` (la ripresa chiede la conferma).
2. **Gruppi del database**: `verumdoc_conferme_registra` al SOLO login dell'API di ciclo-vita
   (senza: ogni decisione sensibile 503); `verumdoc_firme_responsabile` e
   `verumdoc_documentazione` al SOLO login di dashboard-api; `verumdoc_documentazione_archivio`
   al SOLO login della hashchain; `verumdoc_auditor` al login dell'auditor (sola lettura).
3. **Chiavi Ed25519 delle conferme**: coppia generata fuori dal cluster (runbook
   `docs/runbooks/conferme_decisione.md`); privata in `gateway-secrets`
   (`CONFERME_CHIAVE_PRIVATA`), `CONFERME_KID` nella ConfigMap del gateway; pubblica in
   `CONFERME_CHIAVI_PUBBLICHE` di ciclo-vita (API **e** lavoratore, JSON `{kid: chiave}`);
   rotazione con due kid in parallelo e `CONFERME_KID_REVOCATI`. In produzione
   `CONFERMA_DECISIONI=ATTIVO` (vuoto = ATTIVO; `DISATTIVO` rifiutato all'avvio).
4. **Permessi mai delegabili**: `documentazione:genera` (nuovo, R15) e `pratica:riprendi`
   (R14, ora con la conferma) solo ai ruoli fissi del conservatore; il vincolo
   `ck_ruoli_personalizzati_non_delegabili` li comprende entrambi.
5. **Avvisare gli enti** che chiedono annullamenti o rettifiche con una chiave API: con la
   conferma attiva non è più possibile (`LC-403-CONFERMA`), le decide una persona dalla console.
6. **Ripresa d'emergenza via SQL non più possibile**: in produzione il lavoratore blocca una
   pratica rimessa così; la ripresa passa dalla console con la conferma.
7. **VD-P-155**: riversamento in uscita con autorizzazione firmata del produttore
   predefinita RICHIESTA; la migrazione toglie i NON_RICHIESTA non concessi; deroga per un
   ente solo dal conservatore, in console al livello di quell'ente.
8. **Redis per il limite delle prove di firma**: `REDIS_URL` in `signature-secrets` (senza, in
   produzione il servizio di firma non parte).
9. **Tracce OpenTelemetry** (facoltative, spente): `OTEL_TRACCE_ATTIVE`,
   `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_CAMPIONAMENTO` (0–1, predefinito 0,1; vale anche per le
   richieste che arrivano con un `traceparent` esterno).
10. **Dati del conservatore** `CONSERVATORE_*` nel Secret di dashboard-api; regole Prometheus
    `verumdoc-r15.yml` e `verumdoc-r16.yml`; sessioni revocate al cambio dei metodi d'accesso
    (avvisare prima di cambiare quelli del conservatore); Message-ID PEC nel nuovo formato per i
    messaggi prelevati dopo il deploy; profilo v3 del documento informatico solo ad adozione
    esplicita.
11. Chart: **tutte le immagini** (cambiano `libs/shared` e il codice di ogni servizio, anche
    per il lint a zero di R16) e il frontend.

## Test

PostgreSQL reale, database `r17_integ` migrato da zero a `20261112_059`, login non proprietario
dei servizi: **4.582 test Python passati al primo colpo, 0 falliti** (3 saltati XSD); frontend
typecheck, lint, build, e2e 123 con 2 lavoratori; `make verifiche` 0; `ruff check .` 0;
prove di mutazione sui punti della conferma e di VD-P-155 **47/47** uccise. Dettaglio in
`STATO_PROGETTO.md` §4.

Correzioni della revisione: suite toccate su `r17_integ` ricreato alla 059 (gateway 839,
ciclo-vita 276, dashboard-api 274, distribution 165, shared 499, rules 261, conformita 21,
tools/docs 30, integrity 11) tutte verdi; e2e **152** con 2 lavoratori; `make verifiche` 0;
ruff 0; mutazioni della revisione **8/8**.
