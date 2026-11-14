# Decisioni da prendere — VerumDoc (30/10/2026)

> **Stato (30/10/2026): tutte le consigliate adottate dal committente.** Pianificazione in `docs/architettura/ROADMAP_R07_R13.md`.

Elenco unico di tutte le decisioni aperte, con opzioni e scelta consigliata. Criteri usati per consigliare, come chiesto dal committente:

1. **Automatizzare** i processi ripetitivi (proposte, avvisi, rinnovi, verifiche) senza mai togliere le autorizzazioni che la norma richiede.
2. **Parametrizzare** quasi tutto: valori predefiniti sensati, modificabili dalla console con livelli di accesso (conservatore → ente → classe → accordo), dentro limiti minimi e massimi.
3. **Semplificare** la vita di chi usa l'interfaccia: meno campi obbligatori, valori precompilati, azioni guidate, avvisi in anticipo invece di errori a cose fatte.

Il meccanismo per i parametri esiste già (R05): catena di livelli, bozze, anteprima dell'impatto, applica, storico, valori «imposti dalla norma» bloccati. Molte scelte consigliate consistono nel portare un valore fisso dentro quel meccanismo.

**Come rispondere:** basta il codice e la lettera, per esempio «D01 C, D02 B, tutte le altre consigliate».

Legenda livelli: **Cons.** = conservatore (SUPER_ADMIN/RSC); **Ente** = amministratore dell'ente (ADMIN_TENANT); **Utente** = preferenza personale.

---

## Rinviata per scelta del committente

**Firma e sigillo qualificati, marca temporale, ancoraggio esterno della catena, controfirma del testimone sull'attestazione** (VD-P-05, VD-P-07, VD-C-068, VD-C-124). Si decide alla fine. Nel frattempo si prepara un'interfaccia a **fornitori intercambiabili**: firma remota CSC v2, HSM/PKCS#11, TSA RFC 3161. Ogni fornitore è registrato dal conservatore in console, con priorità, ripiego automatico su un secondo fornitore e prova di collegamento. Si mantiene anche un fornitore «di prova» per sviluppo e collaudo.

---

## A. Decisioni normative e tecniche ancora aperte

### D01 — Verifica delle firme dei documenti versati (VD-P-03, VD-C-073; chiude anche VD-C-120 e VD-C-141)
- A. EU DSS (libreria della Commissione europea) in un servizio Java affiancato: Trusted List UE, OCSP/CRL, tutti i formati (CAdES, PAdES, XAdES, ASiC).
- B. pyHanko + asn1crypto in Python: più semplice, copertura parziale (soprattutto PDF).
- C. Servizio esterno a pagamento.
- **D. (consigliata)** Verificatori **intercambiabili**, con EU DSS come predefinito. Il livello di verifica è un parametro della classe documentale: nessuna / firma rilevata / crittografica / crittografica con stato di revoca. L'effetto (BLOCCA, AVVISA, IGNORA) usa il motore regole esistente. Livello: Cons. fissa il minimo, Ente può solo alzarlo. Con la verifica vera, l'autorizzazione allo scarto e le richieste di annullamento diventano verificabili davvero.

### D02 — Riconoscimento dei formati (VD-P-04, VD-C-074)
- A. Siegfried (PRONOM).
- B. DROID.
- **C. (consigliata)** Siegfried + veraPDF per i PDF/A, con un **catalogo dei formati ammessi** modificabile in console. Il conservatore imposta il catalogo base dall'Allegato 2, l'ente lo restringe per classe. Ogni formato ha un esito: ammesso, ammesso con avviso, da convertire, rifiutato. L'interfaccia mostra prima del versamento quali file verrebbero rifiutati (validatore preventivo già esistente).

