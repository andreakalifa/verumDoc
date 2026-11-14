<!-- File generato da tools/conformita/genera_documenti.py: non modificare a mano (REGOLE_SVILUPPO §0.2, §28). -->

# Catalogo delle route del frontend

*Generato da `frontend/src/App.tsx` e `frontend/src/components/Layout.tsx`*

| Voce | Valore |
|---|---|
| Edizione | del repository: versione, data e firma si assegnano alla generazione dalla console «Documentazione del servizio» |
| Impronta del contenuto (SHA-256) | 387480569c9d759fc716e8f28f2c55e16477a0d55c77d4dffca66e87857e1c31 |
| Dati da compilare | 0 |
| Requisiti da verificare sul testo ufficiale | 0 |
| Stato della firma | non firmato: la firma del responsabile si appone sulla versione stampabile |

<details><summary>Fonti della generazione</summary>

- repo:frontend/src/App.tsx — sha256 82581f4de9361b4d4f3b0fc1bac7c328c1b33eb437cf4591068379601442459b
- rilascio — changelog/CHANGELOG-2026-11-14-rilascio-R18_2.md (2026-11-14)

</details>

## 1. Route

51 route; 6 pagine senza voce di menu (raggiungibili da collegamenti interni). Il servizio ricontrolla sempre i permessi: la guardia della pagina evita solo di mostrare ciò che sarebbe rifiutato.

| Percorso | Pagina | Ruoli | Abilitazione | Menu |
|---|---|---|---|---|
| `/login` | LoginPage | pubblica | — | collegamenti interni |
| `/attiva` | AttivaPage | pubblica | — | collegamenti interni |
| `/` | — | tutti gli utenti autenticati | — | reindirizzamento |
| `/preferenze` | PreferenzePage | tutti gli utenti autenticati | — | collegamenti interni |
| `/preferenze/notifiche` | NotifichePreferenzePage | tutti gli utenti autenticati | — | collegamenti interni |
| `/preferenze/credenziali` | CredenzialiPage | tutti gli utenti autenticati | — | collegamenti interni |
| `/overview` | OverviewPage | tutti gli utenti autenticati | — | Servizio · Quadro del servizio |
| `/versamento` | VersamentoLivePage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore | versamento:ud | Versamento · Versa un documento |
| `/versamenti` | VersamentPage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Responsabile del servizio | — | Versamento · Pacchetti in arrivo |
| `/aip` | AIPPage | tutti gli utenti autenticati | — | Archivio · Pacchetti conservati |
| `/blockchain` | BlockchainExplorerPage | tutti gli utenti autenticati | — | Integrità · Blocchi |
| `/hashchain` | HashchainPage | Amministrazione del servizio, Amministrazione dell'ente, Responsabile del servizio | — | Integrità · Catena delle impronte |
| `/search` | SearchPage | tutti gli utenti autenticati | — | Archivio · Ricerca |
| `/distribution` | DistributionPage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Consultazione, Responsabile del servizio | — | Esibizioni · Esibizioni e copie |
| `/classi-documentali` | ClassiDocumentaliPage | Amministrazione del servizio, Amministrazione dell'ente, Responsabile del servizio | — | Enti · Classi documentali |
| `/tenants` | TenantsPage | Amministrazione del servizio | — | Enti · Enti serviti |
| `/utenti` | UtentiPage | Amministrazione del servizio, Amministrazione dell'ente | — | Enti · Utenti e chiavi API |
| `/notifiche` | NotifichePage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Responsabile del servizio | — | Servizio · Notifiche |
| `/sla` | SLAPage | Amministrazione del servizio, Amministrazione dell'ente, Responsabile del servizio | — | Conformità · Livelli di servizio |
| `/manuale` | ManualePage | Amministrazione del servizio, Responsabile del servizio | — | Conformità · Manuale di conservazione |
| `/documentazione-servizio` | DocumentazioneServizioPage | Amministrazione del servizio, Responsabile del servizio | — | Conformità · Documentazione del servizio |
| `/audit` | AuditLogPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Conformità · Registro delle operazioni |
| `/fascicoli` | FascicoliPage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Responsabile del servizio | — | Archivio · Fascicoli |
| `/contratti` | ContrattiPage | Amministrazione del servizio, Responsabile del servizio | — | Conformità · Contratti |
| `/nomine-rsc` | NomineRSCPage | Amministrazione del servizio, Responsabile del servizio | — | Conformità · Nomine |
| `/code-scarto` | CodeScartoPage | Amministrazione del servizio, Responsabile del servizio | — | Servizio · Messaggi non elaborati |
| `/configurazione` | ConfImpostazioniPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore, Consultazione | — | Configurazione · Impostazioni e regole |
| `/configurazione/profili` | ConfProfiliPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore, Consultazione | — | Configurazione · Profili di metadati |
| `/configurazione/classi-accordi` | ConfClassiAccordiPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Configurazione · Classi e accordi |
| `/configurazione/quote` | ConfQuotePage | Amministrazione del servizio, Responsabile del servizio | — | Configurazione · Quote degli enti |
| `/configurazione/firme` | ConfFirmePage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore, Consultazione | — | Configurazione · Verifica delle firme |
| `/configurazione/formati` | ConfFormatiPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore, Consultazione | — | Configurazione · Catalogo dei formati |
| `/configurazione/ciclo-vita` | ConfCicloVitaPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore, Consultazione | — | Configurazione · Ciclo di vita automatico |
| `/configurazione/accesso` | ConfAccessoPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Configurazione · Accesso e notifiche |
| `/configurazione/firma-qualificata` | ConfFirmaQualificataPage | Amministrazione del servizio, Responsabile del servizio | — | Configurazione · Fornitori di firma e marca |
| `/firme-responsabile` | FirmeResponsabilePage | Amministrazione del servizio, Responsabile del servizio | — | Conformità · Firme del responsabile |
| `/utenti/inviti` | InvitiPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Enti · Inviti e metodi d'accesso |
| `/utenti/ruoli` | RuoliPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Enti · Ruoli personalizzati |
| `/configurazione/bozze` | ConfBozzePage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Configurazione · Bozze |
| `/configurazione/bozze/:id` | ConfBozzaPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | collegamenti interni |
| `/configurazione/proposte` | ConfPropostePage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Configurazione · Proposte |
| `/configurazione/validatore` | ValidatorePage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente, Operatore | — | Configurazione · Validatore |
| `/canali/sftp` | CanaliSftpPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Canali · Account SFTP |
| `/canali/webhook` | CanaliWebhookPage | Amministrazione dell'ente | — | Canali · Webhook |
| `/canali/pec` | CanaliPecPage | Amministrazione del servizio, Responsabile del servizio, Amministrazione dell'ente | — | Canali · Canali PEC |
| `/registro` | RegistroPage | Amministrazione del servizio, Responsabile del servizio | — | Integrità · Registro e verifiche |
| `/pratiche` | PratichePage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Responsabile del servizio | — | Pratiche · Pratiche |
| `/pratiche/nuova` | NuovaPraticaPage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Responsabile del servizio | — | Pratiche · Nuova pratica |
| `/pratiche/:id` | PraticaPage | Amministrazione del servizio, Amministrazione dell'ente, Operatore, Responsabile del servizio | — | collegamenti interni |
| `/da-realizzare/:funzione` | DaRealizzarePage | tutti gli utenti autenticati | — | collegamenti interni |
| `/*` | — | tutti gli utenti autenticati | — | reindirizzamento |
