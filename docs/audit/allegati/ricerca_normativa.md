# Ricerca normativa: requisiti per un servizio di conservazione digitale in Italia

Data della ricerca: 20 ottobre 2026. Scopo: base per la gap analysis di un software di conservazione.

Legenda affidabilità:
- **[V]** verificato su fonte primaria (AgID, ACN, EUR-Lex, ETSI, Gazzetta Ufficiale)
- **[V2]** verificato su fonte secondaria autorevole (documenti di PA, stampa specializzata)
- **[NV]** non verificato o verificato solo in parte (vedi §9)

Nota di metodo: il proxy di rete bloccava il download diretto dei PDF (curl 403), quindi i testi sono stati letti con WebFetch. Questo strumento **tronca i PDF lunghi a circa 100.000 caratteri**. Per questo motivo la seconda metà del cap. 4 delle Linee Guida (processo, infrastrutture, esibizione, sicurezza, scarto) **non si è potuta leggere nel testo definitivo**. È stata ricostruita dalla bozza ufficiale su docs.italia.it e da fonti incrociate (vedi §1.6 e §9).

---

## 0. Quadro normativo in sintesi

| Livello | Atto | Stato al 20/10/2026 | Fonte |
|---|---|---|---|
| Legge | CAD, D.Lgs. 82/2005: art. 34 c.1-bis (affidamento a terzi da parte della PA), art. 43 e 44 (requisiti di conservazione), art. 71 (Linee guida) | vigente | [AgID – Conservazione](https://www.agid.gov.it/piattaforme/conservazione) [V] |
| Linee guida | Linee Guida AgID sulla formazione, gestione e conservazione dei documenti informatici (settembre 2020, aggiornate maggio 2021), con 6 allegati | in applicazione dal 1/1/2022; dal 2022 a oggi non ho trovato modifiche formali | [PDF AgID](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf) [V] |
| Regolamento | Regolamento sui criteri per la fornitura dei servizi di conservazione (Det. AgID 455/2021, modificata dalla Det. 629/2021), Allegato A (requisiti), Allegato B (piano di cessazione) | in vigore dal 1/1/2022; abroga la Circolare AgID 65/2014 | [Notizia AgID](https://www.agid.gov.it/it/agenzia/stampa-e-comunicazione/notizie/2021/12/17/documento-informatico-modifiche-al-regolamento-sui-servizi-conservazione) [V] |
| Cloud PA | Regolamento ACN per le infrastrutture digitali e i servizi cloud per la PA (Decreto direttoriale ACN n. 21007/24 del 27/6/2024) | in applicazione dal 1/8/2024 | [Testo](https://dirittobancario.it/wp-content/uploads/2024/07/Regolamento-ACN-27-giugno-2024.pdf) [V2] |
| UE | eIDAS 910/2014 modificato dal Reg. (UE) 2024/1183 (eIDAS 2), con il nuovo servizio fiduciario qualificato di **archiviazione elettronica** | vigente | [Reg. esec. 2025/2532](https://eur-lex.europa.eu/eli/reg_impl/2025/2532/oj) [V] |
| UE | Reg. di esecuzione (UE) 2025/2532 (norme di riferimento per i servizi qualificati di archiviazione elettronica) | in vigore dal 6/1/2026 | [EUR-Lex](https://eur-lex.europa.eu/eli/reg_impl/2025/2532/oj) [V] |
| UE | Reg. di esecuzione (UE) 2025/1946 (servizi qualificati di conservazione di firme e sigilli qualificati) | in vigore dal 20/10/2025 | [EUR-Lex](https://eur-lex.europa.eu/eli/reg_impl/2025/1946/oj/eng) [V] |
| Fiscale | DM MEF 17 giugno 2014 (GU n. 146 del 26/6/2014) | vigente | [Gazzetta Ufficiale](http://www.gazzettaufficiale.it/eli/id/2014/06/26/14A04778/sg) [V] |
| Beni culturali | D.Lgs. 42/2004, art. 21 (autorizzazione allo scarto e al trasferimento) e art. 41 (versamento agli Archivi di Stato) | vigente | [Brocardi art. 21](https://www.brocardi.it/codice-dei-beni-culturali-e-del-paesaggio/parte-seconda/titolo-i/capo-iii/sezione-i/art21.html) [V2] |

Abrogazioni: con l'applicazione delle Linee Guida decadono il DPCM 3/12/2013 (conservazione), il DPCM 13/11/2014 e parte del DPCM 3/12/2013 sul protocollo. Restano in vigore alcune norme sul protocollo. [V] ([LG, cap. 1](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf))

---

## 1. Linee Guida AgID (cap. 4, Conservazione)

### 1.1 Struttura del capitolo 4 nel testo definitivo (maggio 2021)

Il sommario del PDF definitivo elenca 11 sezioni senza numerazione [V] ([PDF](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf)):

1. Sistema di conservazione
2. Pacchetti informativi
3. Modelli organizzativi della conservazione
4. Ruoli e responsabilità
5. Responsabile della conservazione
6. Manuale di conservazione
7. Processo di conservazione
8. Infrastrutture
9. Modalità di esibizione
10. Misure di sicurezza
11. Selezione e scarto dei documenti informatici

**Attenzione alla numerazione [NV].** AgID cita il manuale come "par. 4.6" ([pagina AgID](https://www.agid.gov.it/piattaforme/conservazione)) e il processo come "par. 4.7 lett. h" ([AgID, Modelli di interoperabilità, dic. 2022](https://www.agid.gov.it/sites/default/files/repository_files/interoperabilita_aip.pdf)). Questo è coerente con l'elenco sopra (4.1–4.11). I rimandi interni del testo definitivo ("par. 4.7" per il manuale, "par. 4.11" per la sicurezza) sembrano invece ereditati dalla bozza. La bozza aveva in più un §4.4 "Conservatori accreditati", poi eliminato ([bozza docs.italia](https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/conservazione.html)). **Nella gap analysis conviene citare i titoli dei paragrafi, non i numeri.**

### 1.2 Sistema di conservazione e pacchetti [V]

- Il sistema deve garantire **autenticità, integrità, affidabilità, leggibilità e reperibilità** degli oggetti conservati ([AgID](https://www.agid.gov.it/piattaforme/conservazione)).
- Il sistema di conservazione è **almeno logicamente distinto** dal sistema di gestione documentale. Conserva documenti, aggregazioni (fascicoli e serie) e metadati per tutto il periodo previsto dal piano di conservazione ([LG](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf)).
- I pacchetti informativi sono tre: **PdV** (versamento), **PdA** (archiviazione) e **PdD** (distribuzione). L'interoperabilità si basa su **UNI 11386 (SInCRO)** ([LG](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf)).
- **Modelli organizzativi**: la PA può conservare in proprio o affidare la conservazione, in tutto o in parte, a soggetti esterni pubblici o privati. Responsabilità e requisiti vanno formalizzati nel manuale e nel contratto. Il testo richiama ISO/IEC 27001, ISO 14721 (OAIS) ed ETSI TS 101 533-1 ([LG](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf)). Nota: per il Regolamento, ETSI TS 101 533-1 è stata superata da ETSI TS 119 511 dopo il 31/12/2022 (vedi §2.2, RG6).

### 1.3 Ruoli [V]

Fonte: [LG](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf) e [FAQ AgID](https://www.agid.gov.it/it/domande-frequenti/conservazione).

| Ruolo | Descrizione |
|---|---|
| **Titolare dell'oggetto della conservazione** | Il soggetto produttore degli oggetti conservati |
| **Produttore dei PdV** | Chi forma e trasmette i PdV. Nella PA è un soggetto interno, di norma il responsabile della gestione documentale o il coordinatore |
| **Utente abilitato** | Chi richiede l'accesso agli oggetti conservati nei limiti di legge |
| **Responsabile della conservazione** | Nella PA è una persona fisica dipendente del titolare. Nei privati può essere esterno, purché terzo rispetto al conservatore |
| **Conservatore** | Il soggetto, pubblico o privato, che svolge il servizio quando la conservazione è affidata all'esterno |

**Compiti del responsabile della conservazione (lettere a–m)** [V] ([LG](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf)). Possono essere affidati al conservatore, tranne la redazione del manuale.

- a) definisce politiche di conservazione e requisiti funzionali del sistema
- b) gestisce il processo di conservazione e ne garantisce nel tempo la conformità
- c) **genera e sottoscrive il rapporto di versamento**
- d) **genera e sottoscrive il PdD**, con firma qualificata nei casi previsti dal manuale
- e) monitora il corretto funzionamento del sistema
- f) **verifica periodicamente integrità e leggibilità, con cadenza non superiore a cinque anni**
- g) adotta misure contro il degrado dei supporti e l'obsolescenza dei formati
- h) provvede a duplicazione o copia in base all'evoluzione tecnologica
- i) predispone le misure di sicurezza fisica e logica
- j) assicura la presenza di un pubblico ufficiale quando richiesto
- k) fornisce assistenza e risorse agli organismi di verifica e vigilanza
- l) per le amministrazioni statali, versa i documenti all'Archivio centrale dello Stato o agli Archivi di Stato (art. 41 Codice beni culturali)
- m) **predispone e aggiorna il manuale di conservazione**

