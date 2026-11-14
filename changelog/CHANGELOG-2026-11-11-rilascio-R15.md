# CHANGELOG — R15 — Documentazione di conformità generata dalla configurazione (01/11/2026)

Manuale di conservazione, piano della sicurezza, guida alle funzionalità e catalogo delle
pagine generati dalla configurazione reale del servizio, dal database (sola lettura) e dai
registri del repository, mai con valori inventati (VD-C-177, residuo di VD-C-082).

- **Base**: R14 (`changelog/CHANGELOG-2026-11-10-rilascio-R14.md`, commit d56979f). Migrazioni
  fino a `20261109_056`; migrazione nuova, nata `20261110_057` e
  rinumerata all'unione con R16 (R17) in `20261111_058` (file `20261111_058_documentazione.py`,
  down `20261110_057_residui`).
- **Criticità**: VD-C-177 chiusa con riserve; nuove VD-C-181 (due generatori del manuale fino
  alla firma del responsabile), 182 (generazione nel processo dell'API), 183 (requisiti da
  riscontrare sul testo ufficiale); nate 178–180 e rinumerate all'unione con R16 (R17). 180 registrate: 24 aperte, 7 parzialmente chiuse, 46
  chiuse con riserve, 103 chiuse.
- **Scelte da confermare**: VD-P-185…189 (`docs/registro/decisioni.md`, «Scelte di R15»; nate 165…169, rinumerate all'unione con R16).
- **Documentazione**: `docs/architettura/documentazione.md` (nuovo); generati
  `docs/manuale/manuale_conservazione.md`, `docs/manuale/piano_sicurezza.md`,
  `GUIDA_FUNZIONALITA.md`, `docs/catalogo/route.md`, `docs/catalogo/permessi.md`;
  `docs/architettura/sicurezza.md` (tabelle fuori dalla RLS); REGOLE §0.4 e §31.
- **Console**: pagina «Documentazione del servizio» nell'area «Conformità» (solo SUPER_ADMIN e
  RSC_CONSERVATORE) nel sistema R12c: testa chiusa dal binario, colonna unica, tabellone
  impilato, stati vuoti con `Vuoto`; prove a 390 e 1280 px in chiaro e scuro
  (`frontend/e2e/r15.spec.ts`).

> **Avviso operativo**
> - **Gruppi del database (migrazione 058)**: `verumdoc_documentazione` al SOLO login di
>   dashboard-api, `verumdoc_documentazione_archivio` al SOLO login della hashchain,
>   `verumdoc_auditor` al login dell'auditor (sola lettura). Senza il primo la generazione
>   finisce in ERRORE; senza il secondo le versioni non vanno nel WORM (allarme
>   `DocumentazioneNonArchiviata`).
> - **Permesso nuovo** `documentazione:genera`, mai delegabile (anche nel vincolo
>   `ck_ruoli_personalizzati_non_delegabili`): spetta solo ai ruoli fissi del conservatore.
> - **Dati del conservatore**: i documenti leggono `CONSERVATORE_*` dall'ambiente di
>   dashboard-api; ciò che manca resta «DA COMPILARE» con chi lo fornisce.

## Cosa cambia

