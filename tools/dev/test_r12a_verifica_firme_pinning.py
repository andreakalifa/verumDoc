# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-144): pinning dell'indirizzo nel client protetto di ``verifica-firme``
(``RecuperoProtetto.java``) provato con il solo JDK: ``javac`` compila la classe e
la prova ``RecuperoProtettoProva`` (server HTTP locale del JDK) e ``java`` la
esegue. Senza ``javac`` (ambiente senza JDK) il test si salta con il motivo; la CI
Python ha il JDK del runner.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
SERVIZIO = RADICE / "services" / "verifica-firme" / "src"
PACCHETTO = "it/verumdoc/verificafirme"


@pytest.mark.skipif(not (shutil.which("javac") and shutil.which("java")),
                    reason="JDK non disponibile (javac/java)")
def test_pinning_e_limiti_del_recupero_protetto(tmp_path: Path) -> None:
    sorgenti = [SERVIZIO / "main" / "java" / PACCHETTO / "RecuperoProtetto.java",
                SERVIZIO / "test" / "java" / PACCHETTO / "RecuperoProtettoProva.java"]
    compila = subprocess.run(["javac", "-d", str(tmp_path), *map(str, sorgenti)],  # noqa: S603, S607
                             capture_output=True, text=True, timeout=300)
    assert compila.returncode == 0, compila.stderr[-3000:]
    esegui = subprocess.run(["java", "-cp", str(tmp_path),  # noqa: S603, S607
                             "it.verumdoc.verificafirme.RecuperoProtettoProva"],
                            capture_output=True, text=True, timeout=120)
    assert esegui.returncode == 0, esegui.stdout[-2000:] + esegui.stderr[-2000:]
    assert "TUTTI I CONTROLLI SUPERATI" in esegui.stdout
