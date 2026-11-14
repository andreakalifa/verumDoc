<!-- File generato da tools/conformita/genera_documenti.py: non modificare a mano (REGOLE_SVILUPPO §0.2, §28). -->

# Piano della sicurezza del sistema di conservazione

*Allegato del manuale di conservazione — documento generato dalla configurazione*

| Voce | Valore |
|---|---|
| Edizione | del repository: versione, data e firma si assegnano alla generazione dalla console «Documentazione del servizio» |
| Impronta del contenuto (SHA-256) | 5926bea4f249cdc0dd3af945720f5c3709c519c54aee3a1ee91448e6f3838c65 |
| Dati da compilare | 18 |
| Requisiti da verificare sul testo ufficiale | 2 |
| Stato della firma | non firmato: la firma del responsabile si appone sulla versione stampabile |

<details><summary>Fonti della generazione</summary>

- codice:libs/conformita/documenti.py — registro nel codice del rilascio
- config:CONSERVATORE_* — nessuna impostata
- db:canali_pec — non letta (edizione senza database)
- db:fornitori_firma — non letta (edizione senza database)
- db:fornitori_identita — non letta (edizione senza database)
- repo:docs/registro/criticita.md — sha256 cc965372a2b67dbf45bb1ecafe8666727b29ecf8bd9c4ab164d609354ebe6877
- repo:docs/runbooks/audit_worm.md — sha256 f0bccb2da0cb86a4711326f428b203ce7ade4a2c252489b2567d3b71e6bcd294
- repo:docs/runbooks/conferme_decisione.md — sha256 03900eb503927fb706418477c2febde0a8b083669e3836ed5796483525ed31a7
- repo:docs/runbooks/disaster_recovery.md — sha256 810a690727e39c61e620bbcfecbe8f11e84768ee3ce4b9cc93ff53cdc36169a6
- repo:docs/runbooks/health-check.md — sha256 f21cb958a01e38d58c365e6fd1e406bcbccd2795426950521bbe3941066e97f7
- repo:docs/runbooks/migrazioni_audit.md — sha256 88172edfcf3b952ec975a40ca212a4ee6ed248a2d572abbe10eafa5f4e4879a8
- repo:docs/runbooks/pratica_sospesa.md — sha256 fdfa0ff4f220323f162708e77374164f172ea109583bd3294efe57d8d1f8b1e4
- repo:docs/runbooks/ripristino_registro_da_backup.md — sha256 020c54a7f6d26e6bba180de1288f79301b63daf034ad73104a0e612ee50b8754
- repo:docs/runbooks/rotazione_chiavi_jwt.md — sha256 9215ffd92b5bf98bab62691d1d6b3a2852333d43f7995687bc4760802c54aa29
- repo:docs/runbooks/scarto_incompleto.md — sha256 04cce37da2eb79d21a56c1b600a91ff53417b24eb22db2845983a8189d5be5ad
- repo:infra/helm/values.yaml — sha256 d5fbbed98f8cf1f4b444783b41186d49d7ed211f823d1b802c4d4fe038d3f342
- repo:infra/observability/regole/verumdoc-r04.yml — sha256 6a6ba736b0231ca00bcf8a9df4cf20326ef060ae69f313f8526731685f2dab10
- repo:infra/observability/regole/verumdoc-r05.yml — sha256 4c30c29410988d671dd2e4e816bc55b369b9c88db3c18aa4a8106a11a5eccd9d
- repo:infra/observability/regole/verumdoc-r06.yml — sha256 d144f3127a6bd82e51e162f99b549de04437f928586430f6ef792848e4e051c4
- repo:infra/observability/regole/verumdoc-r09.yml — sha256 8bf74308317366d94b7104f9680cf95113de3389c278e83b9c2757dff103d963
- repo:infra/observability/regole/verumdoc-r12.yml — sha256 45ad048c78b1acf736d61a1b8e5379c4bde7101ba2e68fed1a87bd30fe50fdbc
- repo:infra/observability/regole/verumdoc-r13.yml — sha256 f6f9c67e8fcdb4fc2e398000fec226fda9beeecc5acb590bbf35767d760ddb9e
- repo:infra/observability/regole/verumdoc-r14.yml — sha256 3018bce10632ea51524fa9324ab6439e441e2780fd295716c56267029327fe57
- repo:infra/observability/regole/verumdoc-r15.yml — sha256 6ce4b7f4733f8bdf46849a73ed9b603ca12ee8f453a7dc18890b14faad0c7502
- repo:infra/observability/regole/verumdoc-r16.yml — sha256 b7a831b0312a4a9c8946aa7aa2bbd9dd44d59939d1e6c50bbcaa2758aa4f7bc1
- repo:infra/terraform/environments/prod/main.tf — sha256 00b4c7c84a7e9ff14e545ff4ba66ad30077ae33854c766f3b3d191d0fe889742
- repo:infra/terraform/modules/rds/main.tf — sha256 e8fb9f5c5fd3c29377f09af19789d3fab0eebe82c02e3cc62869ea535c5a6150
- repo:infra/terraform/modules/s3/main.tf — sha256 23ad6f75b9daa7ef76e6481d7c3491147f603de3d0c491d69a09a18adc449c94
- repo:libs/verumdoc_rules/permessi.py — sha256 8916b032a2ee15b8163d135f5d6981a5d3f7543d6671be44f0a11c9a4895b0f8
- rilascio — changelog/CHANGELOG-2026-11-14-rilascio-R18_2.md (2026-11-14)

