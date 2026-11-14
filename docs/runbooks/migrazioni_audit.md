# Migrazioni sulle tabelle dell'audit e degli altri registri

Riferimenti:
- migrazioni `20261023_031`, `033` e `034`;
- `libs/shared/db/migrazioni_registri.py`;
- `docs/architettura/infrastruttura.md`, «Ruoli del database»;
- `catena.md` §11;
- test `services/gateway/tests/test_r04_correzioni_registri_ruoli.py`.

## Perché serve una procedura

I registri append-only sono:
- `audit_global` e `verifiche_registro`, con le loro partizioni;
- `catena_pda`, `catena_registro`, `catena_nodi`, `catena_checkpoint`.

Sono protetti da **event trigger** che valgono anche per l'amministratore che migra:

| Event trigger | Quando | Rifiuta |
|---|---|---|
| `vd_protezione_registri_oggetti` | fine di CREATE/ALTER FUNCTION, RULE, TRIGGER, POLICY, CREATE TABLE | sostituire una funzione di protezione; regole, trigger e politiche sui registri diversi da quelli delle difese; `CREATE TABLE … PARTITION OF` un registro |
| `vd_protezione_registri_stato` | fine di ALTER TABLE, CREATE/ALTER INDEX | registro rinominato; partizione staccata o rinominata; RLS o FORCE tolte; trigger delle difese spenti; **struttura dei registri cambiata** (colonne, tipi, NOT NULL, vincoli, indici unici) rispetto all'impronta in `protezione.impronte_registri`; vincoli, indici unici o NOT NULL propri di una partizione |
| `vd_protezione_registri_alter` | inizio di ALTER TABLE | DETACH, DISABLE e NO FORCE sui registri |
| `vd_protezione_registri_drop` | `sql_drop` | DROP di registri, partizioni, funzioni di protezione, trigger, regole e politiche dei registri |

Una migrazione **legittima** che cambia un registro va quindi scritta in modo speciale. Esempi:
- aggiungere una colonna o un vincolo all'audit;
- creare un indice unico;
- cambiare una funzione di protezione;
- aggiungere una politica RLS.

Gli event trigger si **sospendono** per il solo lavoro, si **riattivano** nello stesso file e si aggiornano le impronte della struttura.

Senza la procedura la migrazione fallisce con «struttura di un registro modificata fuori da una migrazione», «politica non ammessa» e simili. È voluto: meglio una migrazione ferma che difese spente in esercizio.

Indici **non unici** (`CREATE INDEX`), `COMMENT` e `SET STATISTICS` non cambiano l'impronta e non richiedono la procedura.

## La funzione di supporto

```python
from alembic import op

from libs.shared.db.migrazioni_registri import registri_modificabili


def upgrade() -> None:
    with registri_modificabili(op, motivo="R05: colonna ente_codice nell'audit"):
        op.execute("ALTER TABLE public.audit_global ADD COLUMN ente_codice varchar(20)")
        op.execute("ALTER TABLE public.audit_global ADD CONSTRAINT ck_audit_ente_codice "
                   "CHECK (ente_codice ~ '^[A-Z0-9]+$') NOT VALID")


def downgrade() -> None:
    with registri_modificabili(op, motivo="downgrade R05: colonna ente_codice"):
        op.execute("ALTER TABLE public.audit_global DROP CONSTRAINT ck_audit_ente_codice")
        op.execute("ALTER TABLE public.audit_global DROP COLUMN ente_codice")
```

**`registri_modificabili(op, motivo=…)`:**
1. Sospende i quattro event trigger (`ALTER EVENT TRIGGER … DISABLE`) con un `NOTICE` che riporta il motivo. Il motivo è obbligatorio.
2. Esegue il blocco.
3. **Sempre**, in `finally`:
   - riattiva gli event trigger (`ENABLE ALWAYS`);
   - se il blocco è riuscito, aggiorna le impronte (`protezione.aggiorna_impronte()`);
   - controlla che siano tutti `ENABLE ALWAYS`, e altrimenti fallisce.
4. Se il blocco fallisce dentro una migrazione transazionale, la transazione è abortita e la riattivazione non si può eseguire. Non serve: `ALTER EVENT TRIGGER` è transazionale e il ROLLBACK annulla anche la sospensione. L'errore originale non viene mai coperto.
5. Nei blocchi fuori transazione (`op.get_context().autocommit_block()`, per `CONCURRENTLY`) la riattivazione avviene comunque, anche dopo un errore.

