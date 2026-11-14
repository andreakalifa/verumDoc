# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Toglie il vecchio file della chiave di sviluppo dalla cronologia Git (decisione D09).

Riferimenti: VD-C-004, VD-P-28; README di questa cartella (riallineamento delle
copie locali). La rotazione della chiave è già fatta: questa pulizia toglie la
vecchia chiave privata dalla STORIA del repository, così chi clona non la trova
più nei commit vecchi.

Uso (Windows, macOS e Linux, solo Python standard più ``git filter-repo``):

    python tools/manutenzione/pulisci_cronologia_git.py            # PROVA: mostra e basta
    python tools/manutenzione/pulisci_cronologia_git.py --esegui   # riscrive in una copia

In prova elenca i commit che contengono il file e il comando che userebbe; non
cambia nulla. Con ``--esegui`` (revisione R07, W7):
  1. controlla che ``git filter-repo`` ci sia e che l'albero di lavoro sia pulito,
     poi chiede di digitare ``RISCRIVI``;
  2. salva una copia completa del repository (``git bundle``) fuori dal progetto e
     la VERIFICA (``git bundle verify``);
  3. crea un clone NUOVO ``--mirror --no-local`` fuori dal progetto ed esegue lì
     ``git filter-repo --invert-paths --path <file>`` SENZA ``--force``: vale il
     controllo «clone appena fatto» di filter-repo. Il repository di lavoro non
     viene toccato;
  4. controlla che il file non ci sia più e stampa i passi per il push coordinato
     dal clone, il riallineamento delle copie locali e l'avviso di cancellare la
     copia di sicurezza (che CONTIENE la vecchia chiave) dopo il controllo.

``--percorso`` accetta solo il file di chiave noto (``PERCORSI_AMMESSI``); un altro
file si toglie solo con la conferma digitata del percorso; cartelle e caratteri
jolly non si accettano.

NON va lanciato in automatico né nella CI: si esegue una volta, d'accordo con
tutti quelli che hanno una copia del repository.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
#: File della vecchia chiave privata di sviluppo (VD-C-004): l'unico ammesso senza conferma
PERCORSI_AMMESSI = ("infra/dev_jwt.env",)
PERCORSI_PREDEFINITI = PERCORSI_AMMESSI
#: Parola da digitare per riscrivere la storia
CONFERMA = "RISCRIVI"

ISTRUZIONI = """
Storia riscritta nel clone: {clone}
Il repository di lavoro NON è cambiato.

Passi successivi (vedi tools/manutenzione/README.md):
  1. controllare il risultato nel clone:
        git -C "{clone}" log --all --oneline -- {percorsi}   (vuoto)
  2. ricollegare il remoto nel clone (git filter-repo lo toglie per sicurezza) e fare
     il push coordinato di tutti i rami e tag (la storia è riscritta: serve --force):
        git -C "{clone}" remote add origin <url>
        git -C "{clone}" push --force --mirror origin
  3. chi ha una copia locale la RICLONA (consigliato) oppure la riallinea:
        git fetch origin && git reset --hard origin/<ramo>   (per ogni ramo)
     e non rispinge mai rami vecchi (riporterebbero il file).
  4. chiedere al servizio Git di eliminare i riferimenti nascosti e le cache
     delle pull request che contengono ancora i commit vecchi.

ATTENZIONE: la copia di sicurezza CONTIENE ancora la vecchia chiave:
     {copia}
  Cancellarla (insieme alle copie locali vecchie) appena il push è controllato e
  tutti hanno riallineato. Non va condivisa né caricata da nessuna parte.
"""


def _git(*argomenti: str, cartella: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *argomenti], cwd=cartella, capture_output=True, text=True,  # noqa: S603, S607 — comando costruito dallo strumento, eseguibile nel PATH
                          check=False)


def commit_con_file(cartella: Path, percorsi: tuple[str, ...]) -> list[str]:
    """Commit (di qualunque ramo o tag) che toccano uno dei percorsi."""
    esito = _git("log", "--all", "--format=%h %ad %s", "--date=short", "--", *percorsi,
                 cartella=cartella)
    if esito.returncode != 0:
        raise SystemExit(f"ERRORE: git log non riuscito: {esito.stderr.strip()}")
    return [r for r in esito.stdout.splitlines() if r.strip()]


def filter_repo_disponibile() -> bool:
    return shutil.which("git-filter-repo") is not None


def comando_filter_repo(percorsi: tuple[str, ...]) -> list[str]:
    """Senza ``--force``: filter-repo rifiuta un repository che non è un clone nuovo."""
    comando = ["git", "filter-repo", "--invert-paths"]
    for p in percorsi:
        comando += ["--path", p]
    return comando


def albero_pulito(cartella: Path) -> bool:
    esito = _git("status", "--porcelain", "--untracked-files=no", cartella=cartella)
    return esito.returncode == 0 and not esito.stdout.strip()


