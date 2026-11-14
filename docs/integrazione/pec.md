# Canale PEC (R13b, VD-C-078)

Comunicazioni formali all'ente per posta elettronica certificata, con lo stato del
messaggio fino alla ricevuta di consegna. Codice completo e provato con il simulatore
`tools/simulatore_pec`; mancano contratto e casella presso un gestore PEC accreditato.

## 1. Architettura

- **Registro** `canali_pec` (console «Canali → Canali PEC», solo ruoli fissi del
  conservatore con il permesso mai delegabile `pec_canali:gestisci`): tipo `SMTP_IMAP`
  (gestore accreditato) o `SVILUPPO` (simulatore, vietato in produzione a tre livelli),
  configurazione senza segreti (`libs/shared/pec/configurazione.py`).
- **Segreti** nel Secret `notification-posta-secrets`, chiave `PEC_SEGRETI_JSON`:
  `{"pec-conservatore": {"host": ["smtps.pec.gestore.it", "imaps.pec.gestore.it"], "utente": "…", "password": "…"}}`.
  Facoltativa nella stessa voce: `"ca_pem"`, la CA privata dei server SMTP/IMAP (vuoto = CA
  di sistema); la console non può indicarla (revisione R13b, rilievo 3). I segreti sono
  **legati agli host** della voce: un canale con host SMTP o IMAP fuori elenco non si
  costruisce e ogni connessione lo ricontrolla (stessa regola di R13a). Le repliche API non
  ricevono il segreto: in produzione non partono se lo ricevono.
- **Lavoratore della posta** (`notification-posta`, R12b): `ciclo_pec` invia via SMTP
  (TLS 465 o STARTTLS 587, certificato verificato) e legge le ricevute via IMAP (TLS 993,
  sola lettura, UID crescenti, limite di dimensione). Più repliche insieme: prelievo con
  `SKIP LOCKED`, ricevute idempotenti per (canale, UIDVALIDITY, UID).
- **Casi d'uso** per ente: parametro `PEC_CASI_USO` (SCARTO, RESTITUZIONE, RIVERSAMENTO,
  SCADENZE; predefinito nessuno, l'ente può solo aggiungerne). Destinatario:
  `tenants.pec_referente`; senza indirizzo valido il messaggio è `NON_PARTITO` con il motivo.
- **API REST dei gestori**: proprietarie e diverse fra gestori; nessun secondo adattatore
  generalizzabile (VD-P-147). Un gestore con API propria = un costruttore in
  `services/notification/app/infrastructure/pec_canale.py` (`COSTRUTTORI`).

## 2. Stati del messaggio