### D03 — Norma UNI 11386:2020 con il suo XSD (VD-N-01, VD-C-071)
- **A. (consigliata)** Acquistarla. Senza XSD l'indice del pacchetto di archiviazione non è verificabile contro lo schema ufficiale.
- B. Non acquistarla per ora e dichiarare il limite.
- **Stato R11 (31/10/2026):** predisposizione realizzata senza la norma (validatore XSD caricabile, test di conformità che si attivano con lo schema ufficiale, istruzioni nel README). L'acquisto resta da fare.

### D04 — Durata del blocco WORM per le classi a conservazione permanente (VD-N-03, VD-C-070)
- A. 50 anni.
- B. 100 anni.
- **C. (consigliata)** Parametro con **rinnovo automatico**. Predefinito 30 anni. Un processo giornaliero estende il blocco quando mancano meno di N anni (parametro, predefinito 5). Il legal hold resta come seconda protezione. Livello: Cons. (valore e soglia), Ente in sola lettura. Si evitano scadenze dimenticate e si paga solo l'orizzonte necessario. Vale anche per allegati, verbali e atti del ciclo di vita (oggi 100 anni fissi).

### D05 — Frequenza della verifica continua dell'archivio (VD-C-116)
- A. Fissa a 365 giorni.
- **B. (consigliata)** Parametro: predefinito 365 giorni, massimo imposto dal manuale. Classi o enti più critici possono avere una frequenza maggiore (per esempio 90 giorni). Livello: Cons. In più, verifica a campione dopo ogni incidente di storage, avviata in automatico dagli allarmi.

### D06 — Dati personali dei pacchetti scartati (VD-C-137)
- A. Restano tutti i metadati.
- **B. (consigliata)** Dopo lo scarto resta solo l'**elenco di scarto minimo**: identificativo, classe, date, impronta, pratica, verbale. Gli altri metadati si cancellano in automatico dopo un periodo parametrico (predefinito 30 giorni, per consentire controlli). Livello: Cons., con riferimento al GDPR nel manuale.
- C. Cancellare tutto subito: perde la prova di cosa è stato scartato.

### D07 — Profili di metadati dell'Allegato 5 (VD-C-136)
Non è una scelta ma un lavoro: alcuni campi sono segnati «da riscontrare» sul testo ufficiale.
- **A. (consigliata)** Riscontro sul testo ufficiale e profili mantenuti come dati versionati, modificabili in console dal conservatore. L'editor esiste dal R05; l'ente può solo aggiungere campi facoltativi propri.
- B. Lasciarli come sono.
- **Stato R11 (31/10/2026): realizzata la A** sul testo ufficiale AgID (versioni 2 ad adozione esplicita per classe, `docs/conformita/riscontro_allegato5.md`); resta il §4 oltre p. 84 (VD-C-157) e la firma del responsabile prima dell'attivazione.

### D08 — Accesso delle persone in produzione (VD-C-069)
- A. Solo SPID/CIE tramite un aggregatore qualificato.
- **B. (consigliata)** Fornitori d'identità **configurabili**: SPID/CIE via aggregatore per la PA, più accesso con passkey o password e codice TOTP per i clienti privati senza SPID. Livello: Cons. registra i fornitori; Ente sceglie quali metodi sono ammessi per i propri utenti (per esempio una PA può imporre solo SPID/CIE). Le persone vengono invitate per e-mail con attivazione guidata.
- C. Solo account locali.

### D09 — Pulizia della cronologia Git dalla vecchia chiave (VD-P-28)
- **A. (consigliata)** Pulire una volta con `git filter-repo`, con script pronto e istruzioni per riallineare le copie locali. La rotazione della chiave è già fatta.
- B. Basta la rotazione.

### D10 — Licenza di asyncssh per il server SFTP (VD-P-32)
- **A. (consigliata)** Accettarla: usata senza modifiche e senza distribuire binari.
- B. Passare a Paramiko (circa 350 righe da riscrivere).

