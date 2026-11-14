# CHANGELOG — R07 — Parametri in console (30/10/2026)

Primo rilascio della roadmap R07–R13. Prerequisito: R06 applicato (migrazioni fino a `20261030_045`, gruppo `verumdoc_sessioni_portale`, sessione per inattività). Valori prima fissi nel codice o nelle variabili d'ambiente diventano impostazioni del motore delle regole: livelli conservatore → ente, bozza, anteprima, applicazione, storico e decorrenza futura. Decisioni D09–D11, D13–D19, D25, D28, D29, D31 (opzioni consigliate adottate il 30/10/2026).

Contenuto:
- **Quote (D13)**: limiti dei piani modificabili dal conservatore (`QUOTA_PIANO_<PIANO>_*`, valori R03 come modello; le variabili `QUOTE_*` restano il valore da ambiente), deroga per ente come prima; avvisi automatici all'80% e al 100% (evento `quota.soglia_raggiunta` + e-mail al referente, una volta per giorno e soglia, con il piano suggerito); oltre la quota `QUOTA_SUPERAMENTO = RALLENTA` (un versamento ogni `QUOTA_RALLENTAMENTO_SECONDI`) invece del 429 fino a mezzanotte, **solo se il conservatore lo consente** (valore generale o limite per l'ente, vedi W1).
- **Sessione (D14)**: inattività per ruolo in console (15 minuti per SUPER_ADMIN, RSC_CONSERVATORE e ADMIN_TENANT, 30 per gli altri; fra 5 e 120); l'ente può solo ridurre; avviso «Resta collegato» 2 minuti prima (al più a metà del limite).
- **Staging dei rifiutati (D15)**: `STAGING_SCARTATI_GIORNI` per ente (90, fra 30 e 180, l'ente può solo ridurre), stessa query nel gateway e in dashboard-api; l'avviso 7 giorni prima porta il collegamento «Rielabora» (ora ai versamenti rifiutati dell'ente) ed è anche un'e-mail.
- **«Rielabora» dall'ente** (richiesta del committente): ADMIN_TENANT e OPERATORE rielaborano da sé i propri versamenti rifiutati per un errore del servizio (messaggi della verifica e della costruzione del PdA nelle code di scarto), con anteprima, motivo e conferma: `POST /api/dashboard/versamenti/{pdv_id}/rielabora/anteprima` e `…/applica`, pulsante «Rielabora» nei «Pacchetti in arrivo» rifiutati. Isolamento per ente (filtro e RLS); il conservatore la mantiene, anche nella coda di scarto.
- **Webhook (D16)**: la 443 sempre; altre porte solo dal conservatore per singolo ente (`WEBHOOK_PORTE_AGGIUNTIVE`, con motivo), controllate alla creazione e a ogni consegna; coerenza con la NetworkPolicy in `docs/integrazione/webhook.md`.
- **Idempotenza (D17)**: fine della transizione per ente riservata al conservatore; avviso settimanale `idempotenza.chiave_omessa` agli enti che versano senza Idempotency-Key.
- **Restituzione (D18)**: validità dei PdD ZIP `PDD_VALIDITA_ORE` (72 h, fino a 720) per ente, l'ente può solo ridurre; `POST /api/v2/distribution/pdd/{id}/rigenera` e «Rigenera se scaduto» in console.
- **Audit (D19)**: periodo dell'export nel WORM `AUDIT_EXPORT_PERIODO_MINUTI` (15, fra 5 e 60), solo conservatore.
- **Modifiche programmate (D28)**: ogni impostazione si applica anche da una data futura (bozza PROGRAMMATA, al più entro 366 giorni); il giorno prima il lavoro periodico di onboarding ricalcola anteprima e impatto e avvisa l'autore per e-mail; alla decorrenza applica con i permessi attuali dell'autore (altrimenti torna aperta con il motivo e un'altra e-mail).
- **Console (D29, D31)**: ente in testata per i ruoli globali con «Tutti gli enti»; preferenze personali sul server (tema, densità, pagina iniziale solo interna, colonne visibili): `GET/PUT /api/v1/onboarding/preferenze`.
- **Natura dell'ente (D25)**: motivo obbligatorio solo per PRIVATO; per la PA, se manca, l'audit registra «Codice IPA dichiarato … (non verificato sull'indice IPA)».
- **Motore delle regole**: verso «più sicuro» dichiarato per ogni parametro (`verso_sicuro`) e **limite del conservatore per ente** distinto dal valore (`LIMITE.<codice>`, voce generata dal registro, riservata al conservatore), vedi W1.
- **Pulizia**: script `tools/manutenzione/pulisci_cronologia_git.py` (D09, da eseguire una volta, vedi README di rilascio); asyncssh accettata in `THIRD_PARTY.md` (D10); motore OPA tolto (D11, VD-C-034: `libs/policy-engine` nell'elenco di `tools/pulizia/pulisci_progetto.py`, servizio `opa` fuori dal compose, `OPA_URL` e `make opa-test` tolti).

Esito sul registro (`docs/registro/criticita.md`): chiusa VD-C-034; riserve ridotte per VD-C-004 (script pronto, da eseguire) e VD-C-127 (inattività per ruolo in console). Totale 142: 25 aperte, 7 parzialmente chiuse, 24 chiuse con riserve, 86 chiuse.

Fonti: rapporto di sviluppo R07 e revisione indipendente («approvabile con correzioni prima della produzione»: 2 rilievi medi, 6 bassi), tutti corretti prima del rilascio con test permanenti che falliscono senza la correzione; 8 prove di mutazione nello sviluppo e 11 nella correzione (W1, W2), tutte uccise.

---

## Revisione indipendente: rilievi e correzioni

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| **W1** «Solo ridurre» valeva solo per la sessione: l'ADMIN_TENANT alzava validità del PdD (720 h), staging (180 g) e sceglieva RALLENTA oltre il valore del conservatore, che non poteva imporre un tetto per l'ente | Media | Motore: `verso_sicuro` per ogni voce (MINORE, MAGGIORE, ORDINE_SCELTE, SOTTOINSIEME) e distinzione fra **valore** e **limite**: il conservatore fissa per un ente `LIMITE.<codice>` (riservato, l'ente non lo scrive né lo annulla). Tetto = limite per l'ente o, senza, valore del conservatore; la console blocca un valore oltre il tetto e la risoluzione lo riporta comunque al tetto (anche per righe salvate prima). Voci rese «solo nel verso sicuro»: inattività, `STAGING_SCARTATI_GIORNI`, `PDD_VALIDITA_ORE`, `QUOTA_SUPERAMENTO` (RIFIUTA prima di RALLENTA); le altre sono riservate. Stessa regola nel frammento SQL dello staging e nella lettura delle quote | `libs/verumdoc_rules/tests/test_r07_revisione_tetti.py`, `gateway/tests/test_r04_staging_scartati.py::test_scadenza_staging_configurabile`, `gateway/tests/test_r07_quote_piani.py`, `distribution/tests/test_r07_pdd_validita.py` |
| **W2** L'ADMIN_TENANT annullava le modifiche PROGRAMMATE dal conservatore sul suo ente, anche su voci riservate | Media | `annulla_bozza` (APERTA e PROGRAMMATA): bozza creata o programmata da un utente del conservatore → 403 `CFG-403`; bozza di un altro autore con voci che l'attore non può modificare → 403 con l'elenco delle voci; la propria bozza resta annullabile | `onboarding/tests/test_r07_revisione.py::test_w2_*` |
| **W3** Il risolutore applicava righe a un livello non ammesso (es. riga ENTE di un piano) | Bassa | `libs/shared/parametri.risolvi` le ignora con errore nel log e metrica `verumdoc_parametri_scartati_total{codice,livello,motivo}`; vale anche per la lettura delle quote | `libs/shared/tests/test_r07_revisione_parametri.py` |
| **W4** E-mail del giorno prima e di «non applicata» non inviate agli autori del conservatore | Bassa | Non riprodotto: gli utenti del conservatore hanno l'ente del conservatore (`users.tenant_id` obbligatorio) e l'e-mail parte, intestata a quell'ente; test di regressione. Un autore che non si può avvisare (senza utente o senza indirizzo) è segnalato da log e metrica `verumdoc_programmate_avvisi_non_inviati_total`; decorrenza al più 366 giorni | `onboarding/tests/test_r07_revisione.py::test_w4_*`, `::test_programmata_al_piu_entro_un_anno` |
| **W5** Inattività di 1 minuto con avviso di 2 | Bassa | Minimo 5 minuti nel motore; avviso al più a metà del limite | `test_r07_revisione_tetti.py::test_w5_*`, `gateway/tests/test_r07_revisione_avviso.py` |
| **W6** `pagina_iniziale` ammetteva «//nome» | Bassa | Solo percorsi interni (`^/(?:[a-z0-9-]+/?)*$`: «/» singolo, nessun «//», schema o «\\»), stesso controllo nella console prima del reindirizzamento | `onboarding/tests/test_r07_revisione.py::test_w6_*` |
| **W7** Script della cronologia Git con `--force`, copia non verificata, percorso libero | Bassa | Nessun `--force`: riscrive in un clone nuovo `--mirror --no-local` (vale il controllo di filter-repo), il repository di lavoro non cambia; `git bundle verify`; conferma digitata `RISCRIVI`; `--percorso` solo il file di chiave noto o confermato riscrivendolo (niente cartelle né caratteri jolly); avviso a cancellare la copia | `tools/manutenzione/test_pulisci_cronologia_git.py` |
| **W8** Audit «Ente presente nell'indice IPA» senza verifica | Bassa | «Codice IPA dichiarato … (non verificato sull'indice IPA)», `origine_motivo = "dichiarato_ipa"` | `onboarding/tests/test_r07_parametri_console.py::test_natura_pa_precompilata_dal_codice_ipa`, `frontend/e2e/r07.spec.ts` |

## Cambi incompatibili
- **ADMIN_TENANT: inattività da 30 a 15 minuti** (D14). Gli altri ruoli come in R06 (15 per SUPER_ADMIN e RSC, 30 per OPERATORE e VIEWER); il valore si legge all'accesso.
- **Periodo dell'audit nel WORM da 60 a 15 minuti** (D19): al cambio i segmenti restano contigui; quattro volte gli oggetti di export.
- **Variabili tolte**: `SESSIONE_INATTIVITA_MINUTI`, `SESSIONE_INATTIVITA_PRIVILEGIATI_MINUTI`, `SESSIONE_RUOLI_PRIVILEGIATI` (gateway), `STAGING_SCARTATI_GIORNI` (gateway, dashboard-api), `AUDIT_EXPORT_PERIODO_SECONDI` (hashchain), `OPA_URL` (gateway, validation, onboarding). I valori si impostano in console; le variabili rimaste nei ConfigMap sono ignorate.
- **`PDD_ZIP_VALIDITA_ORE` diventa il tetto** (720 h): la validità è `PDD_VALIDITA_ORE` dell'ente (72 h); il ciclo di vita del bucket dei PdD (Terraform) è a **31 giorni**.
- **Due topic Kafka nuovi**: `quota.soglia_raggiunta`, `idempotenza.chiave_omessa` (crearli prima del rilascio: `quota.soglia_raggiunta` scritto dal gateway, `idempotenza.chiave_omessa` da onboarding, entrambi letti da notification; ACL in `infra/kafka/topics.yaml`).
- **Valori dell'ente oltre il tetto** (W1): un valore ENTE più alto del valore del conservatore per `PDD_VALIDITA_ORE`, `STAGING_SCARTATI_GIORNI`, inattività, e `QUOTA_SUPERAMENTO = RALLENTA` senza consenso del conservatore, valgono come il tetto. Per concedere di più a un ente il conservatore imposta `LIMITE.<codice>` per quell'ente.
- **Annullamento delle bozze** (W2): l'ADMIN_TENANT non annulla più le bozze del conservatore (403 `CFG-403`).
- **Accesso al portale**: dopo il login si va alla pagina iniziale delle preferenze (predefinita la panoramica).
- Collegamento «Rielabora» dell'avviso di scadenza dello staging: ora `/versamenti?stato=ERRORE&pdv_id=…` (prima la coda di scarto del conservatore).
- Codici d'errore nuovi: `CFG-424`, `DST-409`, `ONB-412`.

## Variabili, Secret, database
| Dove | Cosa | Note |
|---|---|---|
| onboarding | `CONFIGURAZIONE_LAVORATORE_ATTIVO` (true), `CONFIGURAZIONE_INTERVALLO_SECONDI` (300) | Lavoro periodico delle modifiche programmate e dell'avviso dell'idempotenza; gira in ogni replica (SKIP LOCKED) |
| Kafka | topic `quota.soglia_raggiunta`, `idempotenza.chiave_omessa` | `infra/kafka/topics.yaml`, `make kafka-topics` |
| Storage | ciclo di vita del bucket dei PdD a 31 giorni (Terraform) | Tetto di validità 720 h |
| ConfigMap | togliere le variabili elencate sopra | Ignorate se restano |

## Migrazione `20261030_046` (head, dopo la 045)
- Bozze di configurazione: stato `PROGRAMMATA` e colonne della programmazione (`valido_dal`, `programmata_il`, `programmata_da`, anteprima del giorno prima, avviso all'autore).
- Tabelle `preferenze_utente` (per utente, senza `tenant_id`) e `avvisi_automatici` (RLS per ente, vincolo unico per giorno e soglia).
- Indice parziale dei versamenti senza Idempotency-Key (in linea con `CONCURRENTLY`, nello script offline senza).
- Riprendibile, `lock_timeout` sulle DDL. Downgrade rifiutato se esistono bozze programmate.

## Scelte da confermare
VD-P-57…69 in `docs/registro/decisioni.md`; quelle coerenti con i criteri del committente (automatizzare, parametrizzare, semplificare) sono marcate «in linea con D01–D32».

## Residui
- **VD-C-004** chiusa con riserve: lo script di pulizia della cronologia è pronto e provato, la riscrittura della storia va eseguita una volta d'accordo con chi ha una copia (README di rilascio).
- «Rielabora» dall'ente vale per i rifiuti dovuti a un errore del servizio (messaggi nelle code di scarto); un rifiuto per i dati versati si corregge versando di nuovo.
- Righe ENTE salvate prima di R07 oltre il tetto restano nel database (valgono come il tetto): la console le mostra al livello dell'ente.
- Già aperti: VD-C-141, VD-C-142 (R06), firma qualificata (VD-C-068), verifica delle firme (VD-C-073) e gli altri del registro.
