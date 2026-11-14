# CHANGELOG — R13b — Canali esterni predisposti (31/10/2026)

Decisione del committente: le risorse esterne (contratto con un gestore PEC, aggregatore
SPID/CIE, account AWS) si attivano alla fine; ora il codice è completo, provato con
simulatori e configurabile, così che dopo bastino parametri e credenziali.

- **Base**: R13a (`changelog/CHANGELOG-2026-11-08-rilascio-R13a.md`, che comprende R12c).
  Migrazioni fino a `20261107_054`; migrazione nuova `20261108_055`.
- **Criticità**: VD-C-078 chiusa con riserve, VD-C-163 chiusa; VD-C-069, 109, 154 con il
  lavoro di R13b annotato (restano aperte/parziali finché mancano aggregatore e AWS);
  VD-C-162 resta «da decidere» con l'opzione pronta; nuove VD-C-169…173 (173 registrate:
  24 aperte, 11 parzialmente chiuse, 40 chiuse con riserve, 98 chiuse).
- **Scelte da confermare**: VD-P-145…154 «da confermare»; VD-P-155 «da decidere» (il
  valore dell'opzione VD-C-162 resta al committente; predefinito come oggi).
- **Documentazione**: `docs/integrazione/pec.md` (checklist «attivazione gestore PEC», §4),
  `docs/architettura/accesso_spid_cie.md` (confronto con le regole pubbliche e checklist
  «attivazione aggregatore», §4), `docs/integrazione/webhook.md` §5.5 (rotazione),
  `docs/architettura/giorno1_staging.md` §8.4 (MSK IAM/ACL), §14 (NLB/ALB e PROXY
  protocol), §14-bis (R13b).
- **Console**: pagina nuova «Canali → Canali PEC» e rotazione del segreto in «Canali →
  Webhook», nel sistema grafico R12c; voce «Il tuo account» nella campanella.

> **Avviso operativo**
> - **Gruppi del database (migrazione 055)**: senza `verumdoc_pec_accoda` il consumer di
>   notification non accoda le PEC dei casi d'uso (ma nessun caso è attivo finché un ente
>   non imposta `PEC_CASI_USO`); senza `verumdoc_notifiche_personali` la campanella non
>   mostra «Il tuo account»; il gateway scrive l'avviso personale con il gruppo già
>   esistente `verumdoc_credenziali_accesso` (GRANT INSERT dato dalla 055).
> - Comportamento invariato finché non si registra un canale: nessuna PEC parte,
>   i webhook firmano come prima (una sola firma `v1`), l'accesso SPID/CIE configurato in
>   R10 funziona come prima con in più PKCE, il riversamento in uscita non chiede
>   l'autorizzazione del produttore (predefinito `NON_RICHIESTA`).
> - **Federazione OpenID**: fuori sviluppo si attiva solo con `SPID_HOST_AMMESSI` (anche gli
>   host del Trust Anchor e degli intermedi).
> - **Gateway in produzione**: con `SPID_CLIENT_SECRET` o `SPID_CHIAVE_PRIVATA_CLIENT`
>   valorizzati e `SPID_HOST_AMMESSI` vuoto il gateway **non parte** (i segreti andrebbero
>   a qualunque host scritto in console).

## Revisioni di sicurezza

Due revisioni indipendenti dopo lo sviluppo (la prima sul commit wip 925e04a, la seconda
mirata sulle correzioni): **tutti i rilievi sono corretti** prima del rilascio, con test nel
repository (comprese le due riproduzioni del revisore, ora rifiutate) e mutazioni uccise.

