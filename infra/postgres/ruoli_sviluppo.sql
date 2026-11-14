-- SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
-- SPDX-License-Identifier: EUPL-1.2
-- VerumDoc — ruoli di login di SVILUPPO (R04 secondo giro, rilievo 5).
-- Eseguito dal servizio compose «db-ruoli» (idempotente, anche su un volume
-- esistente) con il superutente dell'immagine (vd_user), che in sviluppo fa
-- solo le migrazioni del gateway. Nessuna tabella qui (REGOLE §16.2): i
-- privilegi sulle tabelle li danno le migrazioni 20261023_031 e 033 ai gruppi.
--
--   vd_app           servizi: membro di verumdoc_applicazione (solo DML, nessuna
--                    proprietà; niente UPDATE/DELETE sui registri append-only)
--   vd_ciclo_vita_lavoratore  lavoratore di ciclo-vita (revisione 3 di R05, T5):
--                    verumdoc_applicazione e verumdoc_ciclo_vita_lavoratore
--                    (INSERT negli esiti di esecuzione, migrazione 043). Il
--                    login dell'API di ciclo-vita in esercizio NON ne è membro.
--   verumdoc_sessioni_portale (gruppo, revisione R06 V4): diritti su
--                    sessioni_attive (migrazione 045); in esercizio SOLO i login
--                    dei servizi del portale, non il server SFTP. In sviluppo
--                    vd_app serve tutti i servizi e quindi ne è membro.
--   vd_manutenzione  manutenzione delle partizioni: verumdoc_manutenzione
--                    (proprietario delle sole tabelle partizionate operative) e
--                    verumdoc_applicazione; NON proprietario dei registri
--
-- Password di sviluppo (*_dev_only): rifiutate fuori dallo sviluppo.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_applicazione') THEN
        CREATE ROLE verumdoc_applicazione NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_manutenzione') THEN
        CREATE ROLE verumdoc_manutenzione NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_ciclo_vita_lavoratore') THEN
        CREATE ROLE verumdoc_ciclo_vita_lavoratore NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_sessioni_portale') THEN
        CREATE ROLE verumdoc_sessioni_portale NOLOGIN;
    END IF;
    -- R12a (VD-C-148): elenco degli oggetti del registro, solo per la hashchain
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_registro_worm') THEN
        CREATE ROLE verumdoc_registro_worm NOLOGIN;
    END IF;
    -- R13a (migrazione 054): documenti da sigillare, fornitori di firma, servizio di firma
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_firme_documenti') THEN
        CREATE ROLE verumdoc_firme_documenti NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_firme_documenti_sigillo') THEN
        CREATE ROLE verumdoc_firme_documenti_sigillo NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_firma_fornitori') THEN
        CREATE ROLE verumdoc_firma_fornitori NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_firma') THEN
        CREATE ROLE verumdoc_firma NOLOGIN;
    END IF;
    -- R13b (migrazione 055): canali PEC (console, lavoratore della posta, chi accoda)
    -- e canale personale della campanella (notification)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_pec_canali') THEN
        CREATE ROLE verumdoc_pec_canali NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_pec') THEN
        CREATE ROLE verumdoc_pec NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_pec_accoda') THEN
        CREATE ROLE verumdoc_pec_accoda NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_notifiche_personali') THEN
        CREATE ROLE verumdoc_notifiche_personali NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_notifiche_pulizia') THEN
        CREATE ROLE verumdoc_notifiche_pulizia NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_firme_responsabile') THEN
        CREATE ROLE verumdoc_firme_responsabile NOLOGIN;
    END IF;
    -- R17 (migrazione 059): registrazione delle conferme delle decisioni (API di ciclo-vita)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_conferme_registra') THEN
        CREATE ROLE verumdoc_conferme_registra NOLOGIN;
    END IF;
    -- R15 (migrazione 058): versioni della documentazione (dashboard-api), archivio WORM
    -- delle versioni (hashchain) e sola lettura dell'auditor (anche R09, migrazione 048)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_documentazione') THEN
        CREATE ROLE verumdoc_documentazione NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_documentazione_archivio') THEN
        CREATE ROLE verumdoc_documentazione_archivio NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verumdoc_auditor') THEN
        CREATE ROLE verumdoc_auditor NOLOGIN;
    END IF;
    -- login dell'auditor per tools/conformita/genera_documenti.py (sola lettura)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vd_auditor') THEN
        CREATE ROLE vd_auditor LOGIN PASSWORD 'vd_auditor_dev_only' IN ROLE verumdoc_auditor;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vd_firma') THEN
        CREATE ROLE vd_firma LOGIN PASSWORD 'vd_firma_dev_only' IN ROLE verumdoc_firma;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vd_ciclo_vita_lavoratore') THEN
        CREATE ROLE vd_ciclo_vita_lavoratore LOGIN PASSWORD 'vd_ciclo_vita_lavoratore_dev_only'
            IN ROLE verumdoc_applicazione, verumdoc_ciclo_vita_lavoratore;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vd_app') THEN
        CREATE ROLE vd_app LOGIN PASSWORD 'vd_app_dev_only' IN ROLE verumdoc_applicazione;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vd_manutenzione') THEN
        CREATE ROLE vd_manutenzione LOGIN PASSWORD 'vd_manutenzione_dev_only'
            IN ROLE verumdoc_manutenzione, verumdoc_applicazione;
    END IF;
