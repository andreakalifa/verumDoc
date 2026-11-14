<!-- File generato da tools/docs/genera_cataloghi.py: non modificare a mano (REGOLE_SVILUPPO §28). -->

# Catalogo delle impostazioni

62 voci del registro `libs/verumdoc_rules/parametri_servizio.py` (console «Impostazioni»; catena di risoluzione in `libs/shared/parametri.py`).

| Codice | Gruppo | Tipo | Predefinito | Limiti | Livelli | Verso più sicuro | Applicata da | Riferimento |
|---|---|---|---|---|---|---|---|---|
| `ACCESSO_BLOCCO_MINUTI` | accesso | int | 15 | 1–1440 | CONSERVATORE (riservata al conservatore) | maggiore | gateway (accesso), onboarding (inviti) |  |
| `ACCESSO_METODI_AMMESSI` | accesso | codes | SPID_CIE |  | CONSERVATORE, ENTE | sottoinsieme | gateway (accesso), onboarding (inviti) | CAD, art. 64 (SPID e CIE per l'accesso ai servizi in rete) |
| `ACCESSO_RIAUTENTICAZIONE_MINUTI` | accesso | int | 5 | 1–15 | CONSERVATORE, ENTE | minore | gateway (gestione delle proprie credenziali) |  |
| `ACCESSO_TENTATIVI_MASSIMI` | accesso | int | 5 | 3–20 | CONSERVATORE (riservata al conservatore) | minore | gateway (accesso), onboarding (inviti) |  |
| `INVITO_VALIDITA_ORE` | accesso | int | 72 | 1–336 | CONSERVATORE, ENTE | minore | gateway (accesso), onboarding (inviti) |  |
| `PASSWORD_LUNGHEZZA_MINIMA` | accesso | int | 12 | 12–64 | CONSERVATORE, ENTE | maggiore | gateway (accesso), onboarding (inviti) |  |
| `ANNULLAMENTO_DOPPIA_APPROVAZIONE` | annullamento | choice | DOPO_CONSERVAZIONE | SEMPRE / DOPO_CONSERVAZIONE / MAI | CONSERVATORE, ENTE | ordine_scelte | ciclo-vita (annullamento: API e lavoratore) |  |
| `ANNULLAMENTO_RUOLI_APPROVAZIONE` | annullamento | codes | ADMIN_TENANT |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | ciclo-vita (annullamento: API e lavoratore) |  |
| `ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE` | annullamento | codes | ADMIN_TENANT |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | ciclo-vita (annullamento: API e lavoratore) |  |
| `ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE` | annullamento | codes | ADMIN_TENANT, OPERATORE, API_SYSTEM |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | ciclo-vita (annullamento: API e lavoratore) |  |
| `AUDIT_EXPORT_PERIODO_MINUTI` | audit | int | 15 | 5–60 | CONSERVATORE (riservata al conservatore) | minore | hashchain (export dell'audit) |  |
| `AUDIT_VERIFICA_COMPLETA_GIORNO` | audit | int | 1 | 1–28 | CONSERVATORE (riservata al conservatore) | minore | hashchain (CronJob verifica-audit) |  |
| `AUDIT_VERIFICA_FINESTRA_GIORNI` | audit | int | 2 | 1–31 | CONSERVATORE (riservata al conservatore) | maggiore | hashchain (CronJob verifica-audit) |  |
| `QUOTA_RALLENTAMENTO_SECONDI` | consumi | int | 30 | 1–3600 | CONSERVATORE (riservata al conservatore) | maggiore | gateway (quota giornaliera) |  |
| `QUOTA_SUPERAMENTO` | consumi | choice | RIFIUTA | RIFIUTA / RALLENTA | CONSERVATORE, ENTE | ordine_scelte | gateway (quota giornaliera) |  |
| `ATTESTAZIONE_SIGILLO` | firma_qualificata | choice | DISATTIVO | SEMPRE / SU_RICHIESTA / DISATTIVO | CONSERVATORE (riservata al conservatore) | ordine_scelte | hashchain (prove d'inclusione) |  |
| `CATENA_MARCA_INTERVALLO_MINUTI` | firma_qualificata | int | 60 | 1–1440 | CONSERVATORE (riservata al conservatore) | minore | hashchain (checkpoint) |  |
| `FIRMA_ALGORITMI_AMMESSI` | firma_qualificata | codes | sha256, sha384, sha512 |  | CONSERVATORE (riservata al conservatore) | sottoinsieme | hashchain (rimarcatura), signature | ETSI TS 119 312 (algoritmi crittografici) |
| `FIRMA_ALGORITMO_IMPRONTA` | firma_qualificata | choice | sha256 | sha512 / sha384 / sha256 | CONSERVATORE (riservata al conservatore) | ordine_scelte | signature (firma e marca) | ETSI TS 119 312 (algoritmi crittografici) |
| `FIRMA_LIVELLO_DOCUMENTI` | firma_qualificata | choice | B-LT | B-LTA / B-LT / B-T | CONSERVATORE (riservata al conservatore) | ordine_scelte | signature (firma dei documenti) | ETSI EN 319 122-1 (CAdES), EN 319 142-1 (PAdES) |
| `FIRMA_RINNOVO_PREAVVISO_GIORNI` | firma_qualificata | int | 90 | 30–730 | CONSERVATORE (riservata al conservatore) | maggiore | hashchain (rimarcatura) |  |
| `FIRMA_SENZA_FORNITORE` | firma_qualificata | choice | NON_FIRMATO | BLOCCA / NON_FIRMATO | CONSERVATORE (riservata al conservatore) | ordine_scelte | hashchain (documenti del conservatore, conservazione) | Linee guida AgID 2021, §4.7 e Allegato 4 (indice e rapporto firmati) |
| `FIRMA_TENTATIVI_MASSIMI` | firma_qualificata | int | 10 | 1–100 | CONSERVATORE (riservata al conservatore) | minore | hashchain (sigillo dei documenti) |  |
| `ANNULLAMENTO_RICHIESTA_FIRMATA` | firme | choice | FACOLTATIVA | OBBLIGATORIA / FACOLTATIVA | CONSERVATORE, ENTE | ordine_scelte | ciclo-vita (lavoratore: autorizzazioni, nulla osta, annullamento) |  |
| `CICLO_VITA_FIRMATARI_AUTORIZZATI` | firme | codes | (vuoto) |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | ciclo-vita (lavoratore: autorizzazioni, nulla osta, annullamento) | DPCM 3/12/2013 conservazione, art. 7; Linee guida AgID 2021, §4.11 |
| `CICLO_VITA_FIRMATARI_NULLA_OSTA` | firme | codes | (vuoto) |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | ciclo-vita (lavoratore: autorizzazioni, nulla osta, annullamento) | D.Lgs. 42/2004, art. 21 |
| `CICLO_VITA_FIRME_LIVELLO` | firme | choice | CRITTOGRAFICA | CON_REVOCHE / CRITTOGRAFICA | CONSERVATORE, ENTE | ordine_scelte | ciclo-vita (lavoratore: autorizzazioni, nulla osta, annullamento) | Regolamento (UE) 910/2014 (eIDAS), art. 32 |
| `NULLA_OSTA_FIRMA` | firme | choice | AVVISO | BLOCCANTE / AVVISO | CONSERVATORE, ENTE | ordine_scelte | ciclo-vita (lavoratore: autorizzazioni, nulla osta, annullamento) | D.Lgs. 42/2004, art. 21; DPR 445/2000, art. 69 |
| `NOTIFICHE_OBBLIGATORIE` | notifiche | codes | (vuoto) |  | CONSERVATORE, ENTE | sovrainsieme | notification (campanella e posta) |  |
| `NOTIFICHE_PERSONALI_GIORNI` | notifiche | int | 365 | 30–3650 | CONSERVATORE (riservata al conservatore) | maggiore | notification (lavoratore della posta, pulizia periodica) |  |
| `PEC_CASI_USO` | pec | codes | (vuoto) |  | CONSERVATORE, ENTE | sovrainsieme | notification (canale PEC) | DPR 68/2005; CAD art. 48 (trasmissione con PEC) |
| `PEC_CONSEGNA_ATTESA_ORE` | pec | int | 24 | 1–72 | CONSERVATORE (riservata al conservatore) | minore | notification (console PEC) | DM 2/11/2005, art. 12 (avviso di mancata consegna per superamento dei tempi massimi) |
| `QUOTA_PIANO_BUSINESS_PDV_GIORNO` | quote | int | 20000 | 1–10000000 | CONSERVATORE (riservata al conservatore) | minore | libs/shared/security/quote (tutti i servizi) |  |
| `QUOTA_PIANO_BUSINESS_RICHIESTE_MINUTO` | quote | int | 600 | 1–1000000 | CONSERVATORE (riservata al conservatore) | minore | libs/shared/security/quote (tutti i servizi) |  |
| `QUOTA_PIANO_ENTERPRISE_PDV_GIORNO` | quote | int | 200000 | 1–10000000 | CONSERVATORE (riservata al conservatore) | minore | libs/shared/security/quote (tutti i servizi) |  |
| `QUOTA_PIANO_ENTERPRISE_RICHIESTE_MINUTO` | quote | int | 6000 | 1–1000000 | CONSERVATORE (riservata al conservatore) | minore | libs/shared/security/quote (tutti i servizi) |  |
| `QUOTA_PIANO_STARTER_PDV_GIORNO` | quote | int | 1000 | 1–10000000 | CONSERVATORE (riservata al conservatore) | minore | libs/shared/security/quote (tutti i servizi) |  |
| `QUOTA_PIANO_STARTER_RICHIESTE_MINUTO` | quote | int | 120 | 1–1000000 | CONSERVATORE (riservata al conservatore) | minore | libs/shared/security/quote (tutti i servizi) |  |
| `PDD_VALIDITA_ORE` | restituzione | int | 72 | 1–720 | CONSERVATORE, ENTE | minore | distribution (PdD ZIP) |  |
| `RETENTION_PERMANENTE_ANNI` | retention | int | 30 | 5–100 | CONSERVATORE (riservata al conservatore) | maggiore | aip-builder, hashchain, ciclo-vita (lavoratore: rinnovo) | Linee guida AgID 2021, §4.6 e allegato 4 |
| `RETENTION_RINNOVO_SOGLIA_ANNI` | retention | int | 5 | 1–30 | CONSERVATORE (riservata al conservatore) | maggiore | aip-builder, hashchain, ciclo-vita (lavoratore: rinnovo) |  |
| `RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE` | riversamento | choice | RICHIESTA | RICHIESTA / NON_RICHIESTA | CONSERVATORE, ENTE (riservata al conservatore) | ordine_scelte | ciclo-vita (riversamento in uscita) |  |
| `RUOLI_PERMESSI_CONCEDIBILI` | ruoli | codes | archivio:consulta, versamento:esegui, esibizione:richiedi, rettifica:richiedi, annullamento:richiedi |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | onboarding (ruoli personalizzati), gateway (accesso) |  |
| `SCARTO_MINIMIZZAZIONE_GIORNI` | scarto | int | 30 | 1–365 | CONSERVATORE (riservata al conservatore) | minore | ciclo-vita (lavoro periodico del lavoratore) | Regolamento (UE) 2016/679, art. 5, par. 1, lett. c) ed e) |
| `SCARTO_PROPOSTA_AUTOMATICA` | scarto | choice | DISATTIVA | ATTIVA / DISATTIVA | CONSERVATORE, ENTE | ordine_scelte | ciclo-vita (lavoro periodico del lavoratore) | Linee guida AgID 2021, §4.11; D.Lgs. 42/2004, art. 21 |
| `SCARTO_PROPOSTA_CLASSI_ESCLUSE` | scarto | codes | (vuoto) |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | ciclo-vita (lavoro periodico del lavoratore) |  |
| `SCARTO_PROPOSTA_FREQUENZA_GIORNI` | scarto | int | 30 | 7–365 | CONSERVATORE, ENTE | minore | ciclo-vita (lavoro periodico del lavoratore) |  |
| `SCARTO_RIPRESE_AUTOMATICHE` | scarto | int | 3 | 0–20 | CONSERVATORE (riservata al conservatore) | minore | ciclo-vita (lavoro periodico del lavoratore) |  |
| `SCARTO_TENTATIVI_PDA` | scarto | int | 5 | 1–100 | CONSERVATORE (riservata al conservatore) | minore | ciclo-vita (lavoratore: scarto) |  |
| `SESSIONE_INATTIVITA_MINUTI_ADMIN_TENANT` | sessione | int | 15 | 5–120 | CONSERVATORE, ENTE | minore | gateway (accesso al portale) |  |
| `SESSIONE_INATTIVITA_MINUTI_OPERATORE` | sessione | int | 30 | 5–120 | CONSERVATORE, ENTE | minore | gateway (accesso al portale) |  |
| `SESSIONE_INATTIVITA_MINUTI_RSC_CONSERVATORE` | sessione | int | 15 | 5–120 | CONSERVATORE, ENTE | minore | gateway (accesso al portale) |  |
| `SESSIONE_INATTIVITA_MINUTI_SUPER_ADMIN` | sessione | int | 15 | 5–120 | CONSERVATORE, ENTE | minore | gateway (accesso al portale) |  |
| `SESSIONE_INATTIVITA_MINUTI_VIEWER` | sessione | int | 30 | 5–120 | CONSERVATORE, ENTE | minore | gateway (accesso al portale) |  |
| `SFTP_ALLOWLIST_OBBLIGATORIA` | sftp | choice | FACOLTATIVA | OBBLIGATORIA / FACOLTATIVA | CONSERVATORE, ENTE | ordine_scelte | sftp-ingest (accesso), onboarding (elenco degli indirizzi) |  |
| `SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4` | sftp | int | 16 | 0–32 | CONSERVATORE (riservata al conservatore) | maggiore | sftp-ingest (accesso), onboarding (elenco degli indirizzi) |  |
| `SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6` | sftp | int | 48 | 0–128 | CONSERVATORE (riservata al conservatore) | maggiore | sftp-ingest (accesso), onboarding (elenco degli indirizzi) |  |
| `SFTP_ALLOWLIST_VOCI_MASSIME` | sftp | int | 20 | 1–500 | CONSERVATORE (riservata al conservatore) | minore | sftp-ingest (accesso), onboarding (elenco degli indirizzi) |  |
| `STAGING_SCARTATI_GIORNI` | staging | int | 90 | 30–180 | CONSERVATORE, ENTE | minore | gateway (pulizia dello staging), dashboard-api |  |
| `VERIFICA_CAMPIONE_INCIDENTE_PDA` | verifiche | int | 200 | 10–1000 | CONSERVATORE (riservata al conservatore) | maggiore | hashchain (verifica continua) |  |
| `VERIFICA_CICLO_GIORNI` | verifiche | int | 365 | 7–365 | CLASSE, CONSERVATORE, ENTE | minore | hashchain (verifica continua) | Linee guida AgID 2021, §4.7 (verifica periodica dell'integrità) |
| `WEBHOOK_PORTE_AGGIUNTIVE` | webhook | codes | (vuoto) |  | CONSERVATORE, ENTE (riservata al conservatore) | sottoinsieme | notification (webhook) |  |