| Revisione | Gravità | Rilievo | Correzione | Esito |
|---|---|---|---|---|
| Prima | Grave | Busta di anomalia firmata dal gestore con un `daticert.xml` falso dentro il messaggio allegato (`message/rfc822`) e `X-Ricevuta` assente accettata: un terzo poteva far segnare CONSEGNATO o MANCATA_CONSEGNA | `daticert.xml` solo come parte diretta del `multipart/mixed` firmato; `X-Ricevuta` e `X-Riferimento-Message-ID` obbligatorie e uguali ai dati firmati; `X-Trasporto: errore` respinto (`libs/shared/pec/tests/test_r13b_pec_libreria.py`) | Corretta |
| Prima | Grave | Trust chain che legava le chiavi per solo `kid`: configurazione dell'OP autofirmata dall'attaccante con lo stesso `kid` accettata | Chiavi di federazione di ogni entità solo dal Subordinate Statement del superiore; configurazione e dichiarazioni emesse verificate con quelle; thumbprint RFC 7638 (`libs/shared/security/tests/test_r13b_oidc_federazione.py`) | Corretta |
| Prima | Bassa | CA dei server SMTP/IMAP (`ca_pem`) indicabile dalla console | Solo nella voce del canale in `PEC_SEGRETI_JSON`; la console la rifiuta | Corretta |
| Prima | Bassa | `authority_hints` e `federation_fetch_endpoint` senza vincoli | Solo https, host di `SPID_HOST_AMMESSI` (obbligatorio fuori sviluppo), niente redirect, al più 64 KiB per risposta | Corretta |
| Prima | Bassa | Trigger dei messaggi PEC senza tabella dei passaggi di stato | `PASSAGGI_PEC` nella migrazione 055 (uguale al dominio, test che li confronta): IN_CODA solo da IN_INVIO, mai indietro | Corretta |
| Prima | — | Dati di prova con domini di un ente reale (anche da R12c) | Domini fittizi `comune-immaginario.example`, utenti SFTP `borgo-*`; nessun ente reale nel repository (REGOLE §31) | Corretta |
| Seconda | — | Verifica mirata delle correzioni (ricevute, trust chain, indirizzi di federazione, trigger, dati di prova) | Nessun problema rilevato | — |

Mutazioni: 23/23 sui punti di sicurezza dello sviluppo, 11/11 della prima revisione
(rilievo 1: 4, rilievo 2: 5, rilievo 4: 2); i rilievi 3 e 5 sono coperti da test diretti
(`test_ca_dei_server_solo_dal_secret`, `test_passaggi_del_dominio_uguali_a_quelli_del_trigger`).

## Cosa servirà dall'esterno

**Dal gestore PEC** (checklist completa in `docs/integrazione/pec.md` §4.1): casella PEC
dedicata con SMTP e IMAP abilitati; host e porte (465 o 587, 993) ed eventuali IP d'uscita
da autorizzare; utente e password e politica di rotazione; formato delle ricevute
(completa/breve/sintetica) con **esempi reali** di accettazione, consegna e mancata consegna
per il collaudo; certificati con cui firma le ricevute (o riferimento all'indice pubblico
dei gestori) e avvisi di cambio; limiti di dimensione, invio e conservazione della casella.

**Dall'aggregatore SPID/CIE** (`docs/architettura/accesso_spid_cie.md` §4.1): documentazione
dell'interfaccia OIDC (issuer, endpoint, JWKS o Entity Configuration); metodo di
autenticazione del cliente (`client_secret_*` o `private_key_jwt` con registrazione della
chiave pubblica); request object e `prompt`; dove arriva il codice fiscale (id_token o
UserInfo firmato/cifrato e algoritmi); valori `acr` per SPID e CIE; indirizzo di ritorno da
registrare; host e IP; ambiente di collaudo con identità di prova. Ogni requisito non
verificabile da qui è marcato «da verificare».

**Da AWS** (`giorno1_staging.md` §8.4, §14, §14-bis): cluster MSK con autenticazione IAM
(SASL/OAUTHBEARER) e porta 9098, ruoli IAM per servizio con le azioni `kafka-cluster:*`
per topic e gruppi come da tabella; NLB davanti a SFTP con PROXY protocol v2 e
`SFTP_PROXY_PROTOCOL_DA`, ALB davanti al gateway con `X-Forwarded-For` e `TRUSTED_PROXIES`
limitati alle sottoreti dell'ALB; uscita verso il gestore PEC (465/587/993) e verso
l'aggregatore.