| Area | Modifica | Prove |
|---|---|---|
| Libreria | `libs/conformita`: modello dei documenti con i blocchi dichiarati «DA COMPILARE» (chi, dove, campo) e «da verificare sul testo ufficiale»; requisiti (Linee guida, lettere a–l; Regolamento, Allegato A) dalla ricerca interna con il marcatore V/NV; stato delle funzioni con codice e test, abbassato se una criticità collegata è aperta; raccolta dal repository (fotografia senza date) e dal database (READ ONLY, solo configurazione); Markdown e HTML stampabile autonomo; versione, data, impronta, fonti, differenze per sezione | `libs/conformita/tests/test_documenti.py`, `libs/conformita/tests/test_database.py` |
| Strumento | `tools/conformita/genera_documenti.py`: edizione del repository e `--controlla` (in `make verifiche`); generazione versionata con il database (login dell'auditor) in una cartella con `indice.json` | stessi test |
| API | dashboard-api `/api/dashboard/documentazione-servizio`: generazione asincrona (202, una alla volta, scadenza, interrotta e ripresa, ERRORE esplicito con metrica e allarme), quadro, storico, dettaglio, scaricamento Markdown e HTML con impronta; permesso mai delegabile, audit | `services/dashboard-api/tests/test_r15_documentazione.py` |
| Database | migrazione 058: `documentazione_generazioni`, `documentazione_versioni` immutabili (trigger anche BEFORE INSERT), GRANT minimi per colonna, auditor | stesso file, `services/gateway/tests/test_r15_migrazione_058.py` |
| WORM | hashchain: passo `documentazione` del ciclo del registro, `registro/documentazione/<tipo>/<impronta>.md|.html`, impronte ricalcolate, idempotente, fencing | `services/hashchain/tests/test_r15_archivio_documentazione.py` |
| Verifiche | `tools/docs/verifica_documentazione.py`: segnaposto non dichiarati nei documenti generati, decisioni `VD-N`/`VD-DR` citate | `tests/integrity/test_documentazione.py` |
| Console | pagina «Documentazione del servizio» | `frontend/e2e/r15.spec.ts` |
| Osservabilità | `verumdoc_documentazione_generazioni_totali`, `verumdoc_documentazione_da_archiviare`; regole `infra/observability/regole/verumdoc-r15.yml` | `tests/integrity/test_osservabilita.py` |

## Requisiti «da verificare sul testo ufficiale»

Numero del paragrafo «Manuale di conservazione»; fasi del processo di conservazione e numero
massimo di rifiuti; modalità di esibizione; infrastrutture; misure di sicurezza (art. 32 GDPR,
misure minime, violazioni dei dati); selezione e scarto (VD-C-183).

## Prima del deploy
1. Migrazioni `20261110_057` (R16) e `20261111_058` con il Job `gateway-migrazioni`; controllo `alembic current` =
   `20261111_058`. Mai consegnate prima.
2. GRANT dei gruppi (avviso operativo) e login dell'auditor.
3. `CONSERVATORE_*` nel Secret di dashboard-api; regole `verumdoc-r15.yml` in Prometheus.
4. Chart: dashboard-api, hashchain, frontend.
5. Controlli: «Genera la documentazione» → COMPLETATA con quattro versioni; dopo un giro della
   hashchain le versioni risultano «nel WORM».

## Mutazioni

25 mutazioni sui punti di sicurezza e integrità (permessi mai delegabili, chiave API, valori
inventati, dati di prova, requisiti non riscontrati, idempotenza, sola lettura, segnaposto,
ricalcolo delle impronte e fencing nella hashchain, 11 sulla migrazione: impronte, versione
successiva, contenuto uguale, richiesta non in corso, archivio all'inserimento, UPDATE, DELETE,
archivio due volte, impronta nella chiave, GRANT, richiesta conclusa): 22 uccise al primo giro;
due sopravvissute uccise con test nuovi (chi di soli spazi; archivio insieme a un cambio di
contenuto), una equivalente per il vincolo CHECK riformulata e uccisa: **25/25**.

## Test
PostgreSQL reale (`r15_integ`, copia di `r14_integ` migrata alla 057), login non proprietario
dei servizi. Test nuovi: `libs/conformita` 16 (20 con i parametri), dashboard-api 10 (14),
hashchain 6, gateway 1 (migrazione) più le teste aggiornate, `tests/integrity` 1, e2e 6.
Suite dei servizi toccati: dashboard-api 258, hashchain 363, onboarding 297, ciclo-vita 245,
`libs/conformita` + `libs/verumdoc_rules` + `libs/shared` 691, `tools/docs` + `tests/integrity`
41, gateway 822 passati e 4 falliti al primo giro (3 corretti: teste alembic in due test e
VALIDATE fuori dal lock esclusivo; poi 24/24 sui file interessati); resta
`test_r04_correzioni_partizioni.py::test_consegna_riaccodata_durante_il_distacco_partizione_riagganciata`,
che fallisce identico sulla base R14 (partizioni giornaliere da creare prima della corsa, come
nello script di R14). Frontend: typecheck, lint, build, e2e **108** con 2 lavoratori (102 di
R14 + 6 di R15). `make verifiche` verde. Corsa completa fatta dopo l'unione con R16, in R17 (4.582 test Python
passati al primo colpo; il test delle partizioni sopra è corretto in R17).

Consegnato con R16 e R17 nel pacchetto unico R15–R17: «Prima del deploy» cumulativo in
`changelog/CHANGELOG-2026-11-12-rilascio-R17.md` e in `README_RILASCIO.md`.
