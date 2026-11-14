# Audit funzionale di VerumDoc — dove siamo, dove dobbiamo arrivare

> Data: 20/10/2026 · Perimetro: tutto il monorepo (9 servizi, librerie, frontend, infrastruttura, test, documentazione).
> Metodo: lettura del codice riga per riga, esecuzione dei test, prove dirette (PostgreSQL 16, Redis e S3 locali; generazione e validazione dell'IPdA; esecuzione della hashchain; build del frontend con screenshot reali), confronto con la normativa vigente verificata su fonti primarie.
> I rapporti di lavoro con i riferimenti `file:riga` erano allegati interni e non sono pubblicati (§9); resta pubblicata la ricerca normativa.

---

## 1. Verdetto

**Il progetto è completo per circa il 15–20% rispetto a un servizio di conservazione a norma.** Non è «sostanzialmente completo» come dichiarano README, STATO_PROGETTO e Piano v5: molte voci segnate «✅» sono smentite dal codice.

Quello che esiste e funziona davvero è uno **scheletro di pipeline**: un endpoint accetta un upload, salva i file su S3, inserisce una riga e pubblica un evento Kafka; i servizi a valle costruiscono un XML, una catena di impronte e qualche schermata. Mancano o sono finti proprio gli elementi che fanno di un archivio un **sistema di conservazione**: firma e marca temporale qualificate, indice del pacchetto conforme UNI SInCRO, rapporto di versamento valido, immutabilità applicata dal codice, verifica delle firme e dei formati dei documenti versati, esibizione, scarto autorizzato, verifiche periodiche, interoperabilità tra conservatori.

L'obiettivo architetturale del committente («predisposto a variare se variano le norme») **non è soddisfatto**: regole, formati, metadati e schemi XML sono scritti nel codice.

Il lato positivo: lo stack scelto è adatto, il modello di dominio (Allegato 5, tipologie DI/DAI/AGG, tipi di PdD, conservazione permanente) è nella direzione giusta e la parte di accesso al database è solida. Il lavoro da fare è grande ma su fondamenta recuperabili.

---

## 2. Dichiarato contro reale

| Dichiarazione (README / STATO_PROGETTO / Piano v5) | Realtà verificata |
|---|---|
| «Sviluppo applicativo sostanzialmente completo; per andare live servono solo decisioni contrattuali» | Mancano firma, marca, IPdA valido, RdV, esibizione, scarto, verifiche periodiche, login reale; la pipeline non porta mai un pacchetto a `CONSERVATO` |
| SInCRO 2020 ✅ «DI, DAI, AGG con namespace XSD conforme» | Namespace e radice inventati; nessuno XSD nel repository; l'IPdA generato fallisce la validazione su namespace e radice della norma; non è firmato (contiene `StatoFirma=DA_APPORRE`) |
| Ricevuta di versamento preliminare e definitiva ✅ | XML con namespace inventato, senza impronte per documento, senza firma né marca; l'endpoint `/rdv` risponde sempre 409; il riferimento alla RdV definitiva si scrive su una colonna che non esiste e l'errore è ignorato |
| Hashchain «immutabilità» ✅ | La radice di Merkle è vulnerabile (ultima foglia duplicata, nessuna separazione foglia/nodo: collisioni provate); nessun blocco firmato o marcato; chi ha accesso al DB riscrive la catena e la verifica la dà integra; l'offset Kafka è confermato prima del sigillo (pacchetti persi a ogni crash); con traffico basso l'ultimo lotto non si sigilla mai |
| WORM Object Lock Compliance | Solo in Terraform; il codice scrive senza retention, su un bucket diverso da quello di Terraform; in sviluppo nessun lock e sovrascritture possibili |
| Firma CAdES-T «adapter pronto, cambio = solo config» | La firma è un JSON in base64; la verifica accetta una busta scritta a mano; i provider reali sollevano `NotImplementedError` ma lo stato dichiara «Firma eIDAS attiva»; nessun servizio chiama il servizio di firma |
| FIX-003 «Onboarding auth obbligatoria» ✅ | Il router utenti decodifica il token **senza verificarne la firma**: con un token falso si gestiscono utenti e API key di qualsiasi cliente |
| FIX-022 «Login con API key reale» ✅ | Il frontend ignora il secret; il login che funziona è il `dev-token`, che conia `SUPER_ADMIN` per chiunque in ogni ambiente diverso da `production` (staging compreso) |
| FIX-027 «Notification DLQ» ✅ | La DLQ non funziona, i messaggi falliti si perdono; destinatari finti (`tenant-x@verumdoc.local`); in produzione gli eventi automatici restano log |
| FIX-031 Piano di cessazione, Manuale ✅ | Testi fissi con CF `00000000000` e «RSC da nominare»; il manuale dichiara funzioni inesistenti (Object Lock, SInCRO, Vault, backup cross-region, PEC) |
| FIX-033 Fascicoli, Contratti, Nomine RSC ✅ | Creazione e dettaglio fascicoli falliscono; contratti e nomine leggono colonne inesistenti e mostrano «vuoto»; nessun backend per crearli |
| Multi-tenant isolation ✅ | Accesso ai documenti di altri clienti in distribution (esibizione senza controllo del cliente); classi documentali condivise modificabili da un cliente per tutti |
| 151 test di regressione, copertura ~85% | Circa 1 test su 5 fallisce (136 su 659); molti test «verdi» cercano stringhe nel sorgente o mockano tutto; `make test` maschera i fallimenti (`| tail -3`) |
| Migrazioni Alembic, schema unico | Le migrazioni non arrivano a `head` su DB vuoto (007 e 008 rotte); a runtime si usa `create_all`; 98 differenze tra modelli e migrazioni; enum con nomi diversi; `init.sql` crea enum divergenti; il seed fallisce sempre |
| Kubernetes, Helm, Terraform pronti | Terraform non supera il parsing (tutti i 10 moduli); probe sbagliate, Secret inesistenti, ConfigMap con variabili che le app non leggono, ingress che manda tutto al gateway (dashboard in 404), nessun deploy del frontend |

---

## 3. Dove siamo: completamento reale per componente

| Area | Componente | Reale | Note principali |
|---|---|---|---|
| Ingresso | Endpoint di versamento | 35% | Caso felice ok; niente idempotenza, duplicati, outbox; hash verificato solo se SHA-256; accetta eseguibili rinominati, file non dichiarati, classi inventate (create al volo) |
| | Stato PdV e ricevuta | 25% | Lo stato non supera mai `RICEVUTO` |
| | Autenticazione e ruoli | 35% | JWT RS256 ok; backdoor `dev-token`; `id_token` SPID non verificato; chiavi rigenerate a ogni avvio se mancano |
| | Lifecycle (scarto/migrazione) | 0–5% | Endpoint senza autenticazione, import di un modulo inesistente, sempre 503 |
| | PDND | 5–10% | SQL non valido, in produzione sempre 503, tutto nel tenant demo |
| Verifica | Pipeline tecnica | 30% | 9 passi strutturali, *fail-open* (hash validato se S3 non risponde), non scrive sul DB, consumer che perde messaggi |
| | Conformità AgID della verifica | 5–10% | Nessuna verifica firme, nessuna identificazione del formato, Allegato 2 e 5 quasi assenti |
| Conservazione | Builder SInCRO | ~10% | XML ben formato ma non conforme |
| | Pipeline PdA | ~25% | Impronte dichiarate mai ricalcolate; PdA creato anche senza file |
| | Hashchain | ~25% | Funziona meccanicamente, senza valore probatorio |
| | RdV | ~10% | |
| | Firma e marca | ~3% | Stub che si dichiara reale |
| | WORM | ~20% | Solo Terraform |
| Distribuzione | PdD ed esibizione | 10% | Tutti e 4 i tipi in errore 500; URL firmato con bucket e chiave invertiti; IDOR |
| Governo | Onboarding (clienti, utenti, API key, classi) | 30% | Bypass di autenticazione; classi globali senza versioni |
| | Dashboard API | 35% | 8 endpoint con SQL non valido; manuale e piano di cessazione statici |
| | Notifiche | 15% | Nessun invio reale |
| Librerie | DB condivisa | 80% | Solida |
| | Modelli condivisi | 45% | Disallineati dalle migrazioni; enum non conformi all'Allegato 5 |
| | Kafka condivisa | 40% | Perdita silenziosa se il broker è giù (il client riceve 202) |
| | s3, crypto, sincro, policy-engine | 0% | Vuoti o `allow := true` mai chiamato |
| | Osservabilità | 5–20% | Mai collegata |
| Frontend | Interfaccia | vedi audit grafico | 12/40 Nielsen, 6/20 audit tecnico; build rotta (manca `tsconfig.json`) |
| Infrastruttura | docker-compose | 50–55% | Avviabile con difetti |
| | Kubernetes / Helm / Terraform | 0–15% | Non applicabili così |
| Qualità | Test come rete di sicurezza | ~20% | |
| **Totale** | | **~15–20%** | |

### 3.1 Stato dei test (eseguiti)
| Suite | Passati | Falliti |
|---|---|---|
| gateway | 111 | 20 + 1 errore (17 con Redis attivo e PyJWT installato) |
| validation | 71 | 9 |
| aip-builder | 57 | 37 |
| hashchain | 20 | 6 |
| signature | 41 | 11 |
| dashboard-api | 72 | 23 |
| distribution | 41 | 7 |
| onboarding | 49 | 13 (non si importa con FastAPI 0.115) |
| notification | 44 | 2 |
| sdk | 12 | 0 |
| libs/shared/models | 5 | 7 |
| libs (collection) | — | interrotta: un test importa una funzione inesistente |

Ruff sul perimetro di ingresso: 232 errori (E501 67, I001 64, F401 44, …); la regola B008, che avrebbe segnalato l'endpoint lifecycle senza autenticazione, è disattivata.

---

## 4. Criticità bloccanti (P0) — da correggere prima di qualsiasi funzionalità nuova

| Codice | Criticità | Dove |
|---|---|---|
| VD-C-001 | Bypass di autenticazione: token decodificato senza verifica della firma (gestione utenti e API key di ogni cliente) | `services/onboarding/app/routers/utenti.py:17-26` |
| VD-C-002 | `dev-token` che conia `SUPER_ADMIN` in ogni ambiente diverso da `production` | `services/gateway/app/routers/dev_auth.py` |
| VD-C-003 | Endpoint lifecycle senza autenticazione | `services/gateway/app/routers/lifecycle.py:40,91` |
| VD-C-004 | Chiave privata RSA nel repository (compromessa) | `infra/dev_jwt.env` → rimossa da R01, da ruotare |
| VD-C-005 | Esibizione di documenti di altri clienti (IDOR) | `services/distribution/app/services/distribution_engine.py:32-46` |
| VD-C-006 | `id_token` SPID non verificato | `services/gateway/app/auth/spid.py:320` |
| VD-C-007 | Schema DB non riproducibile: migrazioni rotte, `create_all` a runtime, `init.sql` divergente, seed che fallisce | `services/gateway/alembic/`, `entrypoint.sh:36-43`, `infra/postgres/init.sql`, `seed.py:42` |
| VD-C-008 | Impronte dichiarate dal versatore mai ricalcolate; PdA creato anche se il file non si legge; hash validato se S3 non risponde | `aip_engine.py:164,169-184`; passo 5 della validazione |
| VD-C-009 | Hashchain: offset confermato prima del sigillo (pacchetti persi); aip-builder e validation: offset confermato anche in errore, senza ritentativi | `hashchain/consumer.py:74-78,128`; `aip_engine.py:89-97` |
| VD-C-010 | Firma e marca finte che si dichiarano reali | `services/signature/app/services/signer.py:80-153` |
| VD-C-011 | Nessuna scrittura su storage con Object Lock / retention | `aip-builder/.../s3_uploader.py:31-36` |
| VD-C-012 | Il versamento non è mai completato: nessuno porta PdV/PdA a `CONSERVATO`; la validazione non scrive lo stato | validation, aip-builder, hashchain |
| VD-C-013 | Manuale di conservazione che dichiara funzioni inesistenti | `services/dashboard-api/app/services/manuale.py` |
| VD-C-014 | Frontend: errori mostrati come «vuoto» (es. «Nessun RSC nominato… obbligatoria» su un errore 500), dati finti con «Live», SLA «conforme» senza dati | vedi audit grafico |

L'elenco completo, con gravità e riferimenti, è in `docs/registro/criticita.md`.

---

## 5. Gap normativo: requisito per requisito

Fonti: Linee guida AgID sulla formazione, gestione e conservazione dei documenti informatici (maggio 2021, in vigore dal 1/1/2022) e allegati; Regolamento sui criteri per la fornitura dei servizi di conservazione (Det. AgID 455/2021, modificata dalla 629/2021) con Allegato A (requisiti) e B (piano di cessazione); D.Lgs. 42/2004 art. 21; DM MEF 17/6/2014; ETSI TS 119 511/512. Dettaglio e link in `allegati/ricerca_normativa.md`. Nessuna modifica formale a Linee guida e Regolamento risulta tra il 2023 e oggi; la qualificazione cloud è passata al Regolamento ACN 2024 (livelli QC1–QC4); dal 6/1/2026 eIDAS 2 introduce il servizio fiduciario qualificato di archiviazione elettronica (Reg. di esecuzione UE 2025/2532).

| # | Requisito | Origine | Stato | Cosa serve |
|---|---|---|---|---|
| 1 | Versamento via API REST e/o SOAP + interfaccia web | Allegato A RG1 | Parziale | Schema del PdV versionato, idempotenza, outbox, canali come adattatori della stessa operazione, upload grandi in streaming |
| 2 | Controlli su formato (Allegato 2), firma, metadati (Allegato 5), impronte; rifiuto motivato | LG, processo | Assente | Identificazione per contenuto (Siegfried/DROID + PRONOM, veraPDF), verifica firme CAdES/PAdES/XAdES con Trusted List e OCSP/CRL alla data, profili di metadati versionati, catalogo regole blocca/avvisa/ignora, rapporto di rifiuto |
| 3 | Rapporto di versamento con identificativo, tempo UTC, impronte; firmato o sigillato | LG; responsabile lett. c | Finto | RdV generato all'accettazione, con impronte per documento, sigillo qualificato e marca, conservato come oggetto |
| 4 | PdA con indice UNI 11386:2020, firmato o sigillato e marcato | LG; RG3 | Non conforme | Acquisto della norma e dello XSD; builder per versione con validazione obbligatoria; sigillo e marca da TSA qualificata |
| 5 | PdD per esibizione; PdD coincidente con PdA per migrazione | LG; responsabile lett. d | Rotto | Esibizione come azione sul documento con attestazione firmata, link a scadenza, registro delle esibizioni; export di migrazione SInCRO |
| 6 | Ricerca per metadati, chiavi fiscali comprese | DM 17/6/2014 | Parziale | Indice di ricerca reale (oggi OpenSearch non collegato), campi fiscali |
| 7 | Duplicati, copie, riversamento | LG; Allegato 2 | Assente | Copia in sito separato verificata; riversamento con nuovo PdA collegato |
| 8 | Verifica integrità e leggibilità almeno ogni 5 anni, con evidenza | Responsabile lett. f | Assente | Job periodico che rilegge gli oggetti e ricalcola le impronte, report sigillato, registro delle esecuzioni |
| 9 | Scarto con elenco, autorizzazione (Soprintendenza per la PA), tracciamento, eliminazione dai backup | LG; D.Lgs. 42/2004 art. 21 | Assente e pericoloso (oggi passaggio diretto a «eliminazione») | Proposta da piano di conservazione → approvazioni → esecuzione → verbale; compatibilità con Object Lock |
| 10 | SPID/CIE | RG2 | Mock non verificato | Aggregatore reale con verifica dell'`id_token` |
| 11 | OAIS, ISO 16363, EN 319 401 / TS 119 511 | RG4, RG5, RG7 | Solo dichiarato | Documentazione e processi; profili di conservazione versionati |
| 12 | ISO 9001/20000, ISO 27001, ISO 37001, BC ISO 22313, OWASP | RQ1, RS1–RS3, RG10 | Assente | Percorso organizzativo; test OWASP in CI |
| 13 | Manuale di conservazione (12 contenuti) + piano della sicurezza | LG; RO7 | Statico e non veritiero | Manuale con allegati tecnici **generati dal sistema** (§28 delle regole) |
| 14 | Piano di cessazione (Allegato B) + export massivo | Reg. art. 8; Allegato B; RQ2 | Statico | Piano compilato dai dati reali; export massivo provato |
| 15 | Account di test per AgID | RQ3 | Assente | Ambiente dimostrativo |
| 16 | Qualificazione ACN per SaaS alla PA | RG11 → Reg. ACN 2024 | Assente | Infrastruttura e documentazione per QC1 (QC2+ per dati critici) |
| 17 | Log di tutte le operazioni, monitoraggio, gestione anomalie | RG8; EN 319 401 | Debole | Registro automatico a prova di manomissione, metriche, allarmi |
| 18 | Ubicazione dei data center dichiarata | RO5, RO6 | Implicita (AWS eu-south-1) | Dichiarazione nel manuale |
| 19 | Ruoli: responsabile del servizio, della funzione archivistica, ecc.; responsabile della conservazione del cliente | LG ruoli; RO1 | Errati | Modello ruoli rifatto (§14.8 delle regole), nomine con validità |
| 20 | Predisposizione al cambio normativo | Requisito del committente | Assente | Regole come dati, fotografia delle regole su ogni pacchetto, formati di scambio versionati (§8 delle regole) |

---

## 6. Dove dobbiamo arrivare: la mappa del servizio completo

Il servizio a regime è fatto di **otto capacità**. Per ciascuna: cosa deve fare, chi la usa, cosa c'è oggi.

| Capacità | Cosa deve fare | Utenti | Oggi |
|---|---|---|---|
| **1. Accordo e configurazione del cliente** | Contratto, accordo di versamento (disciplinare tecnico) versionato: classi documentali, formati ammessi, metadati richiesti, termini, volumi; nomine (responsabile della conservazione e delegati); attivazione guidata con requisiti | Amministratore del cliente, responsabile del servizio | Classi globali senza versioni; contratti e nomine senza backend |
| **2. Versamento** | Canali (API, portale a lotti, SFTP, connettori protocollo/ERP/SdI) → stessa operazione; idempotenza; staging; verifica configurabile; RdV o rapporto di rifiuto | Operatori, sistemi versanti | Scheletro |
| **3. Conservazione** | PdA con IPdA SInCRO valido, sigillo e marca; storage WORM con retention per oggetto; copia in sito separato; catena delle impronte con ancoraggio marcato | Sistema | Non conforme |
| **4. Integrità nel tempo** | Verifiche periodiche, rinnovo marche prima della scadenza, monitoraggio dei formati e riversamento, conservazione delle evidenze di validazione | Responsabile del servizio | Assente |
| **5. Ricerca, esibizione, distribuzione** | Ricerca per metadati e chiavi fiscali; esibizione con attestazione e link a scadenza; export per migrazione; registro delle esibizioni | Clienti, ispettori | Rotta |
| **6. Ciclo di vita e scarto** | Piano di conservazione per cliente; proposta di scarto; autorizzazioni; esecuzione con verbale; blocchi legali | Responsabile della conservazione, Soprintendenza | Assente |
| **7. Governo e prova** | Registro attività a prova di manomissione; manuale e piano di cessazione generati; report periodici al cliente; SLA misurati; consumi | Responsabili, auditor, AgID | Statico |
| **8. Piattaforma** | Identità (SPID/CIE, OIDC, MFA, API M2M), multi-tenant fail-closed, osservabilità, backup provati, deploy riproducibile, qualificazione ACN | Tutti | Parziale |

Trasversale a tutte: il **motore delle regole** (`libs/verumdoc_rules`, proposto) con registro delle impostazioni e riferimento normativo, catena di risoluzione, catalogo regole, profili di metadati e formati di scambio versionati, fotografia delle regole su ogni pacchetto. È ciò che realizza il requisito «predisposto a variare se variano le norme».

---

## 7. Piano proposto (da approvare)

Ogni fase si chiude con i criteri del §5 delle regole. I rilasci sono numerati e non si riusano.

| Fase | Obiettivo | Contenuto | Criterio di uscita |
|---|---|---|---|
| **0 — Fondamenta** (R01–R04) | Un sistema onesto e riproducibile | R01 pulizia (questo rilascio); R02 chiusura P0 di sicurezza (VD-C-001…006); R03 schema unico da Alembic, seed, `init.sql` rimosso, enum allineati; R04 test permanenti di integrità + CI + correzione dei test esistenti + correzioni minime di onestà del frontend (build, errori mostrati come tali, dati finti rimossi); documentazione veritiera | Installazione da zero in CI; zero P0 di sicurezza; suite verde senza test «a stringhe» |
| **1 — Motore delle regole** (R05–R07) | Predisposizione normativa | `libs/verumdoc_rules`: registro impostazioni, catalogo regole, profili metadati versionati (Allegato 5), tabella formati (Allegato 2 ↔ PRONOM), fotografia sul PdV; accordo di versamento versionato | Una regola nuova si aggiunge senza toccare i servizi; test dei tre effetti |
| **2 — Versamento vero** (R08–R11) | PdV → RdV a norma | Schema PdV, idempotenza, outbox, staging; identificazione formato, antivirus, verifica firme; stati e rifiuti; RdV con impronte, sigillo e marca | Un PdV reale produce RdV valido, verificabile con strumenti indipendenti |
| **3 — Conservazione vera** (R12–R15) | PdA a norma e immutabile | IPdA SInCRO valido contro XSD ufficiale; sigillo + marca qualificati (provider reale); Object Lock applicativo con controllo all'avvio; hashchain corretta (separazione foglia/nodo, ancoraggio marcato, offset dopo commit, sigillo a tempo) | Validazione XSD verde; verifica d'integrità con prova; nessuna perdita al crash (test) |
| **4 — Esibizione e distribuzione** (R16–R18) | Dimostrare a terzi | Esibizione con attestazione firmata e link; PdD; export di migrazione; ricerca reale | Esibizione end-to-end; export reimportabile |
| **5 — Ciclo di vita e governo** (R19–R23) | Tutto ciò che il responsabile deve poter fare | Verifiche periodiche, rinnovo marche; scarto con workflow; ruoli LG e nomine; manuale e piano di cessazione generati; registro a prova di manomissione; report e SLA misurati | Evidenze archiviate per ogni processo periodico |
| **R — Redesign frontend** (in parallelo dalla Fase 1) | Nuova personalità e interfaccia onesta | Dopo la decisione `VD-DR-01`: sistema grafico, componenti, IA per compiti, form generati dai profili, stati veri | Audit grafico ≥ 16/20 tecnico, ≥ 28/40 Nielsen |
| **6 — Produzione e qualificazione** | Clienti reali | Infrastruttura coerente (Terraform valido, Helm/K8s allineati), osservabilità, backup e DR provati, test OWASP, percorso ISO 27001/9001/37001, qualificazione ACN, requisiti Allegato A | Criteri §5.2 delle regole |

---

## 8. Decisioni che servono al committente

| Codice | Domanda | Opzioni | Raccomandazione |
|---|---|---|---|
| VD-P-01 | Creare il pacchetto puro `libs/verumdoc_rules` come unico luogo delle regole? | Sì / regole per servizio | Sì: è il cuore della predisposizione normativa |
| VD-P-02 | Row Level Security PostgreSQL in aggiunta ai filtri applicativi? | Sì / no | Sì, come difesa in profondità |
| VD-P-03 | Verifica delle firme dei documenti versati | EU DSS (Java, sidecar) / pyHanko + asn1crypto / servizio esterno | EU DSS: è lo standard di riferimento europeo e copre CAdES/PAdES/XAdES/ASiC con la Trusted List |
| VD-P-04 | Identificazione dei formati | Siegfried / DROID / libmagic | Siegfried (licenza Apache, veloce, PRONOM) + veraPDF per PDF/A |
| VD-P-05 | Fornitore di sigillo/firma e TSA qualificati | Un prestatore qualificato eIDAS da scegliere | Decisione commerciale; l'adattatore va scritto sul fornitore scelto |
| VD-N-01 | Acquisto della UNI 11386:2020 e del suo XSD | — | Necessario: senza lo schema ufficiale l'IPdA non è verificabile |
| VD-P-06 | Servizio per scarto, verifiche periodiche e riversamento | Nuovo servizio `lifecycle` / dentro aip-builder | Nuovo servizio: responsabilità e permessi distinti |
| VD-P-07 | Hashchain: mantenerla? | Tenere con ancoraggio marcato / sostituire con evidence record RFC 4998 | Tenere, corretta, con sigillo periodico marcato; valutare RFC 4998 per le evidenze ETSI |
| VD-P-08 | Mercato: solo PA, misto, solo privati? | — | Determina SPID/CIE, Designers Italia, priorità fiscali |
| VD-DR-01 | Direzione grafica | Segnatura / Orizzonte / Sigillo | Vedi `docs/design/AUDIT_GRAFICO_2026-10-20.md` §9 |
| VD-DR-02 | Rapporto con Designers Italia / Bootstrap Italia | Adozione piena / solo pattern / nessun riferimento | Vedi audit grafico §8 |

---

## 9. Allegati

| File | Contenuto |
|---|---|
| `allegati/ricerca_normativa.md` | Requisiti normativi con fonti, Allegato A, Allegato 5, ETSI, punti non verificati |

I rapporti di lavoro di dettaglio (versamento e validazione, conservazione, servizi e infrastruttura, audit grafico) erano materiale interno e non fanno parte della pubblicazione open source (R18); i rilievi sono riportati in questo documento e nel registro delle criticità.