## Prima del deploy
1. **Migrazione `20261108_055`** con il Job `gateway-migrazioni`. Controllo: `alembic
   current` = `20261108_055`. Mai consegnata prima: nessun database del committente può
   essere già a 055. Il downgrade si rifiuta se esistono PEC inviate o ricevute associate.
2. **GRANT ai login di produzione** (dopo la 055):
   - `verumdoc_pec` al login del lavoratore della posta (`notification-posta`): invio,
     stato dei canali, ricevute (raccomandato un login proprio, VD-P-150);
   - `verumdoc_pec_accoda` e `verumdoc_pec_canali` al login delle repliche API e del
     consumer di notification;
   - `verumdoc_notifiche_personali` al login di notification (campanella).
3. **Secret** `notification-posta-secrets`: chiave `PEC_SEGRETI_JSON` (`{}` finché non c'è
   il gestore; poi `{"<codice canale>": {"host": ["smtp…", "imap…"], "utente": "...",
   "password": "...", "ca_pem": "<facoltativa: CA privata dei server SMTP/IMAP>"}}`).
   Gli `host` sono obbligatori: un indirizzo fuori da quegli host non riceve mai la
   password. La CA dei server sta **solo** qui (la console la rifiuta). Le repliche API
   **non** devono ricevere la chiave: in produzione non partono se la ricevono
   (`verifica_manifest.py` lo controlla).
4. **Gateway**: `SPID_HOST_AMMESSI` (ConfigMap) **obbligatorio fuori sviluppo** con gli host
   dell'aggregatore e, se si usa la federazione, del Trust Anchor e degli intermedi: con
   `SPID_CLIENT_SECRET` o `SPID_CHIAVE_PRIVATA_CLIENT` valorizzati e la lista vuota il
   gateway in produzione non parte. `SPID_CHIAVE_CLIENT_KID` e, se serve,
   `SPID_CHIAVE_PRIVATA_CLIENT` in `gateway-secrets`.
5. **Rete**: NetworkPolicy `allow-notification-posta` con uscita verso internet sulle porte
   465, 587 e, nuova, 993 (IMAP delle ricevute); verificare che il firewall/NAT d'uscita
   le lasci passare verso il gestore. **Chart**: gateway, notification (API, consumer,
   posta), onboarding, ciclo-vita, frontend.
6. **Opzione VD-C-162**: non impostare `RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE`
   (predefinito `NON_RICHIESTA`, comportamento identico a oggi) finché il committente non
   decide (VD-P-155).
7. **Controlli**: «Canali → Canali PEC» mostra il registro vuoto e i parametri del gruppo
   PEC; un webhook di prova firma con una sola `v1`; la campanella mostra «Il tuo account»
   dopo il cambio della propria password.

Nota: helm, kubectl e terraform veri non eseguiti in sviluppo (manifest rigenerati e
verificati con `verifica_manifest.py`); Kafka vero non scaricabile da questo ambiente: la
prova SASL_SSL/OAUTHBEARER usa un broker finto sul protocollo di rete.

## Novità per Windows (sviluppo)
- Simulatori: `python -m tools.simulatore_pec --porta-smtp 2525 --porta-imap 2143`
  (stampa la configurazione SVILUPPO e la voce di `PEC_SEGRETI_JSON` da mettere in
  `infra/dev_pec.env`, facoltativo per il compose) e `tools/simulatore_oidc` (vedi
  `accesso_spid_cie.md` §5).
- Dipendenze Python nuove: `asn1crypto`, `defusedxml` (notification), `joserfc`
  (gateway); `docker compose up -d --build` le installa.