Tutto è SQL in blocchi DO, quindi funziona anche come script offline (`alembic upgrade --sql`).

**Chi la esegue:**
- serve un **superutente**: su RDS l'utente master, membro di `rds_superuser`, cioè il Job `gateway-migrazioni`;
- se gli event trigger non esistono (database creato senza superutente), la funzione salta i passi e la migrazione procede.

## Esempio vero

La migrazione `20261023_034_terzo_giro_r04.py` sostituisce le funzioni degli event trigger e li ricrea con tag nuovi dentro `registri_modificabili`.

## Regole

1. **Un blocco solo e stretto**: dentro solo le istruzioni che toccano i registri; dati e tabelle normali fuori.
2. **Mai** `ALTER EVENT TRIGGER … DISABLE` scritto a mano in una migrazione: solo con la funzione, che riattiva in `finally`.
3. Cambiare una funzione di protezione (`vd_audit_append_only`, `catena_checkpoint_protetto`, `vd_crea_partizione_registro`, `vd_protezione_registri_*`, …):
   - richiede revisione di sicurezza;
   - si fa ricreandola nel blocco;
   - il downgrade ripristina la versione precedente, letta dal file della migrazione che l'aveva creata (vedi 033 e 034).
4. Nuove politiche o trigger permanenti sui registri: aggiornare **anche** l'elenco degli ammessi nell'event trigger «oggetti», altrimenti il prossimo `ALTER POLICY` o `CREATE TRIGGER` della manutenzione fallisce.
5. Test: ogni migrazione così ha un test che, alla testa, verifica gli event trigger `ENABLE ALWAYS` e le impronte aggiornate. Quello generale è `test_event_trigger_dei_registri_enable_always_alla_testa`.

## Partizione senza difese (ripristino)

Dalla migrazione 034 una partizione dei registri si crea **solo** con `vd_crea_partizione_registro`, che la crea con RLS e FORCE, politiche e trigger contro TRUNCATE. Una partizione creata a mano prima della 034 senza difese blocca:
- la manutenzione («RLS e FORCE obbligatorie»);
- ogni ALTER TABLE dei registri.

Ripristino, come amministratore, in una migrazione dati o in un comando versionato (REGOLE §16.7):

```python
with registri_modificabili(op, motivo="difese della partizione audit_global_p209901"):
    op.execute("ALTER TABLE partizioni.audit_global_p209901 ENABLE ROW LEVEL SECURITY, "
               "FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY audit_global_insert_only ON partizioni.audit_global_p209901 "
               "FOR INSERT WITH CHECK (true)")
    op.execute("CREATE POLICY audit_global_select_all ON partizioni.audit_global_p209901 "
               "FOR SELECT USING (true)")
    op.execute("CREATE TRIGGER trg_audit_global_no_truncate BEFORE TRUNCATE ON "
               "partizioni.audit_global_p209901 FOR EACH STATEMENT "
               "EXECUTE FUNCTION public.vd_audit_append_only()")
    op.execute("ALTER TABLE partizioni.audit_global_p209901 OWNER TO verumdoc_audit_proprietario")
```

## R11 — migrazione 20261103_050 fermata da versioni di profilo in conflitto

La migrazione `20261103_050` (adozione dei profili di tipologia) si ferma con

`migrazione 20261103_050 fermata: versioni del conservatore con lo stesso numero delle versioni predefinite R11: fattura v2 …`

se il conservatore aveva creato dalla console una versione 2 di una tipologia: R11 pubblica le versioni 2 predefinite (`libs/verumdoc_rules/dati/profili/*.v2.json`) e le due avrebbero lo stesso numero (ogni versamento della tipologia fallirebbe con un errore di configurazione). La tabella `profili_tipologia` è append-only: **non** si cancella né si rinumera la riga a mano.

Procedura (con il responsabile del servizio, decisione registrata):

1. fermare il deploy di R11 (la migrazione non ha scritto nulla: il controllo è il primo passo);
2. confrontare la versione del conservatore con la v2 predefinita (`docs/conformita/riscontro_allegato5.md`);
3. pubblicare con R11 la v2 predefinita con un **numero libero** (rinominare il file in `<codice>.v<N>.json` con `"versione": N` più alto di quelli del conservatore, aggiornare `impronte.json` e `VERSIONI_R11` della migrazione) in un rilascio correttivo, oppure — se la versione del conservatore va abbandonata — chiuderne la validità con una migrazione dati versionata che la sostituisce (REGOLE §16.7);
4. rilanciare il job `gateway-migrazioni`.
