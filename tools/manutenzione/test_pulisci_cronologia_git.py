# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test di tools/manutenzione/pulisci_cronologia_git.py su un repository finto (D09).

La riscrittura vera la fa ``git filter-repo`` (non installato nella CI): qui un
eseguibile finto con lo stesso nome registra gli argomenti e toglie il file con
``git filter-branch``, così si provano i controlli, la copia e il risultato.

Revisione R07 (W7): niente ``--force`` (il finto fallisce se lo riceve), la
riscrittura avviene in un clone nuovo ``--mirror`` e il repository di lavoro non
cambia, la copia di sicurezza è verificata, conferma digitata, ``--percorso``
limitato al file di chiave noto o confermato."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "pulisci_cronologia_git", Path(__file__).with_name("pulisci_cronologia_git.py"))
modulo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(modulo)  # type: ignore[union-attr]

FILE = "infra/dev_jwt.env"


def _git(cartella: Path, *argomenti: str) -> str:
    return subprocess.run(["git", *argomenti], cwd=cartella, check=True, capture_output=True,  # noqa: S603, S607 — comando costruito dallo strumento, eseguibile nel PATH
                          text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "progetto"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "prova@example.org")
    _git(r, "config", "user.name", "Prova")
    (r / "infra").mkdir()
    (r / FILE).write_text("CHIAVE_FINTA_DI_PROVA=1\n", encoding="utf-8")
    (r / "README.md").write_text("progetto\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "con la chiave")
    _git(r, "rm", "-q", FILE)
    _git(r, "commit", "-q", "-m", "chiave tolta")
    return r


@pytest.fixture
def filter_repo_finto(tmp_path: Path, monkeypatch) -> Path:
    cartella = tmp_path / "bin"
    cartella.mkdir()
    registro = tmp_path / "argomenti.txt"
    finto = cartella / "git-filter-repo"
    finto.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{registro}"\n'
        'case " $* " in *" --force "*) echo "--force non ammesso" >&2; exit 9;; esac\n'
        "FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --index-filter "
        f"'git rm -q --cached --ignore-unmatch {FILE}' -- --all >/dev/null 2>&1\n"
        # come git filter-repo: nessun riferimento di riserva della storia vecchia
        "git for-each-ref --format='%(refname)' refs/original | "
        "xargs -r -n1 git update-ref -d\n",
        encoding="utf-8")
    finto.chmod(finto.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{cartella}{os.pathsep}{os.environ['PATH']}")
    return registro


def test_prova_elenca_e_non_cambia_nulla(repo: Path, capsys) -> None:
    prima = _git(repo, "rev-parse", "HEAD")
    assert modulo.main([], cartella=repo) == 0
    uscita = capsys.readouterr().out
    assert "con la chiave" in uscita and "chiave tolta" in uscita
    assert "PROVA: nulla è stato cambiato" in uscita
    assert "--invert-paths --path infra/dev_jwt.env" in uscita
    assert _git(repo, "rev-parse", "HEAD") == prima


def _risposte(*valori: str):
    elenco = list(valori)
    return lambda _domanda: elenco.pop(0)


def test_esegui_senza_filter_repo_si_ferma(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(modulo, "filter_repo_disponibile", lambda: False)
    assert modulo.main(["--esegui"], cartella=repo) == 3
    assert modulo.commit_con_file(repo, (FILE,))


def test_esegui_con_modifiche_in_sospeso_si_ferma(repo: Path, filter_repo_finto: Path) -> None:
    (repo / "README.md").write_text("modificato\n", encoding="utf-8")
    assert modulo.main(["--esegui"], cartella=repo) == 4
    assert not filter_repo_finto.exists()


def test_esegui_copia_poi_toglie_il_file_in_un_clone_nuovo(repo: Path, filter_repo_finto: Path,
                                                          tmp_path: Path, capsys) -> None:
    copie = tmp_path / "copie"
    copie.mkdir()
    prima = _git(repo, "rev-parse", "HEAD")
    assert modulo.main(["--esegui", "--copia", str(copie)], cartella=repo,
                       chiedi=_risposte("RISCRIVI")) == 0
    assert filter_repo_finto.read_text().split() == ["--invert-paths", "--path", FILE]
    # il repository di lavoro non cambia; il clone non ha più il file
    assert _git(repo, "rev-parse", "HEAD") == prima
    assert modulo.commit_con_file(repo, (FILE,))
    [clone] = list(copie.glob("storia-riscritta-*.git"))
    assert modulo.commit_con_file(clone, (FILE,)) == []
    [bundle] = list(copie.glob("*.bundle"))
    uscita = capsys.readouterr().out
    assert "Copia di sicurezza verificata" in uscita
    assert "push --force --mirror origin" in uscita
    assert "CONTIENE ancora la vecchia chiave" in uscita and "Cancellarla" in uscita
    verifica = subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=repo,  # noqa: S603, S607 — comando costruito dallo strumento, eseguibile nel PATH
                              capture_output=True, text=True)
    assert verifica.returncode == 0


def test_esegui_senza_conferma_digitata_non_fa_nulla(repo: Path, filter_repo_finto: Path,
                                                    tmp_path: Path) -> None:
    assert modulo.main(["--esegui", "--copia", str(tmp_path)], cartella=repo,
                       chiedi=_risposte("si")) == 9
    assert not filter_repo_finto.exists() and not list(tmp_path.glob("*.bundle"))


def test_percorso_limitato_al_file_noto_o_confermato(repo: Path, capsys) -> None:
    # cartelle e caratteri jolly: mai
    for errato in ("infra", "infra/", "infra/*.env", "../fuori.env"):
        assert modulo.main(["--percorso", errato], cartella=repo,
                           chiedi=_risposte("infra")) == 8, errato
    # un altro file: solo se l'utente riscrive il percorso
    assert modulo.main(["--percorso", "README.md"], cartella=repo,
                       chiedi=_risposte("no")) == 8
    assert modulo.main(["--percorso", "README.md"], cartella=repo,
                       chiedi=_risposte("README.md")) == 0
    assert "PROVA" in capsys.readouterr().out


def test_nessun_commit_niente_da_fare(repo: Path, capsys) -> None:
    assert modulo.main(["--percorso", "non/esiste.env"], cartella=repo,
                       chiedi=_risposte("non/esiste.env")) == 0
    assert "niente da fare" in capsys.readouterr().out