### D11 — Motore di policy OPA mai usato (VD-C-034)
- **A. (consigliata)** Toglierlo: le regole vivono già in `verumdoc_rules`, configurabili da console.
- B. Implementarlo: un secondo luogo per le regole, più complessità.

### D12 — IP del cliente sul canale SFTP (VD-C-107)
- **A. (consigliata)** Proxy protocol sul bilanciatore, così l'IP reale arriva al server. Allow-list di IP per account, impostata dall'ente in console e facoltativa. IP nei log d'accesso.
- B. Lasciare com'è (nessun filtro per IP).

---

## B. Parametri da portare in console (valori oggi fissi)

### D13 — Quote per piano (VD-P-33)
- A. Valori fissi di R03 (starter 1.000 PdV al giorno e 120 richieste al minuto; business 20.000 e 600; enterprise 200.000 e 6.000).
- **B. (consigliata)** Piani **modificabili in console** dal conservatore, con quei valori come modello iniziale e deroga per singolo ente. Avvisi automatici all'ente all'80% e al 100%, con proposta di cambio piano. Al superamento: rallentamento invece di rifiuto, se l'ente lo sceglie.

### D14 — Sessione del portale (VD-P-51)
- A. Come realizzato: 30 minuti di inattività, 15 per SUPER_ADMIN e RSC; ADMIN_TENANT a 30.
- **B. (consigliata)** Inattività **per ruolo, modificabile in console**: il conservatore fissa il massimo, l'ente può solo abbassarlo. Predefiniti: 15 minuti per SUPER_ADMIN, RSC e ADMIN_TENANT (che autorizza scarti), 30 per gli altri. Avviso 2 minuti prima con «resta collegato». Revoca immediata senza cache, come oggi.

### D15 — Scadenza dei file dei versamenti rifiutati (VD-P-36)
- A. 30 giorni.
- B. 90 giorni.
- **C. (consigliata)** Parametro per ente: predefinito 90 giorni, tra 30 e 180. Avviso automatico 7 giorni prima, con il link «Rielabora».

### D16 — Porte ammesse per i webhook (VD-P-37)
- A. Solo 443.
- **B. (consigliata)** 443 predefinita. Il conservatore può ammettere altre porte per singolo ente, con motivo registrato.

### D17 — Fine della transizione per la chiave di idempotenza (VD-P-27)
- **A. (consigliata)** 31/03/2027 predefinito, prorogabile per singolo ente dalla console del conservatore, con avviso automatico ai clienti che ancora non la inviano (si vede dai log).
- B. Data unica per tutti.

### D18 — Validità dei pacchetti di restituzione (VD-P-47)
- A. 72 ore fisse.
- **B. (consigliata)** Parametro: predefinito 72 ore, fino a 30 giorni. Rigenerazione con un clic quando scadono.

### D19 — Periodo dell'export dell'audit nel WORM (VD-P-48)
- A. 1 ora con 10 minuti di margine.
- **B. (consigliata)** Parametro del conservatore: predefinito 15 minuti, tra 5 e 60. Riduce il periodo scoperto, al costo di più segmenti (costo trascurabile).

### D20 — Tentativi dello scarto per pacchetto (VD-P-46)
- A. 5 fissi.
- **B. (consigliata)** Parametro (predefinito 5). Più la ripresa **automatica** dei pacchetti in «eliminazione incompleta» quando il blocco legale viene tolto, con notifica. Il pulsante «chiudi con eccezioni» per il conservatore resta per i casi definitivi.

---

## C. Processi da automatizzare

### D21 — Proposte di scarto
- A. Solo manuali, come oggi.
- **B. (consigliata)** Proposta **automatica** periodica: ogni mese, configurabile per ente (attiva/disattiva, frequenza, classi escluse). Il sistema prepara la pratica in bozza con l'anteprima dei pacchetti scartabili e avvisa l'ente. L'esecuzione resta sempre manuale con autorizzazioni e nulla osta, come vuole la norma.

