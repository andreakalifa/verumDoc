<!-- File generato da tools/conformita/genera_documenti.py: non modificare a mano (REGOLE_SVILUPPO §0.2, §28). -->

# Guida alle funzionalità

*Che cosa fa ogni ruolo nella console — documento generato*

| Voce | Valore |
|---|---|
| Edizione | del repository: versione, data e firma si assegnano alla generazione dalla console «Documentazione del servizio» |
| Impronta del contenuto (SHA-256) | b98dd7c05eb599c2c1a86c1ee769055dc97330924ce8b53e0b6a1a8cb9b090c3 |
| Dati da compilare | 0 |
| Requisiti da verificare sul testo ufficiale | 0 |
| Stato della firma | non firmato: la firma del responsabile si appone sulla versione stampabile |

<details><summary>Fonti della generazione</summary>

- repo:frontend/src/App.tsx — sha256 82581f4de9361b4d4f3b0fc1bac7c328c1b33eb437cf4591068379601442459b
- repo:frontend/src/components/Layout.tsx — sha256 81b8052d3a8255af13f4d4566522e501d135638f63a6f680a777cd5062c396f1
- repo:libs/verumdoc_rules/permessi.py — sha256 8916b032a2ee15b8163d135f5d6981a5d3f7543d6671be44f0a11c9a4895b0f8
- rilascio — changelog/CHANGELOG-2026-11-14-rilascio-R18_2.md (2026-11-14)

</details>

## 0. Come leggere questa guida

Per ogni ruolo: che cosa può fare (permessi del codice) e da quali pagine della console. Guida generata da `libs/verumdoc_rules/permessi.py`, `frontend/src/App.tsx` e `frontend/src/components/Layout.tsx`: se cambia un permesso o una pagina, la guida si rigenera (REGOLE §0.2).

Ultima modifica: 2026-11-14 (`changelog/CHANGELOG-2026-11-14-rilascio-R18_2.md`).

## 1. Amministrazione del servizio (SUPER_ADMIN)

- Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento (`archivio:consulta`)
- Versare pacchetti (API, SFTP, portale) (`versamento:esegui`)
- Chiedere pacchetti di distribuzione ed esibizioni (`esibizione:richiedi`)
- Chiedere la rettifica dei metadati di un versamento (`rettifica:richiedi`)
- Chiedere l'annullamento di un versamento (`annullamento:richiedi`)
- Eseguire lo scarto (solo il conservatore) (`scarto:esegui`, mai delegabile)
- Autorizzare lo scarto come produttore (solo l'amministratore dell'ente) (`scarto:autorizza`, mai delegabile)
- Approvare un annullamento come seconda persona (solo l'amministratore dell'ente) (`annullamento:approva`, mai delegabile)
- Gestire i firmatari autorizzati (solo il conservatore) (`firmatari:gestisci`, mai delegabile)
- Chiudere uno scarto con eccezioni (solo il conservatore) (`pratica:chiudi_eccezioni`, mai delegabile)
- Invitare e gestire gli utenti dell'ente (`utenti:gestisci`, mai delegabile)
- Creare e assegnare ruoli personalizzati (`ruoli:gestisci`, mai delegabile)
- Scegliere i metodi d'accesso dell'ente (`accesso:configura`, mai delegabile)
- Registrare e provare i fornitori di firma, sigillo e marca temporale qualificati (solo il conservatore) (`firma_fornitori:gestisci`, mai delegabile)
- Registrare e provare i canali PEC del conservatore e leggerne le ricevute (solo il conservatore) (`pec_canali:gestisci`, mai delegabile)
- Riprendere una pratica ferma dopo ripetuti errori del servizio (solo il conservatore) (`pratica:riprendi`, mai delegabile)
- Generare e scaricare il manuale di conservazione, il piano della sicurezza e la documentazione del servizio (solo il conservatore) (`documentazione:genera`, mai delegabile)

