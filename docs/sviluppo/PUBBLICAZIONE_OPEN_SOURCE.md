# Pubblicazione open source con storia nuova

> R18, decisione VD-P-190 (confermata dal committente il 14/11/2026): VerumDoc si pubblica con licenza **EUPL-1.2** e con una **storia git nuova**, cioè un unico commit iniziale. La cronologia privata **non si pubblica mai**: contiene una chiave JWT di sviluppo compromessa e materiale di lavoro interno. Questa guida è per chi pubblica (PowerShell su Windows; i comandi git sono gli stessi su Linux e macOS).

## 0. Prima di iniziare

1. Il rilascio R18 è applicato e committato nel repository privato (README_RILASCIO.md), sul ramo principale (`master`), e la cartella di lavoro è pulita:

   ```powershell
   cd C:\percorso\del\progetto\verumdoc
   git switch master
   git status --short          # deve essere vuoto
   ```

2. **Termini riservati**: crea il file locale `tools/opensource/pubblicazione.locale.toml` (è in `.gitignore`, non si pubblica) con i termini che non devono comparire nel repository pubblico: nomi e cognomi reali, nomi dell'azienda per cui lavori e dei suoi prodotti, nomi di conservatori e titoli dei loro documenti, nomi utente e percorsi del tuo computer. Esempio di struttura (i valori sono inventati):

   ```toml
   [riferimenti]
   modelli = [
       'Mario\s+Rossi',
       'Esempio\s+S\.p\.A\.',
       'nomeutente123',
   ]
   ```

   Le espressioni sono regolari e ignorano le maiuscole. Il controllo non stampa mai il testo trovato, solo file, riga e numero del modello.

3. Controllo completo (deve finire con «nessun problema»):

   ```powershell
   python tools/opensource/controlla_pubblicazione.py
   make verifiche          # se hai make; altrimenti basta il comando sopra
   ```

## 1. Rotazione delle chiavi di sviluppo

Le chiavi di sviluppo locali non sono mai state pubblicate, ma prima di aprire il progetto rigenerale tutte, così nessuna chiave usata finora sopravvive:

```powershell
python tools/dev/genera_segreti_dev.py --forza
docker compose down -v
docker compose up -d --build
```

Ruota anche ogni chiave o segreto di **staging** e di qualsiasi ambiente condiviso (procedura in `docs/runbooks/rotazione_chiavi_jwt.md` e `docs/runbooks/conferme_decisione.md`). **La chiave JWT compromessa della cronologia privata non va mai riusata**, in nessun ambiente: se compare in una configurazione, sostituiscila e revoca i token emessi con essa.

## 2. Ramo orfano con un solo commit

```powershell
# identità pubblica per QUESTO repository (consigliato: l'indirizzo «noreply» di GitHub)
git config user.name  "Nome pubblico"
git config user.email "id+utente@users.noreply.github.com"

git checkout --orphan pubblico
```

`--orphan` crea un ramo senza storia: l'indice contiene i file tracciati di `master`, nessun commit precedente. I file ignorati (`infra/dev_*.env`, `.env`, chiavi, cache, `node_modules`) non sono nell'indice e non vi entrano.

Controlla che nell'indice non ci sia nulla di riservato:

```powershell
# file tracciati che .gitignore esclude: deve essere vuoto
git ls-files -ci --exclude-standard

# nomi da segreti: deve essere vuoto
git ls-files | Select-String -Pattern '(^|/)\.env($|\.)|dev_.*\.env$|\.(pem|key|p12|pfx|jks|tfstate|tfvars)$'

# controllo completo sull'indice
python tools/opensource/controlla_pubblicazione.py
```

Se qualcosa non va: `git rm --cached <file>` (e, se serve, aggiorna `.gitignore`), poi ripeti i controlli.

Commit unico, firmato DCO (`-s` aggiunge la riga `Signed-off-by`):

```powershell
git commit -s -m "Prima pubblicazione open source"

# verifica: un solo commit, senza genitori, con la firma DCO
git rev-list --count pubblico          # 1
git cat-file -p pubblico | Select-String '^parent'   # nessuna riga
git log -1 --format='%an <%ae>%n%n%B' pubblico
```

## 3. Repository pubblico su GitHub

