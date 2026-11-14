# tools/manutenzione

Operazioni di manutenzione straordinaria, da eseguire a mano e una sola volta.

## Pulizia della cronologia Git dalla vecchia chiave (decisione D09, VD-C-004)

La vecchia chiave privata di sviluppo (`infra/dev_jwt.env`) è stata ruotata in R02 ma
resta nei commit vecchi. `pulisci_cronologia_git.py` la toglie dalla storia di tutti i
rami e tag con [`git filter-repo`](https://github.com/newren/git-filter-repo).

1. **Prova** (non cambia nulla): elenca i commit che contengono il file e il comando.

   ```
   python tools/manutenzione/pulisci_cronologia_git.py
   ```

2. **Esecuzione**, d'accordo con tutti quelli che hanno una copia del repository e senza
   modifiche in sospeso (revisione R07, W7):

   ```
   pip install git-filter-repo
   python tools/manutenzione/pulisci_cronologia_git.py --esegui
   ```

   Lo script chiede di digitare `RISCRIVI`, salva una copia completa (`git bundle`) nella
   cartella che contiene il progetto (`--copia <cartella>` per sceglierne un'altra) e la
   verifica (`git bundle verify`), poi crea un clone NUOVO (`git clone --mirror
   --no-local`) accanto alla copia e riscrive la storia LÌ, senza `--force`: vale il
   controllo «clone appena fatto» di `git filter-repo`. Il repository di lavoro non
   cambia. Alla fine controlla che il file non ci sia più nel clone.

   `--percorso` accetta senza domande solo il file di chiave noto (`infra/dev_jwt.env`);
   un altro file va confermato riscrivendone il percorso; cartelle, `..` e caratteri
   jolly non si accettano.

3. **Push coordinato dal clone** (`git filter-repo` toglie il remoto per sicurezza; la
   storia è riscritta, quindi il push è forzato):

   ```
   git -C <clone> remote add origin <url>
   git -C <clone> push --force --mirror origin
   ```

4. **Cancellare la copia di sicurezza** (`.bundle`) e le copie locali vecchie appena il
   push è controllato e tutti hanno riallineato: CONTENGONO ancora la vecchia chiave.

### Riallineamento delle copie locali

Gli hash dei commit successivi al file cambiano: le copie esistenti NON vanno
fuse con la storia nuova.

- **Consigliato:** cancellare la copia locale e clonare di nuovo.
- **In alternativa**, per ogni ramo locale senza lavoro da salvare:
  `git fetch origin` e poi `git reset --hard origin/<ramo>`.
- Lavoro in corso su un ramo vecchio: `git rebase --onto origin/<ramo> <vecchio-commit-base> <ramo-di-lavoro>`
  oppure riportare le modifiche con `git cherry-pick` sulla storia nuova.
- Non fare mai `git push` di un ramo vecchio: riporterebbe la chiave nella storia.
- In PyCharm: *Git → Fetch*, poi su ogni ramo *Reset Current Branch to Here…* (Hard)
  su `origin/<ramo>`.

Infine chiedere al servizio Git di eliminare riferimenti nascosti e cache delle pull
request che contengono ancora i commit vecchi. La copia di sicurezza (`.bundle`) va
conservata finché tutti hanno riallineato, poi cancellata (contiene la vecchia chiave).