| Area | Pagina | Percorso | Note |
|---|---|---|---|
| Servizio | Quadro del servizio | `/overview` | — |
| Versamento | Versa un documento | `/versamento` | serve l'abilitazione «versamento:ud» |
| Versamento | Pacchetti in arrivo | `/versamenti` | — |
| Archivio | Pacchetti conservati | `/aip` | — |
| Integrità | Blocchi | `/blockchain` | — |
| Integrità | Catena delle impronte | `/hashchain` | — |
| Archivio | Ricerca | `/search` | — |
| Esibizioni | Esibizioni e copie | `/distribution` | — |
| Enti | Classi documentali | `/classi-documentali` | — |
| Enti | Enti serviti | `/tenants` | — |
| Enti | Utenti e chiavi API | `/utenti` | — |
| Servizio | Notifiche | `/notifiche` | — |
| Conformità | Livelli di servizio | `/sla` | — |
| Conformità | Manuale di conservazione | `/manuale` | — |
| Conformità | Documentazione del servizio | `/documentazione-servizio` | — |
| Conformità | Registro delle operazioni | `/audit` | — |
| Archivio | Fascicoli | `/fascicoli` | — |
| Conformità | Contratti | `/contratti` | — |
| Conformità | Nomine | `/nomine-rsc` | — |
| Servizio | Messaggi non elaborati | `/code-scarto` | — |
| Configurazione | Impostazioni e regole | `/configurazione` | — |
| Configurazione | Profili di metadati | `/configurazione/profili` | — |
| Configurazione | Classi e accordi | `/configurazione/classi-accordi` | — |
| Configurazione | Quote degli enti | `/configurazione/quote` | — |
| Configurazione | Verifica delle firme | `/configurazione/firme` | — |
| Configurazione | Catalogo dei formati | `/configurazione/formati` | — |
| Configurazione | Ciclo di vita automatico | `/configurazione/ciclo-vita` | — |
| Configurazione | Accesso e notifiche | `/configurazione/accesso` | — |
| Configurazione | Fornitori di firma e marca | `/configurazione/firma-qualificata` | — |
| Conformità | Firme del responsabile | `/firme-responsabile` | — |
| Enti | Inviti e metodi d'accesso | `/utenti/inviti` | — |
| Enti | Ruoli personalizzati | `/utenti/ruoli` | — |
| Configurazione | Bozze | `/configurazione/bozze` | — |
| Configurazione | Proposte | `/configurazione/proposte` | — |
| Configurazione | Validatore | `/configurazione/validatore` | — |
| Canali | Account SFTP | `/canali/sftp` | — |
| Canali | Canali PEC | `/canali/pec` | — |
| Integrità | Registro e verifiche | `/registro` | — |
| Pratiche | Pratiche | `/pratiche` | — |
| Pratiche | Nuova pratica | `/pratiche/nuova` | — |

Pagine raggiungibili da collegamenti interni: `/preferenze`, `/preferenze/notifiche`, `/preferenze/credenziali`, `/configurazione/bozze/:id`, `/pratiche/:id`, `/da-realizzare/:funzione`.

## 2. Responsabile del servizio (RSC_CONSERVATORE)

- Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento (`archivio:consulta`)
- Versare pacchetti (API, SFTP, portale) (`versamento:esegui`)
- Chiedere pacchetti di distribuzione ed esibizioni (`esibizione:richiedi`)
- Chiedere la rettifica dei metadati di un versamento (`rettifica:richiedi`)
- Chiedere l'annullamento di un versamento (`annullamento:richiedi`)
- Eseguire lo scarto (solo il conservatore) (`scarto:esegui`, mai delegabile)
- Autorizzare lo scarto come produttore (solo l'amministratore dell'ente) (`scarto:autorizza`, mai delegabile)
- Approvare un annullamento come seconda persona (solo l'amministratore dell'ente) (`annullamento:approva`, mai delegabile)
- Gestire i firmatari autorizzati (solo il conservatore) (`firmatari:gestisci`, mai delegabile)
- Chiudere uno scarto con eccezioni (solo il conservatore) (`pratica:chiudi_eccezioni`, mai delegabile)
- Invitare e gestire gli utenti dell'ente (`utenti:gestisci`, mai delegabile)
- Creare e assegnare ruoli personalizzati (`ruoli:gestisci`, mai delegabile)
- Scegliere i metodi d'accesso dell'ente (`accesso:configura`, mai delegabile)
- Registrare e provare i fornitori di firma, sigillo e marca temporale qualificati (solo il conservatore) (`firma_fornitori:gestisci`, mai delegabile)
- Registrare e provare i canali PEC del conservatore e leggerne le ricevute (solo il conservatore) (`pec_canali:gestisci`, mai delegabile)
- Riprendere una pratica ferma dopo ripetuti errori del servizio (solo il conservatore) (`pratica:riprendi`, mai delegabile)
- Generare e scaricare il manuale di conservazione, il piano della sicurezza e la documentazione del servizio (solo il conservatore) (`documentazione:genera`, mai delegabile)

