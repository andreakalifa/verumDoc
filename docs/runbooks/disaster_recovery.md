# Disaster recovery: ripristino del database e ricostruzione del registro dal WORM

Riferimenti: roadmap R12 («Ripristino»); REGOLE_SVILUPPO §27.6 (un backup vale quando è stato ripristinato); `docs/runbooks/ripristino_registro_da_backup.md` (misure D, N, X, T e casi A/B, dettaglio operativo del registro); `docs/architettura/catena.md` §5–§11; `infra/terraform/modules/rds` (Aurora PostgreSQL, scrittore + lettore), `infra/terraform/modules/s3` (bucket dei PdA in Object Lock COMPLIANCE, replica).

Stato: **preparato in R12a senza ambiente**. La parte sul registro è provata in locale (sezione 6); la parte su Aurora e S3 si prova il giorno 1 di staging (`docs/architettura/giorno1_staging.md`, passi 9–11) e qui si annotano i tempi misurati.

## 1. Obiettivi

| Misura | Obiettivo proposto | Da confermare |
|---|---|---|
| RPO del database | ≤ 5 minuti (PITR di Aurora) | con la prova di staging (VD-P-119) |
| RTO del servizio | ≤ 4 ore (versamento e consultazione) | con la prova di staging |
| Contenuti conservati | nessuna perdita: i PdA, i RdV e gli oggetti `registro/` sono nel WORM (COMPLIANCE) e non dipendono dal database | — |
| Registro delle impronte | nessuna biforcazione: riparte solo se `D ≥ max(N, X, T)` (runbook del registro) | — |

## 2. Scenari

| Scenario | Sintomo | Procedura |
|---|---|---|
| A. Istanza scrittrice persa | Aurora fa failover sul lettore da sola (decine di secondi) | nessuna azione sul registro: stesso volume di storage, `D` invariato. Controllare gli allarmi dopo il failover (sezione 5) |
| B. Database corrotto o cancellato per errore | errori applicativi, dati mancanti | ripristino point-in-time su un cluster NUOVO (sezione 3), poi registro (sezione 4) |
| C. Account o regione non disponibili | nessun servizio raggiungibile | nuovo ambiente da Terraform, ripristino dallo snapshot copiato e dal bucket replicato (sezione 3.3), poi registro |
| D. Registro del database indietro o divergente dopo un ripristino | allarme `RegistroBloccato`, «ESITO NON COERENTE» | `ripristino_registro_da_backup.md`, caso B; ricostruzione dal WORM (sezione 4) |

## 3. Ripristino del database

