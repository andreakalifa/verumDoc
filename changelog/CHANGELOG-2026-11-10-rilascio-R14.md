# CHANGELOG — R14 — Debito interno (31/10/2026)

Lavoro che non dipende da contratti né account (roadmap §R14): metriche e allarmi del
lavoratore della posta, retention delle notifiche personali, stati terminali automatici
delle pratiche, concorrenza della cronologia nel database, prova aggiornata nel
verificatore, osservabilità collegata, strumenti di rilascio, pulizia del lint, namespace
dell'IPdA, risigillo degli IPdA storici.

- **Base**: R13b (`changelog/CHANGELOG-2026-11-09-rilascio-R13b.md`). Migrazioni fino a
  `20261108_055`; migrazione nuova `20261109_056`.
- **Criticità**: chiuse VD-C-121, 133, 170, 172, 175; chiuse con riserve VD-C-033, 082, 096,
  123, 165; parzialmente chiuse VD-C-032 (ruff 1.310 → 491) e 122 (allarme); VD-C-120 e 141
  valutate, proposta VD-P-164 (da decidere); nuove VD-C-174…177 (residui; 175 chiusa nella
  revisione). 177 registrate: 22 aperte, 7 parzialmente chiuse, 45 chiuse con riserve, 103
  chiuse.
- **Scelte da confermare** (`docs/registro/decisioni.md`, «Scelte di R14 da confermare»):
  VD-P-156 pratiche sospese dopo i tentativi esauriti e ripresa dalla console, **senza
  chiedere una conferma recente dell'identità** (come la chiusura con eccezioni: il permesso
  `pratica:riprendi` è mai delegabile, solo conservatore con ruolo fisso e accesso proprio,
  versione attesa e audit); VD-P-157 retention delle notifiche personali; VD-P-158 metriche
  della posta dalle repliche API; VD-P-159 cronologia vincolata nel database; VD-P-160
  namespace provvisorio dell'IPdA; VD-P-161 sigillo automatico degli IPdA storici; VD-P-162
  `--prova-aggiornata` nel verificatore; VD-P-163 pulizia del lint: tutte «da confermare».
  VD-P-164 (conferma dell'identità firmata dal gateway per le transizioni distruttive,
  VD-C-120/141) «da decidere», non realizzata.
- **Documentazione**: runbook nuovo `docs/runbooks/pratica_sospesa.md`;
  `docs/integrazione/pec.md` §6 (metriche, allarmi, pulizia); `docs/architettura/ciclo_vita.md`
  §9; `docs/runbooks/scarto_incompleto.md` (allarme `ScartoNonDeterminabile`);
  `docs/architettura/giorno1_staging.md` §14-ter; cataloghi generati
  `docs/catalogo/impostazioni.md`, `topic.md`, `metriche.md`; REGOLE §0.4 e §31.
- **Console** (revisione): la pagina della pratica dice quando è «ferma» (tentativi esauriti)
  e il conservatore la riprende con «Riprendi il lavoro» (motivo, versione attesa), nel
  sistema grafico R12c; controllo a 390 e 1280 px in chiaro e scuro (`frontend/e2e/r14.spec.ts`).

> **Avviso operativo**
> - **Gruppo del database (migrazione 056)**: `verumdoc_notifiche_pulizia` al SOLO login del
>   lavoratore della posta; senza, la pulizia delle notifiche personali registra l'errore a
>   ogni giro e le voci restano (nessun altro effetto).
> - **Pratiche sospese**: con `LAVORATORE_TENTATIVI_MASSIMI` = 30 (predefinito) una pratica
>   che fallisce sempre si ferma dopo circa due giorni e mezzo invece di ritentare per
>   sempre; allarme `PraticaSospesa`, ripresa con il runbook. Per il comportamento di prima:
>   `LAVORATORE_TENTATIVI_MASSIMI=0`. Al deploy, una pratica che ha già 30 tentativi o più
>   (lease scaduti compresi) si sospende al primo giro: controllare prima con
>   `SELECT id, tentativi FROM pratiche WHERE lavoro IS NOT NULL AND tentativi >= 30`.
> - **IPdA nuovi** con il namespace `https://schemi.verumdoc.it/ipda/provvisorio/1` (VD-P-160):
>   chi legge l'IPdA per namespace (sistemi dei clienti) va avvisato; gli IPdA già conservati
>   non cambiano.
> - **Arretrato dei sigilli**: parte da solo quando un fornitore qualificato è attivo
>   (`FIRMA_ARRETRATO_IPDA_ATTIVO=false` per rinviarlo).
> - **Allarmi e cruscotto**: caricare `infra/observability/regole/verumdoc-r14.yml` e
>   `infra/observability/grafana/cruscotti/verumdoc-esercizio.json`.

