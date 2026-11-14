# CHANGELOG — R12a — Criticità aperte e preparazione allo staging (31/10/2026)

Rilascio intermedio prima di R12 (prova su infrastruttura reale), che richiede un account cloud di staging non ancora disponibile. Prerequisito: R11 applicato (migrazioni fino a `20261103_050`). Migrazioni nuove: `20261104_051`, `20261105_052` (riprendibili, `lock_timeout`, indici `CONCURRENTLY` in autocommit e senza `CONCURRENTLY` nello script offline).

> **Avviso operativo**
> - **Chiavi della posta prima del deploy** (VD-C-152): `POSTA_CHIAVE_PRIVATA` (notification) e `POSTA_CHIAVE_PUBBLICA` (onboarding), coppia X25519. Senza, in produzione i due servizi non partono.
> - **Scarto**: autorizzazione e nulla osta caricati da ora devono riportare l'identificativo della pratica nel testo firmato; quelli già caricati in pratiche aperte passano con avviso (VD-P-115).
> - **Verifica pyHanko** in un processo separato per impostazione predefinita (`FIRME_PROCESSO_SEPARATO`).
> - **Ruolo `verumdoc_registro_worm`** (VD-C-148, revisione): le tabelle della migrazione 052 sono concesse solo a questo gruppo; va dato al login della hashchain PRIMA del Chart, altrimenti il passo di rinnovo fallisce per diritti.