`IN_CODA → IN_INVIO → INVIATO → ACCETTATO → CONSEGNATO | MANCATA_CONSEGNA`;
`INVIATO → NON_ACCETTATO`; `NON_PARTITO` per errori permanenti o tentativi esauriti
(`PEC_RITARDI_SECONDI`). Ricevute fuori ordine gestite (consegna prima
dell'accettazione); lo stato non torna mai indietro. «Consegna non confermata» oltre
`PEC_CONSEGNA_ATTESA_ORE` è un avviso della console, non uno stato.

### 2.1 Nessun doppione dopo un arresto (R16, VD-C-171, VD-P-168)

Il Message-ID è **deterministico**: `<pec.<id del messaggio>@<dominio della casella del
canale>>`, fissato dal database al primo prelievo e uguale a ogni ritentativo. Un messaggio
rimasto `IN_INVIO` oltre `PEC_RECUPERO_DOPO_SECONDI` (arresto del lavoratore fra
l'accettazione SMTP del gestore e la scrittura di `INVIATO`) si **riconcilia** prima di
essere rimandato:
- se fra le ricevute registrate del canale ce n'è una con quel Message-ID (il gestore l'ha
  preso in carico), il messaggio diventa `INVIATO` con la nota «riconciliato … nessun
  reinvio» e le ricevute associate lo portano avanti (senza accettazione valida scatta
  `PecSenzaRicevuta`);
- si rimanda (stesso Message-ID) solo se nessuna ricevuta lo cita **e** la casella del
  canale è stata letta con successo almeno `PEC_RECUPERO_DOPO_SECONDI` dopo l'arresto; senza
  una lettura riuscita resta `IN_INVIO` e si riprova al giro successivo.

Prova: `services/notification/tests/test_r16_pec_doppioni.py` (arresto dopo l'accettazione:
una sola PEC al destinatario).

## 3. Ricevute autentiche e associate

Si decide **solo sui dati firmati** (`libs/shared/pec/smime.py`, `ricevute.py`):
1. `multipart/signed` con due parti, firma CMS staccata, un firmatario, attributi firmati
   con `message-digest` uguale ai byte della prima parte;
2. certificato del firmatario che discende da una radice del canale (`radici_pem`: i
   certificati dei gestori PEC dall'indice pubblico dei gestori), valido all'istante della firma;
3. `daticert.xml` come parte DIRETTA del `multipart/mixed` firmato, mai dentro un messaggio
   allegato (`message/rfc822`: le buste di anomalia del gestore allegano il messaggio di un
   terzo, che può contenere un `daticert.xml` falso); XML letto con `defusedxml`;
   `X-Ricevuta` e `X-Riferimento-Message-ID` (non firmati) **obbligatorie** e uguali ai dati
   firmati; una busta con `X-Trasporto: errore` (anomalia) non è mai una ricevuta;
4. associazione: `msgid` firmato = Message-ID del messaggio inviato DA QUEL canale,
   mittente firmato = casella del canale, destinatario fra quelli firmati (o la `consegna`
   per le ricevute per destinatario).
Il database lo ribadisce: uno stato da ricevuta richiede una riga `pec_ricevute` ASSOCIATA
del tipo giusto (trigger `pec_messaggi_protetti`), solo i passaggi di stato ammessi
(`PASSAGGI_PEC` della migrazione 055 = `PASSAGGI` del dominio: IN_CODA solo da IN_INVIO,
mai indietro); le ricevute sono append-only.
Ogni altro messaggio della casella resta come evidenza (NON_AUTENTICA, NON_ASSOCIATA,
OLTRE_LIMITE) nella sezione «Ricevute non associate».

## 4. Checklist «attivazione gestore PEC»

### 4.1 Da chiedere al gestore
1. Casella PEC del conservatore dedicata al servizio, con accesso SMTP e IMAP abilitato.
2. Host e porte SMTP (465 TLS implicito o 587 STARTTLS) e IMAP (993), IP d'uscita da
   autorizzare se il gestore filtra.
3. Credenziali (utente, password; eventuale password per applicazioni) e politica di
   rotazione.
4. Formato delle ricevute: completa, breve, sintetica (richiesta con `X-TipoRicevuta`);
   esempi reali di accettazione, consegna, mancata consegna per il collaudo — **da
   verificare** che corrispondano al formato atteso (`daticert.xml` firmato).
5. Certificati con cui firma le ricevute (o riferimento all'indice dei gestori PEC) e
   avvisi di cambio certificato.
6. Limiti: dimensione dei messaggi, invii al minuto/giorno, conservazione della casella.

### 4.2 Parametri
- Console «Canali PEC» → «Registra un canale»: codice, tipo `SMTP_IMAP`, indirizzo, host
  e porte, cifratura, cartella, tipo di ricevuta, `radici_pem`; attivo.
- Secret `notification-posta-secrets` → `PEC_SEGRETI_JSON` con `host` del gestore (ed
  eventuale `ca_pem` se i server non usano una CA pubblica).
- Helm: `workers.posta.egress.internetPorts` già comprende 465, 587, 993.
- Parametro `PEC_CASI_USO` (conservatore o ente) e indirizzo PEC del referente di ogni ente.

### 4.3 Prove
1. Stato del canale «Pronto» (segreti presenti, nessun problema) dopo un giro del lavoratore.
2. «Prova d'invio» a una casella PEC di prova: messaggio CONSEGNATO con accettazione e
   consegna ASSOCIATE; scarico del messaggio grezzo.
3. Prova verso un indirizzo inesistente: MANCATA_CONSEGNA con il motivo del gestore.
4. Una mail ordinaria o una ricevuta di un altro mittente nella casella: compare fra le
   «Ricevute non associate», nessuno stato cambia.
5. Un caso d'uso reale (proposta di scarto di un ente con `PEC_CASI_USO=SCARTO`).

## 5. Simulatore

`python -m tools.simulatore_pec --porta-smtp 2525 --porta-imap 2143` stampa la
configurazione SVILUPPO da registrare e la voce di `PEC_SEGRETI_JSON` (in
`infra/dev_pec.env`). Destinatari con `nonaccetta`, `errore`, `silenzio` nella parte locale
producono i rispettivi esiti; le ricevute sono firmate da una CA generata all'avvio.

## 6. Metriche, allarmi e pulizia (R14, VD-C-170, VD-C-172)

Il lavoratore della posta resta senza HTTP: le metriche le calcolano dal database le repliche
API di notification (`app/application/metriche_posta.py`, ogni
`METRICHE_POSTA_INTERVALLO_SECONDI`), dalle stesse tabelle che legge la console.

| Metrica | Allarme (`infra/observability/regole/verumdoc-r14.yml`) | Cosa fare |
|---|---|---|
| `verumdoc_posta_coda_eta_secondi` | `PostaCodaFerma` (> 30 min) | Deployment `notification-posta` in esecuzione? log `notifiche_giro_invio_errore`, SMTP raggiungibile, `POSTA_CHIAVE_PRIVATA` presente |
| `verumdoc_posta_messaggi_non_partiti_24h` | `PostaNonPartita` | topic `notification.dlq`, destinatario o sigillo |
| `verumdoc_pec_messaggi_senza_accettazione`, `…_senza_consegna` | `PecSenzaRicevuta` | lettura IMAP (stato del canale), gestore |
| `verumdoc_pec_canali_non_utilizzabili` | `PecCanaleNonUtilizzabile` | console «Canali PEC»: problemi del canale (segreti, host, connessione) |
| `verumdoc_pec_stato_eta_secondi` | `PecLavoratoreFermo` (> 15 min) | lavoratore fermo o `PEC_ATTIVO=false` |
| `verumdoc_pec_ricevute_non_autentiche_24h` | `PecRicevuteNonAutentiche` | ricevute non associate in console: firma o radice non valide |

**Pulizia delle notifiche personali.** Il lavoratore della posta cancella le voci personali
della campanella più vecchie di `NOTIFICHE_PERSONALI_GIORNI` (console, solo conservatore,
predefinito 365) con la funzione `vd_pulisci_notifiche_personali` della migrazione 056 (mai
sotto 30 giorni, al più 10.000 righe per chiamata), con lease e fencing in `lavori_periodici`
(riga `notifiche_personali.pulizia`: `ultimo_giro`, `ultimo_esito`). Il login del lavoratore
deve essere membro di `verumdoc_notifiche_pulizia`.