| Area | Pagina | Percorso | Note |
|---|---|---|---|
| Servizio | Quadro del servizio | `/overview` | — |
| Versamento | Versa un documento | `/versamento` | serve l'abilitazione «versamento:ud» |
| Versamento | Pacchetti in arrivo | `/versamenti` | — |
| Archivio | Pacchetti conservati | `/aip` | — |
| Integrità | Blocchi | `/blockchain` | — |
| Integrità | Catena delle impronte | `/hashchain` | — |
| Archivio | Ricerca | `/search` | — |
| Esibizioni | Esibizioni e copie | `/distribution` | — |
| Enti | Classi documentali | `/classi-documentali` | — |
| Servizio | Notifiche | `/notifiche` | — |
| Conformità | Livelli di servizio | `/sla` | — |
| Conformità | Manuale di conservazione | `/manuale` | — |
| Conformità | Documentazione del servizio | `/documentazione-servizio` | — |
| Conformità | Registro delle operazioni | `/audit` | — |
| Archivio | Fascicoli | `/fascicoli` | — |
| Conformità | Contratti | `/contratti` | — |
| Conformità | Nomine | `/nomine-rsc` | — |
| Servizio | Messaggi non elaborati | `/code-scarto` | — |
| Configurazione | Impostazioni e regole | `/configurazione` | — |
| Configurazione | Profili di metadati | `/configurazione/profili` | — |
| Configurazione | Classi e accordi | `/configurazione/classi-accordi` | — |
| Configurazione | Quote degli enti | `/configurazione/quote` | — |
| Configurazione | Verifica delle firme | `/configurazione/firme` | — |
| Configurazione | Catalogo dei formati | `/configurazione/formati` | — |
| Configurazione | Ciclo di vita automatico | `/configurazione/ciclo-vita` | — |
| Configurazione | Accesso e notifiche | `/configurazione/accesso` | — |
| Configurazione | Fornitori di firma e marca | `/configurazione/firma-qualificata` | — |
| Conformità | Firme del responsabile | `/firme-responsabile` | — |
| Enti | Inviti e metodi d'accesso | `/utenti/inviti` | — |
| Enti | Ruoli personalizzati | `/utenti/ruoli` | — |
| Configurazione | Bozze | `/configurazione/bozze` | — |
| Configurazione | Proposte | `/configurazione/proposte` | — |
| Configurazione | Validatore | `/configurazione/validatore` | — |
| Canali | Account SFTP | `/canali/sftp` | — |
| Canali | Canali PEC | `/canali/pec` | — |
| Integrità | Registro e verifiche | `/registro` | — |
| Pratiche | Pratiche | `/pratiche` | — |
| Pratiche | Nuova pratica | `/pratiche/nuova` | — |

Pagine raggiungibili da collegamenti interni: `/preferenze`, `/preferenze/notifiche`, `/preferenze/credenziali`, `/configurazione/bozze/:id`, `/pratiche/:id`, `/da-realizzare/:funzione`.

## 3. Amministrazione dell'ente (ADMIN_TENANT)

- Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento (`archivio:consulta`)
- Versare pacchetti (API, SFTP, portale) (`versamento:esegui`)
- Chiedere pacchetti di distribuzione ed esibizioni (`esibizione:richiedi`)
- Chiedere la rettifica dei metadati di un versamento (`rettifica:richiedi`)
- Chiedere l'annullamento di un versamento (`annullamento:richiedi`)
- Autorizzare lo scarto come produttore (solo l'amministratore dell'ente) (`scarto:autorizza`, mai delegabile)
- Approvare un annullamento come seconda persona (solo l'amministratore dell'ente) (`annullamento:approva`, mai delegabile)
- Invitare e gestire gli utenti dell'ente (`utenti:gestisci`, mai delegabile)
- Creare e assegnare ruoli personalizzati (`ruoli:gestisci`, mai delegabile)
- Scegliere i metodi d'accesso dell'ente (`accesso:configura`, mai delegabile)

| Area | Pagina | Percorso | Note |
|---|---|---|---|
| Servizio | Quadro del servizio | `/overview` | — |
| Versamento | Versa un documento | `/versamento` | serve l'abilitazione «versamento:ud» |
| Versamento | Pacchetti in arrivo | `/versamenti` | — |
| Archivio | Pacchetti conservati | `/aip` | — |
| Integrità | Blocchi | `/blockchain` | — |
| Integrità | Catena delle impronte | `/hashchain` | — |
| Archivio | Ricerca | `/search` | — |
| Esibizioni | Esibizioni e copie | `/distribution` | — |
| Enti | Classi documentali | `/classi-documentali` | — |
| Enti | Utenti e chiavi API | `/utenti` | — |
| Servizio | Notifiche | `/notifiche` | — |
| Conformità | Livelli di servizio | `/sla` | — |
| Conformità | Registro delle operazioni | `/audit` | — |
| Archivio | Fascicoli | `/fascicoli` | — |
| Configurazione | Impostazioni e regole | `/configurazione` | — |
| Configurazione | Profili di metadati | `/configurazione/profili` | — |
| Configurazione | Classi e accordi | `/configurazione/classi-accordi` | — |
| Configurazione | Verifica delle firme | `/configurazione/firme` | — |
| Configurazione | Catalogo dei formati | `/configurazione/formati` | — |
| Configurazione | Ciclo di vita automatico | `/configurazione/ciclo-vita` | — |
| Configurazione | Accesso e notifiche | `/configurazione/accesso` | — |
| Enti | Inviti e metodi d'accesso | `/utenti/inviti` | — |
| Enti | Ruoli personalizzati | `/utenti/ruoli` | — |
| Configurazione | Bozze | `/configurazione/bozze` | — |
| Configurazione | Proposte | `/configurazione/proposte` | — |
| Configurazione | Validatore | `/configurazione/validatore` | — |
| Canali | Account SFTP | `/canali/sftp` | — |
| Canali | Webhook | `/canali/webhook` | — |
| Canali | Canali PEC | `/canali/pec` | — |
| Pratiche | Pratiche | `/pratiche` | — |
| Pratiche | Nuova pratica | `/pratiche/nuova` | — |

