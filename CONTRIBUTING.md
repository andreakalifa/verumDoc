# Contribuire a VerumDoc

Grazie per l'interesse. VerumDoc è un sistema di conservazione digitale: ogni modifica può toccare prove giuridiche che devono restare valide per decenni, quindi chiediamo cura, test e motivazioni scritte. Le regole complete sono in [`REGOLE_SVILUPPO.md`](REGOLE_SVILUPPO.md); qui trovi l'essenziale.

La documentazione e il codice di dominio sono in **italiano** (i concetti normativi italiani non si traducono); i concetti tecnici generici possono restare in inglese. Issue e pull request in italiano o in inglese.

## 1. Prima di iniziare

- **Vulnerabilità**: non aprire una issue; segui [`SECURITY.md`](SECURITY.md).
- **Bug e proposte**: apri una issue con il modulo adatto (`.github/ISSUE_TEMPLATE/`). Per una modifica non piccola, discuti prima la proposta in una issue: le scelte che interpretano la norma, cambiano permessi, schema del database o formati di scambio sono **decisioni** da registrare (`docs/registro/decisioni.md`, `REGOLE_SVILUPPO.md` §2.3), non semplici correzioni.
- Leggi `STATO_PROGETTO.md` (stato per area) e il registro delle criticità (`docs/registro/criticita.md`): molte idee sono già registrate.

## 2. Ambiente di sviluppo

```bash
python tools/dev/genera_segreti_dev.py        # chiavi di sviluppo locali, mai tracciate
docker compose up -d --build                  # servizi, PostgreSQL, Kafka, storage S3 di sviluppo
make venv-all                                 # un ambiente Python per servizio
cd frontend && npm ci && npm run dev          # console su http://localhost:5173
```

Requisiti: Python 3.12, Node 20, Docker. Dettagli e porte nel [`README.md`](README.md).

## 3. Stile

- **Python**: `ruff check .` senza errori (configurazione in `pyproject.toml`), tipi annotati, nessun `print` fuori dagli strumenti da riga di comando, errori dal catalogo (`libs/shared/errori/catalogo.yaml`, mai un 500 generico: §18).
- **TypeScript**: `npm run typecheck` e `npm run lint` puliti; testi dell'interfaccia in italiano; sistema grafico in `frontend/DESIGN.md`.
- **Nomi**: convenzioni del §30 (servizi kebab-case, tabelle e campi snake_case, endpoint versionati, codici d'errore con prefisso).
- **Nessun valore normativo nel codice**: regole, termini, formati e profili sono dati versionati (§8).
- **Intestazione SPDX** in ogni file sorgente nuovo (R18):

  ```python
  # SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
  # SPDX-License-Identifier: EUPL-1.2
  ```

  `python tools/opensource/controlla_pubblicazione.py --applica-intestazioni` la aggiunge ai file che non l'hanno.
- **Nessun segreto** nel repository, nemmeno di prova: le chiavi dei test si generano durante l'esecuzione; i dati di prova sono fittizi (domini `.example`, nomi inventati).

## 4. Test

Ogni modifica arriva con i suoi test, nello stesso commit (§23): test che **eseguono** il codice, non che cercano stringhe nel sorgente.

```bash
VD_TEST_DATABASE_URL=postgresql+asyncpg://… make test     # servizi, librerie, strumenti
make verifiche                                           # controlli statici (cataloghi, manifest, documentazione, pubblicazione)
cd frontend && npm run typecheck && npm run lint && npm run build && npm run e2e
```

I test d'integrazione usano un PostgreSQL migrato con Alembic (`services/gateway`) come modello; senza `VD_TEST_DATABASE_URL` sono saltati, e in CI un salto fa fallire la corsa.

Se tocchi un catalogo o un documento generato (`docs/catalogo/`, `docs/manuale/`, manifest in `infra/k8s/`), rigeneralo con lo strumento indicato nell'intestazione del file e includilo nella modifica.

## 5. Commit e firma DCO

- Messaggi in stile **Conventional Commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`…), come nel §4.2.
- Raccomandiamo la firma **DCO** (Developer Certificate of Origin, <https://developercertificate.org/>) su ogni commit:

  ```bash
  git commit -s -m "fix: …"
  ```

  La riga `Signed-off-by: Nome Cognome <email>` dichiara che hai il diritto di inviare il contributo con la licenza del progetto. Con la EUPL-1.2 il contributo è concesso con la **stessa licenza** (art. 6: ogni contributore garantisce di avere i diritti sulle proprie modifiche); la firma ne lascia traccia verificabile. Non chiediamo la cessione dei diritti (nessun CLA).
- Non includere codice copiato da progetti con licenza incompatibile né testi o schemi protetti (per esempio norme UNI a pagamento, documentazione riservata di terzi). Ogni dipendenza nuova va motivata e annotata con licenza verificata in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) (§20.8): niente licenze sconosciute, SSPL, BUSL o simili.

## 6. Pull request e revisione

1. Ramo dal `main` aggiornato, una modifica coesa per pull request.
2. Compila il modulo della pull request: cosa cambia, perché, test eseguiti (con i numeri), documenti aggiornati (§0.2), decisioni toccate.
3. La CI deve essere verde (test con PostgreSQL reale, `make verifiche`, ruff, frontend).
4. Almeno **una revisione approvata** di un manutentore (`MAINTAINERS`); per sicurezza, permessi, catena delle impronte, firme o migrazioni del database servono **due** revisioni, una delle quali di un manutentore dell'area.
5. Le migrazioni Alembic sono solo in avanti nel gateway, mai modificate dopo un rilascio (§16).
6. Chi rivede controlla anche la documentazione: un «✅» richiede il test che lo dimostra (§0.1).

## 7. Codice di comportamento

Partecipando accetti il [codice di comportamento](CODE_OF_CONDUCT.md).