### 3.1 Prima di tutto: fermare le scritture
1. Ingresso in sola lettura: `kubectl -n verumdoc scale deploy/gateway deploy/sftp-ingest --replicas=0` (o pagina di manutenzione sull'ALB).
2. Registro sospeso: `CATENA_REGISTRO_SOSPESO=true` nei values della hashchain, `helm upgrade` (il ciclo non sequenzia, non emette checkpoint, non contatta il testimone; gli allarmi continuano).
3. Lavoratori fermi: `ciclo-vita-lavoratore`, `aip-builder`, `validation`, `notification` a 0 repliche (nessuna azione su un database che sta per cambiare).

### 3.2 Point-in-time su un cluster nuovo (scenario B)
```bash
aws rds restore-db-cluster-to-point-in-time \
  --source-db-cluster-identifier verumdoc-<ambiente>-aurora \
  --db-cluster-identifier verumdoc-<ambiente>-ripristino-<data> \
  --restore-to-time <istante UTC prima dell'incidente> \
  --db-subnet-group-name <gruppo del modulo rds> --vpc-security-group-ids <sg_rds>
aws rds create-db-instance --db-cluster-identifier verumdoc-<ambiente>-ripristino-<data> \
  --db-instance-identifier verumdoc-<ambiente>-ripristino-<data>-1 \
  --db-instance-class <come lo scrittore> --engine aurora-postgresql
```
Mai sovrascrivere il cluster originale: resta per l'analisi. Poi:
1. `alembic current` sul cluster ripristinato: deve essere la testa del rilascio in esercizio (altrimenti `alembic upgrade head` con il Job delle migrazioni, che è riprendibile).
2. Ruoli e login dei servizi (i ruoli sono globali del cluster: con un cluster nuovo si ricreano con lo script dei ruoli dell'ambiente, come `infra/postgres/ruoli_sviluppo.sql` in sviluppo), `userlist.txt` di PgBouncer, Secret dei servizi con l'endpoint nuovo.
3. Partizioni: il lavoro di manutenzione le crea all'avvio; controllare `partizioni.*` dei giorni correnti.

### 3.3 Da snapshot copiato e bucket replicato (scenario C)
1. Nuovo ambiente da Terraform (`infra/terraform/environments/<ambiente>`), stesso progetto, regione di ripiego.
2. `aws rds restore-db-cluster-from-snapshot` dallo snapshot copiato nella regione.
3. Bucket dei PdA: la copia replicata ha Object Lock e le stesse versioni; i servizi puntano al bucket replicato (`S3_BUCKET_AIP`). Gli oggetti `registro/` replicati sono quelli da cui si misurano `N` e `X`.
4. Testimone: account separato, non toccato; `T` si legge da lì.

## 4. Registro delle impronte dopo il ripristino

Si segue `ripristino_registro_da_backup.md`. In sintesi:
1. `python -m app.registro.operatore controlla-worm` → misure `D, N, X, T` ed esito.
2. Esito COERENTE (`D ≥ max(N, X, T)`): si toglie la sospensione e il registro riparte.
3. Esito NON COERENTE con registro indietro: ricostruzione dal WORM, prima in prova e poi davvero:
   ```bash
   python -m app.registro.operatore ricostruisci-da-worm --fino-a <N o X> --prova
   python -m app.registro.operatore ricostruisci-da-worm --fino-a <N o X>
   ```
   La ricostruzione verifica firme dei checkpoint, controfirme del testimone, continuità dei segmenti e presenza dei PdA nel database; non procede se qualcosa non torna. Le foglie oltre l'ultimo checkpoint firmato e controfirmato si possono scartare solo con `--scarta-coda-non-coperta` (decisione dell'operatore, annotata).
4. Storia divergente: nessuna ricostruzione automatica; incidente di sicurezza (runbook del registro, B.2).
5. Audit: `python -m app.registro.operatore verifica-audit` (completa) per confrontare `audit_global` con i segmenti nel WORM; le righe d'audit successive all'istante di ripristino sono perse dal database ma restano nei segmenti già esportati.

## 5. Ripartenza e controlli
1. Lavoratori e ingresso di nuovo attivi (ordine: hashchain, aip-builder, validation, notification, ciclo-vita-lavoratore, gateway, sftp-ingest).
2. Allarmi da guardare nella prima ora: `RegistroBloccato`, `CheckpointSenzaControfirma`, `OutboxEventoBloccato`, `OutboxChiaviFerme`, `PassoPeriodicoFermo`, `RegistroWormRinnovoInErrore`, `VerificaAuditFallita`.
3. Verifica continua: i PdA si rileggono dal WORM a rotazione; nessun `ERRORE_IMPRONTA` atteso.
4. Versamenti accettati dopo l'istante di ripristino e prima dell'incidente: sono persi dal database ma i produttori hanno la ricevuta; elenco dagli eventi `pdv.received` del topic Kafka (retention 7 giorni) e comunicazione agli enti (manuale di conservazione, gestione degli incidenti).

## 6. Prova locale della ricostruzione (R12a, 31/10/2026)

Database di test PostgreSQL 16 (copia del template migrato alla testa 20261105_052), storage S3 simulato con Object Lock (moto), testimone finto con chiave Ed25519 generata a runtime:

```bash
cd services/hashchain
VD_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5466/<template> \
  python -m pytest -q tests/test_r05_ricostruzione_worm.py \
  tests/test_r05_revisione_ricostruzione.py tests/test_r05_ricostruzione_volume.py
```

Esito: **13 test superati in 109 s**. Scenario provato: registro con lotti, checkpoint (alcuni controfirmati), una foglia di scarto; istantanea del database come «backup» con il registro vuoto o indietro; ricostruzione in prova (nessuna scrittura) e reale con stesse radici, prove d'inclusione e di consistenza, controllo di coerenza superato e registro che riparte; rifiuto con checkpoint dalla firma falsa, segmento sovrapposto diverso, PdA assente, `--fino-a` oltre il WORM; volume (molte foglie a lotti).

Da fare in staging (giorno 1, passi 9–11): PITR reale di Aurora su un cluster nuovo con i tempi misurati, ricostruzione del registro di staging dal bucket reale, failover dello scrittore. I tempi misurati si annotano qui e chiudono gli obiettivi della sezione 1.