## Per chi usa la console
- **Canali → Canali PEC** (SUPER_ADMIN e RSC_CONSERVATORE con il permesso mai delegabile
  `pec_canali:gestisci`; ADMIN_TENANT vede solo i messaggi PEC del proprio ente): parametri del
  gruppo PEC, stato dei canali (segreti presenti, problemi, ultimo invio e ultima lettura),
  **Prova d'invio** a un indirizzo di prova (limite orario), messaggi con lo stato e le
  ricevute, «Ricevute non associate» con lo scarico del messaggio grezzo come evidenza;
  registro dei canali. Ogni modifica e ogni prova vanno nell'audit.
- **Canali → Webhook**: «Ruota il segreto» (periodo di doppia firma 1–168 ore, predefinito
  24; il segreto nuovo si mostra una volta) e «Chiudi la transizione».
- **Campanella → Il tuo account**: avvisi personali sui cambi delle proprie credenziali
  (mai visibili ad altri, mai negli eventi dell'ente né nei webhook).

## Per chi gestisce il servizio
- Parametri nuovi: `PEC_CASI_USO` (SCARTO, RESTITUZIONE, RIVERSAMENTO, SCADENZE;
  predefinito nessuno, l'ente può solo aggiungere), `PEC_CONSEGNA_ATTESA_ORE` (24),
  `RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE` (NON_RICHIESTA | RICHIESTA, conservatore
  ed ente nel verso più sicuro).
- Permesso nuovo mai delegabile: `pec_canali:gestisci`.
- Impostazioni nuove: notification `PEC_SEGRETI_JSON`, `PEC_ATTIVO`,
  `PEC_INTERVALLO_SECONDI`, `PEC_LOTTO`, `PEC_RITARDI_SECONDI`, `PEC_RECUPERO_DOPO_SECONDI`,
  `PEC_LETTURA_MASSIMO`, `PEC_PROVE_PER_ORA`; gateway `SPID_HOST_AMMESSI`,
  `SPID_CHIAVE_PRIVATA_CLIENT`, `SPID_CHIAVE_CLIENT_KID`.
- Codici d'errore nuovi: `NTF-403-PEC`, `NTF-404-PEC`, `NTF-409-PEC`, `NTF-422-PEC`,
  `NTF-429-PEC`, `WH-013`.
- Audit nuovi: `pec.canale_modificato`, `pec.canale_provato`, `webhook.segreto_ruotato`.
- Rotte nuove: notification `/api/v1/notifiche/pec/canali…`, `/messaggi…`, `/ricevute…`;
  `POST /api/v1/webhook/{id}/segreto/ruota` e `/segreto/chiudi-transizione`.

## Contenuto

### VD-C-078 — PEC e webhook (chiusa con riserve)
- `libs/shared/pec`: configurazione senza segreti (tipi `SMTP_IMAP`, `SVILUPPO` vietato in
  produzione), segreti legati agli host (stessa regola di R13a, `leggi_segreti` e
  `vincoli_deploy` generalizzati in `libs/shared/firma_qualificata/configurazione.py`),
  verifica S/MIME della firma CMS staccata verso le radici del canale, lettura di
  `daticert.xml` dentro la parte firmata, associazione sui dati firmati.
- `services/notification`: adattatore `CanaleSmtpImap` intercambiabile (registro
  `COSTRUTTORI`), `ciclo_pec` nel lavoratore della posta (R12b): invio, ritentativi,
  lettura delle ricevute, stato fino a CONSEGNATO/MANCATA_CONSEGNA/NON_ACCETTATO;
  accodamento dei casi d'uso dal consumer per ente; API della console.
- Database: lo stato «da ricevuta» richiede una ricevuta ASSOCIATA del tipo giusto
  (trigger), ricevute append-only, RLS per ente.
- Webhook: rotazione del segreto con doppia firma (intestazione con più `v1`), scadenza
  misurata a ogni consegna, ritiro anticipato.
- Simulatore `tools/simulatore_pec` (SMTP con AUTH, IMAP minimo, ricevute firmate da una CA
  generata all'avvio, leve `nonaccetta`, `errore`, `silenzio`, ricevute manomesse).

### VD-C-069 — SPID/CIE OIDC (lavoro annotato, resta in attesa dell'aggregatore)
- PKCE S256 sempre; `private_key_jwt` (RFC 7523), request object firmato (RFC 9101),
  `prompt`, `claims` per il codice fiscale, UserInfo JWS/JWE con `sub` uguale all'id_token;
  federazione OpenID facoltativa con chiavi del Trust Anchor fissate
  (`libs/shared/security/oidc_federazione.py`); segreti solo verso `SPID_HOST_AMMESSI`.
- Validazione del profilo in console (onboarding); simulatore `tools/simulatore_oidc`.
- Non applicati (VD-C-173): `metadata_policy`, `trust_marks`, `constraints`, revoche,
  Entity Configuration del relying party (solo per l'adesione diretta).

### VD-C-109, VD-C-154 — MSK e bilanciatori AWS (lavoro annotato)
- Revisione delle azioni IAM/ACL per topic e gruppi, prova SASL_SSL con OAUTHBEARER e TLS
  verificato su broker finto (`libs/shared/kafka/tests/test_r13b_kafka_sasl_tls.py`),
  prova di PROXY protocol v2 e di `X-Forwarded-For` come da NLB/ALB
  (`libs/shared/security/tests/test_r13b_proxy_aws.py`); checklist nel giorno 1.

### VD-C-163 — avviso personale (chiusa) e VD-C-162 — opzione predisposta
- `notifiche_personali` scritta dal gateway nella transazione della modifica delle
  credenziali, letta e segnata come letta solo dalla persona del token.
- `RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE`: fotografata sulla pratica alla
  creazione (`pratiche.richiede_autorizzazione_produttore`); con RICHIESTA il riversamento
  in uscita segue la logica di VD-C-160 (autorizzazione del produttore, riverifica del
  lavoratore prima dell'esecuzione); predefinito come oggi.

### Migrazione `20261108_055`
Tabelle `canali_pec`, `pec_canali_stato`, `pec_messaggi`, `pec_ricevute`,
`notifiche_personali`; colonne del segreto precedente in `webhook_sottoscrizioni`;
`pratiche.richiede_autorizzazione_produttore` e funzione `vd_pratiche_autorizzazioni`
aggiornata; vincoli di `esiti_esecuzione` estesi a `RIVERSAMENTO_USCITA`;
`pec_canali:gestisci` fra i mai delegabili. `lock_timeout`, GRANT minimi per gruppo,
trigger anche su INSERT dove serve l'immutabilità; downgrade rifiutato con evidenze.
Provata up/down/up; modelli coerenti (`alembic check`).

## Test
Corsa completa con PostgreSQL reale (database `r13b_integ` alla testa 055): 4.288 passati
dopo la riesecuzione delle suite corrette (gateway, notification, infra/k8s, sdk: quattro
attese da aggiornare per la testa 055, le rotte PEC dichiarate, la porta 993 e i campi della
rotazione nel modello dell'SDK); 3 saltati di conformità XSD in aip-builder. Frontend:
typecheck, lint, build verdi, **96 end-to-end** (9 nuovi in `frontend/e2e/r13b.spec.ts`, 390
e 1280 px, chiaro e scuro, contrasto AA), 2 lavoratori. `make verifiche` verde. Dopo la
revisione, riesecuzione delle suite toccate (libs/shared 430, notification 327, gateway 824,
frontend 96 e2e, `make verifiche`): **4.315 passati, 0 falliti**.
Mutazioni sui punti di sicurezza: 23/23 uccise (segreti legati agli host, autenticità e
associazione delle ricevute, verifica dei token OIDC e della trust chain, scadenza della
doppia firma HMAC, permessi della console, campanella personale, opzione VD-C-162); due
sopravvissute al primo giro (firma CMS sugli attributi, configurazione del Trust Anchor
autofirmata) uccise con due test aggiunti.