### D22 — Nulla osta della Soprintendenza firmato (VD-P-45)
- A. Firma obbligatoria.
- B. Firma facoltativa, come oggi.
- **C. (consigliata)** Parametro per ente: predefinito «firma richiesta, con avviso se manca». Il conservatore può renderla bloccante. Con D01 la firma viene verificata davvero.

### D23 — Chi può annullare e quando (VD-P-47, VD-P-54)
- A. Regole fisse come oggi.
- **B. (consigliata)** Regola **configurabile per ente**: ruoli ammessi, stati ammessi (prima o dopo la conservazione), e **doppia approvazione** facoltativa (una seconda persona conferma). Predefinito: doppia approvazione attiva dopo la conservazione. Con D01 riduce molto il rischio residuo VD-C-141.

### D24 — Rettifica quando la classe non ammette XML (VD-P-47)
- **A. (consigliata)** L'atto di rettifica XML è sempre ammesso come tipo di sistema, indipendentemente dai formati della classe: nessun rifiuto inatteso e nessuna configurazione da ricordare.
- B. L'ente deve aggiungere XML alla classe.
- C. La rettifica copia anche i file originali (più spazio occupato).

### D25 — Natura dell'ente: motivo obbligatorio anche per la PA (VD-P-56)
- A. Motivo sempre obbligatorio, come oggi.
- **B. (consigliata)** Motivo solo per PRIVATO; per la PA si precompila dall'indice IPA quando il codice è presente.

### D26 — Pacchetti in «eliminazione incompleta» e verifica continua (VD-C-135)
- A. Esclusi del tutto, come oggi.
- **B. (consigliata)** Si verificano solo le versioni rimaste (per esempio quelle sotto blocco legale), senza falsi allarmi.

### D27 — Controllo giornaliero dell'audit (VD-P-55, VD-C-142)
- A. Come oggi: verifica completa ogni giorno con le credenziali della catena.
- **B. (consigliata)** Ruolo e login dedicati in sola lettura, verifica **incrementale** (solo i segmenti nuovi) ogni giorno e completa ogni mese (parametro).

### D28 — Modifiche di configurazione programmate (VD-P-44)
- A. La decorrenza futura vale solo per accordi e profili, come oggi.
- **B. (consigliata)** Decorrenza futura per **ogni** impostazione: si programma oggi una modifica che parte il 1° gennaio, con anteprima dell'impatto ricalcolata il giorno prima e avviso a chi l'ha programmata.

---

## D. Interfaccia e permessi

### D29 — Ruoli globali e scelta dell'ente (VD-P-49)
- A. Il conservatore vede sempre tutti gli enti insieme.
- **B. (consigliata)** Selettore dell'ente in testata con l'opzione «tutti», ricordato fra una sessione e l'altra. Le liste partono dall'ente scelto: meno rumore e meno errori.

### D30 — Ruoli e permessi personalizzabili (VD-P-38, VD-P-14)
- A. Ruoli fissi, come oggi.
- **B. (consigliata)** Ruoli fissi come **modelli**, più ruoli personalizzati che l'ente compone da un elenco di permessi (per esempio «operatore che può anche richiedere annullamenti»). Il conservatore stabilisce quali permessi sono concedibili e quali mai, per esempio l'esecuzione dello scarto. È un lavoro di un rilascio dedicato.

### D31 — Preferenze personali dell'utente (VD-DR-05)
- A. Solo il tema, nel browser.
- **B. (consigliata)** Preferenze salvate sul server e valide su ogni dispositivo: tema (automatico/chiaro/scuro), densità delle tabelle, pagina iniziale, colonne visibili, notifiche ricevute (e-mail o in console, per tipo di evento).

### D32 — Centro notifiche
- A. Solo e-mail e webhook, come oggi.
- **B. (consigliata)** Campanella in console con «da fare per te» (pratiche in attesa, quote vicine al limite, file in scadenza, allarmi). Ogni utente sceglie cosa ricevere e come; l'ente può rendere obbligatori alcuni avvisi.