Ruoli richiesti al conservatore dal Regolamento (Allegato A, RO1) [V] ([Allegato A](https://www.agid.gov.it/sites/default/files/repository_files/allegato_a_.pdf)): almeno il **Responsabile del servizio di conservazione** e il **Responsabile della funzione archivistica di conservazione**. Nella prassi i manuali dei conservatori aggiungono anche Sicurezza, Sistemi informativi, Sviluppo e manutenzione, e DPO. La bozza delle LG elencava questi sei profili [V2] ([bozza](https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/conservazione.html)).

### 1.4 Manuale di conservazione: contenuti minimi (lettere a–l) [V]

Fonte: [LG definitive](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf).

- a) dati dei soggetti che nel tempo hanno avuto la responsabilità del sistema, con eventuali deleghe
- b) struttura organizzativa, funzioni, responsabilità e obblighi dei soggetti coinvolti
- c) tipologie di oggetti conservati, **formati gestiti, metadati** per tipologia ed eventuali eccezioni
- d) modalità di **presa in carico dei PdV** e predisposizione del **rapporto di versamento**
- e) processo di conservazione e trattamento dei **PdA**
- f) modalità di **esibizione ed esportazione** con produzione del **PdD**
- g) descrizione del sistema (componenti tecnologiche, fisiche e logiche) e procedure di gestione ed evoluzione
- h) procedure di **monitoraggio** e di **verifica dell'integrità** degli archivi, con le soluzioni in caso di anomalie
- i) procedure per la produzione di **duplicati o copie**
- j) **tempi di versamento e di scarto**, se non già nel piano di conservazione
- k) modalità di richiesta della presenza di un **pubblico ufficiale**
- l) **normative in vigore nei luoghi** dove sono conservati gli oggetti

