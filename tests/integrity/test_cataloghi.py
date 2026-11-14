# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R14 (VD-C-082): i cataloghi generati di ``docs/catalogo`` sono allineati ai registri
(``tools/docs/genera_cataloghi.py --controlla``; OpenAPI ed errori hanno i loro test in
``tools/docs``)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]


def _strumento():  # type: ignore[no-untyped-def]
    percorso = RADICE / "tools" / "docs" / "genera_cataloghi.py"
    spec = importlib.util.spec_from_file_location("genera_cataloghi", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def test_cataloghi_allineati() -> None:
    g = _strumento()
    vecchi = [nome for nome, genera in g.GENERATORI.items()
              if (RADICE / "docs" / "catalogo" / nome).read_text(encoding="utf-8") != genera()]
    assert vecchi == [], f"rigenerare con python tools/docs/genera_cataloghi.py: {vecchi}"


def test_catalogo_impostazioni_completo() -> None:
    from libs.verumdoc_rules.parametri_servizio import IMPOSTAZIONI_SERVIZIO

    testo = _strumento().impostazioni_md()
    assert all(f"`{v.codice}`" in testo for v in IMPOSTAZIONI_SERVIZIO)
    assert "`NOTIFICHE_PERSONALI_GIORNI`" in testo


def test_controlla_esce_con_errore_se_vecchio(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    g = _strumento()
    for nome, genera in g.GENERATORI.items():
        (tmp_path / nome).write_text(genera(), encoding="utf-8")
    monkeypatch.setattr(g, "CATALOGO", tmp_path)
    assert g.main(["--controlla", "--solo-nuovi"]) == 0
    (tmp_path / "topic.md").write_text("vecchio\n", encoding="utf-8")
    assert g.main(["--controlla", "--solo-nuovi"]) == 1
    assert (tmp_path / "topic.md").read_text(encoding="utf-8") == "vecchio\n"