</details>

> **Avvertenza.** Edizione generata senza il database di esercizio: ogni dato della configurazione in esercizio risulta da compilare. La versione da consegnare si genera dalla console, pagina «Documentazione del servizio».

## 0. Informazioni sul documento

Piano della sicurezza del sistema di conservazione, parte del manuale di conservazione (Regolamento, Allegato A, RO7). Le misure elencate sono quelle realizzate nel codice e nella configurazione, con il file che le attua e il test che le prova.

> *da verificare sul testo ufficiale* — paragrafo «Misure di sicurezza» — piano della sicurezza conforme all'art. 32 GDPR e alle misure minime ICT, procedura per le violazioni dei dati (artt. 33-34 GDPR): ricerca_normativa §1.6 [NV]: contenuti tratti dalla bozza.

> **DA COMPILARE** — estremi della certificazione ISO/IEC 27001 con campo di applicazione specifico (RS1). Fornisce: il responsabile della sicurezza dei sistemi. Dove: testo del piano. Campo: `sicurezza.certificazione_27001`.

*Requisiti: Regolamento AgID sui criteri per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A, RO7; Regolamento AgID sui criteri per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A, RS1; Regolamento AgID sui criteri per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A, RO4.*

## 1. Ambito e responsabilità

| Voce | Valore |
|---|---|
| Responsabile della sicurezza dei sistemi | DA COMPILARE (vedi sotto) |
| Responsabile della protezione dei dati | DA COMPILARE (vedi sotto) |
| Responsabile del servizio di conservazione | DA COMPILARE (vedi sotto) |

> **DA COMPILARE** — responsabile della sicurezza dei sistemi. Fornisce: il conservatore (legale rappresentante o responsabile del servizio). Dove: variabile CONSERVATORE_RESPONSABILE_SICUREZZA_NOME. Campo: `conservatore.responsabile_sicurezza_nome`.

> **DA COMPILARE** — responsabile della protezione dei dati. Fornisce: il conservatore (legale rappresentante o responsabile del servizio). Dove: variabile CONSERVATORE_RESPONSABILE_PROTEZIONE_DATI_NOME. Campo: `conservatore.responsabile_protezione_dati_nome`.

> **DA COMPILARE** — responsabile del servizio di conservazione. Fornisce: il conservatore (legale rappresentante o responsabile del servizio). Dove: variabile CONSERVATORE_RESPONSABILE_SERVIZIO_NOME. Campo: `conservatore.responsabile_servizio_nome`.

## 2. Asset

Servizi applicativi: aip-builder, ciclo-vita, dashboard-api, distribution, frontend, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation, verifica-firme.

