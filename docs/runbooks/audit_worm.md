# Audit nel WORM: export, verifica e differenze

Riferimenti:
- `docs/architettura/catena.md` §4.1 (foglie `audit`) e §11 (il superutente e l'audit);
- `docs/architettura/infrastruttura.md` §5 («Il superutente e l'audit»);
- codice: `services/hashchain/app/registro/audit_worm.py`; migrazione `20261023_039`;
- criticità VD-C-112 (R05).

## Che cosa fa

La hashchain esporta `audit_global` nel WORM, un periodo alla volta:
- un periodo dura `AUDIT_EXPORT_PERIODO_MINUTI` (R07, D19: impostazione del conservatore in console, 15 minuti predefiniti, fra 5 e 60; prima un'ora fissa), allineata al tempo UTC; cambiarlo è sicuro: il segmento successivo parte dalla fine del precedente;
- si esporta solo `AUDIT_EXPORT_MARGINE_SECONDI` (10 minuti) dopo la fine del periodo: una transazione ancora aperta potrebbe aggiungervi righe;
- ogni periodo diventa un segmento `registro/audit/<da>-<a>.jsonl` (Object Lock COMPLIANCE, conservazione permanente):
  - prima riga: intestazione con periodo, numero di righe, radice di Merkle delle righe, impronta del segmento precedente;
  - poi una riga per voce d'audit, in ordine (`created_at`, `id`), in JSON canonico;
- periodi vuoti consecutivi formano un solo segmento; un periodo con più di `AUDIT_EXPORT_RIGHE_MASSIME` righe si divide;
- l'impronta del segmento entra nel registro delle impronte come foglia `audit` e finisce sotto checkpoint firmati e controfirmati dal testimone;
- l'export gira nel ciclo del registro (una volta al minuto) solo quando il registro può scrivere. Il registro sospeso o bloccato ferma anche l'export.

## Allarmi

| Segnale | Significato | Azione |
|---|---|---|
| metrica `verumdoc_catena_audit_ritardo_secondi` alta (oltre ~2 ore) | l'export non avanza | controllare il registro (`python -m app.registro.operatore blocchi`) e i log `registro_passo_fallito passo=audit` |
| log `audit_worm_divergente`, metrica `verumdoc_catena_audit_worm_divergente` = 1 | nel WORM c'è già un segmento per quel periodo con contenuto diverso: righe cambiate o aggiunte dopo l'esportazione, ed esportazione persa dal database | §3 |

Un segmento divergente **non** blocca il registro: la conservazione continua, si ferma soltanto l'export dell'audit.

## 1. Verifica periodica

Almeno a ogni controllo periodico del sistema di conservazione e **dopo ogni uso delle credenziali del master** del database:

```bash
kubectl -n verumdoc exec deploy/hashchain -- python -m app.registro.operatore verifica-audit
kubectl -n verumdoc exec deploy/hashchain -- python -m app.registro.operatore verifica-audit \
  --da 2026-10-01T00:00:00Z --a 2026-11-01T00:00:00Z
```

| Riga | Significato |
|---|---|
| `segmenti`, `righe_worm`, `righe_db` | segmenti letti e righe confrontate |
| `mancanti_nel_db` | righe nel WORM che il database non ha più: **cancellate** |
| `modificate` | stessa riga (stesso `id`) con contenuto diverso |
| `aggiunte_nel_db` | righe del database in un periodo già esportato che il segmento non ha |
| `non_esportate` | righe dopo l'ultimo segmento (informativo: periodo in corso) |
| `PROBLEMA` | segmento non conforme, catena dei segmenti interrotta, impronta non (ancora) nel registro o non coperta da un checkpoint |
| `DIFFERENZA` | una riga per ogni riga cancellata, modificata o aggiunta |
| `ESITO` | `COERENTE` (codice 0) o `DIFFERENZE` (codice 1) |

Subito dopo un export è normale un `PROBLEMA` «non ancora coperta da un checkpoint»: sparisce al checkpoint successivo (un minuto).

## 2. Ci sono differenze

1. Non modificare nulla nel database: i segmenti nel WORM sono la copia di riferimento e non si possono alterare.
2. Salvare l'uscita completa di `verifica-audit` nel registro degli incidenti, con i segmenti coinvolti (`registro/audit/…`).
3. Per ogni differenza:
   - **cancellata** o **modificata**: alterazione del registro d'audit dopo l'esportazione. La riga originale è nel segmento WORM. Incidente di sicurezza: avvisare il responsabile della conservazione e il responsabile della sicurezza, ricostruire chi aveva le credenziali del master e quando;
   - **aggiunta**: una riga con `created_at` in un periodo già esportato. Due cause:
     - una transazione rimasta aperta oltre il margine. Controllare `created_at` e l'evento: se la riga è legittima, è un falso allarme da annotare. Se succede spesso, aumentare `AUDIT_EXPORT_MARGINE_SECONDI` o accorciare i timeout delle transazioni;
     - una riga retrodatata a mano: incidente, come sopra.
4. Dopo un ripristino del database da backup (runbook `ripristino_registro_da_backup.md`), le righe perse con il ripristino risultano «cancellate»: elencarle nel registro degli incidenti. Non sono un'alterazione, ma una perdita del database; il WORM le conserva.

## 3. Segmento divergente durante l'export

Succede quando il database non sa più di aver esportato un periodo e le righe di quel periodo sono cambiate. Per esempio, dopo un ripristino senza la ricostruzione del registro, o dopo un'alterazione.

1. `verifica-audit` mostra le righe diverse rispetto al segmento già nel WORM.
2. Se il database è stato ripristinato: completare prima il runbook `ripristino_registro_da_backup.md`. La ricostruzione dal WORM ricrea le esportazioni dell'audit dalle foglie del registro, e l'export riparte dal periodo successivo.
3. Se il segmento non è nel registro (la sua foglia si è persa prima del sequenziamento) o è un'alterazione (§2, punto 3), dopo l'indagine l'operatore registra il segmento GIÀ nel WORM come esportato, senza riscriverlo:

   ```bash
   kubectl -n verumdoc exec deploy/hashchain -- python -m app.registro.operatore \
     adotta-segmento-audit --segmento registro/audit/<da>-<a>.jsonl \
     --operatore "Mario Rossi" --motivo "ripristino del <data>: righe perse, segmento WORM di riferimento"
   ```

   Il comando accetta solo il segmento successivo della catena (periodo e impronta precedente) e conforme (intestazione, righe, radice). Riusa la foglia del registro se c'è già con la stessa impronta, altrimenti la mette in coda. Scrive una voce `catena.audit_segmento_adottato`. Le differenze con il database restano visibili a `verifica-audit`.

L'export resta fermo finché il periodo non è registrato. La conservazione continua.


## Export trattenuto o sospeso (revisione 2 di R05, S7)

- **`AuditExportTrattenuto`** (`verumdoc_catena_audit_transazione_aperta_secondi > 1800` per 5 minuti): una transazione aperta da prima della fine del periodo trattiene l'export. Individuarla:
  ```sql
  SELECT pid, usename, application_name, state, xact_start, now() - xact_start AS durata
  FROM pg_stat_activity WHERE datname = current_database() AND xact_start IS NOT NULL
  ORDER BY xact_start LIMIT 5;
  ```
  Una sessione di un servizio «idle in transaction» si chiude da sola dopo `idle_in_transaction_session_timeout` del suo login (prerequisito, guardia all'avvio: docs/architettura/sicurezza.md §4 e §7). Una sessione di un operatore (psql) si chiude con l'operatore o, se abbandonata, con `SELECT pg_terminate_backend(<pid>)` (annotare nel registro attività). Una transazione attiva lunga e legittima (es. ricostruzione) va lasciata finire: l'export riprende da solo e nessuna riga si perde.
- **`AuditOrizzonteNonVerificabile`**: il proprietario di `vd_transazioni_aperte()` non vede `pg_stat_activity` di tutte le sessioni: concedergli `pg_read_all_stats` (o usare l'utente master su RDS per le migrazioni). Finché dura, l'export è sospeso (fail-closed).