## Cosa cambia

| Area | Modifica | Prove |
|---|---|---|
| Posta (VD-C-170) | Metriche dal database nelle repliche API di notification (`app/application/metriche_posta.py`): coda ferma, non partiti, PEC senza ricevuta, canali non utilizzabili, stato del lavoratore vecchio, ricevute non autentiche; sei allarmi | `services/notification/tests/test_r14_posta_metriche_pulizia.py` |
| Notifiche personali (VD-C-172) | Parametro `NOTIFICHE_PERSONALI_GIORNI`; pulizia nel lavoratore della posta con lease e fencing (`lavori_periodici`); funzione `vd_pulisci_notifiche_personali` (mai sotto 30 giorni); trigger che cancella solo dentro la funzione | stesso file (soglia, due guardie indipendenti, login senza DELETE, fencing) |
| Ciclo di vita (VD-C-133) | Sospensione esplicita dopo `LAVORATORE_TENTATIVI_MASSIMI` (`LC-409-TENTATIVI-ESAURITI`), anche per lease scaduti; esecuzione sospesa non respingibile; metrica e allarme `PraticaSospesa`; runbook con il comando di ripresa provato | `services/ciclo-vita/tests/test_r14_pratica_sospesa.py` |
| Scarto (VD-C-122) | Metrica `verumdoc_ciclo_vita_oggetti_non_determinabili` e allarme `ScartoNonDeterminabile` | `test_metrica_scarto_non_determinabile` |
| Cronologia (VD-C-121) | `pratiche_stati.precedente_id` dal trigger e indice unico `uq_pratiche_stati_precedente` | `services/ciclo-vita/tests/test_r14_cronologia_concorrente.py` (due connessioni, nessun lock applicativo) |
| Verificatore (VD-C-123) | `--prova-aggiornata`: stato attuale offline con una prova di oggi legata alla ricevuta | `tools/verificatore/tests/test_r14_prova_aggiornata.py` |
| Osservabilità (VD-C-033) | ciclo-vita fra i bersagli di Prometheus; regole R14; descrizioni delle regole a pagina di R04; cruscotto Grafana versionato e provisioning; catalogo delle metriche | `tests/integrity/test_osservabilita.py` |
| Strumenti di rilascio (VD-C-082) | `tests/integrity`, `tools/docs/genera_cataloghi.py`, `tools/docs/verifica_documentazione.py` in `make verifiche` (e `make docs-api`) | `tests/integrity/test_documentazione.py`, `test_cataloghi.py` |
| Lint (VD-C-032) | ruff 0.17.1 con configurazione completa: errori nel repository **1.310 → 491**; codice applicativo (`services/*/app`, `libs` senza test) **213 → 164**; perimetro d'ingresso (gateway, sftp-ingest, validation con i test) **319 → 82**. Correzioni automatiche sicure fuori dalle migrazioni (~180); F811 nei test e T201 negli strumenti ignorati per configurazione motivata (~640); B008 attiva anche su `versamento.py` | corsa completa invariata |
| IPdA (VD-C-096) | Namespace proprio del conservatore, senza `schemaLocation`, dichiarato provvisorio; nessun URI a nome di UNI o AgID | `services/aip-builder/tests/test_sprint2a_sincro_scadenza.py` |
| Firma (VD-C-165) | Censimento degli IPdA dei PdA conservati prima di R13a nel ciclo del registro, solo con fornitore qualificato, impronta dal manifest verificato, cursore e limite per giro, fencing, idempotente | `services/hashchain/tests/test_r14_arretrato_ipda.py` |

