# CHANGELOG — R09 — Ciclo di vita automatico (30/10/2026)

Terzo rilascio della roadmap R07–R13 (decisioni D04, D05, D06, D20, D21, D23, D24, D26, D27, opzione consigliata). Prerequisito: R08 applicato (migrazioni fino a `20261031_047`). Migrazione nuova: `20261101_048` (riprendibile, `lock_timeout`, indici `CONCURRENTLY` fuori transazione). Il ciclo di vita ha ora un **lavoro periodico** nel lavoratore di ciclo-vita (proposte di scarto, riprese, minimizzazione, rinnovo dei blocchi WORM), l'annullamento ha la **doppia approvazione** e la verifica continua controlla anche gli **scarti incompleti**.

> **Avviso operativo**
> - **Annullamento dopo la conservazione: ora serve una seconda persona — avvisare gli enti con un solo amministratore** (predefinito `ANNULLAMENTO_DOPPIA_APPROVAZIONE = DOPO_CONSERVAZIONE`). La richiesta resta «in attesa» finché un'altra persona dell'ente con un ruolo ammesso (`ANNULLAMENTO_RUOLI_APPROVAZIONE`, predefinito ADMIN_TENANT) la approva. Un ente con un solo amministratore non può più annullare un versamento conservato finché non ne nomina un secondo (o il conservatore concede `MAI` con `LIMITE.ANNULLAMENTO_DOPPIA_APPROVAZIONE`).
> - **Orizzonte delle classi permanenti: 30 anni dal motore** (`RETENTION_PERMANENTE_ANNI`), con rinnovo automatico quando ne mancano meno di 5. La variabile `RETENTION_PERMANENTE_ORIZZONTE_ANNI` è tolta da values e compose: aip-builder e hashchain ora **partono** senza segnaposto (VD-C-070). Allegati, verbali e atti nuovi hanno 30 anni invece di 100.
> - **Annullamento applicato dal lavoratore**: `POST /pratiche/annullamento` risponde ora `RICHIESTA` (prima `ESEGUITA`) anche senza doppia approvazione; il versamento diventa `ANNULLATO` al passaggio del lavoratore di ciclo-vita (pochi secondi). Le integrazioni che leggevano subito `ESEGUITA` vanno avvisate.
> - **OPERATORE non approva più gli annullamenti**: in `ANNULLAMENTO_RUOLI_APPROVAZIONE` il motore ammette solo ADMIN_TENANT; con la doppia approvazione la richiesta si fa con l'accesso di una persona, non con una chiave API.
> - **Da creare a mano prima del deploy** (CronJob `verifica-audit`): login `vd_auditor` membro del gruppo `verumdoc_auditor` (sola lettura, creato dalla migrazione 048), voce in `userlist.txt` di PgBouncer, `DATABASE_URL` nel Secret `hashchain-verifica-audit-secrets`, ruolo IRSA proprio `hashchain-verifica-audit` (Terraform); topic Kafka `ciclo_vita.avviso`. Senza login e Secret il job fallisce (allarme `VerificaAuditFallita`).