## Prima del deploy
Da fare in quest'ordine, su staging e poi su produzione (dettagli e comandi in `docs/architettura/giorno1_staging.md` e `README_RILASCIO.md`):
1. **Chiavi della posta** (VD-C-152): generare la coppia X25519 (checklist §5.2) e mettere `POSTA_CHIAVE_PRIVATA` in `notification-secrets` e `POSTA_CHIAVE_PUBBLICA` in `onboarding-secrets` (con `POSTA_CHIAVE_PUBBLICA_ID`/`POSTA_CHIAVE_PRIVATA_ID` se si usa la rotazione). Il file della chiave privata si distrugge (`shred -u`) dopo averla copiata nel Secret.
2. **Ruolo del registro** (VD-C-148): `GRANT verumdoc_registro_worm TO <login della hashchain>;` al solo login della hashchain (come in `infra/postgres/ruoli_sviluppo.sql`; checklist §7.2). Nessun altro login riceve il gruppo.
3. **Migrazioni** con il Job `gateway-migrazioni`: `20261104_051` (elenco di transizione `ciclo_vita_transizione_r12`, indice `idx_pratiche_allegati_impronta`, collegamenti d'invito già inviati o pubblicati tolti) e `20261105_052` (`registro_worm_oggetti`, `registro_worm_censimento`, `ciclo_vita_periodico.generazione`). Controllo: `alembic current` = `20261105_052` (checklist §6.3).
4. **Chart** e **regole Prometheus** `infra/observability/regole/verumdoc-r12.yml` (checklist §3.3 e §6.3).
5. **Controlli dopo il deploy** (checklist §7): un invito di prova arriva con il collegamento e nessun `#invito=` in chiaro resta in `outbox_eventi` e `notifications` dopo un giro del lavoratore di onboarding (§7.2); `verumdoc_catena_worm_rinnovo_in_errore` = 0 e `registro_worm_censimento.completato_il` valorizzato (§7.4); nessun passo periodico fermo.
6. **Enti con scarti in corso**: avvisarli che i nuovi documenti autorizzativi devono riportare l'identificativo della pratica (VD-P-115).

## Per chi usa la console
- **Pratica di scarto**: accanto al caricamento dell'autorizzazione e del nulla osta la console mostra l'identificativo della pratica da riportare nel documento firmato. Un documento di un'altra pratica (o lo stesso documento già usato) è rifiutato.
- **Inviti**: nessun cambiamento visibile; il collegamento arriva per e-mail come prima.
- **Annullamento e rettifica** chiesti con un ruolo personalizzato: se il permesso viene tolto prima che il lavoratore applichi la richiesta, la pratica si chiude RESPINTA con il motivo.

## Per chi gestisce il servizio
- Allarmi nuovi (`infra/observability/regole/verumdoc-r12.yml`): `PassoPeriodicoFermo` (pagina), `PassoPeriodicoSingoloInErrore`, `RegistroWormRinnovoInErrore` (pagina), `RegistroWormInScadenza`.
- Runbook: `docs/runbooks/disaster_recovery.md` (failover, point-in-time su cluster nuovo, ricostruzione del registro dal WORM, obiettivi RPO/RTO da confermare).
- Checklist per l'account di staging: `docs/architettura/giorno1_staging.md`.
- Impostazioni nuove: `POSTA_CHIAVE_PUBBLICA[_ID]` (onboarding), `POSTA_CHIAVE_PRIVATA[_ID]`, `POSTA_CHIAVI_PRIVATE_PRECEDENTI` (notification), `FIRME_PROCESSO_SEPARATO`, `FIRME_MEMORIA_MAX_MB` (servizi che verificano firme), `RINNOVO_WORM_ATTIVO`, `RINNOVO_WORM_SECONDI`, `RINNOVO_WORM_LOTTO`, `CENSIMENTO_WORM_GIORNI` (hashchain; predefiniti adatti).

## Contenuto

### A. Criticità
- **VD-C-145** (chiusa con riserve): `firme.problemi_riferimento_pratica` al caricamento e nel lavoratore; riuso per impronta (`ALLEGATO_GIA_USATO`, revisione: solo tipi autorizzativi, solo pratiche di scarto e solo contro il documento caricato PRIMA; un ALTRO con l'impronta di un documento autorizzativo è rifiutato dall'API); elenco di transizione `ciclo_vita_transizione_r12` scritto solo dalla migrazione 051.
- **VD-C-152** (chiusa con riserve): `libs/shared/security/sigillo.py` (X25519 + HKDF-SHA256 + AES-256-GCM, contesto ente/oggetto/destinatario); onboarding sigilla, notification apre all'invio; minimizzazione dopo l'invio; migrazione 051 per i collegamenti già inviati o pubblicati in chiaro, ripetuta a ogni giro dal lavoratore di onboarding (`minimizza_inviti_in_chiaro`, revisione) per quelli scritti dai pod precedenti durante il deploy.
- **VD-C-148** (chiusa): `app/registro/rinnovo_worm.py` della hashchain (elenco, censimento, rinnovo mai in riduzione con due guardie, Z4, Z5, metriche); `worm.estendi_retention` unica per ciclo-vita e hashchain; migrazione 052, con le due tabelle concesse al solo gruppo `verumdoc_registro_worm` (revisione).
- **VD-C-155** (chiusa): permessi attuali del ruolo personalizzato nel lavoratore (`_difetti_permesso_attuale`), difetto «stato cambiato».
- **VD-C-151** (chiusa): fencing token `ciclo_vita_periodico.generazione` verificato in ogni scrittura dei passi periodici (FOR SHARE fino al commit).
- **VD-C-147** (chiusa): verifica pyHanko in un processo forkserver terminato oltre il tempo o all'annullamento, limite di memoria.
- **VD-C-146** (chiusa): letture parziali di validation legate a VersionId o ETag del passo 5.
- **VD-C-144** (chiusa con riserve): pinning dell'indirizzo in `RecuperoProtetto.java` (client HTTP/1.1 sul socket controllato), provato con il solo JDK; revisione: scadenza complessiva controllata dentro `riga()` con timeout di lettura pari al tempo residuo, IPv6 senza SNI e senza doppie parentesi.
- **VD-C-150** (chiusa con riserve): metriche per passo e allarme del passo fermo.
- **VD-C-149** (chiusa con riserve): indice di ricerca rimesso in coda e fascicoli scollegati nella transazione della minimizzazione.
- Restano aperte con nota: **VD-C-125** (VD-P-117), **VD-C-156** (R12b), **VD-C-032** (file nuovi puliti), **VD-C-133** (invariata). Nuove: **VD-C-160**, **VD-C-161**.

### B. Preparazione R12
- **VD-C-131**: oracolo Go eseguito (modulo `golang.org/x/mod` v0.20.0 clonato da GitHub) e aggiunto alla CI.
- **VD-C-038/081**: `tofu validate` di tutti i moduli e ambienti; chart reso e validato con kubeconform (CRD comprese), kustomize; helm vero non disponibile (passo 6.2 del giorno 1).
- **VD-C-110**: `infra/terraform/environments/testimone` (account separato, precondizioni sull'account) validato.
- k6: `tools/carico/k6/giornata_milione.js` (curva oraria, compressione, campione fino a CONSERVATO, multipart con più documenti) e modalità `giornata` del workflow «Carico»; provato contro un'API finta.
- Runbook di disaster recovery con la prova locale della ricostruzione del registro (13 test).

### Interfaccia
- `frontend/src/pages/PraticaPage.tsx`: identificativo della pratica accanto al caricamento dei documenti dello scarto.

### Strumenti ottenuti in sviluppo
k6 0.54.0 (release GitHub, SHA-256 verificato); OpenTofu 1.8.5, kubeconform 0.6.7, kustomize 5.4.3 (scaricati in precedenza); modulo Go `golang.org/x/mod` v0.20.0 (GitHub, commit fissato). Non ottenibili: helm (get.helm.sh bloccato), kubectl (dl.k8s.io bloccato), terraform (releases.hashicorp.com bloccato), proxy dei moduli Go, Maven Central.

## Revisione di sicurezza: rilievi e correzioni
Revisione fatta sopra lo sviluppo (1 rilievo medio, 3 bassi), tutti corretti prima del rilascio con 4 mutazioni uccise; nessuna voce nuova oltre 160–161.

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| Il controllo di riuso per impronta (VD-C-145) valeva per ogni allegato e in entrambi i versi: una copia caricata dopo, o un ALTRO con la stessa impronta, bloccava la pratica originale | Medio | Riuso limitato ai tipi autorizzativi, alle pratiche di scarto e al documento caricato prima; l'API rifiuta un ALTRO con l'impronta di un documento autorizzativo | `services/ciclo-vita/tests/test_r12a_scarto_legato.py::test_revisione_copia_successiva_non_blocca_l_originale` |
| Le tabelle della migrazione 052 (`registro_worm_oggetti`, `registro_worm_censimento`) erano concesse al gruppo comune dei servizi | Basso | Concesse al solo gruppo `verumdoc_registro_worm`, dato al login della hashchain (`infra/postgres/ruoli_sviluppo.sql`, `libs/shared/db/ruolo_prova.py`) | `services/hashchain/tests/test_r12a_rinnovo_worm_registro.py::test_revisione_diritti_solo_al_gruppo_del_registro` |
| `RecuperoProtetto.java`: la scadenza complessiva si controllava solo fra una riga e l'altra (un server lento su una sola riga la superava); IPv6 con SNI e parentesi doppie | Basso | Scadenza controllata dentro `riga()` con il timeout di lettura pari al tempo residuo; IPv6 senza SNI e senza doppie parentesi | `tools/dev/test_r12a_verifica_firme_pinning.py` (`RecuperoProtettoProva`, 14 controlli) |
| Collegamenti d'invito in chiaro scritti dai pod precedenti durante il deploy, dopo la migrazione 051 | Basso | Minimizzazione periodica nel lavoratore di onboarding (`minimizza_inviti_in_chiaro`), annotata nella checklist §7.2 | `services/onboarding/tests/test_r10_ruoli_inviti.py::test_vdc152_collegamenti_in_chiaro_rimasti_dopo_il_deploy` |

## Criticità
Totale 161: 19 aperte, 12 parzialmente chiuse, 35 chiuse con riserve, 95 chiuse (R11: 159; 28, 12, 29, 90).
- Chiuse: VD-C-146, 147, 148, 151, 155.
- Chiuse con riserve: VD-C-131, 144, 145 (residuo 160), 149, 150, 152 (residuo 161).
- Parzialmente chiusa: VD-C-110 (Terraform del testimone validato; apply e deploy in R12).
- Restano aperte con nota: VD-C-125 (VD-P-117), VD-C-156 (R12b); note su VD-C-032, 038, 081, 133.
- Nuove: VD-C-160 (autorizzazione del riversamento non legata), VD-C-161 (chiave privata della posta nel processo esposto di notification).

## Migrazioni `20261104_051` e `20261105_052`
Riprendibili, `lock_timeout`, indici `CONCURRENTLY` in autocommit e senza `CONCURRENTLY` nello script offline. 051: elenco di transizione delle pratiche di scarto aperte (scritto solo dalla migrazione), indice delle impronte degli allegati, collegamenti d'invito tolti dalle notifiche già inviate e dagli eventi già pubblicati. 052: elenco e censimento degli oggetti del registro (solo `verumdoc_registro_worm`), colonna `generazione` per il fencing dei passi periodici.

## File eliminati o spostati
Nessuno: R12a non rende superfluo alcun file (la pulizia toglie solo cache e foto di prova).

## Scelte da confermare
VD-P-115…122 (`docs/registro/decisioni.md`); VD-P-117 è ancora da decidere (R13).

## Residui
- Controllo «nessun test saltato» della CI: fallirebbe per i salti voluti dello schema XSD (checklist §12.2).
- `verifica-firme` mai costruito (VD-C-143); helm, kubectl e terraform veri non provati (checklist §6.2).
- VD-C-125, VD-C-156 (R12b), VD-C-160, VD-C-161.

## Test
Nuovi: `ciclo-vita/tests/test_r12a_scarto_legato.py`, `test_r12a_permessi_lavoratore.py`, `test_r12a_fencing_periodico.py`; `notification/tests/test_r12_posta_sigillata.py`; `hashchain/tests/test_r12a_rinnovo_worm_registro.py`; `validation/tests/test_r12a_letture_legate.py`; `libs/shared/firme/tests/test_r12a_processo.py`; `tools/dev/test_r12a_verifica_firme_pinning.py`; `tools/carico/test_k6_giornata.py`; onboarding `test_r10_ruoli_inviti.py::test_vdc152_*`. Prove di mutazione sui punti di sicurezza: 26 (25 uccise, una equivalente), comprese le 4 della revisione.

Corsa completa (PostgreSQL 16 reale): frontend typecheck, lint, build ed e2e (53) verdi, `make verifiche` 0, oracolo Go eseguito; 10 fallimenti attesi (testa delle migrazioni, chiave della posta), corretti. Dopo la revisione, suite toccate rieseguite su un database nuovo: gateway 786, notification 280, ciclo-vita 221, hashchain 333, onboarding 282, tools/dev 19, libs/shared 362, `make verifiche` 0. **Totale 4.053 passati, 0 falliti** (R11: 4.014). Dettaglio: `STATO_PROGETTO.md` §4.
