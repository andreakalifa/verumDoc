# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Pulizia del progetto VerumDoc: foto di prova, cache e script già applicati.

Si lancia da PyCharm (tasto destro → Run 'pulisci_progetto') o da terminale:

    python tools/pulizia/pulisci_progetto.py            # mostra l'elenco e chiede conferma
    python tools/pulizia/pulisci_progetto.py --prova    # mostra soltanto, non cancella nulla
    python tools/pulizia/pulisci_progetto.py --si       # cancella senza chiedere

Funziona su Windows, macOS e Linux, con il solo Python standard. La radice del
progetto si ricava dalla posizione dello script, quindi la cartella di lavoro
impostata in PyCharm non conta.

Cosa cancella (nulla di questo serve a far girare VerumDoc):
- le foto di verifica grafica dei rilasci (docs/design/R02_partenze/*.png,
  foto_finali, R04, R05, allegati/screenshot): si rigenerano a richiesta con
  `FOTO=1 npm run e2e -- foto`, in una cartella ignorata da git;
- cache e risultati di prova rigenerabili (__pycache__, .pytest_cache,
  .mypy_cache, .ruff_cache, htmlcov, .coverage, frontend/test-results,
  frontend/playwright-report, frontend/dist);
- gli script di pulizia dei rilasci precedenti, già applicati;
- R07 (D11, VD-C-034): `libs/policy-engine`, il motore di policy OPA mai usato
  (le regole vivono in `libs/verumdoc_rules`, configurabili dalla console);
- R08 (D02, VD-C-074): `services/gateway/app/services/formato_reale.py`, il vecchio
  rilevatore del formato per firma binaria, spostato in
  `libs/shared/formati/firma_binaria.py` (il gateway lo importa da lì);
- R11 (D03): `libs/sincro/schemas/.gitkeep`, segnaposto della cartella degli
  schemi XSD, che ora contiene `LEGGIMI.md` (istruzioni per lo schema UNI 11386);
- R18 (VD-P-190, apertura open source): i documenti di lavoro interni che non si
  pubblicano (rapporti di dettaglio degli audit del 20/10 in `docs/audit/allegati/`
  tranne `ricerca_normativa.md`, la cartella `docs/design/allegati`, il piano storico
  `docs/storico/Piano_Implementazione_Tecnico_Completo_v5.docx`) e `THIRD_PARTY.md`,
  sostituito da `THIRD_PARTY_NOTICES.md`;
- R18.2 (VD-C-187, VD-P-191): gli adattatori di compatibilità del gateway (cartella
  `services/gateway/app/compat`, il cui modulo `corpo.py` è ora in
  `services/gateway/app/services/corpo.py`; rotte, test e guide `compat_*`).

Non tocca: codice, documenti .md (tranne quelli elencati sopra), node_modules, venv, file
.env, dati Docker.
Dopo la pulizia, in PyCharm le eliminazioni compaiono tra le modifiche da
committare (Commit → seleziona tutto).
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]

# Cartelle intere da togliere (percorsi relativi alla radice).
CARTELLE = [
    "docs/design/R02_partenze/foto_finali",
    "docs/design/R04",
    "docs/design/R05",
    "docs/design/allegati/screenshot",
    "docs/design/foto",
    "frontend/test-results",
    "frontend/playwright-report",
    "frontend/dist",
    "htmlcov",
    # R07 (D11, VD-C-034): motore di policy OPA tolto
    "libs/policy-engine",
    # R18 (VD-P-190): rapporti di lavoro dell'audit grafico, non pubblicati
    "docs/design/allegati",
    # R18.2 (VD-P-191): adattatori di compatibilità tolti (corpo.py spostato in app/services)
    "services/gateway/app/compat",
]

# Singoli file o gruppi di file (glob relativi alla radice).
FILE = [
    "docs/design/R02_partenze/*.png",
    "tools/pulizia/elimina_R03.sh",
    "tools/pulizia/elimina_R03.ps1",
    "tools/pulizia/elimina_R04.sh",
    "tools/pulizia/elimina_R04.ps1",
    # R08 (D02, VD-C-074): rilevatore spostato in libs/shared/formati/firma_binaria.py
    "services/gateway/app/services/formato_reale.py",
    # R11 (D03): segnaposto superfluo, la cartella contiene LEGGIMI.md
    "libs/sincro/schemas/.gitkeep",
    ".coverage",
    # R18 (VD-P-190): documenti di lavoro interni non pubblicati; THIRD_PARTY.md rinominato
    "docs/audit/allegati/audit_A_versamento_validazione.md",
    "docs/audit/allegati/audit_B_conservazione.md",
    "docs/audit/allegati/audit_C_servizi_infrastruttura.md",
    "docs/storico/Piano_Implementazione_Tecnico_Completo_v5.docx",
    "THIRD_PARTY.md",
    # R18.2 (VD-P-191): rotte, test e guide degli adattatori di compatibilità tolti
    "services/gateway/app/routers/compat_packer.py",
    "services/gateway/app/routers/compat_parer.py",
    "services/gateway/tests/test_compat_packer.py",
    "services/gateway/tests/test_compat_parer.py",
    "docs/integrazione/compat_packer.md",
    "docs/integrazione/compat_parer.md",
]

# Cartelle di cache da cercare ovunque, tranne dentro le cartelle escluse.
CACHE = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ESCLUSE = {"node_modules", ".git", ".venv", "venv", "env", ".idea", ".terraform"}


def _controlla_radice() -> None:
    if not ((RADICE / "docker-compose.yml").is_file() and (RADICE / "services").is_dir()):
        print(f"ERRORE: {RADICE} non sembra la radice del progetto VerumDoc.")
        sys.exit(2)


def _dimensione(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    totale = 0
    for radice, _cartelle, file in os.walk(p):
        for nome in file:
            try:
                totale += (Path(radice) / nome).stat().st_size
            except OSError:
                pass
    return totale


def _cache_ovunque() -> list[Path]:
    trovate: list[Path] = []
    for radice, cartelle, _file in os.walk(RADICE):
        # Non scendere nelle cartelle escluse né dentro le cache già trovate.
        cartelle[:] = [c for c in cartelle if c not in ESCLUSE]
        for c in list(cartelle):
            if c in CACHE:
                trovate.append(Path(radice) / c)
                cartelle.remove(c)
    return trovate


def raccogli() -> list[Path]:
    voci: set[Path] = set()
    for rel in CARTELLE:
        p = RADICE / rel
        if p.is_dir():
            voci.add(p)
    for modello in FILE:
        for p in RADICE.glob(modello):
            if p.is_file():
                voci.add(p)
    voci.update(_cache_ovunque())
    return sorted(voci)


def _sblocca_e_riprova(funzione, percorso, _errore) -> None:
    # Su Windows i file in sola lettura (es. dentro .git o cache) bloccano rmtree.
    os.chmod(percorso, stat.S_IWRITE)
    funzione(percorso)


def cancella(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, onexc=_sblocca_e_riprova)  # Python >= 3.12 (target del progetto)
    else:
        try:
            p.unlink()
        except PermissionError:
            os.chmod(p, stat.S_IWRITE)
            p.unlink()


def _mb(byte: int) -> str:
    return f"{byte / 1_048_576:.1f} MB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pulizia di foto, cache e script già applicati.")
    parser.add_argument("--prova", action="store_true", help="mostra soltanto, non cancella")
    parser.add_argument("--si", action="store_true", help="cancella senza chiedere conferma")
    args = parser.parse_args(argv)

    _controlla_radice()
    voci = raccogli()
    if not voci:
        print("Niente da pulire.")
        return 0

    totale = 0
    print(f"Radice del progetto: {RADICE}\n")
    for p in voci:
        dim = _dimensione(p)
        totale += dim
        tipo = "cartella" if p.is_dir() else "file    "
        print(f"  {tipo}  {_mb(dim):>9}  {p.relative_to(RADICE).as_posix()}")
    print(f"\n{len(voci)} voci, {_mb(totale)} in tutto.")

    if args.prova:
        print("Modalità prova: non ho cancellato nulla.")
        return 0
    if not args.si:
        risposta = input("Cancello queste voci? Scrivi 's' per confermare: ").strip().lower()
        if risposta not in {"s", "si", "sì", "y", "yes"}:
            print("Annullato: non ho cancellato nulla.")
            return 0

    errori = 0
    for p in voci:
        try:
            cancella(p)
        except OSError as e:
            errori += 1
            print(f"  NON cancellato: {p.relative_to(RADICE).as_posix()} ({e})")
    print(f"Fatto: {len(voci) - errori} voci cancellate, {_mb(totale)} liberati." if not errori
          else f"Finito con {errori} errori (file aperti in un altro programma?).")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
