# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R14 (VD-C-082): la documentazione viva non cita file, criticità o decisioni inesistenti
(``tools/docs/verifica_documentazione.py``, anche in ``make verifiche``)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]


def _strumento():  # type: ignore[no-untyped-def]
    percorso = RADICE / "tools" / "docs" / "verifica_documentazione.py"
    spec = importlib.util.spec_from_file_location("verifica_documentazione", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def test_documentazione_senza_problemi() -> None:
    problemi = _strumento().verifica()
    assert problemi == [], "\n".join(map(str, problemi[:30]))


def test_il_controllo_trova_i_difetti(tmp_path: Path) -> None:
    """Il controllo non è vuoto: un collegamento rotto, un percorso, una criticità e una
    decisione inesistenti sono segnalati; quelli validi no."""
    v = _strumento()
    (tmp_path / "docs" / "registro").mkdir(parents=True)
    (tmp_path / "docs" / "registro" / "criticita.md").write_text(
        "| VD-C-001 | P1 |\n", encoding="utf-8")
    (tmp_path / "docs" / "registro" / "decisioni.md").write_text(
        "| VD-P-01 | x |\n", encoding="utf-8")
    (tmp_path / "docs" / "vero.md").write_text("esiste\n", encoding="utf-8")
    (tmp_path / "LEGGIMI.md").write_text(
        "[buono](docs/vero.md) [rotto](docs/manca.md) [web](https://example.invalid/x)\n"
        "`docs/vero.md` `docs/assente.md` VD-C-001 VD-C-999 VD-P-01 VD-P-77\n"
        "```\n`docs/dentro_un_blocco.md`\n```\n"
        "il file `docs/tolto.md` è stato rimosso\n", encoding="utf-8")
    problemi = {(p.tipo, p.dettaglio) for p in v.verifica(tmp_path)}
    assert problemi == {("collegamento rotto", "docs/manca.md"),
                        ("percorso inesistente", "docs/assente.md"),
                        ("criticità non registrata", "VD-C-999"),
                        ("decisione non registrata", "VD-P-77")}


def test_segnaposto_non_dichiarati_nei_documenti_generati(tmp_path: Path) -> None:
    """R15 (VD-C-177): in un documento generato da ``tools/conformita/genera_documenti.py``
    l'unico segnaposto ammesso è il blocco «DA COMPILARE» con chi lo fornisce e il campo;
    ogni altra forma è segnalata. Un documento non generato non è toccato da questa regola."""
    v = _strumento()
    (tmp_path / "docs" / "registro").mkdir(parents=True)
    (tmp_path / "docs" / "registro" / "criticita.md").write_text("| VD-C-001 | P1 |\n", "utf-8")
    (tmp_path / "docs" / "registro" / "decisioni.md").write_text(
        "| VD-P-01 | x |\n| VD-N-01 | x |\n", encoding="utf-8")
    buono = ("> **DA COMPILARE** — denominazione. Fornisce: il conservatore. Dove: variabile X. "
             "Campo: `conservatore.denominazione`.")
    (tmp_path / "docs" / "generato.md").write_text(
        v.MARCA_GENERATO + " -->\n\n# Titolo\n\n## 1. Sezione\n\n"
        "| Voce | Valore |\n|---|---|\n| Conservatore | DA COMPILARE (vedi sotto) |\n\n"
        + buono + "\n\n"
        "> *da verificare sul testo ufficiale* — paragrafo X: non riscontrato.\n\n"
        "## 2. Altra\n\n| Voce | Valore |\n|---|---|\n| PEC | DA COMPILARE (vedi sotto) |\n\n"
        "> **DA COMPILARE** — sede. Fornisce: . Campo: `x`.\n\n"
        "> **DA COMPILARE** — sede. Fornisce:  . Campo: `x`.\n\n"
        "Testo con [da compilare] e TODO e DA_IMPOSTARE e un DA COMPILARE libero.\n"
        "Decisione VD-N-01 e VD-DR-99.\n", encoding="utf-8")
    (tmp_path / "docs" / "libero.md").write_text("Un TODO in un documento scritto a mano.\n",
                                                 encoding="utf-8")
    problemi = [(p.file, p.tipo, p.dettaglio) for p in v.verifica(tmp_path)]
    generato = [x for x in problemi if x[0] == "docs/generato.md"]
    senza_chi = [x for x in generato
                 if x[2] == "DA COMPILARE senza «Fornisce: <chi>» o senza campo"]
    assert len(senza_chi) == 2          # chi assente e chi di soli spazi
    dettagli = {d for _, t, d in generato if t == "segnaposto non dichiarato"}
    assert {"[da compilare]", "TODO", "DA_IMPOSTARE",
            "«DA COMPILARE» fuori dal blocco dichiarato"} <= dettagli
    assert ("docs/generato.md", "decisione non registrata", "VD-DR-99") in generato
    assert not any(d == "VD-N-01" for _, _, d in generato)
    assert not any(f == "docs/libero.md" for f, _, _ in problemi)
    # la sezione 1 è corretta: nessun rilievo sulle sue righe
    assert len([d for d in dettagli if "vedi sotto" in d]) == 0