Pagine raggiungibili da collegamenti interni: `/preferenze`, `/preferenze/notifiche`, `/preferenze/credenziali`, `/configurazione/bozze/:id`, `/pratiche/:id`, `/da-realizzare/:funzione`.

## 4. Operatore (OPERATORE)

- Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento (`archivio:consulta`)
- Versare pacchetti (API, SFTP, portale) (`versamento:esegui`)
- Chiedere pacchetti di distribuzione ed esibizioni (`esibizione:richiedi`)
- Chiedere la rettifica dei metadati di un versamento (`rettifica:richiedi`)
- Chiedere l'annullamento di un versamento (`annullamento:richiedi`)

| Area | Pagina | Percorso | Note |
|---|---|---|---|
| Servizio | Quadro del servizio | `/overview` | — |
| Versamento | Versa un documento | `/versamento` | serve l'abilitazione «versamento:ud» |
| Versamento | Pacchetti in arrivo | `/versamenti` | — |
| Archivio | Pacchetti conservati | `/aip` | — |
| Integrità | Blocchi | `/blockchain` | — |
| Archivio | Ricerca | `/search` | — |
| Esibizioni | Esibizioni e copie | `/distribution` | — |
| Servizio | Notifiche | `/notifiche` | — |
| Archivio | Fascicoli | `/fascicoli` | — |
| Configurazione | Impostazioni e regole | `/configurazione` | — |
| Configurazione | Profili di metadati | `/configurazione/profili` | — |
| Configurazione | Verifica delle firme | `/configurazione/firme` | — |
| Configurazione | Catalogo dei formati | `/configurazione/formati` | — |
| Configurazione | Ciclo di vita automatico | `/configurazione/ciclo-vita` | — |
| Configurazione | Validatore | `/configurazione/validatore` | — |
| Pratiche | Pratiche | `/pratiche` | — |
| Pratiche | Nuova pratica | `/pratiche/nuova` | — |

Pagine raggiungibili da collegamenti interni: `/preferenze`, `/preferenze/notifiche`, `/preferenze/credenziali`, `/pratiche/:id`, `/da-realizzare/:funzione`.

## 5. Consultazione (VIEWER)

- Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento (`archivio:consulta`)

| Area | Pagina | Percorso | Note |
|---|---|---|---|
| Servizio | Quadro del servizio | `/overview` | — |
| Archivio | Pacchetti conservati | `/aip` | — |
| Integrità | Blocchi | `/blockchain` | — |
| Archivio | Ricerca | `/search` | — |
| Esibizioni | Esibizioni e copie | `/distribution` | — |
| Configurazione | Impostazioni e regole | `/configurazione` | — |
| Configurazione | Profili di metadati | `/configurazione/profili` | — |
| Configurazione | Verifica delle firme | `/configurazione/firme` | — |
| Configurazione | Catalogo dei formati | `/configurazione/formati` | — |
| Configurazione | Ciclo di vita automatico | `/configurazione/ciclo-vita` | — |

Pagine raggiungibili da collegamenti interni: `/preferenze`, `/preferenze/notifiche`, `/preferenze/credenziali`, `/da-realizzare/:funzione`.

## 6. Sistema versante (API_SYSTEM)

- Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento (`archivio:consulta`)
- Versare pacchetti (API, SFTP, portale) (`versamento:esegui`)

| Area | Pagina | Percorso | Note |
|---|---|---|---|
| Servizio | Quadro del servizio | `/overview` | — |
| Archivio | Pacchetti conservati | `/aip` | — |
| Integrità | Blocchi | `/blockchain` | — |
| Archivio | Ricerca | `/search` | — |

Pagine raggiungibili da collegamenti interni: `/preferenze`, `/preferenze/notifiche`, `/preferenze/credenziali`, `/da-realizzare/:funzione`.
