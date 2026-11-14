<!-- File generato da tools/conformita/genera_documenti.py: non modificare a mano (REGOLE_SVILUPPO §0.2, §28). -->

# Catalogo dei permessi e matrice ruoli × permessi

17 permessi, 12 mai delegabili (`libs/verumdoc_rules/permessi.py`; controllo centrale in `libs/shared/security/permessi.py`). Concedibili ai ruoli personalizzati, se il conservatore lo consente: `archivio:consulta`, `versamento:esegui`, `esibizione:richiedi`, `rettifica:richiedi`, `annullamento:richiedi`.

*Ruoli fissi × permessi (`libs/verumdoc_rules/permessi.py`)*

| Permesso | Azione | Amministrazione del servizio | Responsabile del servizio | Amministrazione dell'ente | Operatore | Consultazione | Sistema versante | Delegabile a un ruolo personalizzato |
|---|---|---|---|---|---|---|---|---|
| `archivio:consulta` | Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento | sì | sì | sì | sì | sì | sì | sì |
| `versamento:esegui` | Versare pacchetti (API, SFTP, portale) | sì | sì | sì | sì | — | sì | sì |
| `esibizione:richiedi` | Chiedere pacchetti di distribuzione ed esibizioni | sì | sì | sì | sì | — | — | sì |
| `rettifica:richiedi` | Chiedere la rettifica dei metadati di un versamento | sì | sì | sì | sì | — | — | sì |
| `annullamento:richiedi` | Chiedere l'annullamento di un versamento | sì | sì | sì | sì | — | — | sì |
| `scarto:esegui` | Eseguire lo scarto (solo il conservatore) | sì | sì | — | — | — | — | mai |
| `scarto:autorizza` | Autorizzare lo scarto come produttore (solo l'amministratore dell'ente) | sì | sì | sì | — | — | — | mai |
| `annullamento:approva` | Approvare un annullamento come seconda persona (solo l'amministratore dell'ente) | sì | sì | sì | — | — | — | mai |
| `firmatari:gestisci` | Gestire i firmatari autorizzati (solo il conservatore) | sì | sì | — | — | — | — | mai |
| `pratica:chiudi_eccezioni` | Chiudere uno scarto con eccezioni (solo il conservatore) | sì | sì | — | — | — | — | mai |
| `utenti:gestisci` | Invitare e gestire gli utenti dell'ente | sì | sì | sì | — | — | — | mai |
| `ruoli:gestisci` | Creare e assegnare ruoli personalizzati | sì | sì | sì | — | — | — | mai |
| `accesso:configura` | Scegliere i metodi d'accesso dell'ente | sì | sì | sì | — | — | — | mai |
| `firma_fornitori:gestisci` | Registrare e provare i fornitori di firma, sigillo e marca temporale qualificati (solo il conservatore) | sì | sì | — | — | — | — | mai |
| `pec_canali:gestisci` | Registrare e provare i canali PEC del conservatore e leggerne le ricevute (solo il conservatore) | sì | sì | — | — | — | — | mai |
| `pratica:riprendi` | Riprendere una pratica ferma dopo ripetuti errori del servizio (solo il conservatore) | sì | sì | — | — | — | — | mai |
| `documentazione:genera` | Generare e scaricare il manuale di conservazione, il piano della sicurezza e la documentazione del servizio (solo il conservatore) | sì | sì | — | — | — | — | mai |