| Asset | Dove |
|---|---|
| Oggetti conservati (PdA, RdV, registro, buste di firma) | storage S3 con Object Lock (`infra/terraform/modules/s3`) |
| Database della configurazione e dei registri | PostgreSQL (`infra/terraform/modules/rds`) |
| Messaggi fra i servizi | Kafka (`infra/terraform/modules/kafka`) |
| Indice di ricerca | OpenSearch (`infra/terraform/modules/opensearch`) |
| Chiavi di cifratura | KMS (`infra/terraform/modules/kms`) |
| Chiavi di firma del registro e del testimone | Secret dei servizi hashchain e testimone (`infra/helm/values.yaml`) |

> **DA COMPILARE** — inventario completo degli asset con proprietario e classificazione. Fornisce: il responsabile della sicurezza dei sistemi. Dove: testo del piano. Campo: `sicurezza.inventario`.

## 3. Minacce e misure che le contrastano

| Minaccia | Misure (sezione 4) |
|---|---|
| Accesso non autorizzato ai documenti di un ente | Autenticazione; Sessione; Permessi; Isolamento fra enti |
| Manomissione o cancellazione di un oggetto conservato | Immutabilità; Integrità verificabile; Registri protetti |
| Riscrittura della storia delle operazioni | Registro delle operazioni; Registri protetti; Integrità verificabile |
| Servizio esposto compromesso | Login del database; Permessi dell'infrastruttura; Segreti nelle code |
| Fuga di informazioni tecniche | Errori |
| Perdita di dati o indisponibilità | Copie e ripristino (sezione 8) |
| Abuso da parte di un amministratore | Permessi; Registri protetti; Integrità verificabile |

> **DA COMPILARE** — analisi dei rischi con probabilità, impatto e accettazione del rischio residuo. Fornisce: il responsabile della sicurezza dei sistemi. Dove: testo del piano. Campo: `sicurezza.analisi_rischi`.

## 4. Misure tecniche realizzate

| Misura | Descrizione | Codice | Prova |
|---|---|---|---|
| Autenticazione | token RS256 verificati (emittente, destinatario, scadenza) in ogni servizio; chiavi solo da ambiente, rotazione con più chiavi per `kid` | `libs/shared/security/jwt_rs256.py` | `libs/shared/security/tests/test_jwt_rs256.py` |
| Sessione | cookie HttpOnly con protezione CSRF, uscita che revoca il token, scadenza per inattività | `libs/shared/security/sessione_cookie.py`, `libs/shared/security/revoca.py` | `services/gateway/tests/test_r05_sessione_cookie.py` |
| Permessi | controllo centrale dei permessi; azioni mai delegabili negate a tre livelli (parametro, vincolo del database, servizio) | `libs/shared/security/permessi.py`, `libs/verumdoc_rules/permessi.py` | `services/onboarding/tests/test_r10_ruoli_inviti.py` |
| Isolamento fra enti | Row Level Security per ente su ogni tabella con ente, ambito impostato da un solo punto | `libs/shared/db/ambito.py` | `services/gateway/tests/test_r05_rls_ente.py` |
| Login del database | servizi con login non proprietario; guardia all'avvio sul ruolo e sul timeout delle transazioni | `libs/shared/db/guardia.py` | `services/gateway/tests/test_r05_guardia_ruolo_db.py` |
| Immutabilità | oggetti conservati in S3 Object Lock COMPLIANCE con retention per classe, rinnovo mai in riduzione | `libs/shared/storage/worm.py`, `infra/terraform/modules/s3/main.tf` | `services/aip-builder/tests/test_s3_uploader.py` |
| Integrità verificabile | registro delle impronte con checkpoint firmati e controfirmati dal testimone indipendente | `libs/catena` | `libs/catena/tests`, `services/testimone/tests` |
| Registri protetti | registri append-only protetti anche dal proprietario; modifiche solo con la procedura delle migrazioni | `libs/shared/db/migrazioni_registri.py` | `services/gateway/tests/test_r04_correzioni_registri_ruoli.py` |
| Registro delle operazioni | audit append-only esportato a periodi nel WORM e verificato | `libs/shared/security/audit.py`, `services/hashchain/app/registro/audit_worm.py` | `services/hashchain/tests/test_r05_audit_worm.py` |
| Permessi dell'infrastruttura | un ruolo IAM per servizio con i soli prefissi necessari e negazioni esplicite; ACL Kafka per topic | `infra/terraform/modules/iam_servizi/main.tf`, `infra/k8s/verifica_manifest.py` | `infra/k8s/test_verifica_manifest.py` |
| Segreti nelle code | segreti sigillati per il solo servizio che li usa e legati al contesto | `libs/shared/security/sigillo.py` | `libs/shared/security/tests` |
| Errori | errori solo dal catalogo, mai dettagli interni verso il client | `libs/shared/errori` | `tools/docs/test_genera_catalogo_errori.py` |

