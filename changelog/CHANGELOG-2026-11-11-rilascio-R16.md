# CHANGELOG — R16 — Chiusura dei residui interni (01/11/2026)

Lavoro senza risorse esterne (nessun prestatore, nessun account): firma personale con OTP
del responsabile della conservazione, federazione OpenID completa, consegna PEC senza
doppioni dopo un arresto, limite delle prove condiviso fra le repliche, revoca delle sessioni
ai cambi di metodi e concedibili, tracce distribuite, condizioni fra campi nei profili dei
metadati, lint a zero.

- **Base**: R14 (`changelog/CHANGELOG-2026-11-10-rilascio-R14.md`), commit `d56979f`.
  Migrazioni fino a `20261109_056`; migrazione nuova `20261110_057_residui` (all'unione con R15,
  sviluppato in parallelo, resta 057; quella di R15 è rinumerata `20261111_058_documentazione`).
- **Criticità**: chiuse VD-C-032 (ruff 491 → 0) e 171; chiuse con riserve VD-C-153, 158, 166,
  168, 173, 176; nuove VD-C-178…180 (residui; all'unione le voci di R15 sono state rinumerate 181–183).
  180 registrate: 18 aperte, 6 parzialmente chiuse, 51 chiuse con riserve, 105 chiuse.
- **Scelte da confermare** (`docs/registro/decisioni.md`, «Scelte di R16 da confermare»):
  VD-P-165 firma personale del responsabile (realizza VD-P-140); VD-P-166 limiti del servizio
  di firma in Redis; VD-P-167 federazione completa; VD-P-168 PEC senza doppioni (supera
  VD-P-149); VD-P-169 revoca delle sessioni (aggiorna VD-P-97); VD-P-170 tracce distribuite;
  VD-P-171 regole incrociate dei profili; VD-P-172 lint a zero (aggiorna VD-P-163). R15, in parallelo,
  numera le sue dal 185 in poi.
- **Documentazione**: `docs/architettura/firma_qualificata.md` §9 (firma personale),
  `docs/architettura/accesso_spid_cie.md` §3.1 (federazione completa),
  `docs/integrazione/pec.md` §2.1 (nessun doppione), `docs/architettura/sicurezza.md` (revoca
  ai cambi di metodi e concedibili, tabelle fuori dalla RLS), `docs/architettura/infrastruttura.md`
  §8.1 (tracce), `docs/conformita/riscontro_allegato5.md` (v3 del documento informatico),
  `infra/k8s/secrets/README.md` (R16), cataloghi generati, REGOLE §31.
- **Console**: pagina nuova «Firme del responsabile» (Conformità): lettura del documento,
  «Chiedi l'OTP», firma con OTP e PIN, annullamento con motivo, buste scaricabili; il manuale
  rimanda alla pagina. Sistema grafico R12c, controllo a 390 e 1280 px in chiaro e scuro con
  il dialogo di firma aperto (`frontend/e2e/r16.spec.ts`).

> **Avviso operativo**
> - **`signature-secrets`: chiave nuova `REDIS_URL`** (VD-C-168). In produzione il servizio
>   di firma **non parte** senza; la NetworkPolicy generata apre l'uscita verso Redis.
> - **Gruppo del database (migrazione 057)**: `verumdoc_firme_responsabile` al SOLO login di
>   dashboard-api. Senza, la pagina «Firme del responsabile» risponde 503.
> - **Sessioni revocate al cambio dei metodi d'accesso o dei concedibili** (VD-C-153): dopo il
>   deploy, ogni modifica di `ACCESSO_METODI_AMMESSI` (anche programmata) chiude al prossimo
>   uso le sessioni dell'ambito (del conservatore: tutte); di `RUOLI_PERMESSI_CONCEDIBILI`
>   quelle con un ruolo personalizzato. Le sessioni aperte prima del deploy restano fino alla
>   loro scadenza (al più un'ora). Avvisare prima di cambiare i metodi del conservatore.
> - **PEC** (VD-C-171): Message-ID nuovo formato `<pec.<id>@<dominio della casella>>` per i
>   messaggi prelevati dopo il deploy (quelli già prelevati tengono il loro). Un messaggio
>   rimasto in invio dopo un arresto si rimanda solo dopo una lettura riuscita della casella.
> - **Tracce** (VD-C-176): spente; nello stack locale `VD_TRACCE=true docker compose …` con
>   il profilo di osservabilità (Tempo riceve OTLP/HTTP sulla 4318).
> - **Profili**: nuova v3 del documento informatico ad adozione esplicita
>   (`PROFILO_TIPOLOGIA_VERSIONE = 3`): nessuna classe cambia da sola.
> - **Lint**: `ruff check .` senza errori; le migrazioni già rilasciate sono escluse per
>   configurazione dalle regole di forma (non si toccano).

## VD-C-166 — Firma personale con OTP del responsabile

- `libs/shared/firme_responsabile.py`: punto d'aggancio generico «documento da firmare dal
  responsabile» (`accoda_documento`, qualunque `tipo`, idempotente per tipo e impronta).
- Migrazione 057: `credenziali_firma_personali` (VD-P-140: `credentialID` e impronta attesa
  del certificato presso un fornitore CSC del registro, nessun segreto, una attiva per
  persona) e `firme_responsabile` (DA_FIRMARE → FIRMATO → ARCHIVIATO | RESPINTO; ANNULLATO)
  con trigger su INSERT, UPDATE, DELETE, TRUNCATE e diritti per colonna.
- Servizio di firma: `/api/v1/signature/personale/otp` e `/firma` (solo token utente di un
  ruolo fisso del conservatore, credenziale per `user_id` del token, adattatore che firma
  una volta e non ritenta l'OTP, busta verificata, certificato atteso controllato due volte,
  limite per persona); simulatore CSC con credenziali personali e OTP online monouso.
- dashboard-api: `/api/dashboard/firme-responsabile` (elenco, accodamento generico, manuale e
  piano con il contenuto canonico del sigillo, contenuto, OTP, firma, annullamento, busta),
  audit di ogni richiesta, tentativo, firma e annullamento.
- onboarding: credenziali personali (`/api/v1/onboarding/firma/credenziali-personali`) con
  permesso mai delegabile, versione attesa e audit.
- hashchain: riverifica (contenuto, credenziale, `/verifica`) e archiviazione nel WORM con
  l'impronta nella chiave, idempotente, con fencing; RESPINTO con allarme
  `FirmeResponsabileRespinte` (`infra/observability/regole/verumdoc-r16.yml`).

## VD-C-173 — Federazione OpenID completa

`libs/shared/security/oidc_politiche.py` (metadata_policy: operatori, combinazione,
applicazione, `metadata_policy_crit`; constraints) e `oidc_federazione.risolvi` (crit,
vincoli, politiche, trust mark con firma, emittente, delega, stato; scadenze); configurazione
`federazione.trust_marks_richiesti` e `verifica_stato_trust_marks` in console (onboarding) e
nel gateway.

## VD-C-171 — PEC senza doppioni

Message-ID deterministico e riconciliazione con le ricevute prima del reinvio
(`services/notification/app/application/pec.py`).

## VD-C-168 — Limite delle prove condiviso

`libs/shared/security/riserva.py` (INCR, scadenza, confronto e restituzione in uno script
Lua), `services/signature/app/services/limiti.py`, `REDIS_URL` del servizio di firma.

## VD-C-153 — Revoca delle sessioni

Trigger `vd_versioni_accesso` e tabella `versioni_accesso` (migrazione 057), claim `vm`/`vc`
all'emissione, confronto nella query della revoca.

## VD-C-176 — Tracce distribuite

`libs/shared/observability/tracciamento.py` (OTLP/HTTP JSON, W3C Trace Context su HTTP e
Kafka, campionamento, attributi in elenco chiuso), Tempo con il ricevitore HTTP.

## VD-C-158 — Condizioni fra campi nei profili

`RegolaIncrociata` in `libs/verumdoc_rules/profili.py`, `documento_informatico.v3.json`,
impronta registrata; applicate da RGL-106.

## VD-C-032 — Lint a zero

491 → 0 senza cambiare comportamento (righe lunghe con AST invariato, B904, S110 con log,
`noqa` motivati, esenzioni motivate in `pyproject.toml`).

## Corsa completa e mutazioni

Database `r16_integ` nuovo migrato da zero a `20261110_057`. 4.490 test Python: 4.477 passati
al primo colpo, 13 falliti, tutti corretti (gateway 2: catena delle teste e un test R12b che ora
vede la sessione revocata dal cambio dei metodi, effetto voluto di VD-C-153; hashchain 10:
partizione di ottobre di `audit_global` assente perché la corsa cade il 1° del mese, artefatto
di data dell'ambiente; integrity 1: riferimento alla numerazione di R15); rieseguite gateway 832,
hashchain 365, signature 166, integrity 10, tutte verdi. Frontend: typecheck, lint, build verdi,
e2e con 2 lavoratori **109 passati**. `make verifiche` 0. Mutazioni sui punti di sicurezza e
integrità: **40/40 uccise** (firma personale 14, federazione 13, PEC 3, limiti 4, revoca 6).

Consegnato con R15 e R17 nel pacchetto unico R15–R17: «Prima del deploy» cumulativo in
`changelog/CHANGELOG-2026-11-12-rilascio-R17.md` e in `README_RILASCIO.md`. La revisione di
sicurezza di R17 ha corretto anche il campionamento delle tracce con un `traceparent` esterno.