1. Crea il repository **vuoto** (senza README, licenza né `.gitignore`: il primo commit è il tuo), dall'interfaccia web o con GitHub CLI. Consigliato: crearlo **privato**, controllarlo, poi renderlo pubblico.

   ```powershell
   gh repo create ORGANIZZAZIONE/verumdoc --private --description "Sistema di conservazione digitale a norma (EUPL-1.2)"
   ```

2. Push del **solo** ramo `pubblico`, come `main`:

   ```powershell
   git remote add pubblico https://github.com/ORGANIZZAZIONE/verumdoc.git
   git push pubblico pubblico:main
   ```

   **Mai** `git push --all`, `--tags` o `--mirror` verso il remoto pubblico: porterebbero i rami e i tag della cronologia privata. Non fare mai `merge`, `rebase` o `cherry-pick` fra `master` e `pubblico`: le due storie restano separate per sempre.

3. Controlla sul sito: un solo commit, nessun tag, nessun altro ramo; poi *Settings → General → Danger Zone → Change visibility → Public*.

4. Da qui in poi lo sviluppo continua **nel repository pubblico** (clona `main` in una cartella nuova); il repository privato si archivia in sola lettura e non si collega più al pubblico.

## 4. Impostazioni del repository

- **Protezione del ramo `main`** (*Settings → Rules → Rulesets*): pull request obbligatoria con almeno 1 approvazione (2 per le aree critiche, vedi `CONTRIBUTING.md`), controlli della CI richiesti (`ci`, `ruff`), niente push forzati né cancellazione del ramo, cronologia lineare; facoltativo: controllo DCO (app «DCO» di GitHub) e commit firmati.
- **Sicurezza** (*Settings → Code security*): attiva *Private vulnerability reporting* (usata da `SECURITY.md`), *Secret scanning* con *Push protection*, *Dependabot alerts* e *Dependabot security updates*; la configurazione degli aggiornamenti è in `.github/dependabot.yml`. Attiva *Code scanning* (CodeQL) se disponibile.
- **Segreti della CI**: il flusso di carico (`.github/workflows/carico.yml`) usa `VD_CARICO_TOKEN` e `VD_CARICO_API_KEY`. In un repository pubblico mettili in un *Environment* (es. `staging`) con revisori obbligatori, mai come segreti del repository disponibili a tutti i flussi; i flussi lanciati da pull request di fork non ricevono segreti (lasciare l'impostazione predefinita). Valori nuovi, generati dopo la rotazione del §1.
- **Actions** (*Settings → Actions → General*): permessi del `GITHUB_TOKEN` in sola lettura per impostazione; approvazione richiesta per i flussi dei contributori esterni.
- Issue e pull request: i modelli sono in `.github/`; aggiungi le etichette «errore», «proposta», «da valutare».

## 5. Campi da compilare

Prima di rendere pubblico il repository sostituisci i segnaposto «DA COMPILARE»:

| File | Cosa |
|---|---|
| `MAINTAINERS` | responsabile del progetto, manutentori per area, contatti del progetto |
| `SECURITY.md` | indirizzo dedicato per le vulnerabilità; facoltativa l'impronta della chiave OpenPGP |
| `CODE_OF_CONDUCT.md` | contatto per le segnalazioni (sezione «Enforcement») |
| `publiccode.yml` | `url`, `landingURL`, `description/it/documentation` (indirizzo del repository), `maintenance/contacts/name` |
| `.github/ISSUE_TEMPLATE/config.yml` | indirizzo della pagina *Security* del repository |

Usa indirizzi del progetto, non caselle personali.

## 6. Catalogo di Developers Italia (facoltativo)

`publiccode.yml` segue lo standard publiccode.yml. Dopo aver compilato i campi, validalo con l'editor ufficiale (<https://publiccode-editor.developers.italia.it/>) o con `publiccode-parser`; poi, se vuoi comparire nel catalogo del software a riuso, segui la procedura di Developers Italia per l'organizzazione che pubblica.

## 7. Dopo la pubblicazione

- `python tools/opensource/controlla_pubblicazione.py` è in `make verifiche`: ogni rilascio successivo lo esegue; il file locale dei termini riservati resta sul tuo computer.
- Ogni file sorgente nuovo porta l'intestazione SPDX (REGOLE_SVILUPPO §31, R18).
- Se un segreto finisce per errore in un commit pubblico: consideralo compromesso, ruotalo subito, poi valuta la riscrittura della storia; la rotazione viene prima.