## Revisione

Revisione indipendente sul commit wip 4c7d335: nessun rilievo grave; **tutti corretti** prima
del rilascio, con test e mutazioni.

| Gravità | Rilievo | Correzione | Prova |
|---|---|---|---|
| Media | Con `--prova-aggiornata` la ricevuta si confrontava con il checkpoint corrente solo se questo era più grande: un registro biforcato (stessa dimensione, radice diversa) passava | Confronto SEMPRE quando c'è il corrente | `test_prova_aggiornata_su_registro_biforcato_fallisce` (dato di prova nuovo `prova_biforcata`) |
| Media | IPdA ricostruito a cavallo del rilascio: una costruzione iniziata con R13b (IPdA storico nel WORM) avrebbe dato AIP-STORAGE-001 permanente | Formato dell'IPdA nel record di costruzione (`formato_ipda`, assente = storico); il formato storico produce gli stessi byte di R13b (verificato contro il codice di R13b) | `services/aip-builder/tests/test_r14_formato_ipda.py` |
| Media | Migrazione 056: un `CREATE INDEX CONCURRENTLY` interrotto lascia l'indice INVALIDO, che `IF NOT EXISTS` teneva | DROP dell'indice non valido prima di ricrearlo (come 036–052) | `services/gateway/tests/test_r14_migrazione_056.py` |
| Media | Ripresa della pratica sospesa solo con SQL a mano | `POST /pratiche/{id}/riprendi` (solo conservatore, ruolo fisso e proprio accesso, permesso mai delegabile `pratica:riprendi` anche nel vincolo del database, lock, versione attesa, audit, avviso `PRATICA_RIPRESA`); pulsante in console; runbook aggiornato | `test_ripresa_dalla_console_solo_del_conservatore`, `test_ripresa_mai_con_chiave_api_ne_ruolo_personalizzato`, `frontend/e2e/r14.spec.ts` |
| Bassa | Tentativi non azzerati dopo una presa con progressi | Una presa con almeno un passo riuscito azzera i tentativi | `test_progressi_nella_presa_azzerano_i_tentativi` |
| Bassa | Arretrato dei sigilli: versione corrente dell'IPdA invece di quella con l'impronta del manifest | Elenco delle versioni, si registra quella con lo SHA-256 del manifest | `test_versione_dell_ipda_con_l_impronta_del_manifest` |

Mutazioni della revisione: 11/11 uccise (rilievo 1: 1; 2: 3; 3: 1; 4: 6).

## Migrazione `20261109_056_debito_interno` (down `20261108_055`)

`lavori_periodici` e `firme_arretrato_ipda` (senza ente, fuori dalla RLS, diritti al solo
gruppo che le usa); funzione `vd_pulisci_notifiche_personali` (SECURITY DEFINER, `search_path`
fissato, EXECUTE solo a `verumdoc_notifiche_pulizia`, tolta a PUBLIC); trigger
`notifiche_personali_protette` aggiornato; `pratiche_stati.precedente_id` (solo catalogo) e
trigger di continuità aggiornato; indice unico `CONCURRENTLY` in autocommit (ricreato se
INVALIDO); `pratica:riprendi` fra i mai delegabili nel vincolo `ck_ruoli_personalizzati_non_delegabili`. `lock_timeout`;
riprendibile; provata up/down/up; `alembic check` senza differenze.

## Mutazioni

Sviluppo: 22 mutazioni (stati terminali 6, retention e fencing 4+3 sul database, risigillo 4,
cronologia 2 sul database, prova aggiornata 3); 21 uccise al primo giro, la sopravvissuta
(legame di foglia della prova aggiornata) uccisa con un test nuovo: **22/22**. Revisione:
**11/11** (vedi sopra).

## Prima del deploy
1. **Pratiche con molti tentativi**: `SELECT id, tentativi FROM pratiche WHERE lavoro IS NOT
   NULL AND tentativi >= 30` (o il valore scelto per `LAVORATORE_TENTATIVI_MASSIMI`): quelle
   righe si sospendono al primo giro del lavoratore. Per tenere il comportamento di prima:
   `LAVORATORE_TENTATIVI_MASSIMI=0`.