Le PA adottano il manuale con provvedimento formale e lo pubblicano in "Amministrazione trasparente" ([FAQ AgID](https://www.agid.gov.it/it/domande-frequenti/conservazione)). Il Regolamento (Allegato A, RO7) chiede al conservatore un **manuale che includa il piano della sicurezza aggiornato e il modello organizzativo** [V].

### 1.5 Processo di conservazione [NV sul testo definitivo, V2 sulla sostanza]

Fonte principale: bozza ufficiale ([docs.italia, §4.8 bozza](https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/conservazione.html)). La sostanza è confermata dalla versione definitiva tramite il documento [AgID interoperabilità 2022](https://www.agid.gov.it/sites/default/files/repository_files/interoperabilita_aip.pdf), il [commento Filodiritto 2021](https://www.filodiritto.com/linee-guida-agid-processo-di-conservazione-dei-documenti-informatici) (sigillo elettronico e SInCRO introdotti nella versione 2021) e i manuali InfoCert e Unimatica.

1. **Acquisizione** del PdV per la presa in carico.
2. **Verifica** di coerenza del PdV e dei suoi oggetti con il manuale e con l'**Allegato 2** (formati).
3. **Rifiuto** del PdV in caso di anomalie. La bozza prevedeva un numero massimo di rifiuti stabilito nel contratto e non inferiore a tre; non ho verificato se la clausola sia rimasta nel testo definitivo.
4. Generazione, anche automatica, del **rapporto di versamento** (RdV) per uno o più PdV. Il RdV ha un **identificativo univoco**, un **riferimento temporale in UTC** e **una o più impronte** calcolate sull'intero contenuto del PdV.
5. Eventuale **sottoscrizione** del RdV (firma digitale o qualificata del responsabile, oppure sigillo).
6. Preparazione, **sottoscrizione** (firma qualificata o digitale, o **sigillo elettronico** qualificato) e gestione del **PdA** secondo la struttura dati dell'**Allegato 4**, cioè l'indice **UNI 11386 SInCRO**.
7. Preparazione e sottoscrizione del **PdD** per l'esibizione richiesta dall'utente.
8. Per l'**interoperabilità tra sistemi di conservazione**, produzione di **PdD coincidenti con i PdA**. AgID conferma che questa voce è nel testo definitivo ("par. 4.7 lett. h") ma la giudica un obiettivo generico, privo di indicazioni operative ([AgID 2022](https://www.agid.gov.it/sites/default/files/repository_files/interoperabilita_aip.pdf)).
9. Produzione di **duplicati e copie** informatiche su richiesta.
10. **Riversamento**, cioè adeguamento dei formati per garantire la leggibilità nel tempo (Allegato 2).
11. **Scarto** del PdA alla scadenza dei termini del piano di conservazione.
12. Per archivi pubblici o privati di particolare interesse storico, scarto **solo previa autorizzazione del Ministero della Cultura** (vedi §3.3).

### 1.6 Infrastrutture, esibizione, sicurezza, scarto [NV sul testo definitivo]

Questi contenuti sono tratti dalla **bozza**. La versione definitiva può essere diversa: in particolare non ho potuto verificare se il vincolo sul territorio nazionale sia rimasto.

- **Infrastrutture (bozza)**: conservazione dei dati e delle copie di sicurezza **sul territorio nazionale**; componenti separate fisicamente e logicamente; se in cloud, servizio qualificato e presente nel catalogo cloud della PA. Va confrontato con l'Allegato A del Regolamento, che chiede solo di **dichiarare** se i data center sono in Italia, nell'UE o extra-UE (RO5) e, per l'extra-UE, la consultazione preventiva del Garante (RO6) [V]. **Il Regolamento è quindi meno restrittivo della bozza.**
- **Esibizione**: accesso diretto, anche da remoto, degli utenti autorizzati agli oggetti conservati, tramite **PdD**. In caso di affidamento esterno le modalità sono concordate tra le parti ([bozza](https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/conservazione.html)).
- **Misure di sicurezza**: nella PA il responsabile della conservazione predispone il **piano della sicurezza** del sistema, insieme al responsabile della sicurezza e al responsabile per la transizione digitale, sentito il DPO. Il piano rispetta l'art. 32 GDPR e le **Misure minime ICT AgID**. Il manuale descrive la procedura per i data breach (artt. 33-34 GDPR). I privati possono fare riferimento all'art. 51 CAD ([bozza](https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/conservazione.html)).
- **Log**: **non ho trovato** un requisito esplicito sui log nel cap. 4. I log sono richiesti indirettamente da:
  - il monitoraggio (compito e) e le verifiche di integrità (manuale, lett. h);
  - ETSI EN 319 401 / TS 119 511 richiamate dall'Allegato A (RG7) (vedi §5);
  - i requisiti di sicurezza dei sistemi di protocollo (LG §3.1.6), che però riguardano la gestione documentale.

- **Selezione e scarto (bozza)**: lo scarto è deciso dal titolare secondo il piano di conservazione. Il responsabile della conservazione genera l'**elenco dei PdA da scartare** e lo comunica al responsabile della gestione documentale. Servono poi le **autorizzazioni** degli organi di tutela. La distruzione viene **tracciata con metadati** e l'esito notificato agli organi di tutela. Lo scarto diventa efficace solo dopo l'aggiornamento delle **copie di sicurezza** e va eseguito anche nei sistemi del titolare.

### 1.7 Allegati alle Linee Guida [V]

Fonte: [docs.italia – Allegati](https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/allegati.html) e [LG](https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf).

| N. | Titolo | Rilevanza per il conservatore |
|---|---|---|
| 1 | Glossario dei termini e degli acronimi | Definizioni (PdV, PdA, PdD, RdV, impronta, riferimento temporale…) |
| 2 | Formati di file e riversamento | Formati ammessi, valutazione di interoperabilità e procedure di riversamento: controllo dei formati in acquisizione |
| 3 | Certificazione di processo | Copie per immagine di documenti analogici con certificazione di processo |
| 4 | Standard e specifiche tecniche | Elenco degli standard. Per la conservazione: UNI 11386, ISO 14721, ISO 15836, ISO/TR 18492, ISO 20652, ISO 20104, SIARD 2.0, METS, PREMIS, EAD/ISAD(G), EAC/ISAAR(CPF). Per l'affidabilità: ISO 16363, ISO 16919, ISO 17068. Per la sicurezza: ETSI TS 101 533-1 e TR 101 533-2 ([Allegato 4](https://www.agid.gov.it/sites/default/files/repository_files/allegato_4_standard_e_specifiche_tecniche.pdf)) |
| 5 | Metadati | Set minimo per documento, documento amministrativo e aggregazione (vedi §1.8) |
| 6 | Comunicazione tra AOO di documenti amministrativi protocollati | Sostituisce la circolare AgID 60/2013. Riguarda il protocollo più che la conservazione |

### 1.8 Allegato 5: metadati obbligatori [V]

Fonte: [Allegato 5, maggio 2021](https://www.agid.gov.it/sites/agid/files/2024-06/allegato_5_metadati.pdf). La tabella delle aggregazioni era troncata dopo "ProcedimentoAmministrativo"; gli ultimi campi sono da verificare.

**Documento informatico**

| Metadato | Obbligatorietà |
|---|---|
| IdDoc (ImprontaCrittograficaDelDocumento: Impronta + Algoritmo; Identificativo) | O |
| ModalitaDiFormazione | O |
| TipologiaDocumentale | O |
| DatiDiRegistrazione (TipologiaDiFlusso, TipoRegistro, data e numero) | O |
| Soggetti (ruoli: autore, mittente, destinatario, operatore…) | O |
| ChiaveDescrittiva: Oggetto (O), ParoleChiave (F) | O |
| Allegati (numero, IdDoc e descrizione) | O |
| Riservato | O |
| IdentificativoDelFormato | O |
| Verifica (firma, sigillo, marcatura, conformità copie) | O, condizionato alla modalità di formazione |
| IdIdentificativoDocumentoPrimario | O solo se esiste un documento primario |
| NomeDelDocumento | O |
| VersioneDelDocumento | O |
| TracciatureModificheDocumento | O se versione > 1 o in caso di annullamento |
| Classificazione, Agg, TempoDiConservazione, Note | F |

**Documento amministrativo informatico**: gli stessi campi del documento informatico, più:
- **IdDoc.Segnatura**, obbligatoria se il documento è protocollato;
- **Classificazione**, obbligatoria (il piano di classificazione è facoltativo);
- **Agg** (identificativo dell'aggregazione), obbligatorio.

TempoDiConservazione e Note restano facoltativi.

**Aggregazione documentale informatica (fascicolo o serie)**

| Metadato | Obbligatorietà |
|---|---|
| IdAgg (TipoAggregazione, IdAggregazione) | O |
| TipologiaFascicolo | O solo per i fascicoli |
| Soggetti (con RUP per i fascicoli) | O |
| Assegnazione | O per i fascicoli |
| DataApertura | O |
| Classificazione (Indice, Descrizione) | O |
| Progressivo | O |
| ChiaveDescrittiva (Oggetto) | O |
| DataChiusura | O quando l'aggregazione viene chiusa |
| ProcedimentoAmministrativo, IndiceDocumenti, TempoDiConservazione, Note… | **[NV]**, testo troncato |

---

## 2. Regolamento conservatori (Det. AgID 455/2021 e 629/2021)

### 2.1 Articolato [V]

Fonte: [Regolamento PDF](https://www.agid.gov.it/sites/default/files/repository_files/regolamento_sui_criteri_di_conservazione.pdf).

- **Art. 1 – Ambito**: requisiti generali, di qualità, sicurezza e organizzazione per chi conserva documenti **per conto della PA**. È **esclusa la conservazione a lungo termine** regolata dal Codice dei beni culturali.
- **Art. 2 – Requisiti**: quelli dell'Allegato A. Il loro possesso è condizione per l'iscrizione al marketplace.
- **Art. 3 – Marketplace**: istituito da AgID. **L'iscrizione è facoltativa**; la PA può rivolgersi anche a conservatori non iscritti, nel rispetto dell'art. 7.
- **Art. 4 – Iscrizione**: domanda sulla piattaforma. AgID decide **entro 30 giorni** dopo una verifica formale (autocertificazione ex DPR 445/2000). Può verificare i requisiti in ogni momento, anche su segnalazione e tramite terzi. Il conservatore deve comunicare ogni evento che incida sui requisiti e mantenere valide le certificazioni.
- **Art. 5 – Cancellazione**: per requisiti mancanti o persi, o per violazioni. Preavviso di 30 giorni per le controdeduzioni. Restano salve le sanzioni dell'art. 32-bis CAD.
- **Art. 6 – Durata**: iscrizione di **48 mesi**. Nei 60 giorni precedenti la scadenza il conservatore invia aggiornamenti e la dichiarazione di persistenza dei requisiti; il rinnovo è automatico.
- **Art. 7 – Conservatori non iscritti**: la PA trasmette il contratto ad AgID **entro 30 giorni** dalla stipula, e AgID verifica i requisiti dell'Allegato A.
- **Art. 8 – Piano di cessazione**: allegato alla domanda. Le modifiche vanno trasmesse ad AgID entro 20 giorni. La cessazione va comunicata ad AgID e alla PA **almeno 60 giorni prima**, con comunicazione firmata dal legale rappresentante.
- **Art. 9 – Entrata in vigore** 1/1/2022, con abrogazione della Circolare 65/2014. I contratti in essere restano validi fino alla scadenza.

### 2.2 Allegato A: requisiti [V]

Fonte: [Allegato A](https://www.agid.gov.it/sites/default/files/repository_files/allegato_a_.pdf). I codici RG, RQ, RS e RO sono quelli usati da AgID nelle FAQ (es. "RG2").

**Requisiti generali (RG)**
- **RG1**: **API SOAP e/o REST** per versamento ed esibizione. Le altre API vanno documentate nel manuale.
- **RG2**: integrabilità con **SPID, CIE** o identità digitali UE notificate, su richiesta del titolare.
- **RG3**: indice del PdA conforme a **UNI 11386 (SInCRO)**.
- **RG4**: conformità a **ISO 14721 (OAIS)**.
- **RG5**: conformità a **ISO 16363** (repository digitali affidabili).
- **RG6**: ETSI TS 101 533-1 fino al 31/12/2022, soddisfatta anche con ETSI TS 119 511. Oggi superata.
- **RG7**: conformità a **ETSI EN 319 401**, applicando i criteri di **ETSI TS 119 511**.
- **RG8**: documentazione del sistema: componenti, procedure di gestione ed evoluzione, **monitoraggio, verifiche di integrità, gestione delle anomalie**.
- **RG9**: documentazione delle procedure di accesso, ricerca, recupero e uso, con relativo monitoraggio e nel rispetto della privacy.
- **RG10**: certificazione **UNI ISO 37001** (anticorruzione), oppure processo di certificazione avviato.
- **RG11**: se in **cloud**, qualificazione ai sensi della Circolare AgID 3/2018. **Il riferimento è superato**: oggi vale il Regolamento ACN 21007/2024 (vedi §2.4).
- **RG12**: mantenimento dei requisiti per restare nel marketplace o partecipare agli affidamenti.

**Requisiti di qualità (RQ)**
- **RQ1**: **ISO 9001** con campo di applicazione specifico per i servizi di conservazione, in alternativa **ISO/IEC 20000**.
- **RQ2**: procedure di cessazione e migrazione secondo l'Allegato B.
- **RQ3**: **account di test** a disposizione di AgID, su richiesta.

**Requisiti di sicurezza (RS)**
- **RS1**: **ISO/IEC 27001** con campo di applicazione specifico per la conservazione.
- **RS2**: test **OWASP** sulle componenti, sviluppate secondo le linee guida AgID per lo sviluppo di software sicuro. I VA/PT sono facoltativi.
- **RS3**: **business continuity** secondo le buone pratiche di **ISO/IEC 22313**.

**Requisiti organizzativi (RO)**
- **RO1**: competenze adeguate, almeno Responsabile del servizio di conservazione e Responsabile della funzione archivistica.
- **RO2**: **polizza assicurativa** di almeno **1.000.000 € per annualità** e **300.000 € per sinistro**.
- **RO3**: iscrizione al registro delle imprese da almeno 90 giorni.
- **RO4**: conformità GDPR.
- **RO5**: dichiarazione dell'**ubicazione dei data center** (Italia, UE o extra-UE).
- **RO6**: per data center extra-UE, consultazione preventiva del Garante (art. 36 GDPR).
- **RO7**: manuale di conservazione con piano della sicurezza e modello organizzativo.
- **RO8**: verifica periodica della conformità a norme e standard.

**Non presenti nell'Allegato A**: SLA quantitativi, ISO 27017 e ISO 27018.

### 2.3 Allegato B: piano di cessazione [V]

Fonte: [Allegato B](https://www.agid.gov.it/sites/default/files/repository_files/allegato_b_-_piano_di_cessazione_.pdf). Riferimenti: ETSI TS 119 511 §7.12 ed EN 319 401. Struttura richiesta del piano:

1. Introduzione e responsabili
2. Definizioni
3. Riferimenti normativi
4. Terze parti, inclusi i responsabili ex art. 28 GDPR
5. **Analisi dei rischi** (UNI ISO 31000), con valutazione dei rischi sui dati personali
6. Programmazione: **matrice RACI**, verifiche, **Gantt**
7. **Processo di cessazione**:
   - comunicazioni;
   - **analisi preliminare dei PdA**, con elenchi di consistenza per produttore (volumi, numero di documenti, dati personali, aggregazioni, piani di classificazione e scarto);
   - trasferimento con le misure dell'art. 32 GDPR;
   - infrastruttura di migrazione;
   - controlli del conservatore cessante;
   - presa in carico da parte del subentrante.
8. **Cancellazione degli archivi**, con prova della distruzione.

Requisiti funzionali che ne derivano per il software: **export massivo dei PdA/PdD**, elenchi di consistenza, verifiche post-trasferimento e **accesso garantito per un periodo congruo dopo la cessazione**.

### 2.4 Marketplace, ACN e novità 2023–2026

- **Marketplace AgID** ([conservatoriqualificati.agid.gov.it](https://conservatoriqualificati.agid.gov.it/)) [V]: è attivo. Il sito precisa che **non è un albo**. L'iscrizione è possibile dal 1/1/2022 tramite autocertificazione.
- **Competenze**: la conservazione documentale resta in capo ad **AgID**. **Non ho trovato alcun passaggio di competenze ad ACN** [V per assenza nelle fonti AgID consultate; NV in senso assoluto]. ACN è competente sulla **qualificazione cloud**, che serve a un conservatore SaaS per servire la PA:
  - **Regolamento ACN** (DD n. **21007/24** del 27/6/2024), applicato dal **1/8/2024** ([testo](https://dirittobancario.it/wp-content/uploads/2024/07/Regolamento-ACN-27-giugno-2024.pdf), [Agenda Digitale](https://www.agendadigitale.eu/cittadinanza-digitale/passaggio-al-cloud-dei-piccoli-comuni-cosa-dice-il-nuovo-regolamento-acn/)) [V2].
  - Livelli dei servizi cloud **QC1–QC4**. Dati **ordinari** su QC1–QC4, **critici** da QC2, **strategici** da QC3 (art. 17 c.4).
  - Infrastrutture: livelli **AI1–AI4**.
  - Qualifica per **massimo 36 mesi**, rinnovo da chiedere **90 giorni prima**. Istruttoria di 60 giorni (prorogabile). Revoca con continuità d'uso per massimo 6 mesi.
  - Abroga, tra gli altri, la Det. AgID 628/2021 e le Det. ACN 306 e 307/2022.
  - I requisiti per livello sono negli **Allegati 3–4** del Regolamento, che **non ho letto** [NV].
- **Piano Triennale 2024–2026 (Aggiornamento 2025), Obiettivo 3.3** ([notizia AgID 11/8/2025](https://www.agid.gov.it/it/notizie/agid-avvia-lattivita-di-monitoraggio-sullattuazione-delle-linee-guida-sulla-formazione)) [V]:
  - target **2025**: manuale di gestione documentale e nomina del responsabile della gestione documentale per ogni AOO;
  - target **2026**: **manuale di conservazione adottato e pubblicato e nomina del responsabile della conservazione** per il 100% delle PA;
  - AgID ha avviato un **monitoraggio** (form compilabile da ottobre a dicembre 2025).

  Per i fornitori ne deriva un'opportunità: **template del manuale e supporto al responsabile della conservazione dell'ente**.
- **eIDAS 2 e archiviazione elettronica qualificata (UE)** [V]:
  - Il Reg. (UE) 2024/1183 introduce il servizio fiduciario di **archiviazione elettronica** (artt. 45 decies–undecies eIDAS).
  - Il **Reg. di esecuzione (UE) 2025/2532** (16/12/2025, in vigore dal **6/1/2026**) fissa le norme di riferimento, con presunzione di conformità: **CEN/TS 18170:2025** (norma base, con adattamenti), **ETSI EN 319 401 v3.1.1**, **ETSI EN 319 421** (TSA), **ISO 14721:2025** (OAIS), ISO/IEC 15408, FIPS 140-3 e meccanismi crittografici ENISA (ACM) ([testo](https://www.dirittobancario.it/wp-content/uploads/2025/12/Regolamento-di-esecuzione-UE-2025_2532.pdf)).
  - Il **Reg. di esecuzione (UE) 2025/1946** (29/9/2025, in vigore dal 20/10/2025) riguarda la **conservazione qualificata di firme e sigilli qualificati** ([EUR-Lex](https://eur-lex.europa.eu/eli/reg_impl/2025/1946/oj/eng)). Gli standard del suo allegato non sono stati letti [NV]; con ogni probabilità è ETSI TS 119 511.
  - Per un conservatore italiano diventare **QTSP di archiviazione elettronica** è facoltativo, ma è un fattore competitivo.
- **Linee Guida AgID 2024–2026**: **nessuna modifica formale trovata**. Il PDF è stato ricaricato sul sito AgID a maggio 2024 con copertina "maggio 2021". La pagina tematica AgID non mostra notizie successive al 2023 (l'ultima, del 7/4/2023, è un approfondimento sulla conservazione delle basi di dati) ([AgID argomenti/conservazione](https://www.agid.gov.it/it/argomenti/conservazione)) [V con limiti: ho visto solo la prima pagina di risultati].

---

## 3. Requisiti fiscali e beni culturali

### 3.1 DM MEF 17 giugno 2014 [V2]

Fonti: [GU n. 146 del 26/6/2014](http://www.gazzettaufficiale.it/eli/id/2014/06/26/14A04778/sg) e la sintesi articolo per articolo di [Diritto.it](https://www.diritto.it/il-decreto-del-ministero-dell-economia-del-17-giugno-2014-sugli-oblighi-fiscali-relativi-ai-documenti-informatici/). Il testo integrale della GU non era leggibile con lo strumento.

- **Art. 2**: i documenti informatici fiscalmente rilevanti devono essere **immodificabili, integri, autentici e leggibili**, nei formati del CAD o in quelli scelti dal responsabile della conservazione con motivazione nel manuale.
- **Art. 3**:
  - funzioni di **ricerca ed estrazione** almeno per **cognome, nome, denominazione, codice fiscale, partita IVA, data**, o loro combinazioni;
  - il processo si **chiude con un riferimento temporale opponibile a terzi sul PdA**;
  - **termine**: art. 7 c.4-ter del DL 357/1994, oggi **entro tre mesi dalla scadenza della dichiarazione dei redditi** dell'anno di riferimento ([ecnews](https://www.ecnews.it/fiscale/in-pratica/guida-agli-adempimenti/conservazione-e-consultazione-delle-fatture-due-scelte-distinte/)).
- **Art. 4**: copie per immagine di documenti analogici, chiuse con firma qualificata o digitale. Per gli originali unici serve l'attestazione di un pubblico ufficiale.
- **Art. 5**: la conservazione elettronica va **indicata nella dichiarazione dei redditi**. L'**esibizione** avviene presso la sede o per via telematica.
- **Art. 6**: imposta di bollo assolta telematicamente: entro 120 giorni dalla chiusura dell'esercizio, e per libri e registri ogni 2.500 registrazioni.
- **Durata della conservazione**: 10 anni per le scritture contabili (art. 2220 c.c.), comunque fino alla definizione degli accertamenti (art. 22 DPR 600/1973) [NV, non verificato su fonte primaria].
- **Servizio gratuito dell'Agenzia delle Entrate** per le fatture SdI: conservazione per **15 anni** previa adesione all'accordo nel portale "Fatture e Corrispettivi" ([ecnews](https://www.ecnews.it/fiscale/in-pratica/guida-agli-adempimenti/conservazione-e-consultazione-delle-fatture-due-scelte-distinte/), [Eutekne](https://www.eutekne.info/Sezioni/Art_588226_fatture_elettroniche_conservate_per_15_anni_col_servizio_gratuito.aspx)) [V2].

**Implicazioni per il software**:
- flag "fiscale" per tipologia o registro;
- indici di ricerca obbligatori: CF, P.IVA, denominazione, data;
- chiusura del PdA con marca temporale;
- scadenzario dei termini di conservazione;
- export per l'esibizione telematica.

### 3.2 Registro giornaliero di protocollo [V]

Va inviato in conservazione **entro la giornata lavorativa successiva** ([AgID](https://www.agid.gov.it/piattaforme/conservazione)). Fonte originaria: DPCM 3/12/2013 art. 7 c.5; da verificare se la regola sia stata ripresa nelle LG §3.1.

### 3.3 PA: beni culturali, scarto e piano di conservazione

- **D.Lgs. 42/2004, art. 21 c.1** [V2] ([Brocardi](https://www.brocardi.it/codice-dei-beni-culturali-e-del-paesaggio/parte-seconda/titolo-i/capo-iii/sezione-i/art21.html)): servono **l'autorizzazione del Ministero** (in pratica della **Soprintendenza archivistica**):
  - lett. d) per lo **scarto** di documenti di archivi pubblici e di archivi privati dichiarati;
  - lett. e) per il **trasferimento ad altre persone giuridiche** di complessi organici di documentazione.

  La migrazione tra conservatori non è citata espressamente; la sua riconducibilità all'art. 21 è un'interpretazione [NV].
- **Art. 41**: versamento delle amministrazioni statali all'Archivio centrale dello Stato e agli Archivi di Stato (richiamato dalla LG, compito l).
- **Piano di conservazione / massimario di scarto**: è un allegato del **manuale di gestione documentale**. Le PA lo applicano per i tempi di trasferimento e di scarto; i privati che non lo hanno devono definire criteri analoghi ([FAQ AgID](https://www.agid.gov.it/it/domande-frequenti/conservazione)) [V]. Ogni ente ha un proprio massimario (es. piani di classificazione e conservazione per Comuni, Università, ASL); non esiste un massimario unico nazionale [V2].
- **Sanzioni**: art. 18-bis CAD, da 10.000 a 100.000 € per la mancata applicazione delle Linee Guida da parte delle PA ([Actainfo](https://www.actainfo.it/news/agid-monitoraggio/)) [V2]. Ai conservatori si applica l'art. 32-bis CAD (richiamato dall'art. 5 del Regolamento) [V].

---

## 4. Panoramica di mercato (non pubblicata)

La panoramica dei servizi offerti da altri conservatori, usata internamente per la gap analysis, non fa parte della pubblicazione open source (R18): il progetto non cita né riproduce documentazione di altri conservatori. Le funzioni di VerumDoc derivano dai requisiti normativi di questo documento.

---

## 5. Standard ETSI per la conservazione

### 5.1 ETSI TS 119 511 [V]

Ultima versione trovata: **V1.2.1 (2025-10)** ([PDF ETSI](https://etsi.org/deliver/etsi_ts/119500_119599/119511/01.02.01_60/ts_119511v010201p.pdf)). Titolo: *Policy and security requirements for trust service providers providing long-term preservation of digital signatures or general data using digital signature techniques*.

- **Base**: estende **ETSI EN 319 401** (requisiti generali dei TSP). Supporta il servizio qualificato eIDAS di conservazione di firme e sigilli qualificati (Annex A).
- **Obiettivi di conservazione**:
  - **PDS**: conservazione delle firme digitali (mantenerne nel tempo la validabilità);
  - **PGD**: conservazione di dati generici (prova di esistenza e integrità);
  - **AUG**: augmentation di evidenze fornite dal client.
- **Modelli di storage**: **WST** (con storage, il modello di un conservatore), **WTS** (storage temporaneo), **WOS** (senza storage).
- **Concetti**:
  - **preservation profile**: pubblico, identificato in modo univoco e stabile nel tempo;
  - **preservation evidence**: time-stamp RFC 3161 o **evidence record RFC 4998/6283**;
  - **evidence policy** e signature validation policy.
- **Requisiti chiave**:
  - **practice statement** e termini e condizioni pubblici;
  - storico delle policy pubblico e tracciabile;
  - TSA conforme a **EN 319 421**;
  - chiavi in un dispositivo sicuro (**EAL4+ o FIPS 140-3 livello 3**);
  - **monitoraggio della robustezza crittografica** (TS 119 312) e **augmentation** delle evidenze prima della loro scadenza;
  - **export/import** in formato standard solo verso soggetti autorizzati, con registrazione;
  - **piano di cessazione** (§7.12);
  - protocollo sicuro e autenticato, con **cancellazioni motivate e registrate**;
  - notifiche se un algoritmo diventa insicuro.
- **Escluso**: la conversione di formato contro l'obsolescenza. In Italia è coperta dal **riversamento** (Allegato 2 LG).

### 5.2 ETSI TS 119 512 [V]

Versione **V1.2.1 (2023-05)** ([PDF ETSI](https://etsi.org/deliver/etsi_TS/119500_119599/119512/01.02.01_60/ts_119512v010201p.pdf)). Titolo: *Protocols for trust service providers providing long-term data preservation services*.

- **Operazioni**: **RetrieveInfo** (profili), **PreservePO**, RetrievePO e DeletePO (condizionali), UpdatePOC, **RetrieveTrace** (traccia delle operazioni), ValidateEvidence e **Search** (opzionali).
- **Binding**: XML/**SOAP** (WSDL) e JSON/**REST** (JSON Schema e **OpenAPI**).
- **Concetti**: **POC** (preservation object container, es. ASiC-S o ASiC-E), **SubDO** (oggetto sottomesso), evidence.
- **Rilevanza**: l'Allegato A **non** impone la TS 119 512. È però un riferimento utile per progettare API di versamento e recupero standard.

### 5.3 Altri standard

- **ETSI EN 319 401** [V]: requisiti generali di policy per i TSP (governance, personale, sicurezza, cessazione, log). RG7 la rende obbligatoria per i conservatori PA.
- **CEN/TS 18170:2025** [V]: norma base per i **servizi qualificati di archiviazione elettronica** eIDAS 2 (Reg. 2025/2532).
- **ISO 14721 (OAIS)**, **ISO 16363**, **UNI 11386:2020** [V]: vedi §2.2.
- UNI 11386:2020 è stata emanata il **7/5/2020**. Novità: indice rinominato **PIndex**, elemento Relation, ruoli Submitter, Holder e AuthorizedSigner, uso di **MoreInfo** per i metadati dell'Allegato 5 ([AgID 2022](https://www.agid.gov.it/sites/default/files/repository_files/interoperabilita_aip.pdf)).

---

## 6. Implicazioni architetturali (normativa che cambia)

I punti già mobili o "datati" nella normativa vigente suggeriscono di **parametrizzare** questi aspetti:

1. **Riferimenti cloud**: RG11 cita la Circolare 3/2018, ma oggi vale il Regolamento ACN 2024. **Matrice requisiti → norma** versionata.
2. **Standard ETSI**: si è passati da TS 101 533-1 a TS 119 511, e ora CEN/TS 18170 per eIDAS 2. **Profili di conservazione** versionati (approccio TS 119 511) e algoritmi crittografici configurabili, con augmentation o ri-marcatura.
3. **Metadati Allegato 5**: AgID valuta estensioni con set specifici (questioni aperte nel documento sull'interoperabilità). **Schema dei metadati versionato per tipologia** e validazione via XSD o JSON Schema.
4. **Indice PdA**: SInCRO con MoreInfo; possibile export E-ARK/METS. **Serializzatori pluggable**.
5. **Firma delle evidenze**: firma qualificata del responsabile o sigillo qualificato. **Provider di firma, sigillo e TSA astratti** (HSM/remote signing).
6. **Formati (Allegato 2)**: lista dei formati e delle regole di riversamento come **configurazione**, non come codice.
7. **Termini e scarto**: piani di conservazione diversi per ente, **workflow di autorizzazione** (Soprintendenza) configurabile.

---

## 7. Checklist di conformità per la gap analysis (estratto)

| # | Requisito | Origine |
|---|---|---|
| 1 | Acquisizione PdV via API (REST e/o SOAP) + interfaccia web | Allegato A RG1; LG processo |
| 2 | Controlli su formato (Allegato 2), firma, metadati (Allegato 5), hash; rifiuto con motivazione | LG processo |
| 3 | RdV con ID univoco, tempo UTC, impronte del PdV; firmato o sigillato | LG processo; responsabile lett. c |
| 4 | PdA con indice UNI 11386:2020, firmato o sigillato e marcato | LG; RG3 |
| 5 | PdD per esibizione, firmabile; PdD coincidente con PdA per migrazione | LG; responsabile lett. d |
| 6 | Ricerca per metadati, incluse le chiavi fiscali (CF, P.IVA, denominazione, data) | DM 17/6/2014 art. 3 |
| 7 | Duplicati e copie; riversamento | LG processo; Allegato 2 |
| 8 | Verifica integrità e leggibilità almeno ogni 5 anni, con evidenza | Responsabile lett. f |
| 9 | Scarto con elenco, autorizzazione, tracciamento ed eliminazione dai backup | LG scarto; art. 21 D.Lgs. 42/2004 |
| 10 | SPID/CIE per gli utenti | RG2 |
| 11 | OAIS, ISO 16363, EN 319 401 / TS 119 511 (documentazione e processi) | RG4, RG5, RG7 |
| 12 | ISO 9001 (o 20000), ISO 27001 con scope conservazione, ISO 37001, BC ISO 22313, OWASP | RQ1, RS1–RS3, RG10 |
| 13 | Manuale di conservazione (12 contenuti) + piano della sicurezza | LG manuale; RO7 |
| 14 | Piano di cessazione (Allegato B) + export massivo | Art. 8; Allegato B; RQ2 |
| 15 | Account di test per AgID | RQ3 |
| 16 | Qualificazione ACN QC1+ se SaaS per PA (QC2/QC3 per dati critici o strategici) | RG11 → Reg. ACN 2024 |
| 17 | Log di tutte le operazioni, monitoraggio, gestione anomalie | RG8; EN 319 401 |
| 18 | Dichiarazione dell'ubicazione dei data center; extra-UE solo previa consultazione del Garante | RO5, RO6 |
| 19 | Polizza da 1 M€ per annualità / 300 k€ per sinistro | RO2 |

---

## 8. Elenco fonti

**Primarie**
- AgID – Conservazione: https://www.agid.gov.it/piattaforme/conservazione
- AgID – Linee Guida (PDF, maggio 2021): https://www.agid.gov.it/sites/agid/files/2024-05/linee_guida_sul_documento_informatico.pdf
- AgID – Allegato 5 Metadati: https://www.agid.gov.it/sites/agid/files/2024-06/allegato_5_metadati.pdf
- AgID – Allegato 4 Standard: https://www.agid.gov.it/sites/default/files/repository_files/allegato_4_standard_e_specifiche_tecniche.pdf
- Linee Guida, bozza (docs.italia): https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/conservazione.html
- Linee Guida, allegati (docs.italia): https://docs.italia.it/AgID/documenti-in-consultazione/lg-documenti-informatici-docs/it/bozza/allegati.html
- AgID – Regolamento conservatori: https://www.agid.gov.it/sites/default/files/repository_files/regolamento_sui_criteri_di_conservazione.pdf
- AgID – Allegato A: https://www.agid.gov.it/sites/default/files/repository_files/allegato_a_.pdf
- AgID – Allegato B: https://www.agid.gov.it/sites/default/files/repository_files/allegato_b_-_piano_di_cessazione_.pdf
- AgID – Notizia Det. 455/2021: https://www.agid.gov.it/it/agenzia/stampa-e-comunicazione/notizie/2021/06/25/conservazione-documenti-informatici-pubblicato-il-regolamento-sui-criteri-fornitura
- AgID – Notizia Det. 629/2021: https://www.agid.gov.it/it/agenzia/stampa-e-comunicazione/notizie/2021/12/17/documento-informatico-modifiche-al-regolamento-sui-servizi-conservazione
- AgID – FAQ conservazione: https://www.agid.gov.it/it/domande-frequenti/conservazione
- AgID – Marketplace: https://conservatoriqualificati.agid.gov.it/
- AgID – Monitoraggio LG (11/8/2025): https://www.agid.gov.it/it/notizie/agid-avvia-lattivita-di-monitoraggio-sullattuazione-delle-linee-guida-sulla-formazione
- AgID – Argomenti/conservazione: https://www.agid.gov.it/it/argomenti/conservazione
- AgID – Modelli di interoperabilità (dic. 2022): https://www.agid.gov.it/sites/default/files/repository_files/interoperabilita_aip.pdf
- EUR-Lex – Reg. esec. (UE) 2025/2532: https://eur-lex.europa.eu/eli/reg_impl/2025/2532/oj
- EUR-Lex – Reg. esec. (UE) 2025/1946: https://eur-lex.europa.eu/eli/reg_impl/2025/1946/oj/eng
- ETSI TS 119 511 V1.2.1: https://etsi.org/deliver/etsi_ts/119500_119599/119511/01.02.01_60/ts_119511v010201p.pdf
- ETSI TS 119 512 V1.2.1: https://etsi.org/deliver/etsi_TS/119500_119599/119512/01.02.01_60/ts_119512v010201p.pdf
- Gazzetta Ufficiale – DM 17/6/2014: http://www.gazzettaufficiale.it/eli/id/2014/06/26/14A04778/sg

**Secondarie (testi normativi riprodotti o commentati)**
- Regolamento ACN cloud (copia): https://dirittobancario.it/wp-content/uploads/2024/07/Regolamento-ACN-27-giugno-2024.pdf
- Reg. 2025/2532 (copia): https://www.dirittobancario.it/wp-content/uploads/2025/12/Regolamento-di-esecuzione-UE-2025_2532.pdf
- Agenda Digitale – Regolamento ACN: https://www.agendadigitale.eu/cittadinanza-digitale/passaggio-al-cloud-dei-piccoli-comuni-cosa-dice-il-nuovo-regolamento-acn/
- Agenda Digitale – Migrazione tra conservatori: https://www.agendadigitale.eu/documenti/conservazione-digitale-come-fare-la-migrazione-da-un-conservatore-ad-un-altro/
- Diritto.it – DM 17/6/2014: https://www.diritto.it/il-decreto-del-ministero-dell-economia-del-17-giugno-2014-sugli-oblighi-fiscali-relativi-ai-documenti-informatici/
- Ecnews – Conservazione fatture: https://www.ecnews.it/fiscale/in-pratica/guida-agli-adempimenti/conservazione-e-consultazione-delle-fatture-due-scelte-distinte/
- Eutekne – Servizio AdE 15 anni: https://www.eutekne.info/Sezioni/Art_588226_fatture_elettroniche_conservate_per_15_anni_col_servizio_gratuito.aspx
- Brocardi – art. 21 D.Lgs. 42/2004: https://www.brocardi.it/codice-dei-beni-culturali-e-del-paesaggio/parte-seconda/titolo-i/capo-iii/sezione-i/art21.html
- Filodiritto – Processo di conservazione: https://www.filodiritto.com/linee-guida-agid-processo-di-conservazione-dei-documenti-informatici
- Actainfo – Monitoraggio AgID: https://www.actainfo.it/news/agid-monitoraggio/

---

## 9. Cosa NON sono riuscito a verificare

1. **Testo definitivo delle LG su processo, infrastrutture, esibizione, misure di sicurezza e scarto** (le ultime 5 sezioni del cap. 4): il PDF AgID viene troncato. Ho usato la **bozza** su docs.italia, confermata solo in parte. In particolare non ho verificato:
   - se il vincolo "territorio nazionale" sia rimasto (sezione Infrastrutture);
   - se il "numero massimo di rifiuti non inferiore a tre" sia nel testo definitivo.

   **Azione**: scaricare il PDF e leggere le pp. 36–40.
2. **Numerazione esatta dei paragrafi** del cap. 4 definitivo: i rimandi interni sono incoerenti con le citazioni di AgID.
3. **Allegato 5**: ultimi campi dell'aggregazione documentale (ProcedimentoAmministrativo e successivi), troncati.
4. **Allegati 3–4 del Regolamento ACN** (requisiti per livello QC1–QC4, certificazioni ISO richieste per livello) e il **catalogo ACN** con l'elenco dei conservatori qualificati.
5. **Elenco nominativo dei conservatori iscritti** al marketplace AgID: la pagina elenco non era leggibile.
6. **Eventuali atti AgID o ACN 2024–2026** che modifichino LG o Regolamento: non trovati. Ho consultato solo la prima pagina della sezione AgID "conservazione". Nessuna evidenza di passaggio di competenze sulla conservazione ad ACN.
7. **Allegato del Reg. (UE) 2025/1946** (standard per la conservazione qualificata di firme): non letto.
8. **Testo integrale del DM 17/6/2014**: letto tramite sintesi secondaria. Il termine "tre mesi dalla dichiarazione" deriva dalla modifica del DL 34/2019 all'art. 7 c.4-ter DL 357/1994, che non ho verificato su Normattiva (sito in errore).
9. **Testo vigente degli artt. 34 e 44 CAD su Normattiva**: errore di caricamento. I contenuti sono presi dalla pagina AgID.
10. **ISO 16363 ed ETSI TS 119 511**: dalle fonti non risulta quanti conservatori siano formalmente certificati rispetto a semplicemente "conformi". L'Allegato A chiede la "conformità", non la certificazione.