def motivo_percorso_non_valido(cartella: Path, percorso: str) -> str | None:
    """Solo un FILE preciso (nessuna cartella, nessun carattere jolly, nessun «..»)."""
    pulito = percorso.replace("\\", "/")
    if not pulito or pulito.startswith("/") or pulito.endswith("/") \
            or any(c in pulito for c in "*?[]") or ".." in pulito.split("/"):
        return "indicare un singolo file con il percorso relativo alla radice del progetto"
    if (cartella / pulito).is_dir():
        return "è una cartella: si toglie solo un file preciso"
    alberi = _git("log", "--all", "--format=", "--name-only", "--", f"{pulito}/",
                  cartella=cartella)
    if alberi.returncode == 0 and alberi.stdout.strip():
        return "nella storia è una cartella: si toglie solo un file preciso"
    return None


def percorsi_confermati(cartella: Path, richiesti: list[str] | None,
                        chiedi: Callable[[str], str]) -> tuple[str, ...] | None:
    """Percorsi ammessi: quelli noti; un altro file solo se l'utente lo riscrive."""
    percorsi: list[str] = []
    for grezzo in richiesti or PERCORSI_PREDEFINITI:
        percorso = grezzo.replace("\\", "/")
        motivo = motivo_percorso_non_valido(cartella, percorso)
        if motivo:
            print(f"ERRORE: --percorso {grezzo}: {motivo}.")
            return None
        if percorso not in PERCORSI_AMMESSI:
            print(f"ATTENZIONE: {percorso} non è il file di chiave noto "
                  f"({', '.join(PERCORSI_AMMESSI)}).")
            if chiedi("Per toglierlo comunque dalla storia riscrivere il percorso: ").strip() \
                    != percorso:
                print("Percorso non confermato: nulla è stato fatto.")
                return None
        percorsi.append(percorso)
    return tuple(dict.fromkeys(percorsi))


def main(argv: list[str] | None = None, *, cartella: Path | None = None,
         chiedi: Callable[[str], str] = input) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--esegui", action="store_true",
                        help="riscrive davvero la storia in un clone nuovo (senza: solo prova)")
    parser.add_argument("--percorso", action="append", default=None,
                        help=f"file da togliere (predefinito e unico senza conferma: "
                             f"{', '.join(PERCORSI_AMMESSI)})")
    parser.add_argument("--copia", default=None,
                        help="cartella della copia di sicurezza e del clone (predefinita: la "
                             "cartella che contiene il progetto)")
    args = parser.parse_args(argv)
    cartella = cartella or RADICE

    if _git("rev-parse", "--git-dir", cartella=cartella).returncode != 0:
        print(f"ERRORE: {cartella} non è un repository Git.")
        return 2
    percorsi = percorsi_confermati(cartella, args.percorso, chiedi)
    if percorsi is None:
        return 8
    commit = commit_con_file(cartella, percorsi)
    print(f"File da togliere dalla storia: {', '.join(percorsi)}")
    if not commit:
        print("Nessun commit contiene questi file: niente da fare.")
        return 0
    print(f"Commit che li contengono ({len(commit)}):")
    for riga in commit[:50]:
        print(f"  {riga}")
    if len(commit) > 50:
        print(f"  … e altri {len(commit) - 50}")
    print("Comando (in un clone nuovo): " + " ".join(comando_filter_repo(percorsi)))

    if not args.esegui:
        print("\nPROVA: nulla è stato cambiato. Per eseguire: --esegui")
        return 0
    if not filter_repo_disponibile():
        print("ERRORE: git filter-repo non è installato (pip install git-filter-repo).")
        return 3
    if not albero_pulito(cartella):
        print("ERRORE: ci sono modifiche non committate: committarle o metterle da parte.")
        return 4
    if chiedi(f"La storia di TUTTI i rami e tag sarà riscritta (in un clone). "
              f"Digitare {CONFERMA} per continuare: ").strip() != CONFERMA:
        print("Non confermato: nulla è stato fatto.")
        return 9

    destinazione = Path(args.copia) if args.copia else cartella.parent
    istante = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    copia = destinazione / f"copia-prima-di-filter-repo-{istante}.bundle"
    esito = _git("bundle", "create", str(copia), "--all", cartella=cartella)
    if esito.returncode != 0:
        print(f"ERRORE: copia di sicurezza non riuscita: {esito.stderr.strip()}")
        return 5
    verifica = _git("bundle", "verify", str(copia), cartella=cartella)
    if verifica.returncode != 0:
        print(f"ERRORE: la copia di sicurezza non si verifica: {verifica.stderr.strip()}")
        return 5
    print(f"Copia di sicurezza verificata: {copia}")

    clone = destinazione / f"storia-riscritta-{istante}.git"
    esito = _git("clone", "--mirror", "--no-local", str(cartella), str(clone),
                 cartella=destinazione)
    if esito.returncode != 0:
        print(f"ERRORE: clone nuovo non riuscito: {esito.stderr.strip()}")
        return 6
    esito = subprocess.run(comando_filter_repo(percorsi), cwd=clone, check=False)  # noqa: S603 — comando e argomenti costruiti dallo strumento
    if esito.returncode != 0:
        print("ERRORE: git filter-repo non riuscito (o il clone non risulta nuovo); il "
              "repository di lavoro e la copia di sicurezza sono intatti.")
        return 6
    rimasti = commit_con_file(clone, percorsi)
    if rimasti:
        print(f"ERRORE: {len(rimasti)} commit del clone contengono ancora i file.")
        return 7
    print(ISTRUZIONI.format(clone=clone, percorsi=" ".join(percorsi), copia=copia))
    return 0


if __name__ == "__main__":
    sys.exit(main())