*Dettaglio delle scelte di sicurezza: `docs/architettura/sicurezza.md`.*

> **DA COMPILARE** — esito delle prove OWASP sulle componenti sviluppate (RS2). Fornisce: il responsabile della sicurezza dei sistemi. Dove: rapporto delle prove. Campo: `sicurezza.prove_owasp`.

*Requisiti: Regolamento AgID sui criteri per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A, RS2.*

## 5. Cifratura

*Terraform*

| Voce | Valore dichiarato |
|---|---|
| Database cifrato a riposo | sì |
| Chiavi gestite | KMS (`infra/terraform/modules/kms`) |
| Modalità di Object Lock | COMPLIANCE |

> **DA COMPILARE** — valore di esercizio `ingress.annotations.alb.ingress.kubernetes.io/certificate-arn`. Fornisce: l'operatore dell'infrastruttura (pipeline di rilascio). Dove: infra/helm/values.yaml o pipeline. Campo: `infra.ingress.annotations.alb.ingress.kubernetes.io/certificate-arn`.

*Algoritmi di impronta e firma*

| Impostazione | Significato | Valore | Origine | Riferimento |
|---|---|---|---|---|
| `FIRMA_ALGORITMO_IMPRONTA` | Algoritmo d'impronta delle firme e delle marche del conservatore | sha256 | predefinito del codice (database non letto) | ETSI TS 119 312 (algoritmi crittografici) |
| `FIRMA_ALGORITMI_AMMESSI` | Algoritmi d'impronta ancora considerati sicuri: le firme e le marche fatte con un algoritmo tolto da qui si rimarcano | sha256, sha384, sha512 | predefinito del codice (database non letto) | ETSI TS 119 312 (algoritmi crittografici) |

## 6. Controllo degli accessi

Azioni mai delegabili (solo ruoli fissi): `accesso:configura`, `annullamento:approva`, `documentazione:genera`, `firma_fornitori:gestisci`, `firmatari:gestisci`, `pec_canali:gestisci`, `pratica:chiudi_eccezioni`, `pratica:riprendi`, `ruoli:gestisci`, `scarto:autorizza`, `scarto:esegui`, `utenti:gestisci`.

> **DA COMPILARE** — fornitori d'identità registrati (SPID/CIE, password con codice a tempo, passkey) e loro stato. Fornisce: il responsabile del servizio di conservazione. Dove: console, «Accesso e notifiche». Campo: `db.fornitori_identita`.

*Impostazioni di accesso e sessione*

