# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Pagina web del verificatore provata con Playwright (Chromium) sugli stessi
dati di prova della CLI (dati_prova.py): casi felici e di errore, browser
senza Ed25519, nessuna richiesta di rete, vettori RFC 6962, telefono 360px."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))

import dati_prova  # noqa: E402

SCRIPT = QUI.parent / "web" / "prova_pagina.mjs"
NODE = shutil.which("node")
BROWSER = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))

richiede_browser = pytest.mark.skipif(
    NODE is None or not BROWSER.is_dir(),
    reason="servono node e i browser di Playwright (PLAYWRIGHT_BROWSERS_PATH)")


@richiede_browser
def test_pagina_web_tutti_i_casi_playwright(tmp_path: Path) -> None:
    percorsi = dati_prova.costruisci(tmp_path / "dati")
    file_percorsi = tmp_path / "percorsi.json"
    file_percorsi.write_text(json.dumps(percorsi), encoding="utf-8")
    ambiente = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(BROWSER))
    esito = subprocess.run(  # noqa: S603 — comando fisso: node e lo script del repository
        [NODE, str(SCRIPT), str(file_percorsi)], capture_output=True,
                           text=True, timeout=180, env=ambiente, check=False)
    assert esito.returncode != 2, f"playwright non disponibile: {esito.stderr}"
    rapporto = json.loads(esito.stdout)
    falliti = [e for e in rapporto["esiti"] if not e["ok"]]
    assert esito.returncode == 0 and not falliti, json.dumps(falliti, indent=2, ensure_ascii=False)
    assert rapporto["casi"] >= 20