2. **Migrazione `20261109_056`** con il Job `gateway-migrazioni`. Controllo: `alembic current`
   = `20261109_056`. L'indice unico `uq_pratiche_stati_precedente` si crea `CONCURRENTLY` in
   autocommit (nessun blocco lungo di `pratiche_stati`); se la migrazione si interrompe, la
   ripresa elimina l'indice rimasto INVALIDO e lo ricrea. Controllo: `SELECT indisvalid FROM
   pg_index WHERE indexrelid = 'uq_pratiche_stati_precedente'::regclass` = `true`. Mai
   consegnata prima: nessun database del committente può essere già a 056.
3. **Permesso nuovo `pratica:riprendi`** (mai delegabile, anche nel vincolo
   `ck_ruoli_personalizzati_non_delegabili` della 056): spetta solo al ruolo fisso del
   conservatore; nessuna configurazione da fare, nessun ruolo personalizzato può riceverlo.
4. **GRANT** (dopo la 056): `verumdoc_notifiche_pulizia` al SOLO login del lavoratore della
   posta (`notification-posta`).
5. **Prometheus e Grafana**: caricare le regole `infra/observability/regole/verumdoc-r14.yml`
   (e le descrizioni aggiornate di `verumdoc-r04.yml`), aggiungere ciclo-vita ai bersagli
   (`infra/observability/prometheus.yml`), importare il cruscotto
   `infra/observability/grafana/cruscotti/verumdoc-esercizio.json` (provisioning in
   `infra/observability/grafana/provisioning/dashboards/verumdoc.yaml`). **Allarmi nuovi**:
   `PostaCodaFerma`, `PostaNonPartita`, `PecSenzaRicevuta`, `PecCanaleNonUtilizzabile`,
   `PecLavoratoreFermo`, `PecRicevuteNonAutentiche`, `PraticaSospesa`,
   `ScartoNonDeterminabile`, `ArretratoIpdaScartati`; elenco completo in
   `docs/catalogo/metriche.md`. Instradarli come gli altri della stessa gravità.
6. **Chart**: gateway, ciclo-vita (API e lavoratore), notification (API e posta), aip-builder,
   hashchain, frontend. Variabili nuove con valore predefinito (nessuna da impostare):
   `LAVORATORE_TENTATIVI_MASSIMI` (30, ciclo-vita), `FIRMA_ARRETRATO_IPDA_ATTIVO` (vero,
   hashchain); parametro di console `NOTIFICHE_PERSONALI_GIORNI` (365, solo conservatore).
7. **Clienti che leggono l'IPdA per namespace**: avvisarli del namespace provvisorio
   (VD-P-160).
8. **Controlli**: una pratica sospesa mostra «ferma» e il pulsante «Riprendi il lavoro» solo
   al conservatore; `docs/catalogo/metriche.md` e Prometheus elencano gli stessi allarmi;
   `make verifiche` verde.

## Test
Corsa completa con PostgreSQL reale (`r14_integ` alla testa 056, Siegfried reale):
**4.447 passati, 0 falliti** al primo colpo (3 saltati di conformità XSD in aip-builder);
frontend typecheck, lint, build ed e2e 96 con 2 lavoratori; `make verifiche` verde. Dopo la
revisione, riesecuzione delle suite toccate: gateway 825 (con il test della migrazione 056),
aip-builder 196 (+3 saltati), hashchain 357, notification 332, ciclo-vita 245, libs/shared
430, verumdoc_rules 241, sincro 18, tools/docs 30, verificatore 62, sdk 93, `tests/integrity`
10, frontend typecheck, lint, build ed **e2e 102** (6 nuovi in `frontend/e2e/r14.spec.ts`, 390 e
1280 px, chiaro e scuro, contrasto AA), `make verifiche` verde: tutte verdi.

## Cosa servirà dall'esterno

Nulla di nuovo: il risigillo degli IPdA storici si attiva con il prestatore qualificato
(VD-P-05); gli allarmi vanno caricati nello staging (giorno 1, §14-ter).