| Impostazione | Significato | Valore | Origine | Riferimento |
|---|---|---|---|---|
| `ACCESSO_BLOCCO_MINUTI` | Minuti di blocco dopo troppi tentativi sbagliati | 15 | predefinito del codice (database non letto) | scelta di servizio |
| `ACCESSO_METODI_AMMESSI` | Metodi con cui le persone accedono: SPID_CIE (tramite aggregatore), PASSKEY, PASSWORD_TOTP (password più codice a tempo). Il conservatore li abilita, l'ente sceglie fra quelli abilitati | SPID_CIE | predefinito del codice (database non letto) | CAD, art. 64 (SPID e CIE per l'accesso ai servizi in rete) |
| `ACCESSO_RIAUTENTICAZIONE_MINUTI` | Minuti di validità della conferma dell'identità (password e codice a tempo, o passkey) richiesta per modificare le proprie credenziali nella console | 5 | predefinito del codice (database non letto) | scelta di servizio |
| `ACCESSO_TENTATIVI_MASSIMI` | Tentativi sbagliati (password, codice a tempo o d'invito) dopo cui l'accesso si blocca per un po' | 5 | predefinito del codice (database non letto) | scelta di servizio |
| `INVITO_VALIDITA_ORE` | Ore di validità del collegamento d'invito inviato per e-mail | 72 | predefinito del codice (database non letto) | scelta di servizio |
| `PASSWORD_LUNGHEZZA_MINIMA` | Lunghezza minima della password (accesso con password e codice a tempo) | 12 | predefinito del codice (database non letto) | scelta di servizio |
| `SESSIONE_INATTIVITA_MINUTI_ADMIN_TENANT` | Minuti di inattività dopo cui la sessione del portale del ruolo ADMIN_TENANT si chiude; l'ente può solo ridurre il valore del conservatore | 15 | predefinito del codice (database non letto) | scelta di servizio |
| `SESSIONE_INATTIVITA_MINUTI_OPERATORE` | Minuti di inattività dopo cui la sessione del portale del ruolo OPERATORE si chiude; l'ente può solo ridurre il valore del conservatore | 30 | predefinito del codice (database non letto) | scelta di servizio |
| `SESSIONE_INATTIVITA_MINUTI_RSC_CONSERVATORE` | Minuti di inattività dopo cui la sessione del portale del ruolo RSC_CONSERVATORE si chiude; l'ente può solo ridurre il valore del conservatore | 15 | predefinito del codice (database non letto) | scelta di servizio |
| `SESSIONE_INATTIVITA_MINUTI_SUPER_ADMIN` | Minuti di inattività dopo cui la sessione del portale del ruolo SUPER_ADMIN si chiude; l'ente può solo ridurre il valore del conservatore | 15 | predefinito del codice (database non letto) | scelta di servizio |
| `SESSIONE_INATTIVITA_MINUTI_VIEWER` | Minuti di inattività dopo cui la sessione del portale del ruolo VIEWER si chiude; l'ente può solo ridurre il valore del conservatore | 30 | predefinito del codice (database non letto) | scelta di servizio |

*Matrice ruoli × permessi: allegato 4 del manuale e `docs/catalogo/permessi.md`.*

## 7. Registro delle operazioni e log

| Funzione | Stato | Prove | Limiti e riferimenti |
|---|---|---|---|
| Registro delle operazioni append-only esportato nel WORM e verificato | realizzata | `services/hashchain/tests/test_r05_audit_worm.py` | — |
| Metriche, allarmi e cruscotto di esercizio | realizzata in parte | `libs/shared/observability/tests/test_metriche_servizi.py` | tracing distribuito non collegato; VD-C-176 |

*Impostazioni dell'audit*

| Impostazione | Significato | Valore | Origine | Riferimento |
|---|---|---|---|---|
| `AUDIT_EXPORT_PERIODO_MINUTI` | Durata dei periodi dell'audit esportati nel WORM (minuti) | 15 | predefinito del codice (database non letto) | scelta di servizio |
| `AUDIT_VERIFICA_COMPLETA_GIORNO` | Giorno del mese della verifica COMPLETA dell'audit (tutti i segmenti nel WORM); negli altri giorni la verifica è incrementale | 1 | predefinito del codice (database non letto) | scelta di servizio |
| `AUDIT_VERIFICA_FINESTRA_GIORNI` | Verifica giornaliera INCREMENTALE dell'audit: segmenti nel WORM degli ultimi giorni (agganciati al segmento precedente) | 2 | predefinito del codice (database non letto) | scelta di servizio |

## 8. Copie di sicurezza e ripristino

*Terraform, ambiente di produzione*

| Voce | Valore dichiarato |
|---|---|
| Copie del database (giorni) | 35 |
| Regione di replica degli oggetti | eu-central-1 |

Procedura: `docs/runbooks/disaster_recovery.md`.

> **DA COMPILARE** — RPO misurato. Fornisce: il responsabile della sicurezza dei sistemi. Dove: prova di ripristino. Campo: `continuita.rpo`.

> **DA COMPILARE** — RTO misurato. Fornisce: il responsabile della sicurezza dei sistemi. Dove: prova di ripristino. Campo: `continuita.rto`.

*Requisiti: Regolamento AgID sui criteri per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A, RS3.*

## 9. Gestione degli incidenti

36 allarmi a pagina (intervento immediato) su 57; ogni allarme rimanda a un runbook (`docs/catalogo/metriche.md`, controllo `tests/integrity/test_osservabilita.py`).

| Runbook | File |
|---|---|
| Audit nel WORM: export, verifica e differenze | `docs/runbooks/audit_worm.md` |
| Runbook — conferma firmata delle decisioni sensibili (R17, VD-P-164) | `docs/runbooks/conferme_decisione.md` |
| Disaster recovery: ripristino del database e ricostruzione del registro dal WORM | `docs/runbooks/disaster_recovery.md` |
| Runbook: Health Check Servizi | `docs/runbooks/health-check.md` |
| Migrazioni sulle tabelle dell'audit e degli altri registri | `docs/runbooks/migrazioni_audit.md` |
| Runbook — pratica sospesa dopo i tentativi esauriti (`LC-409-TENTATIVI-ESAURITI`) | `docs/runbooks/pratica_sospesa.md` |
| Ripristino del registro delle impronte da backup senza biforcazioni | `docs/runbooks/ripristino_registro_da_backup.md` |
| Rotazione delle chiavi JWT (RS256) senza disconnettere gli utenti | `docs/runbooks/rotazione_chiavi_jwt.md` |
| Runbook — scarto con cancellazione incompleta (PdA `ELIMINAZIONE_INCOMPLETA`) | `docs/runbooks/scarto_incompleto.md` |

> **DA COMPILARE** — procedura di notifica delle violazioni dei dati personali (artt. 33-34 GDPR): chi decide, tempi, modelli. Fornisce: il responsabile della protezione dei dati. Dove: testo del piano. Campo: `sicurezza.violazioni_dati`.

> *da verificare sul testo ufficiale* — paragrafo «Misure di sicurezza» — piano della sicurezza conforme all'art. 32 GDPR e alle misure minime ICT, procedura per le violazioni dei dati (artt. 33-34 GDPR): ricerca_normativa §1.6 [NV]: contenuti tratti dalla bozza.

## 10. Fornitori e terze parti

Infrastruttura dichiarata per il provider Terraform «hashicorp/aws» (regione eu-south-1).

> **DA COMPILARE** — fornitore qualificato di firma o sigillo elettronico attivo. Fornisce: il responsabile del servizio di conservazione, dopo il contratto con il prestatore qualificato (VD-P-05). Dove: console, «Fornitori di firma e marca». Campo: `db.fornitori_firma`.

> **DA COMPILARE** — fornitore qualificato di marca temporale (TSA) attivo. Fornisce: il responsabile del servizio di conservazione, dopo il contratto con il prestatore qualificato (VD-P-05). Dove: console, «Fornitori di firma e marca». Campo: `db.fornitori_marca`.

> **DA COMPILARE** — canale PEC del conservatore attivo. Fornisce: il responsabile del servizio di conservazione, dopo il contratto con il gestore PEC. Dove: console, «Canali PEC». Campo: `db.canali_pec`.

> **DA COMPILARE** — fornitori d'identità registrati (SPID/CIE, password con codice a tempo, passkey) e loro stato. Fornisce: il responsabile del servizio di conservazione. Dove: console, «Accesso e notifiche». Campo: `db.fornitori_identita`.

> **DA COMPILARE** — contratti, nomine a responsabile del trattamento e qualificazioni dei fornitori. Fornisce: il responsabile della protezione dei dati. Dove: registro dei fornitori. Campo: `sicurezza.contratti_fornitori`.

## 11. Rischi residui (criticità aperte del registro)

28 criticità aperte o parzialmente chiuse nel registro `docs/registro/criticita.md`: sono i rischi residui accettati finché non si chiudono.

| Codice | Gravità | Area | Criticità | Stato |
|---|---|---|---|---|
| VD-C-038 | P2 | Infrastruttura | Terraform non supera il parsing; probe, Secret, ConfigMap, network policy, ingress sbagliati; nessun deploy del frontend | Parzialmente chiusa |
| VD-C-069 | P1 | Sicurezza | Nessun accesso per le persone in produzione: aggregatore SPID/CIE qualificato non contrattualizzato né configurato (SPID-001); utenti senza codice fiscale non entrano | Aperta |
| VD-C-071 | P1 | Conformità | IPdA non verificabile contro lo schema ufficiale UNI 11386:2020 (XSD non disponibile) | Aperta |
| VD-C-081 | P2 | Test | Infrastruttura vera mai provata: immagini Docker mai costruite, `helm lint` e `nginx -t` non eseguiti, Kafka simulato nei test, Object Lock provato su moto e non su MinIO/S3, CI mai eseguita su GitHub | Parzialmente chiusa |
| VD-C-109 | P1 | Esercizio | Kafka su MSK con autenticazione IAM e ACL per topic realizzati ma mai provati su un cluster MSK reale | Aperta |
| VD-C-110 | P1 | Esercizio | Testimone in account separato: Terraform dell'account non scritto; in produzione l'avvio della hashchain senza `TESTIMONE_URL` fallisce (voluto) | Parzialmente chiusa |
| VD-C-122 | P3 | Integrità | Scarto: con lo storage illeggibile alla chiusura il numero di versioni rimaste è «non determinabile»; gli oggetti avviati senza inventario non si estendono e restano `non_determinabile`. Serve un controllo manuale prima della ripresa | Parzialmente chiusa |
| VD-C-125 | P3 | Multi-tenant | Fuori dalla RLS: `catena_pda`, `catena_foglie_interne` (tabelle protette del registro), `sessioni_revocate`; `audit_global` e `verifiche_registro` senza RLS per ente | Aperta |
| VD-C-132 | P3 | Esercizio | Export dell'audit: una transazione dimenticata (anche di un operatore con un altro login) lo trattiene finché resta aperta; `idle_in_transaction_session_timeout` non copre transazioni attive lunghe; righe del periodo non ancora esportato (≤ 70 min) alterabili senza traccia; forma del jsonb da riverificare a ogni aggiornamento maggiore di PostgreSQL | Aperta |
| VD-C-134 | P3 | Integrità | Documento già caricato nel WORM resta orfano (con retention) se fallisce la transazione finale di verbale, export o manifesto | Aperta |
| VD-C-143 | P1 | Conformità | Il servizio `verifica-firme` (EU DSS: Trusted List UE dalla LOTL, OCSP/CRL, XAdES e ASiC) è scritto con Dockerfile, values di Helm e NetworkPolicy ma non è stato costruito né provato: in sviluppo Maven Central non era raggiungibile. Finché non è in esercizio la verifica usa pyHanko con le trust anchor di `FIRME_RADICI_PEM` (senza Trusted List) | Aperta |
| VD-C-154 | P3 | Esercizio | PROXY protocol e indirizzi ammessi provati con il server asyncssh vero e intestazioni v1/v2 simulate, non con l'NLB AWS reale (annotazione `aws-load-balancer-proxy-protocol`, reti fidate = sottoreti dell'NLB dopo la revisione V4); lo stesso vale per `X-Forwarded-For` dietro l'ALB (`PROXY_FIDATI`, revisione V1) | Aperta |
| VD-C-157 | P2 | Conformità | Allegato 5, §4 oltre p. 84 non riscontrato: stringhe dei ruoli dei Soggetti delle aggregazioni (schema a p. 89) e obbligo per ruolo, procedimento amministrativo, collegamento ad altre aggregazioni, indice dei documenti, tempo di conservazione, note (campi `da_riscontrare` della v2 dell'aggregazione) | Aperta |
| VD-C-159 | P3 | Conformità | IPdA: i metadati dei profili v2 escono con i nomi dell'Allegato 5 in un contenitore provvisorio (`MetadatiAllegato5`); tipo registro e modalità di formazione nella forma del testo (lettera a–d, «Protocollo Ordinario/Protocollo Emergenza») e non in quella degli schemi dell'Allegato 5 | Aperta |
| VD-C-167 | P2 | Conformità | CAdES B-LTA (`archive-time-stamp-v3`, `ats-hash-index-v3`) costruito secondo ETSI EN 319 122-1 §5.5.3 ma verificato solo dal nostro verificatore (ricalcolo dell'impronta) e da pyHanko per firma e marca di base; non ancora con EU DSS o un verificatore del prestatore | Aperta |
| VD-C-169 | P2 | Esercizio | Canale PEC provato solo con il simulatore: formati reali delle ricevute dei gestori (completa, breve, sintetica, codifiche della data), certificati di firma dei gestori dall'indice pubblico e limiti della casella da verificare con il gestore scelto | Aperta |
| VD-C-174 | P3 | Integrità | La pagina web del verificatore non usa la prova aggiornata: lo stato attuale si dimostra solo con il comando `verifica_ricevuta.py --prova-aggiornata` | Aperta |
| VD-C-178 | P3 | Esercizio | Tracce distribuite: il contesto della richiesta non attraversa l'outbox (la traccia del consumatore parte dal relay, non dalla richiesta che ha creato l'evento) e i manifest del cluster non hanno un collettore né le variabili `OTEL_*` (le tracce si attivano solo nello stack locale) | Aperta |
| VD-C-179 | P3 | Governo | Credenziali di firma personali registrabili solo con l'API di onboarding (`PUT /api/v1/onboarding/firma/credenziali-personali/{user_id}`, con audit): nessuna sezione nella console «Fornitori di firma e marca»; firma personale provata solo con il simulatore CSC | Aperta |
| VD-C-180 | P3 | Conformità | Regole incrociate dei profili solo nella v3 del documento informatico: il documento amministrativo informatico (§3, ruoli propri dei Soggetti) e l'aggregazione documentale (§4) non le hanno; prodotto software «quando rilevabile» e documento primario «se presente» restano non verificabili dal servizio | Aperta |
| VD-C-181 | P3 | Documentazione | Due generatori del manuale: la bozza JSON di R02 (`services/dashboard-api/app/services/manuale.py`, pagina «Manuale di conservazione», con `STATO_FUNZIONI` scritto a mano e non aggiornato) e «Pubblica» di R13a che sigilla quella bozza convivono con il manuale generato di R15 | Aperta |
| VD-C-182 | P3 | Esercizio | La generazione della documentazione gira nel processo di dashboard-api dopo la risposta (`BackgroundTasks`): se il pod termina, la richiesta resta IN_CORSO fino alla scadenza (10 minuti) e si dichiara interrotta solo alla richiesta successiva | Aperta |
| VD-C-183 | P2 | Conformità | Requisiti dei documenti generati non riscontrati sul testo ufficiale: numero del paragrafo del manuale, fasi del processo di conservazione e numero massimo di rifiuti, esibizione, infrastrutture, misure di sicurezza, selezione e scarto | Aperta |
| VD-C-184 | P3 | Sicurezza | Conferma firmata delle decisioni: chi possiede il token di sessione della persona (anche un pod che lo vede passare) può, nella finestra della conferma dell'identità (R12b, 5 minuti), consumarla per primo per un'azione del ruolo di quella persona; con solo SPID/CIE vale l'accesso fatto da meno della finestra | Aperta |
| VD-C-185 | P3 | Governo | Decisioni sensibili non coperte dalla conferma firmata: restituzione all'ente e chiusura con eccezioni; le integrazioni con chiave API non chiedono più annullamenti e rettifiche con la conferma attiva | Aperta |
| VD-C-186 | P3 | Frontend | Residui dell'audit finale della console R17 non corretti nella revisione: corsa della pratica come tabellone e del pacchetto come linea (R17-24), larghezza delle note di sezione (R17-25), 483 stili in linea (R17-32, debito VD-C-027), versamento a lotti (resto di R17-06), descrizioni del catalogo v1 con codici di decisione (R17-26, dati versionati), bersagli dei bottoni-link a 24 px e non 44 (R17-22) | Aperta |
| VD-C-188 | P3 | Licenze | Ambiente di sviluppo locale con immagini non open source o con termini da verificare: `hashicorp/vault:1.16` (BUSL-1.1), `redis:7-alpine` (dalla 7.4 RSALv2/SSPLv1), `confluentinc/cp-kafka:7.6.0` (termini dell'immagine) | Aperta |
| VD-C-189 | P3 | Licenze | Dipendenze Java transitive di verifica-firme non elencate con lo strumento: Maven Central non raggiungibile dall'ambiente di R18 | Aperta |

## 12. Verifica periodica

> **DA COMPILARE** — calendario e responsabili della verifica periodica di conformità a norme e standard (RO8). Fornisce: il responsabile del servizio di conservazione. Dove: testo del piano. Campo: `sicurezza.verifica_periodica`.

*Requisiti: Regolamento AgID sui criteri per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A, RO8.*
