# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prove reali di tools/dev/genera_chiavi_dev.sh (eseguito con bash e openssl in una
cartella temporanea: il vero infra/dev_jwt.env non viene mai letto né toccato).

Uso:  python -m pytest -q tools/dev
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "genera_chiavi_dev.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("openssl")), reason="servono bash e openssl"
)


def _esegui(cartella: Path, *argomenti: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *argomenti], cwd=cartella,  # noqa: S603, S607 — comando costruito dallo strumento, eseguibile nel PATH
                          capture_output=True, text=True, check=True)


def _valori(file: Path) -> dict[str, str]:
    righe = [r for r in file.read_text().splitlines() if r and not r.startswith("#")]
    return dict(r.split("=", 1) for r in righe)


def test_file_nuovo_con_chiavi_e_pepper_mai_stampato(tmp_path: Path) -> None:
    esito = _esegui(tmp_path)
    dest = tmp_path / "infra" / "dev_jwt.env"
    valori = _valori(dest)
    assert set(valori) == {"JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY", "API_KEY_PEPPER"}
    assert re.fullmatch(r"[0-9a-f]{64}", valori["API_KEY_PEPPER"])  # 32 byte
    assert valori["JWT_PUBLIC_KEY"].startswith("-----BEGIN PUBLIC KEY-----\\n")
    assert dest.stat().st_mode & 0o777 == 0o600
    for segreto in valori.values():
        assert segreto not in esito.stdout and segreto not in esito.stderr


def test_pepper_diverso_a_ogni_generazione(tmp_path: Path) -> None:
    _esegui(tmp_path)
    primo = _valori(tmp_path / "infra" / "dev_jwt.env")["API_KEY_PEPPER"]
    _esegui(tmp_path, "--forza")
    assert _valori(tmp_path / "infra" / "dev_jwt.env")["API_KEY_PEPPER"] != primo


def test_file_esistente_senza_pepper_riceve_solo_il_pepper(tmp_path: Path) -> None:
    dest = tmp_path / "infra" / "dev_jwt.env"
    dest.parent.mkdir()
    # senza a capo finale: lo script deve aggiungerlo prima del pepper
    dest.write_text("# vecchio\nJWT_PRIVATE_KEY=privata\nJWT_PUBLIC_KEY=pubblica")
    esito = _esegui(tmp_path)
    valori = _valori(dest)
    assert valori["JWT_PRIVATE_KEY"] == "privata" and valori["JWT_PUBLIC_KEY"] == "pubblica"
    assert re.fullmatch(r"[0-9a-f]{64}", valori["API_KEY_PEPPER"])
    assert valori["API_KEY_PEPPER"] not in esito.stdout


def test_file_completo_non_modificato_senza_forza(tmp_path: Path) -> None:
    _esegui(tmp_path)
    dest = tmp_path / "infra" / "dev_jwt.env"
    prima = dest.read_bytes()
    _esegui(tmp_path)
    assert dest.read_bytes() == prima
