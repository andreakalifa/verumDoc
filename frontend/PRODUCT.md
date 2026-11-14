# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
- **Primario: il responsabile del servizio di conservazione**, lato conservatore. Governa il servizio per decine di enti: integrità dell'archivio, eccezioni, scadenze (verifiche periodiche, rinnovo marche, firma del manuale), esibizioni a terzi. Risponde ad AgID e ai clienti. Decisione del 20/10/2026 (VD-DR-03).
- Secondari, confermati: il responsabile della conservazione dell'ente cliente (spesso un dirigente comunale non tecnico, deve dimostrare che l'archivio è a posto), gli operatori che versano documenti (anche centinaia al giorno), i sistemi versanti via API.
- Terzi che ricevono prove: ispettori, Guardia di Finanza, tribunali, cittadini in accesso agli atti.

## Product Purpose
Servizio di conservazione digitale a norma (Linee guida AgID sulla formazione, gestione e conservazione dei documenti informatici; Regolamento conservatori Det. 455/2021; UNI 11386:2020 SInCRO; OAIS). Riceve pacchetti di versamento, li verifica, li conserva immutabili con firma e marca, ne dimostra l'integrità nel tempo, li esibisce a terzi, ne gestisce lo scarto autorizzato. Successo: un ente può dimostrare in ogni momento, a chiunque, che i suoi documenti sono integri e conservati secondo le regole vigenti quando sono stati versati.

## Positioning
Quattro tratti confermati dal committente:
1. **La norma che cambia**: le regole sono dati versionati; ogni pacchetto porta la versione delle regole con cui è stato conservato.
2. **Prova verificabile**: l'integrità è dimostrabile a terzi senza doversi fidare del conservatore.
3. **Semplicità per gli enti**: usabile da Comuni ed enti piccoli senza personale tecnico.
4. **Trasparenza operativa**: il sistema dice sempre la verità sul proprio stato, errori compresi.

## Operating Context
- Mercato misto, con priorità ed enfasi sulla PA (VD-P-08): Comuni, ASL, Unioni di comuni; anche aziende private (ambito fiscale).
- Vocabolario del mestiere: versamento, pacchetto di versamento/archiviazione/distribuzione, rapporto di versamento, segnatura, impronta, marca temporale, esibizione, scarto, nulla osta della Soprintendenza, manuale di conservazione, classe documentale, accordo di versamento, responsabile della conservazione.
- Rituali: verifica periodica (almeno quinquennale), rinnovo marche prima della scadenza, firma del manuale, proposta di scarto, esibizione davanti a un ispettore.
- Uso da ufficio, monitor da scrivania in ambienti illuminati; momenti ad alta responsabilità.

## Capabilities and Constraints
- Stack esistente: React 18 + TypeScript + Vite + TanStack Query (frontend), FastAPI + PostgreSQL + Kafka + S3 WORM (backend, 9 servizi).
- Stato reale al 20/10/2026: completamento ~15–20% (`docs/audit/AUDIT_FUNZIONALE_2026-10-20.md`). Firma, marca, IPdA conforme, esibizione, scarto non funzionano ancora: il design non deve rappresentarli come esistenti nel prodotto in produzione senza marcarli come dimostrativi.
- Interfaccia in italiano.
- Nessun riferimento a Designers Italia / Bootstrap Italia (VD-DR-02): identità propria, non deve sembrare un servizio dello Stato.

## Brand Commitments
- Nome attuale «VerumDoc», **provvisorio**. Nessun logo, colore o carattere vincolante.
- Il sistema grafico precedente (navy + oro, Instrument Serif) è dismesso (VD-DR-00).
- Da evitare per esplicita richiesta del committente: palette tenui e prudenti, composizione da kit (sidebar + card + tabella da template SaaS), tipografia anonima.

## Evidence on Hand
- Nessun cliente reale, testimonianza, certificazione o numero di produzione: ogni dato mostrato in un design è dimostrativo e va etichettato come tale.
- Normativa e requisiti: `docs/audit/allegati/ricerca_normativa.md`.
- Audit dell'interfaccia esistente e screenshot: `docs/design/AUDIT_GRAFICO_2026-10-20.md`.

## Product Principles
1. **La verità prima della rassicurazione**: uno stato sconosciuto o un errore si mostrano come tali, mai come «vuoto» o «tutto bene».
2. **Prova, non promessa**: ogni affermazione d'integrità è accompagnata dalla sua evidenza.
3. **La regola ha una data**: ciò che si mostra di un pacchetto si legge con le regole del suo momento.
4. **Il gesto del mestiere è il centro**: versare, verificare, esibire, scartare sono azioni di prima classe, non tipi di record.
5. **Chiaro per chi non è tecnico**: parole del mestiere, sigle come dettaglio.

## Accessibility & Inclusion
WCAG 2.1 AA vincolante (EN 301 549, Linee guida AgID sull'accessibilità per gli acquisti PA). Utenti anche anziani e non tecnici: testo leggibile, contrasto pieno, navigazione completa da tastiera.
