# Documentazione di conformità generata dalla configurazione (R15)

> Criticità: VD-C-177 (residuo di VD-C-082). Regole: REGOLE_SVILUPPO §0 (il manuale non
> dichiara ciò che il sistema non fa), §28.1 e §28.3 (cataloghi e allegati generati).
> Codice: `libs/conformita`, `tools/conformita/genera_documenti.py`,
> `services/dashboard-api/app/routers/documentazione_servizio.py`,
> `services/hashchain/app/registro/documentazione.py`, migrazione
> `services/gateway/alembic/versions/20261111_058_documentazione.py`.

## 1. Che cosa si genera

| Documento | Tipo | Contenuto | Edizione nel repository |
|---|---|---|---|
| Manuale di conservazione | `MANUALE_CONSERVAZIONE` | contenuti minimi del paragrafo «Manuale di conservazione» delle Linee guida (lettere a–l), funzioni non ancora attive, allegati 1–10 (classi e tempi, formati, metadati e profili, ruoli × permessi, processo, verifiche, firme e marche, continuità RPO/RTO, interoperabilità, impostazioni con riferimento normativo) | `docs/manuale/manuale_conservazione.md` |
| Piano della sicurezza | `PIANO_SICUREZZA` | asset, minacce, misure realizzate con il codice e il test che le provano, cifratura, accessi, audit, copie e ripristino, incidenti, fornitori, rischi residui = criticità aperte del registro (Regolamento, Allegato A, RO7) | `docs/manuale/piano_sicurezza.md` |
| Guida alle funzionalità | `GUIDA_FUNZIONALITA` | per ruolo: permessi e pagine | `GUIDA_FUNZIONALITA.md` |
| Catalogo delle route | `CATALOGO_ROUTE` | route, ruoli, abilitazione, voce di menu | `docs/catalogo/route.md` |
| Matrice ruoli × permessi | — | catalogo | `docs/catalogo/permessi.md` |

## 2. Fonti e regola dei valori

- **Repository** (`libs/conformita/repository.py`): registri delle criticità e delle decisioni,
  permessi e ruoli modello, impostazioni, catalogo dei formati, profili dei metadati, route e
  menu del frontend, Helm e Terraform, runbook, allarmi. Il risultato, senza date, è la
  fotografia `libs/conformita/dati/fonti_repository.json` con l'impronta di ogni file letto:
  la console non ha il repository e usa la fotografia del rilascio.
- **Database** (`libs/conformita/database.py`): sola configurazione del servizio (classi
  documentali, versioni dei profili, impostazioni del conservatore, fornitori d'identità, di
  firma e marca, canali PEC) in una transazione `READ ONLY`, ambito del conservatore. Niente
  dati di esercizio né dati personali: la stessa configurazione dà lo stesso documento.
- **Variabili del conservatore** (`CONSERVATORE_*`, `libs/conformita/conservatore.py`).

Nessun valore inventato: un dato assente, segnaposto, di prova (domini riservati) o non
valido è il blocco **DA COMPILARE** con *chi* lo fornisce e *dove* si inserisce. Un requisito
normativo non riscontrato sul testo ufficiale (fonte interna:
`docs/audit/allegati/ricerca_normativa.md`, marcatori V/NV) è *da verificare sul testo
ufficiale* (`libs/conformita/requisiti.py`). Lo stato delle funzioni viene da
`libs/conformita/funzioni.py`, che cita codice e test e si abbassa se una criticità collegata
è ancora aperta.

## 3. Versioni

Ogni generazione ha versione (per tipo), data, impronta SHA-256 del contenuto, elenco delle
fonti con le impronte e differenze rispetto alla versione precedente (sezioni aggiunte, tolte,
cambiate). Stesso contenuto = nessuna versione nuova. Il file Markdown con l'intestazione ha
la sua impronta (`impronta_sha256`), riverificata dal database e presente nella chiave del WORM.

## 4. Strumento da riga di comando

```bash
python tools/conformita/genera_documenti.py               # edizione del repository
python tools/conformita/genera_documenti.py --controlla   # in make verifiche
python tools/conformita/genera_documenti.py --database-url postgresql+asyncpg://vd_auditor@host/db \
    --uscita /cartella   # versioni con il database (login dell'auditor, sola lettura)
```

In sviluppo il login `vd_auditor` (gruppo `verumdoc_auditor`, sola lettura predefinita) lo
crea `infra/postgres/ruoli_sviluppo.sql`; in esercizio va creato come gli altri login.

## 5. Console e API

Pagina «Documentazione del servizio» (area Conformità, `/documentazione-servizio`), solo
SUPER_ADMIN e RSC_CONSERVATORE con ruolo fisso: permesso mai delegabile
`documentazione:genera` (catalogo, vincolo del database, controllo del servizio).

- `POST /api/dashboard/documentazione-servizio/generazioni` (202): una sola richiesta in corso
  (indice unico nel database), audit nella stessa transazione, scadenza di 10 minuti. Il lavoro
  gira dopo la risposta e chiude la richiesta COMPLETATA (versioni nuove e audit nella stessa
  transazione) o ERRORE con il motivo. Una richiesta scaduta senza esito si dichiara interrotta
  alla richiesta successiva. Metrica `verumdoc_documentazione_generazioni_totali{esito}`,
  allarme `DocumentazioneNonGenerata`: leggere il motivo nella console e generare di nuovo.
- `GET …`, `GET …/versioni?tipo=`, `GET …/versioni/{id}`, `GET …/versioni/{id}/markdown|html`
  (scaricamento con `X-Impronta-SHA256`, audit dello scaricamento).

## 6. Immutabilità e archivio WORM

Tabelle `documentazione_generazioni` e `documentazione_versioni` (senza ente, fuori dalla RLS:
documenti del conservatore). Trigger `documentazione_versioni_nuove` (BEFORE INSERT): versione
successiva e precedente corretto, contenuto diverso dal precedente, impronte ricalcolate dal
database, generazione in corso, archivio vuoto. Trigger `documentazione_versioni_protette`:
nessuna DELETE, TRUNCATE o modifica, tranne la registrazione dell'archivio una sola volta, dal
gruppo `verumdoc_documentazione_archivio`, con l'impronta nella chiave.

La hashchain, con il lease del registro, scrive ogni versione in
`registro/documentazione/<tipo>/<impronta>.md` e `.html` (retention del registro, scrittura
idempotente: un oggetto identico già presente si accetta, uno diverso apre il blocco del
registro). Metrica `verumdoc_documentazione_da_archiviare`, allarme
`DocumentazioneNonArchiviata`: controllare lease, IAM sul prefisso `registro/` e i log
«documentazione_non_archiviata».

## 7. Firma del responsabile (punto di aggancio)

Le versioni non sono firmate. La firma del responsabile (rilascio successivo) si appone sull'HTML
stampabile: `libs/conformita/versioni.py`, funzione `contenuto_da_firmare` (nome, tipo,
impronta e byte); l'API espone per ogni versione `firma.da_firmare.impronta_sha256`. La firma
non cambia la versione registrata.

## 8. Prima del deploy

1. Migrazione `20261111_058` (nata 057 in R15, rinumerata all'unione con R16, dopo `20261110_057_residui`).
2. GRANT: `verumdoc_documentazione` al SOLO login di dashboard-api,
   `verumdoc_documentazione_archivio` al SOLO login della hashchain, `verumdoc_auditor` al
   login dell'auditor.
3. Variabili `CONSERVATORE_*` nel Secret o ConfigMap di dashboard-api.
4. Regole `infra/observability/regole/verumdoc-r15.yml`.