## Per chi usa la console
- **Configurazione → «Ciclo di vita automatico»** (nuova pagina, una scheda per gruppo):
  - **Scarto**: proposta automatica (`SCARTO_PROPOSTA_AUTOMATICA`, predefinita DISATTIVA: l'ente può attivarla), frequenza (`SCARTO_PROPOSTA_FREQUENZA_GIORNI`, 30), classi escluse (`SCARTO_PROPOSTA_CLASSI_ESCLUSE`, del conservatore), tentativi per pacchetto (`SCARTO_TENTATIVI_PDA`, 5), riprese automatiche (`SCARTO_RIPRESE_AUTOMATICHE`, 3), minimizzazione (`SCARTO_MINIMIZZAZIONE_GIORNI`, 30);
  - **Annullamento**: ruoli prima e dopo la conservazione (elenco vuoto = non ammesso), ruoli che approvano, doppia approvazione (SEMPRE, DOPO_CONSERVAZIONE, MAI: l'ente può solo inasprire);
  - **Blocchi WORM**: anni della conservazione permanente (30) e soglia del rinnovo (5);
  - **Verifiche**: frequenza della verifica continua (`VERIFICA_CICLO_GIORNI`, 365; ente e classe possono solo renderla più frequente, minimo 7), pacchetti del campione dopo un incidente (200);
  - **Audit**: finestra della verifica giornaliera (2 giorni) e giorno del mese della verifica completa (1);
  - per il conservatore, **«Lavoro automatico»**: ultimo giro e esito di ogni passo, blocchi permanenti in scadenza, rinnovi degli ultimi 30 giorni, in parole.
- **Pratica**: riquadro «In breve» con l'esito o il prossimo passo in parole semplici.
  - Scarto **eseguito con eccezioni**: «Riprendi» (come prima) oppure **«Chiudi con eccezioni»** (conservatore, motivo di almeno 20 caratteri). Chiusa, la pratica non si riprende più; i pacchetti rimasti restano conservati in parte e verificati.
  - **Proposta di scarto automatica**: pratica in bozza aperta dal «servizio»; l'ente la rivede e la invia come una proposta manuale (autorizzazione firmata, nulla osta, esecuzione del conservatore).
  - **Annullamento in attesa**: riquadro «Seconda approvazione» per chi può approvare («Approva l'annullamento», «Respingi»); chi l'ha chiesto vede perché non può.
- **Avvisi all'ente** (elenco eventi, webhook, e-mail al referente): evento `ciclo_vita.avviso` con sottotipo PROPOSTA_SCARTO, RIPRESA_SCARTO, ANNULLAMENTO_DA_APPROVARE.

## Per chi gestisce il servizio
- **Lavoro periodico nel lavoratore di ciclo-vita** (`PERIODICO_*`, controllo ogni 60 s, un lease per passo in `ciclo_vita_periodico`): proposte (ogni 6 h; per ente con la sua frequenza), riprese (15 min), minimizzazione (6 h), rinnovo (24 h), al più `PERIODICO_LOTTO` (500) elementi per giro.
- **Rinnovo dei blocchi WORM**: il lavoratore estende la retention COMPLIANCE delle versioni dei PdA permanenti (`anni_conservazione = 999`) e degli allegati delle pratiche; mai in riduzione (`storage.estendi_retention` rilegge la retention prima di scrivere). Permesso IAM nuovo: `retention` anche sulle chiavi dei PdA per `ciclo-vita-lavoratore`. Gli oggetti `registro/` della hashchain **non** si rinnovano ancora (VD-C-148).
- **Verifica continua**: frequenza dal motore (conservatore, ente, classe; il valore d'ambiente `VERIFICA_CICLO_GIORNI` vale solo senza righe); i PdA in `ELIMINAZIONE_INCOMPLETA` tornano in rotazione con le sole versioni rimaste; un'anomalia o errori di lettura ripetuti avviano una **verifica a campione** (al più una ogni `VERIFICA_INCIDENTE_PAUSA_ORE`, 6). Il conservatore la avvia anche a mano: `POST /api/v1/hashchain/verifiche/campione`.
- **Verifica dell'audit**: `verifica-audit --automatica` (CronJob giornaliero): completa il giorno `AUDIT_VERIFICA_COMPLETA_GIORNO`, altrimenti incrementale sugli ultimi `AUDIT_VERIFICA_FINESTRA_GIORNI` agganciata al segmento precedente. Login in sola lettura `verumdoc_auditor` (migrazione 048: `SELECT` su `audit_global`, tabelle del registro lette, `impostazioni_valori`), ServiceAccount `hashchain-verifica-audit` con lettura ed elenco del solo `registro/`.
- Topic Kafka nuovo **`ciclo_vita.avviso`** (produttore ciclo-vita, consumatore notification): crearlo prima di applicare.
- Impostazioni d'ambiente di ciclo-vita tolte: `ANNULLAMENTO_RUOLI_*`, `ANNULLAMENTO_DOPO_CONSERVAZIONE`, `ALLEGATI_RETENTION_ANNI` (ora parametri del motore). `SCARTO_TENTATIVI_MAX_PDA` resta come valore d'ambiente valido solo senza la riga del conservatore.

## Contenuto
- **D21 — proposte di scarto automatiche**: `app/application/periodico.py` (passo «proposte»), stessa funzione della proposta manuale (`scarto.crea_bozza`, estratta da `applica`), creazione del SISTEMA provata dal fatto `PROPOSTA_GENERATA` del lavoratore (la verifica della catena delle autorizzazioni la pretende: `CREAZIONE_SISTEMA_NON_PROVATA`), una sola proposta automatica aperta per ente, avviso.
- **D20 — ripresa automatica e «chiudi con eccezioni»**: ripresa (ruolo SISTEMA, stessa richiesta d'esecuzione del conservatore, `pratiche.richiedi_esecuzione`) quando almeno un PdA incompleto non ha più versioni bloccate, al più `SCARTO_RIPRESE_AUTOMATICHE`; transizione `ESEGUITA_CON_ECCEZIONI → CHIUSA_CON_ECCEZIONI` (lavoro `CHIUDI`, motivo obbligatorio) applicata dal lavoratore dopo la riverifica (`autorizzazioni.verifica_chiusura`: conservatore attivo, motivo, voce d'audit, PdA incompleti), fatto `CHIUSO_CON_ECCEZIONI` e foglia `chiusura_eccezioni` per PdA; tentativi per PdA dal parametro.
- **D26 — verifica dopo lo scarto incompleto**: `hashchain/app/verifica_continua/incompleti.py`: un oggetto mancante è atteso solo se lo scarto l'aveva inventariato prima di cancellarlo; uno stato cambiato durante la lettura non registra nulla e torna in coda.
- **D06 — minimizzazione**: oltre `SCARTO_MINIMIZZAZIONE_GIORNI` dalla conclusione, per ogni PdA eliminato per intero: oggetto e protocollo del PdV, metadati del PdV (resta il RdV definitivo), nomi dei file e metadati dell'allegato 5 tolti in una transazione con il fatto `METADATI_MINIMIZZATI` (elenco minimo nei dettagli), poi foglia `minimizzazione`.
- **D23 — annullamento**: regola per ente dal motore (`app/application/parametri.py`), stati `APPROVATA` e `RESPINTA`, endpoint `POST /pratiche/{id}/approvazione`, quattro occhi veri (`permessi.motivo_blocco_approvazione`) e riverifica del lavoratore (`difetti_iter_puntuale`: `QUATTRO_OCCHI`, `APPROVAZIONE_ASSENTE`; approvatore attivo, dell'ente, con ruolo ammesso adesso); indice unico solo per le pratiche non respinte.
- **D24 — rettifica**: `libs/verumdoc_rules/atti_sistema.py`: l'atto XML di rettifica dal canale CICLO_VITA è sempre ammesso (RGL-105, RGL-107/108, estensioni del gateway, passo 4 della verifica); da un altro canale vale la regola della classe.
- **D04 — retention permanente**: `libs/shared/parametri.orizzonte_permanente_anni` per aip-builder e hashchain; rinnovo (passo «rinnovo») con fatto `RINNOVATA` (PdA, senza pratica) o `RINNOVATA_ALLEGATO` e foglia `rinnovo_retention`; `aip.retention_fino_a` con indice parziale sui permanenti.
- **D05 — frequenza e campione**: `hashchain/app/verifica_continua/cicli.py` (stessa regola «più sicuro» del motore, con `LIMITE.VERIFICA_CICLO_GIORNI`), `repository.campione` e `campione_dopo_incidente`.
- **D27 — controllo dell'audit**: `audit_worm.verifica(incrementale=True)` con àncora, `operatore.modo_automatico`, CronJob con login e ruolo propri (`topologia.account_di_servizio` e Terraform includono i job periodici con `iam`).
- **Registro delle impronte**: tipi di foglia `chiusura_eccezioni`, `minimizzazione`, `rinnovo_retention` (`libs/catena/foglie.py`, non invalidano il contenuto) con riscontro nel database (`hashchain/app/registro/interne.py`).
- **Errori nuovi**: `LC-422-MOTIVO`, `LC-403-QUATTRO-OCCHI`, `LC-409-APPROVAZIONE`.
- **Frontend**: `ConfCicloVitaPage`, riquadri «In breve» e «Seconda approvazione», motivo minimo per «Chiudi con eccezioni»; prova end-to-end `e2e/r09.spec.ts`.

## Correzioni della revisione indipendente (Z1–Z5)
- **Z1 (grave) — quattro occhi senza scorciatoie** (VD-P-90): con la doppia approvazione l'annullamento si chiede con l'accesso di una persona (403 `LC-403-QUATTRO-OCCHI` con una chiave API o il ruolo API_SYSTEM); approva solo ADMIN_TENANT (il motore rifiuta OPERATORE in `ANNULLAMENTO_RUOLI_APPROVAZIONE`) con un account nato prima della richiesta; una richiesta arrivata con una chiave si può solo respingere. Il lavoratore ricontrolla lo stesso (`RICHIEDENTE_NON_PERSONA`, `APPROVATORE_POSTERIORE`).
- **Z2 — versioni trattenute** (D26): lo scarto registra chiave e version id di ogni versione rimasta (`versioni_trattenute`); la verifica continua le cerca una per una e una loro scomparsa è MANCANTE con allarme; mancano «per scarto» solo le chiavi cancellate per intero.
- **Z3 — annullamento applicato dal lavoratore** (VD-P-91): l'API mette in attesa (`RICHIESTA` o `APPROVATA`, lavoro `ANNULLA`), il lavoratore riverifica e in una transazione scrive il fatto `AUTORIZZAZIONI_VERIFICATE`, annulla il PdV e porta la pratica a `ESEGUITA`. Una regola resa più severa nel frattempo rimette la pratica in attesa (PdV intatto). Gli annullamenti in coda al deploy ricevono il fatto dalla migrazione 048. **Cambio visibile**: la risposta di `POST /pratiche/annullamento` senza doppia approvazione è `RICHIESTA` (prima `ESEGUITA`); il PdV diventa `ANNULLATO` al passaggio del lavoratore (secondi).
- **Z4 — rinnovo impossibile** (VD-P-92): vincolo congiunto nel motore (`RETENTION_PERMANENTE_ANNI` > `RETENTION_RINNOVO_SOGLIA_ANNI`, bozza bloccata); il lavoratore non segna il passo come fatto; metrica `verumdoc_ciclo_vita_passi_periodici_in_errore` e allarme `PassoPeriodicoInErrore`.
- **Z5 — rinnovo senza fame**: aip-builder scrive `aip.retention_fino_a`; due code (in scadenza, da censire), solo le versioni sotto la soglia, PdA senza oggetti fuori coda (data sentinella 9999-12-31, metrica e allarme `PdaPermanenteSenzaOggetti`), errori isolati per elemento (`elementi_in_errore`), termine aggiornato solo dopo la foglia (ripresa idempotente).
- **Lease dei passi periodici**: rinnovato prima di ogni elemento; perso il lease il passo si ferma (VD-C-151 per il residuo).

## Correzioni del secondo giro di revisione (W1–W3, sull'annullamento applicato dal lavoratore)
- **W1 — sempre un'uscita**: prima dell'applicazione, se qualcosa è cambiato (PdV preso dalla validazione, chi ha chiesto o approvato non più attivo o ammesso, regola o ruoli cambiati) il lavoratore chiude la pratica **RESPINTA** (motivo in parole, audit `ciclo_vita.annullamento.respinto_dal_sistema`, avviso `ANNULLAMENTO_RESPINTO`); si può chiedere di nuovo. Una pratica bloccata per manomissione (allarme) la respinge un ADMIN_TENANT dell'ente o il conservatore da `POST /pratiche/{id}/approvazione` (`RESPINGI`; approvarla non si può); in console il riquadro «Annullamento fermo». Transizioni nuove: `RICHIESTA → RESPINTA` anche del SISTEMA e del conservatore, `APPROVATA → RESPINTA`.
- **W2 — completamento dopo l'applicazione**: atto, fatto e foglia si completano anche se nel frattempo l'approvatore è disattivato o la regola cambia: dopo il fatto `AUTORIZZAZIONI_VERIFICATE` si ricontrolla solo l'integrità.
- **W3 — annullamenti R08 fra migrazione e Chart**: recuperati dal lavoratore al primo avvio, in modo idempotente (`puntuali.recupera_annullamenti_r08`); mai quelli nati dopo il primo avvio né quelli con approvazioni. `ciclo_vita_periodico` ora la scrive solo il ruolo del lavoratore (migrazione 048).

## Migrazione `20261101_048`
`esiti_esecuzione` (tipo RETENTION, esiti nuovi, ANNULLAMENTO / `AUTORIZZAZIONI_VERIFICATE` con quel fatto per gli annullamenti in coda al deploy, `pratica_id` facoltativo solo per il rinnovo di un PdA, impronta obbligatoria), tipi di foglia nuovi in `catena_foglie_interne` e `catena_registro`, `aip.retention_fino_a` con indice parziale, tabella `ciclo_vita_periodico`, indice unico degli annullamenti non respinti, ruolo `verumdoc_auditor`. Downgrade rifiutato se esistono fatti o foglie dei tipi nuovi.

## Test
Nuovi: `ciclo-vita/tests/test_r09_ciclo_vita_automatico.py` (17), `test_dominio.py` (+3), `hashchain/tests/test_r09_audit_incrementale.py` (2), `test_r05_revisione3_verifica_continua.py` (riscritto per D26/D05: 5), `onboarding/tests/test_r09_console_ciclo_vita.py` (2), `libs/verumdoc_rules/tests/test_r09_atti_sistema.py` (2), gateway e validation (D24), notification (avviso), `infra/k8s` (CronJob con login e ruolo propri), `frontend/e2e/r09.spec.ts` (3). Prove di mutazione sui punti di sicurezza (quattro occhi nell'API e nel lavoratore, approvazione obbligatoria, chiusura con eccezioni: ruolo e motivo, minimizzazione: periodo e solo PdA eliminati, rinnovo mai in riduzione: filtro e guardia dello storage): 10 mutazioni, tutte uccise. Correzione Z: `ciclo-vita/tests/test_r09_revisione_z.py` (14, prove del revisore come regressione), `hashchain` D26 (+3), `libs/verumdoc_rules` (+1, vincolo congiunto), aip-builder (`retention_fino_a`); 12 mutazioni su Z1 e Z3, tutte uccise. Correzione W: `ciclo-vita/tests/test_r09_revisione_w.py` (8, prove del revisore come regressione), 2 test Z aggiornati (RESPINTA invece di blocco); 9 mutazioni su W1–W3 e le 12 di Z1/Z3 rieseguite, tutte uccise.

## Criticità
Chiuse: VD-C-070, VD-C-116, VD-C-135, VD-C-142; con riserve VD-C-137 (VD-C-149). Parzialmente chiuse: VD-C-122 (ripresa automatica solo con lo storage leggibile; gli oggetti senza inventario restano «non determinabili»), VD-C-133 (uscita terminale «chiudi con eccezioni» per gli scarti conclusi con eccezioni; un'esecuzione avviata che non riesce a scrivere verbale o foglie continua a ritentare con l'allarme). Nuove: VD-C-148…151 (vedi registro; 150 parzialmente chiusa dalla correzione Z4–Z5). Scelte di dettaglio da confermare: VD-P-80…92.

Totale 151: 22 aperte, 12 parzialmente chiuse, 27 chiuse con riserve, 90 chiuse.

**Fonti**:
- rapporto di sviluppo R09;
- revisione indipendente in due giri: il primo con Z1–Z5 (1 grave, 4 medi) più una nota sul lease, il secondo mirato sulle correzioni con W1–W3 (3 medi). Tutti i rilievi sono stati corretti prima del rilascio, con test permanenti;
- prove di mutazione: 10 su 10 uccise nello sviluppo, 12 su 12 (Z1, Z3) e 9 su 9 (W1–W3) nella correzione.

---

## Revisione indipendente: rilievi e correzioni

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| **Z1** Quattro occhi aggirabili: richiesta con una chiave API il cui responsabile è un'altra persona | Grave | Con la doppia approvazione la richiesta si fa solo con l'accesso di una persona (403 `LC-403-QUATTRO-OCCHI` per chiave API e API_SYSTEM); approva solo ADMIN_TENANT con account nato prima della richiesta (OPERATORE rifiutato dal motore); una richiesta via chiave si può solo respingere; stessi controlli nel lavoratore (`RICHIEDENTE_NON_PERSONA`, `APPROVATORE_POSTERIORE`). VD-P-90 | `ciclo-vita/tests/test_r09_revisione_z.py` |
| **Z2** D26: accettata anche la scomparsa delle versioni trattenute dello scarto | Medio | Lo scarto registra `versioni_trattenute` (chiave e version id); la verifica continua le cerca una per una (HEAD con VersionId, budget di letture) e la scomparsa è MANCANTE con allarme; «attese mancanti» solo le chiavi cancellate per intero | `hashchain/tests/test_r05_revisione3_verifica_continua.py` (+3) |
| **Z3** Annullamento applicato dall'API, poi BLOCCATO dal lavoratore: PdV ANNULLATO senza atto, fatto né foglia | Medio | L'API mette solo in attesa (lavoro `ANNULLA`); il lavoratore riverifica e in una transazione scrive il fatto `AUTORIZZAZIONI_VERIFICATE`, annulla il PdV, porta la pratica a ESEGUITA; la 048 scrive il fatto per gli annullamenti in coda. VD-P-91 | `test_r09_revisione_z.py` |
| **Z4** Rinnovo WORM fermo in silenzio con anni ≤ soglia | Medio | Vincolo congiunto nel motore (`VINCOLI_CONGIUNTI`), `RinnovoNonPossibile` nel lavoratore (passo non fatto), metrica e allarme `PassoPeriodicoInErrore`. VD-P-92 | `test_r09_revisione_z.py::test_z4_*`, `libs/verumdoc_rules` (+1) |
| **Z5** Rinnovo dei PdA permanenti: fame e blocco in testa alla coda | Medio | `aip.retention_fino_a` scritto da aip-builder; due code; solo versioni sotto soglia; PdA senza oggetti fuori coda (sentinella, allarme `PdaPermanenteSenzaOggetti`); errori isolati per elemento; termine aggiornato dopo la foglia. VD-P-92 | `test_r09_revisione_z.py::test_z5_*`, aip-builder |
| Nota sul lease dei passi periodici | Minore | `tieni_lease()` prima di ogni elemento; lease perso = passo fermo senza concludere. Residuo VD-C-151 | `test_r09_revisione_z.py` |
| **W1** Annullamento bloccato prima dell'applicazione: nessuna uscita, PdV non più annullabile | Medio | Stato cambiato prima dell'applicazione → pratica RESPINTA dal SISTEMA (motivo in parole, audit, avviso `ANNULLAMENTO_RESPINTO`), nuova richiesta possibile; manomissione → BLOCCATA con allarme e uscita «Respingi» (ADMIN_TENANT dell'ente o conservatore); console «Annullamento fermo». VD-P-91 | `ciclo-vita/tests/test_r09_revisione_w.py` |
| **W2** Blocco dopo l'applicazione: PdV ANNULLATO senza atto né foglia | Medio | Con il fatto `AUTORIZZAZIONI_VERIFICATE` la fase REGISTRO ricontrolla solo l'integrità, non persone, ruoli e parametri attuali | `test_r09_revisione_w.py` |
| **W3** Annullamenti R08 nati fra migrazione e Chart senza il fatto | Medio (probabilità bassa) | `puntuali.recupera_annullamenti_r08` a ogni avvio del lavoratore, idempotente, con confine al primo avvio R09 (orologio del DB, `ciclo_vita_periodico` scrivibile solo dal ruolo del lavoratore) | `test_r09_revisione_w.py` |

## Cambi incompatibili
- **Annullamento**: risposta `RICHIESTA` (prima `ESEGUITA`), PdV `ANNULLATO` al passaggio del lavoratore; dopo la conservazione doppia approvazione per impostazione predefinita; con la doppia approvazione niente richieste con chiave API; OPERATORE non approva.
- **Retention**: allegati, verbali e atti nuovi 30 anni con rinnovo automatico (prima 100); orizzonte permanente dal motore.
- **Variabili tolte**: `RETENTION_PERMANENTE_ORIZZONTE_ANNI` (aip-builder, hashchain), `ANNULLAMENTO_RUOLI_*`, `ANNULLAMENTO_DOPO_CONSERVAZIONE`, `ALLEGATI_RETENTION_ANNI` (ciclo-vita).
- **CronJob `verifica-audit`**: login e ruolo IAM propri; non usa più il login della hashchain via PgBouncer.

## Variabili, Secret, database
| Dove | Cosa | Note |
|---|---|---|
| ciclo-vita (lavoratore) | `PERIODICO_ATTIVO` (true), `PERIODICO_CONTROLLO_SECONDI` (60), `PERIODICO_PROPOSTE_SECONDI` (21600), `PERIODICO_RIPRESE_SECONDI` (900), `PERIODICO_MINIMIZZAZIONE_SECONDI` (21600), `PERIODICO_RINNOVO_SECONDI` (86400), `PERIODICO_LEASE_SECONDI` (1800), `PERIODICO_LOTTO` (500) | |
| hashchain (verifica continua) | `VERIFICA_INCIDENTE_CAMPIONE` (true), `VERIFICA_INCIDENTE_PAUSA_ORE` (6) | `VERIFICA_CICLO_GIORNI` vale solo senza il parametro del motore |
| Secret | `hashchain-verifica-audit-secrets`: `DATABASE_URL` del login `vd_auditor` via PgBouncer | Modello in `infra/k8s/secrets/hashchain-verifica-audit-secrets.template.yaml` |
| Database (amministratore) | login `vd_auditor` `IN ROLE verumdoc_auditor`, voce in `userlist.txt` di PgBouncer | Comandi nel README di rilascio, avvertenza 2 |
| Kafka | topic `ciclo_vita.avviso` (3 partizioni, 7 giorni) | Da creare prima di avviare ciclo-vita |
| IAM / Terraform | ruolo `hashchain-verifica-audit` (lettura ed elenco di `registro/`); `retention` di `ciclo-vita-lavoratore` anche sulle chiavi dei PdA | Applicare prima del Chart |
| Console (conservatore) | parametri del gruppo «Ciclo di vita automatico» | Proposta automatica spenta per impostazione predefinita |

## Codici d'errore nuovi
`LC-422-MOTIVO`, `LC-403-QUATTRO-OCCHI`, `LC-409-APPROVAZIONE`.

## File eliminati o spostati
Nessuno: in R09 nessun file del progetto diventa superfluo e `tools/pulizia/pulisci_progetto.py` è invariato.

## Scelte da confermare
VD-P-80…92 in `docs/registro/decisioni.md`. VD-P-80…89 vengono dallo sviluppo, VD-P-90…92 dalla correzione della revisione (Z1, Z3/W1–W3, Z4–Z5).

## Residui
- **VD-C-148**: il rinnovo WORM non copre ancora gli oggetti `registro/` della hashchain (prima della scadenza dei 30 anni).
- **VD-C-149**: la minimizzazione non tocca indice di ricerca, fascicoli, eventi già pubblicati e dettagli dell'audit (R10).
- **VD-C-150** (parzialmente chiusa): lavoro periodico e CronJob dell'audit provati solo in sviluppo; login, PgBouncer, Secret e IRSA da creare e provare in esercizio.
- **VD-C-151**: il lease si rinnova prima di ogni elemento, ma un singolo elemento più lungo del lease può sovrapporsi a un'altra replica; manca il fencing per scrittura (R12).
- Restano le riserve R08 (EU DSS VD-C-143, veraPDF).

## Test eseguiti
Corsa completa dello sviluppo (migrazione a `20261101_048`, PostgreSQL reale, storage S3 con Object Lock): tutte le suite verdi; frontend typecheck, lint, build ed e2e (**47**); `make verifiche` ok.

Dopo la correzione Z (048 su/giù/su): ciclo-vita 195, hashchain 328, aip-builder 185, onboarding 265, libs/shared 339, verumdoc_rules 196, tools/docs 30, infra 7, gateway 770, 0 falliti. Dopo la correzione W: ciclo-vita 203, notification 271, gateway 770, frontend con e2e 47, `make verifiche` ok.

Totale **3.873 passati, 0 falliti** (1 saltato facoltativo). Mutazioni: 10 su 10 nello sviluppo, 12 su 12 (Z) e 9 su 9 (W) nella correzione.

Alla chiusura è stato rieseguito solo il test della pulizia (`tools/pulizia`).
