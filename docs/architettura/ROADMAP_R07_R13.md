# Roadmap dei rilasci R07–R13 — VerumDoc (30/10/2026, aggiornata al 01/11/2026)

Base: decisioni D01–D32 di `docs/registro/DECISIONI_DA_PRENDERE.md`, **adottate tutte nella versione consigliata** dal committente il 30/10/2026. Partenza: R06, completamento stimato ~63%.

Criteri d'ordine:
1. Prima ciò che sblocca il resto (parametri in console, verifica delle firme).
2. Poi le automazioni, che si appoggiano a parametri e firme verificate.
3. La prova su infrastruttura reale prima della firma qualificata, che resta per ultima come deciso, con fornitori intercambiabili.

Regole di lavoro valide per ogni rilascio (REGOLE_SVILUPPO.md):
- niente foto e screenshot;
- test mirati durante lo sviluppo e una sola corsa completa a fine rilascio;
- prove di mutazione solo sui punti di sicurezza;
- una revisione indipendente, con un secondo giro solo se trova difetti gravi;
- script di pulizia nel pacchetto.

Le stime sono giorni di lavoro effettivo (con revisione e pacchetto) e consumo indicativo di token con queste regole (R06: circa 1,4 milioni).

| Rilascio | Tema | Decisioni | Durata stimata | Token stimati | Completamento dopo |
|---|---|---|---|---|---|
| R07 ✅ fatto (30/10/2026) | Parametri in console | D09, D10, D11, D13–D19, D25, D28, D29, D31 | 3–4 giorni | 1,5–2 M | ~67% |
| R08 ✅ fatto (30/10/2026, con riserve) | Firme e formati verificati | D01, D02, D22 | 6–8 giorni | 2,5–3 M | ~73% |
| R09 ✅ fatto (30/10/2026) | Ciclo di vita automatico | D04, D05, D06, D20, D21, D23, D24, D26, D27 | 4–5 giorni | 2–2,5 M | ~78% |
| R10 ✅ fatto (30/10/2026) | Accesso, ruoli e notifiche | D08, D12, D30, D32 | 6–7 giorni | 2,5–3 M | ~83% |
| R11 ✅ parziale (31/10/2026): parte A fatta, parte B predisposta in attesa della UNI | Conformità dei pacchetti | D03, D07 | 2–3 giorni (dopo l'acquisto della UNI) | 1–1,5 M | ~86% |
| R12a ✅ fatto (31/10/2026) | Criticità aperte e preparazione allo staging (senza account) | — | 1–2 giorni | 1–1,5 M | ~87% |
| R12b ✅ fatto (31/10/2026) | Le mie credenziali e residui di R12a (senza account) | — | 1 giorno | ~1 M | ~87% |
| R12c ✅ fatto (31/10/2026) | Revisione grafica della console (solo frontend) | — | 1 giorno | ~1 M | ~87% |
| R12 ⏳ in attesa dell'account cloud di staging | Prova su infrastruttura reale | criticità d'esercizio | 4–6 giorni + costi cloud | 1,5–2 M | ~91% |
| R13a ✅ fatto (31/10/2026) | Firma e marca qualificate predisposte (senza prestatore) | VD-P-05/07 | 2–3 giorni | 2–2,5 M | ~89% (stima prudente) |
| R13b ✅ fatto (31/10/2026) | Canali esterni predisposti: PEC, webhook, SPID/CIE, MSK/AWS (senza contratti) | VD-C-069, 078, 109, 154, 162, 163 | 1–2 giorni | 2–2,5 M | ~89% (stima prudente) |
| R14 ✅ fatto (31/10/2026) | Debito interno (senza risorse esterne) | VD-C-170, 172, 121, 122, 123, 133, 033, 082, 032, 096, 165 | 1–2 giorni | 1–1,5 M | ~89% (stima prudente) |
| R15 ✅ fatto (01/11/2026) | Documentazione di conformità generata dalla configurazione | manuale, piano della sicurezza, VD-C-177 | 2–3 giorni | 1,5–2 M | ~89% (stima prudente) |
| R16 ✅ fatto (01/11/2026) | Chiusura dei residui interni (senza risorse esterne) | VD-C-032, 153, 158, 166, 168, 171, 173, 176 | — (non pianificato) | — | ~89% (stima prudente) |
| R17 ✅ fatto (01/11/2026) | Collaudo finale: unione R15 + R16, decisioni confermate, conferma firmata dal gateway, audit della console | VD-P-155, VD-P-164; VD-C-120, 141, 162 | — (non pianificato) | — | ~90% (parte interna ~95%) |
| R13 | Attivazione del prestatore qualificato, del gestore PEC e dell'aggregatore | VD-P-05 | 1–2 giorni dopo i contratti | 0,5 M | ~95% |

Totale indicativo: **29–38 giorni di lavoro, 12–16 milioni di token**. Il resto fino al 100% è documentale e organizzativo: manuale di conservazione e piano della sicurezza (generati da R15, da completare con i dati del conservatore e da firmare), qualificazione ACN, certificazioni.

**Stato al 01/11/2026 (pacchetto unico R15–R17):** tutti i rilasci che non dipendono da risorse esterne sono fatti. Restano **R12** (staging: serve l'account cloud), **R13** (attivazione di prestatore qualificato, gestore PEC e aggregatore: servono i contratti, VD-P-05) e le voci esterne della tabella «Dipendenze esterne» (UNI 11386 con XSD, PDND, qualificazione ACN, certificazioni). Elenco aggiornato in `STATO_PROGETTO.md` §3 «Che cosa resta».

---

## R07 — Parametri in console (base della configurabilità) ✅ fatto

Rilasciato il 30/10/2026 (`changelog/CHANGELOG-2026-10-30-rilascio-R07.md`), con le correzioni della revisione W1–W8 (tetto del conservatore per ente con verso «più sicuro» dichiarato per ogni parametro) e, su richiesta del committente, «Rielabora» dei versamenti rifiutati direttamente dall'ente. Scelte di dettaglio da confermare: VD-P-57…69.

Obiettivo: ogni valore oggi fisso diventa un parametro del motore regole (livelli conservatore → ente → classe → accordo, con bozza, anteprima, applica e storico), visibile e modificabile in console secondo il ruolo.

**Parametri**
- **D13 – Piani e quote:** piani modificabili dal conservatore, deroga per ente, avvisi automatici all'80% e al 100%, rallentamento invece del rifiuto se l'ente lo sceglie.
- **D14 – Sessione:** inattività per ruolo (15 minuti per SUPER_ADMIN, RSC e ADMIN_TENANT; 30 per gli altri), massimo fissato dal conservatore e riducibile dall'ente, avviso 2 minuti prima con «resta collegato».
- **D15 – Staging dei versamenti rifiutati:** scadenza per ente (90 giorni, da 30 a 180), avviso automatico 7 giorni prima con link «Rielabora».
- **D16 – Webhook:** porte ammesse per ente, con motivo.
- **D17 – Idempotenza:** fine della transizione prorogabile per ente, avviso automatico ai clienti che non inviano la chiave.
- **D18 – Restituzione:** validità dei pacchetti parametrica (72 ore, fino a 30 giorni) e rigenerazione con un clic.
- **D19 – Audit:** periodo di export parametrico, predefinito 15 minuti.

**Modifiche programmate e console**
- **D28 – Modifiche programmate:** decorrenza futura per ogni impostazione, anteprima ricalcolata il giorno prima, avviso all'autore.
- **D29 – Selettore dell'ente:** in testata per i ruoli globali, con l'opzione «tutti», ricordato fra una sessione e l'altra.
- **D31 – Preferenze personali:** salvate sul server (tema, densità, pagina iniziale, colonne).
- **D25 – Natura dell'ente:** motivo solo per PRIVATO; per la PA si precompila dall'IPA.

**Pulizia**
- **D09 – Cronologia Git:** script `git filter-repo` per eliminare la vecchia chiave, con istruzioni di riallineamento.
- **D10 – asyncssh:** licenza accettata e registrata in `THIRD_PARTY.md` (dal R18 `THIRD_PARTY_NOTICES.md`).
- **D11 – OPA:** rimozione di `libs/policy-engine` (VD-C-034).

Criticità chiuse: VD-C-034; riserve ridotte per VD-C-004 (script pronto, da eseguire) e VD-C-127.

## R08 — Firme e formati verificati ✅ fatto (con riserve)

Rilasciato il 30/10/2026 (`changelog/CHANGELOG-2026-10-30-rilascio-R08.md`). Riserve: il servizio EU DSS `verifica-firme` è scritto ma non costruito né provato (Maven Central non raggiungibile in sviluppo, VD-C-143): il verificatore provato è pyHanko; veraPDF non installato (PDF/A dichiarato, non validato); le parti Java delle correzioni sono controllate solo con `javac`. Correzioni della revisione indipendente in due giri (X1–X14, Y1–Y3) tutte prima del rilascio: solo TOTAL_PASSED da CRITTOGRAFICA, effetti delle regole solo più severi per l'ente, firmatari autorizzati del ciclo di vita (elenchi del conservatore, vuoti = nessuno autorizzato), limiti zip per metodo di compressione, riferimento legato alla firma autorizzata. Scelte di dettaglio da confermare: VD-P-70…79.

**D01 – Servizio di verifica delle firme**
- Servizio nuovo `verifica-firme` con EU DSS (Java, immagine dedicata) dietro un'interfaccia a verificatori intercambiabili. pyHanko resta come ripiego per i soli PDF.
- Trusted List UE con aggiornamento automatico, OCSP/CRL, rapporto di verifica conservato nel pacchetto.
- Livello di verifica per classe (nessuna / rilevata / crittografica / con revoche) ed effetto con il motore regole: il conservatore fissa il minimo, l'ente può alzarlo.

**D02 – Formati**
- Siegfried (PRONOM) + veraPDF.
- Catalogo dei formati ammessi in console: base del conservatore dall'Allegato 2, restrizioni dell'ente per classe, esiti ammesso / avviso / da convertire / rifiutato.
- Il validatore preventivo mostra i rifiuti prima del versamento.

**D22 – Nulla osta e autorizzazioni**
- Firma del nulla osta configurabile per ente (predefinito: richiesta, con avviso se manca).
- Autorizzazione allo scarto e richieste di annullamento verificate crittograficamente.

Criticità chiuse: VD-C-073, VD-C-074 (entrambe con riserve). Fortemente ridotte: VD-C-120, VD-C-141 (parzialmente chiuse). Nuove: VD-C-143 (EU DSS non costruito né provato) e VD-C-144…147 (residui della revisione).

## R09 — Ciclo di vita automatico ✅ fatto

Rilasciato il 30/10/2026 (`changelog/CHANGELOG-2026-11-01-rilascio-R09.md`). Lavoro periodico nel lavoratore di ciclo-vita (proposte, riprese, minimizzazione, rinnovo dei blocchi WORM), doppia approvazione dell'annullamento con quattro occhi riverificati dal lavoratore, verifica delle versioni rimaste degli scarti incompleti, verifica dell'audit incrementale e completa con login e ruolo in sola lettura. Riserve: rinnovo degli oggetti `registro/` (VD-C-148), minimizzazione fuori dal database principale (VD-C-149), prova in esercizio (VD-C-150), lease senza fencing per il singolo elemento (VD-C-151). Revisione indipendente in due giri (Z1 grave, Z2–Z5 medi; W1–W3 medi), tutti corretti prima del rilascio: con la doppia approvazione la richiesta la fa una persona e approva solo ADMIN_TENANT; l'annullamento lo applica il lavoratore (la risposta è `RICHIESTA`). Scelte di dettaglio da confermare: VD-P-80…92.

**Scarto**
- **D21 – Proposte automatiche:** proposta di scarto mensile per ente (attivabile, frequenza, classi escluse), pratica in bozza con anteprima e notifica. L'esecuzione resta manuale con autorizzazioni e nulla osta.
- **D20 – Ripresa automatica:** i pacchetti in eliminazione incompleta riprendono da soli alla rimozione del blocco legale; tentativi parametrici; «chiudi con eccezioni» per il conservatore.
- **D26 – Verifica dopo lo scarto incompleto:** si verificano le versioni rimaste dei pacchetti in eliminazione incompleta.
- **D06 – Minimizzazione:** dopo lo scarto resta l'elenco minimo; il resto dei metadati si cancella in automatico dopo un periodo parametrico (30 giorni).

**Annullamento e rettifica**
- **D23 – Annullamento:** regola configurabile per ente (ruoli, stati, doppia approvazione; predefinita dopo la conservazione).
- **D24 – Rettifica:** atto XML sempre ammesso come tipo di sistema.

**Retention e verifiche**
- **D04 – Retention permanente:** parametrica (30 anni) con rinnovo automatico quando ne mancano meno di 5; vale anche per allegati, verbali e atti.
- **D05 – Frequenza della verifica continua:** parametrica per classe o ente, verifica a campione automatica dopo gli incidenti di storage.
- **D27 – Controllo dell'audit:** ruolo e login in sola lettura, verifica incrementale giornaliera e completa mensile.

Criticità chiuse: VD-C-070, VD-C-116, VD-C-135, VD-C-142; con riserve VD-C-137; parzialmente chiuse VD-C-122 (resta il controllo manuale degli oggetti senza inventario) e VD-C-133 (resta il ritentativo con allarme per verbale e foglie). Nuove: VD-C-148, VD-C-149, VD-C-151 (aperte) e VD-C-150 (parzialmente chiusa da metriche e allarmi del lavoro periodico).

## R10 — Accesso, ruoli e notifiche ✅ fatto

Rilasciato il 30/10/2026 (`changelog/CHANGELOG-2026-11-02-rilascio-R10.md`). Fornitori d'identità in console (SPID/CIE collegato alla configurazione, passkey, password con codice a tempo), metodi scelti dall'ente nel verso «più sicuro», inviti con attivazione guidata; ruoli personalizzati senza escalation e con i permessi mai delegabili esclusi a ogni livello; campanella «da fare per te» con preferenze e avvisi obbligatori; PROXY protocol solo dalle sorgenti fidate e indirizzi ammessi per account SFTP. Riserve: VD-C-069 (aggregatore da contrattualizzare), VD-C-152…156. Scelte da confermare: VD-P-93…104 (103–104 dalla revisione indipendente: IP reale e limite per IP, permessi dei ruoli personalizzati sugli endpoint per ruolo base).

- **D08 – Accesso:**
  - fornitori d'identità configurabili: SPID/CIE via aggregatore OIDC (codice già pronto); passkey (WebAuthn) e password con TOTP per i clienti privati;
  - l'ente sceglie i metodi ammessi;
  - inviti per e-mail con attivazione guidata.
- **D30 – Ruoli personalizzati:** ruoli fissi come modelli, ruoli personalizzati composti dall'ente dai permessi concedibili fissati dal conservatore, con l'esecuzione dello scarto mai delegabile.
- **D32 – Centro notifiche:** campanella «da fare per te» (pratiche, quote, scadenze, allarmi), scelta per utente di cosa ricevere e come, avvisi obbligatori decisi dall'ente.
- **D12 – SFTP:** proxy protocol sul bilanciatore, allow-list di IP per account impostata dall'ente, IP nei log.

Criticità chiuse: VD-C-107 con riserve (prova sull'NLB reale in VD-C-154, R12). Codice pronto e collegato alla console per VD-C-069, che resta aperta finché l'aggregatore non è contrattualizzato: è una decisione commerciale, non software. Nuove: VD-C-152 (segreto d'invito in chiaro nella coda della posta), VD-C-153 (concedibili e metodi dal prossimo accesso, limite accettato), VD-C-154 (NLB e ALB reali, R12), VD-C-155 (lavoratore senza i permessi del ruolo personalizzato), VD-C-156 (gestione delle proprie credenziali in console). VD-C-149 (minimizzazione nell'indice e negli eventi), indicata nel registro per R10, non è stata affrontata e resta aperta (da pianificare). Revisione indipendente in due giri (V1 grave, V2–V4 e U1 medi) corretta prima del rilascio.

## R11 — Conformità dei pacchetti ✅ parziale: parte A fatta, parte B predisposta in attesa della UNI

Rilasciato il 31/10/2026 (`changelog/CHANGELOG-2026-11-03-rilascio-R11.md`) senza il prerequisito: la UNI 11386:2020 con il suo XSD non è stata ancora acquistata.

- **D07 – Profili Allegato 5:** fatto. Riscontro sul testo ufficiale AgID (`docs/conformita/riscontro_allegato5.md`), versioni 2 append-only ad adozione esplicita per classe con anteprima, Soggetti, Tracciature e Assegnazione strutturati, fattura/registro/contratto dichiarati profili propri del conservatore; l'ente aggiunge solo campi facoltativi propri; metadati nell'IPdA con i nomi dell'Allegato 5.
- **D03 – Indice del pacchetto:** solo la predisposizione. Validatore pluggable contro lo schema caricabile (impronta SHA-256, mai «conforme» senza schema ufficiale), test di conformità che si attivano con l'XSD, istruzioni nel README. Restano l'acquisto, l'adeguamento del costruttore e la validazione prima della scrittura.

Criticità chiuse: VD-C-136 con riserve (VD-C-157…159). VD-C-071 resta aperta (predisposizione fatta; dopo l'acquisto: impronte in `libs/sincro/schemi_ufficiali.py` con decisione `VD-N`, `make conformita-ipda`). Revisione indipendente T1–T4 corretta prima del rilascio. Scelte da confermare: VD-P-105…114.

## R12 — Prova su infrastruttura reale ⏳ in attesa dell'account cloud

Prerequisito: account cloud di staging (non ancora disponibile al 31/10/2026). Il lavoro preparabile in locale è in R12a e R12b; all'arrivo dell'account si esegue `docs/architettura/giorno1_staging.md`.
- **Account del testimone:** Terraform dell'account separato e deploy (VD-C-110).
- **Kafka e carico:** MSK con autenticazione IAM e ACL (VD-C-109), prova di carico k6 a 1 milione di documenti al giorno, misure di costo reali.
- **Ripristino:** prova di disaster recovery (ripristino da replica, ricostruzione del registro dal WORM), runbook verificati.
- **CI:** oracolo Go del formato dei checkpoint in CI (VD-C-131), pipeline di rilascio.

Criticità chiuse: VD-C-109, VD-C-110, VD-C-131.

**R12a ✅ fatto (31/10/2026), senza account:** criticità risolvibili senza dipendenze esterne chiuse (145, 146, 147, 148, 151, 152, 155 e altre: `changelog/CHANGELOG-2026-11-05-rilascio-R12a.md`; revisione di sicurezza, 1 media e 3 basse, corretta prima del rilascio); 110 parzialmente chiusa e 131 chiusa con riserve, la chiusura piena resta a R12; preparati Terraform dell'account del testimone (validato), oracolo Go in CI, script k6 della giornata da 1 milione di documenti, runbook di disaster recovery con la prova locale della ricostruzione del registro e la checklist `docs/architettura/giorno1_staging.md` per quando arriva l'account.

**R12b ✅ fatto (31/10/2026), senza account:** gestione delle proprie credenziali in console (VD-C-156, chiusa con riserve), restituzione legata alla pratica e riverificata dal lavoratore (VD-C-160, chiusa con riserve), invio della posta in un lavoratore dedicato non esposto (VD-C-161, chiusa), permesso Kafka di onboarding su `notification.send` (VD-C-164, chiusa); nuove VD-C-162 e 163 da decidere; revisione di sicurezza (4 rilievi bassi) corretta prima del rilascio (`changelog/CHANGELOG-2026-11-06-rilascio-R12b.md`). R12 resta ⏳ in attesa dell'account cloud.

**R12c ✅ fatto (31/10/2026), senza account:** revisione grafica della console richiesta dal committente dopo la prova su docker: testata responsive con menu a scomparsa e menu della persona, ente in un solo punto, quadro del servizio con Partenze dai dati veri, accesso a due colonne, motivi ferroviari; 27 difetti dell'audit corretti, prove automatiche di sovrapposizione, scorrimento e contrasto (`changelog/CHANGELOG-2026-11-07-rilascio-R12c.md`). Solo frontend, nessuna migrazione.

## R13a — Firma e marca qualificate predisposte ✅ fatto (31/10/2026)

Rilasciato il 31/10/2026 (`changelog/CHANGELOG-2026-11-08-rilascio-R13a.md`, documentazione `docs/architettura/firma_qualificata.md`). Decisione del committente: le risorse esterne alla fine, il codice completo ora. Interfaccia unica di firma e marca con registro dei fornitori in console (CSC v2 con sigillo automatico, PKCS#11 provato con SoftHSM2, TSA RFC 3161 in ripiego, fornitore di sviluppo vietato in produzione), CAdES B-T/LT/LTA e PAdES B-LT/LTA con verifica della firma prodotta; IPdA, RdV, manuale e piano sigillati dalla hashchain, `NON_FIRMATO` esplicito o conservazione ferma (`FIRMA_SENZA_FORNITORE`), marca dell'ultimo checkpoint, rimarcatura con lease e fencing, sigillo facoltativo dell'attestazione; simulatore CSC/TSA per collaudo. Criticità chiuse con riserve: VD-C-068, VD-C-124; nuove VD-C-165…168. Scelte da confermare: VD-P-130…144.

## R13b — Canali esterni predisposti ✅ fatto (31/10/2026)

Rilasciato il 31/10/2026 (`changelog/CHANGELOG-2026-11-09-rilascio-R13b.md`, documentazione `docs/integrazione/pec.md`, `docs/architettura/accesso_spid_cie.md`, `docs/architettura/giorno1_staging.md` §8.4, §14, §14-bis). Stessa decisione del committente: risorse esterne alla fine, codice completo ora, provato con simulatori. Canale PEC SMTP/IMAP intercambiabile nel lavoratore della posta con ricevute S/MIME verificate e associate sui soli dati firmati, stati fino a CONSEGNATO/MANCATA_CONSEGNA imposti dal database; rotazione del segreto dei webhook con doppia firma; adattatore OIDC allineato a SPID/CIE OIDC (PKCE, `private_key_jwt`, request object, UserInfo cifrato, federazione facoltativa con chiavi dal livello superiore); checklist MSK IAM/ACL e NLB/ALB; campanella personale; opzione `RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE` (predefinito come oggi). Criticità: VD-C-078 chiusa con riserve, VD-C-163 chiusa; nuove VD-C-169…173. Scelte: VD-P-145…154 da confermare, VD-P-155 da decidere. Revisione (2 gravi, 3 basse) corretta prima del rilascio; seconda revisione mirata senza rilievi.

## R14 — Debito interno ✅ fatto (31/10/2026)

Lavoro che non dipende da contratti né account: metriche e allarmi del lavoratore della posta (VD-C-170), retention di `notifiche_personali` (VD-C-172), residui interni ancora aperti o parziali che non richiedono risorse esterne (per esempio VD-C-121, 123, 133 e la pulizia di VD-C-032), riallineamento dei test e dei registri.

Esito (`changelog/CHANGELOG-2026-11-10-rilascio-R14.md`): chiuse VD-C-121, 133, 170, 172, 175; chiuse con riserve 033, 082, 096, 123, 165; parzialmente chiuse 032 e 122; 120 e 141 con la proposta VD-P-164 (da decidere); migrazione `20261109_056`; nuove 174–177. La parte documentale generata (VD-C-177) passa a R15.

## R15 — Documentazione di conformità generata dalla configurazione ✅ fatto (01/11/2026)

Manuale di conservazione e piano della sicurezza generati dalla configurazione reale del servizio (parametri, canali, fornitori, ruoli, infrastruttura dichiarata) e dal database, con sezioni marcate «da compilare» dove manca il dato, mai valori inventati; base per la qualificazione e per l'adeguamento a norme che cambiano (si cambia la configurazione, il documento si rigenera). Il sigillo qualificato dei documenti arriva con R13.

Esito (`changelog/CHANGELOG-2026-11-11-rilascio-R15.md`): `libs/conformita`, `tools/conformita/genera_documenti.py` (in `make verifiche`), pagina «Documentazione del servizio», versioni immutabili nel WORM; VD-C-177 chiusa con riserve; nuove 181–183; migrazione `20261111_058_documentazione` (nata 057, rinumerata all'unione).

## R16 — Chiusura dei residui interni ✅ fatto (01/11/2026)

Esito (`changelog/CHANGELOG-2026-11-11-rilascio-R16.md`): firma personale con OTP del responsabile (simulatore CSC), federazione OpenID completa, PEC senza doppioni, limite delle prove condiviso in Redis, revoca delle sessioni ai cambi di metodi e concedibili, tracce distribuite, regole incrociate dei profili, lint a zero; chiuse VD-C-032 e 171, chiuse con riserve 153, 158, 166, 168, 173, 176; nuove 178–180; migrazione `20261110_057_residui`.

## R17 — Collaudo finale ✅ fatto (01/11/2026)

Esito (`changelog/CHANGELOG-2026-11-12-rilascio-R17.md`): unione di R15 e R16; decisioni del committente del 01/11/2026 applicate; conferma firmata dal gateway delle decisioni sensibili (VD-P-164, VD-C-120 e 141 chiuse con riserve); autorizzazione del produttore al riversamento in uscita richiesta (VD-P-155, VD-C-162 chiusa); test indipendenti dalla data; revisione di sicurezza (1 media, 4 basse) e audit finale della console (`docs/audit/AUDIT_FINALE_2026-11-01.md`, 32 rilievi) corretti; nuove 184–186; migrazione `20261112_059_conferme_decisione`. Consegnati insieme in un pacchetto unico R15–R17.

## R13 — Attivazione del prestatore qualificato, del gestore PEC e dell'aggregatore (alla fine, come deciso)

Prerequisito: scelta e contratto del prestatore (VD-P-05), del gestore PEC e dell'aggregatore SPID/CIE. Il codice è in R13a e R13b: restano le checklist «attivazione fornitore» (`docs/architettura/firma_qualificata.md` §7): credenziali nel Secret, fornitori e radici in console, prove, `FIRMA_SENZA_FORNITORE=BLOCCA`; «attivazione gestore PEC» (`docs/integrazione/pec.md` §4) e «attivazione aggregatore» (`docs/architettura/accesso_spid_cie.md` §4); poi VD-C-165 (IPdA storici), VD-C-166 (firma personale del responsabile), VD-C-167 (B-LTA con EU DSS), VD-C-169 (ricevute reali del gestore).

---

## Dipendenze esterne da avviare subito (non bloccano R07–R10)

| Cosa | Serve a | Entro |
|---|---|---|
| Acquisto UNI 11386:2020 | R11 | prima di R11 |
| Contratto con l'aggregatore SPID/CIE | accesso reale (R10 lo collega, R13b predispone) | prima della produzione |
| Contratto con un gestore PEC accreditato | comunicazioni PEC (R13b predispone) | prima della produzione |
| Account cloud di staging e testimone | R12 | prima di R12 |
| Scelta del fornitore di firma e marca | R13 | prima di R13 |
| Manuale di conservazione, piano della sicurezza, qualificazione ACN | messa in esercizio | in parallelo da R09 |
