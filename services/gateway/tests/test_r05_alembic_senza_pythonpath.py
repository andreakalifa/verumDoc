# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (agente r05-sicurezza) — VD-C-117: i comandi di gestione delle migrazioni
(``alembic heads``, ``alembic history``) caricano TUTTI i file di
``alembic/versions`` senza eseguire ``env.py``: una migrazione che importa
``libs`` al caricamento li faceva fallire fuori dal container senza
``PYTHONPATH``. Regola: le migrazioni importano ``libs`` solo dentro le funzioni
(import pigro, come la 034).

Il test esegue i comandi veri in un processo separato, dalla cartella del
gateway, con l'ambiente ripulito da ``PYTHONPATH``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1]


def _alembic(*argomenti: str) -> subprocess.CompletedProcess[str]:
    ambiente = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(  # noqa: S603 — interprete e argomenti costanti
        [sys.executable, "-m", "alembic", *argomenti], cwd=GATEWAY, env=ambiente,
        capture_output=True, text=True, timeout=120, check=False)


def test_libs_non_importabile_senza_pythonpath():
    """Precondizione: senza PYTHONPATH ``libs`` non si trova (altrimenti il test
    sotto non proverebbe nulla)."""
    ambiente = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    esito = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import libs"], cwd=GATEWAY, env=ambiente,
        capture_output=True, text=True, timeout=60, check=False)
    if esito.returncode == 0:
        pytest.skip("libs installato nell'ambiente: il caso senza PYTHONPATH non si riproduce")


@pytest.mark.parametrize("comando", [("heads",), ("history",)])
def test_comandi_di_gestione_senza_pythonpath(comando):
    esito = _alembic(*comando)
    assert esito.returncode == 0, esito.stderr[-2000:]
    assert "ModuleNotFoundError" not in esito.stderr
    assert "20261114_060" in esito.stdout