---

## E. Da ratificare in blocco (già realizzate, nessuna controindicazione)

Si possono confermare tutte insieme salvo obiezioni:
- **Regole e isolamento:** VD-P-01 (motore regole), VD-P-02 (RLS), VD-P-06/43 (servizio ciclo di vita).
- **Canali e Kafka:** VD-P-34 (chiave Kafka per risorsa), VD-P-35 (cambi incompatibili R03).
- **Dettagli tecnici R04–R06:**
  - VD-P-42 (dettagli R04, salvo il ciclo di 365 giorni, vedi D05);
  - VD-P-44 (permessi della configurazione), salvo D28;
  - VD-P-45/46 (regole ed esecuzione dello scarto), salvo D20, D22, D26;
  - VD-P-47, salvo D18, D23, D24;
  - VD-P-48 (catena e verificatore), salvo D19;
  - VD-P-49, salvo D29;
  - VD-P-50 (integrazione);
  - VD-P-52 (rifiuto delle credenziali doppie);
  - VD-P-53 (privilegi della tabella sessioni);
  - VD-P-55, salvo D27;
  - VD-P-56, salvo D25.
- **Interfaccia:** VD-DR-05 (console R05 e tema scuro automatico).

---

## Riepilogo delle consigliate

| Codice | Consigliata | Impatto per l'utente |
|---|---|---|
| D01 | Verificatori intercambiabili, EU DSS predefinito, livello per classe | Firme verificate davvero; chiude i rischi residui di scarto e annullamento |
| D02 | Siegfried + veraPDF, catalogo formati in console | Rifiuti previsti prima di versare |
| D03 | Acquistare la UNI 11386 | Indice verificabile |
| D04 | Retention parametrica con rinnovo automatico | Nessuna scadenza dimenticata |
| D05 | Frequenza della verifica come parametro | Classi critiche controllate più spesso |
| D06 | Elenco di scarto minimo, cancellazione automatica del resto | Conformità GDPR |
| D07 | Riscontro dei profili ed editor in console | — |
| D08 | Fornitori d'identità configurabili, SPID/CIE e passkey | Accesso anche per i clienti privati |
| D09 | Pulizia della cronologia Git | — |
| D10 | Accettare asyncssh | — |
| D11 | Togliere OPA | — |
| D12 | Proxy protocol e allow-list IP per account | Sicurezza SFTP |
| D13 | Piani e quote in console, avvisi automatici | Nessun blocco a sorpresa |
| D14 | Inattività per ruolo in console, avviso prima della scadenza | Meno uscite inattese |
| D15 | Scadenza staging parametrica con avviso | — |
| D16 | Porte webhook per ente | — |
| D17 | Transizione idempotenza prorogabile per ente | — |
| D18 | Validità restituzione parametrica | — |
| D19 | Export audit ogni 15 minuti (parametro) | — |
| D20 | Ripresa automatica dello scarto incompleto | Meno interventi manuali |
| D21 | Proposte di scarto automatiche mensili | L'ente riceve la pratica pronta |
| D22 | Firma del nulla osta configurabile | — |
| D23 | Annullamento configurabile con doppia approvazione | — |
| D24 | Atto di rettifica sempre ammesso | Nessun rifiuto inatteso |
| D25 | Motivo solo per PRIVATO | Un campo in meno |
| D26 | Verifica delle versioni rimaste | Niente falsi allarmi |
| D27 | Controllo dell'audit incrementale in sola lettura | — |
| D28 | Modifiche programmate per ogni impostazione | Cambi pianificati |
| D29 | Selettore dell'ente in testata | Liste più pulite |
| D30 | Ruoli personalizzati da modelli | Flessibilità per l'ente |
| D31 | Preferenze personali sul server | Console su misura |
| D32 | Centro notifiche con «da fare per te» | Nulla sfugge |
