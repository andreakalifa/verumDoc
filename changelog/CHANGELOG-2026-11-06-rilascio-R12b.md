# CHANGELOG — R12b — Le mie credenziali e residui di R12a (31/10/2026)

Rilascio intermedio prima di R12 (prova su infrastruttura reale, ancora senza account cloud). Prerequisito: R12a applicato (migrazioni fino a `20261105_052`). Migrazione nuova: `20261106_053` (riprendibile, `lock_timeout`, vincoli `NOT VALID` convalidati in autocommit; nessun diritto nuovo). Criticità: VD-C-161 chiusa, VD-C-156 e VD-C-160 chiuse con riserve; nuove VD-C-162, 163 (residui, da decidere) e 164 (trovata e chiusa). Scelte da confermare: VD-P-123…129.

> **Avviso operativo**
> - **Secret della posta spostato (VD-C-161)**: `POSTA_CHIAVE_PRIVATA`, `POSTA_CHIAVI_PRIVATE_PRECEDENTI`, `SMTP_UTENTE` e `SMTP_PASSWORD`, più una copia di `DATABASE_URL` (il lavoratore NON riceve `notification-secrets`), vanno in **`notification-posta-secrets`** (lavoratore nuovo `notification-posta`) e si TOLGONO da `notification-secrets`: con una di queste chiavi le repliche API di notification in produzione e staging **non partono** (log `configurazione dei webhook non valida: … VD-C-161`). Senza `POSTA_CHIAVE_PRIVATA` valida il lavoratore della posta non parte.
> - **Permesso Kafka (VD-C-164)**: `notification.send` ha ora come produttori anche `onboarding` (mancava: in esercizio inviti e avvisi programmati sarebbero rimasti nell'outbox) e `gateway` (avvisi delle credenziali). Il ruolo IAM si rigenera con Terraform dagli stessi file prima del Chart.
> - **Restituzione legata alla pratica (VD-C-160)**: l'autorizzazione del produttore caricata da ora deve riportare l'identificativo della pratica nel testo firmato; quelle già caricate in restituzioni aperte passano con avviso (VD-P-124).
> - **Gestione delle proprie credenziali (VD-C-156)**: attiva dove l'ente ammette passkey o password con codice a tempo; con solo SPID/CIE la pagina lo spiega. Nessuna configurazione da fare: la finestra della conferma è il parametro `ACCESSO_RIAUTENTICAZIONE_MINUTI` (5).

## Prima del deploy
Da fare in quest'ordine, su staging e poi su produzione:
1. **Secret** `notification-posta-secrets` con `DATABASE_URL` (stesso login di notification), `POSTA_CHIAVE_PRIVATA` (la stessa di R12a), le eventuali chiavi precedenti, `SMTP_UTENTE` e `SMTP_PASSWORD`; poi **togliere** le stesse chiavi da `notification-secrets` (modelli in `infra/k8s/secrets/`, checklist `docs/architettura/giorno1_staging.md` §5.2).
2. **Terraform** (`iam_servizi`): permesso di scrittura su `notification.send` per onboarding e gateway (da `infra/kafka/topics.yaml`), applicato PRIMA del Chart: senza, gli eventi di gateway e onboarding restano nell'outbox.
3. **Migrazione** con il Job `gateway-migrazioni`: `20261106_053`. Controllo: `alembic current` = `20261106_053`.
4. **Chart**: gateway, notification (API, `notification-posta`, `notification-webhook`), ciclo-vita (API e lavoratore), frontend.
5. **Controlli dopo il deploy**: un invito di prova arriva (lo invia `notification-posta`, log `lavoratore_posta_avviato`); `notification` e `notification-webhook` senza uscita verso le porte SMTP; una modifica di prova delle proprie credenziali produce l'e-mail di avviso.
6. **Enti con restituzioni in corso**: avvisarli che le nuove autorizzazioni devono riportare l'identificativo della pratica. Le autorizzazioni già caricate in restituzioni aperte passano con avviso (VD-P-124).

Nota: helm, kubectl e terraform veri NON sono stati eseguiti in sviluppo (non scaricabili); il Chart è stato reso con il renderer minimo e validato con kubeconform, Terraform non rieseguito. I passi 1–4 vanno provati prima su staging.

## Per chi usa la console
- **Preferenze → «Le mie credenziali»** (`/preferenze/credenziali`): passkey (aggiungerne una, per esempio di riserva su un altro dispositivo; cambiarle nome; revocarla, mai l'ultima con cui si accede), cambio della password (con quella attuale), nuovo codice a tempo (attivo dopo un codice valido), nuovi codici di recupero (mostrati una sola volta). Ogni modifica chiede prima di confermare l'identità (password e codice, codice di recupero o passkey), valida per pochi minuti e solo in quella finestra del browser. Dopo il cambio della password, del codice a tempo o la revoca di una passkey le altre sessioni aperte si chiudono; a ogni modifica arriva un'e-mail di avviso.
- **Pratica di restituzione**: accanto al caricamento dell'autorizzazione la console mostra l'identificativo della pratica da riportare nel documento firmato.

## Per chi gestisce il servizio
- Parametro nuovo del motore (gruppo `accesso`): `ACCESSO_RIAUTENTICAZIONE_MINUTI` (5, da 1 a 15, verso MINORE: l'ente può solo ridurlo).
- Impostazione nuova di notification: `POSTA_INVIO_ATTIVO` (true predefinito: in sviluppo un processo fa tutto; nel Chart false nel servizio e true nel lavoratore `notification-posta`, comando `python -m app.lavoratore_posta`, senza HTTP).
- Codici d'errore nuovi: `ACC-403-CREDENZIALI`, `ACC-403-RIAUTENTICAZIONE`, `ACC-422-RIAUTENTICAZIONE`, `ACC-404-CREDENZIALE`, `ACC-409-ULTIMA` (`docs/catalogo/errori.md`).
- Audit nuovi: `credenziali.riautenticazione`, `credenziali.passkey_aggiunta`, `credenziali.passkey_rinominata`, `credenziali.passkey_revocata`, `credenziali.password_cambiata`, `credenziali.totp_rigenerato`, `credenziali.codici_recupero_rigenerati`; per la restituzione `ciclo_vita.restituzione.autorizzazioni_non_verificate`.
- E-mail nuova: `accesso.credenziali_modificate` (outbox del gateway, topic `notification.send`).

## Contenuto

### VD-C-156 — le mie credenziali (chiusa con riserve)
- Gateway `services/gateway/app/auth/credenziali_utente.py`, rotte `/api/v1/auth/credenziali`: stato senza segreti; conferma dell'identità (`/riautenticazione`, anche con passkey: `/riautenticazione/passkey/opzioni`); passkey (`/passkey/opzioni`, `POST /passkey`, `PATCH`/`DELETE /passkey/{id}`); `PUT /password`; `POST /totp` e `/totp/conferma`; `POST /codici-recupero` (`Cache-Control: no-store`).
- Solo da una sessione personale del portale (claim `ses`), mai con chiavi API o token di canale; metodi ATTUALI dell'ente (solo SPID/CIE → 403 `ACC-403-CREDENZIALI`).
- Conferma dell'identità legata alla SESSIONE (impronta del token) per `ACCESSO_RIAUTENTICAZIONE_MINUTI`; Redis non raggiungibile → 503 (VD-P-125).
- Tentativi come riserva atomica PRIMA della verifica, per IP reale e per persona, restituita se l'esito non è un errore; blocco dell'account come all'accesso (VD-P-126).
- Ultima credenziale: righe della persona bloccate prima del conteggio, metodi attuali dell'ente, SPID/CIE solo con il codice fiscale (VD-P-129).
- Audit, avviso per e-mail (VD-P-127) e chiusura delle altre sessioni (VD-P-123) nella transazione della modifica.
- Frontend: `CredenzialiPage` (`/preferenze/credenziali`), collegamento da «Preferenze», `console/credenziali.ts`; e2e `frontend/e2e/r12b.spec.ts`.

### VD-C-160 — restituzione legata alla pratica (chiusa con riserve)
- `firme.TIPI_PRATICA_LEGATI` = scarto e restituzione: al caricamento l'autorizzazione deve citare la pratica nel contenuto firmato (stesse regole X12/Y2).
- Lavoratore (`autorizzazioni.TIPI_VERIFICATI`): prima di esportare una restituzione riverifica dal WORM iter, firma, firmatario autorizzato, riferimento, riuso (solo tipi autorizzativi, solo pratiche dello stesso tipo, solo il documento caricato prima: la logica corretta in R12a) e che ogni oggetto sia un PdA dell'ente; difetto → pratica bloccata, nessun PdD chiesto; fatto `RESTITUZIONE`/`AUTORIZZAZIONI_VERIFICATE`.
- Transizione (VD-P-124): migrazione 053 → elenco `ciclo_vita_transizione_r12` delle autorizzazioni delle restituzioni aperte.
- Riserva: VD-C-162 (riversamento verso un altro conservatore senza autorizzazione del produttore).

### VD-C-161 — posta nel lavoratore dedicato (chiusa)
- `services/notification/app/lavoratore_posta.py` (ciclo di invio spostato da `app.main`, producer della sola coda di scarto), `configurazione.problemi_posta` (controllo d'avvio coerente con `POSTA_INVIO_ATTIVO`), `app.main` avvia l'invio solo con `POSTA_INVIO_ATTIVO=true` e in `/api/v1/info` rimanda al lavoratore.
- Helm: `workers.posta` (comando, `http: false`, `disattivaNelPadre: POSTA_INVIO_ATTIVO`, SOLO il Secret proprio con `segretiServizio: false` e `DATABASE_URL` da `envSegreti`, uscita solo 587/465; `topologia.py` applica `segretiServizio` anche ai lavoratori, `verifica_manifest.SOLO_SEGRETI_PROPRI`); il servizio senza uscita su Internet; il lavoratore dei webhook solo 443. `verifica_manifest.CREDENZIALI_SOLO_LAVORATORE["notification"]`. Manifest kustomize rigenerati (Deployment, NetworkPolicy e modello del Secret di `notification-posta`).
- VD-P-128: anche le credenziali SMTP solo al lavoratore; l'accodamento resta nelle repliche API.

### VD-C-164 — produttori di `notification.send` (chiusa)
- `infra/kafka/topics.yaml`: `producers: [dashboard-api, onboarding, gateway]`.

## Migrazione `20261106_053`
Vincoli di `esiti_esecuzione` (tipo `RESTITUZIONE`, coppia `RESTITUZIONE`/`AUTORIZZAZIONI_VERIFICATE`) tolti e rimessi `NOT VALID`, convalidati in autocommit (in transazione nello script offline); elenco di transizione con `ON CONFLICT DO NOTHING`. Nessun diritto nuovo. Downgrade rifiutato se esistono fatti `RESTITUZIONE`. Provata in salita, discesa e di nuovo salita su `r12b_dev` e in salita su `r12b_integ`.

## Strumenti
Chart reso con il renderer minimo (prod, staging, testimone) e validato con kubeconform 0.6.7 (CRD comprese); kustomize 5.4.3 di base, overlay e testimone validato con kubeconform: tutto valido. helm, kubectl e terraform veri non disponibili (come in R12a).

## Test
Nuovi: `gateway/tests/test_r12b_credenziali_utente.py` (15), `ciclo-vita/tests/test_r12b_restituzione_legata.py` (8), `notification/tests/test_r12b_lavoratore_posta.py` (9), `infra/k8s/test_verifica_manifest.py::test_r12b_*` (3), `frontend/e2e/r12b.spec.ts` (3); aggiornati `test_riversamento.py`, `test_r12a_scarto_legato.py`, i test della testa delle migrazioni, il Redis finto del gateway (scadenze, script atomico).

Corsa completa `r12b/esegui.sh` (PostgreSQL 16 reale, `r12b_integ` dal template `r10_integ` migrato a `20261106_053`, partizioni create prima): 31 suite Python, 29 verdi; 2 fallimenti attesi (catena delle revisioni nel test di unione R05; OpenAPI non rigenerata dopo una modifica fatta durante la corsa), frontend typecheck, lint e build verdi, e2e 55 passati e 1 fallito per tempi (`sessione.spec.ts`, avviso d'inattività, non toccato), `make verifiche` fallito per la stessa OpenAPI. Correzioni e rilievi della revisione, poi rieseguite gateway, notification, tools/docs, infra/k8s, e2e e `make verifiche` (`r12b/rieseguo.sh`): gateway 801, notification 289, tools/docs 30, infra/k8s 108, e2e 56, `make verifiche` 0. **Totale (ultima esecuzione di ogni suite) 4.089 passati, 0 falliti**, più 56 end-to-end (R12a: 4.053 e 53).

Mutazioni: 23 sui punti di sicurezza (12 VD-C-156, 6 VD-C-160, 5 VD-C-161), tutte uccise (una dopo un test aggiunto); 6 sui rilievi della revisione, uccise.

## Revisione: rilievi e correzioni
| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| Riserva per persona: INCR ed EXPIRE separati (chiave senza scadenza = blocco permanente); stesso schema nel contatore per IP dell'accesso | Basso | `accesso_locale.incr_con_scadenza` (script Lua: INCR e, se manca, EXPIRE in un passo) usato da entrambi | `test_revisione_contatori_sempre_con_scadenza` |
| Conferma dell'identità scritta in Redis prima dell'audit | Basso | Audit scritto e confermato prima; se fallisce, 503 e nessuna conferma | `test_revisione_conferma_solo_dopo_l_audit` |
| Lavoratore della posta con `notification-secrets` (chiave dei webhook, pepper, Redis) | Basso | `segretiServizio: false`, `DATABASE_URL` nel proprio Secret via `envSegreti`, regola dei manifest; chart e kustomize rivalidati | `test_r12b_revisione_lavoratore_della_posta_senza_il_secret_del_servizio`, `test_revisione_il_lavoratore_non_carica_le_api` |
| Segreto del nuovo codice a tempo senza `Cache-Control: no-store` | Basso | Risposta `no-store` come i codici di recupero | `test_revisione_segreto_del_codice_a_tempo_non_in_cache` |

## Criticità
Totale 164: 18 aperte, 12 parzialmente chiuse, 37 chiuse con riserve, 97 chiuse (R12a: 161; 19, 12, 35, 95).
- Chiusa: VD-C-161; nuova e chiusa: VD-C-164 (P2).
- Chiuse con riserve: VD-C-156 (riserve VD-C-163, VD-P-129), VD-C-160 (riserva VD-C-162).
- Nuove aperte, da decidere: VD-C-162, VD-C-163 (P3).
- Restano aperte con nota: VD-C-125, VD-C-143 e le riserve di R12a.

## Scelte da confermare
VD-P-123…129 (`docs/registro/decisioni.md`).

## File eliminati o spostati
Nessuno. Il ciclo di invio della posta è passato da `services/notification/app/main.py` a `services/notification/app/lavoratore_posta.py`. `tools/pulizia/pulisci_progetto.py` è invariato (nessuna eccezione nuova).

## Residui
- VD-C-162 (riversamento verso un altro conservatore senza autorizzazione del produttore), VD-C-163 (avviso delle credenziali solo per e-mail): da decidere.
- VD-C-164 da riscontrare su MSK reale (R12); restano VD-C-125, VD-C-143 e le riserve di R12a.