END
$$;
-- Revisione 2 di R05 (S7): una transazione dei servizi dimenticata «idle in
-- transaction» si chiude da sola (tratterrebbe l'export dell'audit senza limite).
-- La guardia all'avvio dei servizi (libs/shared/db/guardia.py) lo pretende fuori
-- dallo sviluppo; in esercizio lo imposta l'amministratore su ogni login.
ALTER ROLE vd_app SET idle_in_transaction_session_timeout = '15min';
ALTER ROLE vd_manutenzione SET idle_in_transaction_session_timeout = '15min';
ALTER ROLE vd_ciclo_vita_lavoratore SET idle_in_transaction_session_timeout = '15min';
-- Revisione R06 (V4): servizi del portale (in sviluppo lo stesso login per tutti;
-- ciclo-vita in sviluppo usa il login del lavoratore anche per l'API)
GRANT verumdoc_sessioni_portale TO vd_app;
GRANT verumdoc_sessioni_portale TO vd_ciclo_vita_lavoratore;
-- in sviluppo la hashchain usa vd_app (in esercizio: solo il login della hashchain)
GRANT verumdoc_registro_worm TO vd_app;
-- R13a: in sviluppo vd_app serve aip-builder, hashchain, dashboard-api e onboarding;
-- il servizio di firma ha un login proprio (vd_firma) con la sola lettura di
-- fornitori e parametri
GRANT verumdoc_firme_documenti TO vd_app;
-- revisione R13a (5): in esercizio SOLO il login della hashchain (aggiorna i documenti)
GRANT verumdoc_firme_documenti_sigillo TO vd_app;
GRANT verumdoc_firma_fornitori TO vd_app;
ALTER ROLE vd_firma SET idle_in_transaction_session_timeout = '15min';
-- R13b: in sviluppo vd_app serve notification (console PEC, consumer che accoda,
-- campanella) e il lavoratore della posta; in esercizio il lavoratore ha un login
-- proprio membro di verumdoc_pec (docs/integrazione/pec.md, §6)
GRANT verumdoc_pec_canali TO vd_app;
GRANT verumdoc_pec TO vd_app;
GRANT verumdoc_pec_accoda TO vd_app;
GRANT verumdoc_notifiche_personali TO vd_app;
-- R14 (migrazione 056): pulizia delle notifiche personali e lease dei lavori periodici;
-- in esercizio SOLO il login del lavoratore della posta
GRANT verumdoc_notifiche_pulizia TO vd_app;
-- R16 (migrazione 057): firme personali del responsabile; in esercizio SOLO il login di
-- dashboard-api (la hashchain archivia con verumdoc_firme_documenti_sigillo)
GRANT verumdoc_firme_responsabile TO vd_app;
-- R15 (migrazione 058): in sviluppo vd_app serve dashboard-api (versioni della
-- documentazione) e la hashchain (archivio WORM delle versioni); in esercizio
-- verumdoc_documentazione SOLO al login di dashboard-api e
-- verumdoc_documentazione_archivio SOLO al login della hashchain
GRANT verumdoc_documentazione TO vd_app;
GRANT verumdoc_documentazione_archivio TO vd_app;
ALTER ROLE vd_auditor SET idle_in_transaction_session_timeout = '15min';
ALTER ROLE vd_auditor SET default_transaction_read_only = on;
-- R17 (migrazione 059): in sviluppo l'API di ciclo-vita usa il login del lavoratore; in
-- esercizio verumdoc_conferme_registra SOLO al login dell'API di ciclo-vita
GRANT verumdoc_conferme_registra TO vd_ciclo_vita_lavoratore;
