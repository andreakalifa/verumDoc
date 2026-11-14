# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/opensource/controlla_pubblicazione.py — controllo prima della pubblicazione (R18, VD-P-190).

Controlla i file TRACCIATI da git (l'indice: quello che entrerebbe nel prossimo commit) e
fallisce (uscita 1) se trova:

  1. un sorgente delle cartelle di codice senza intestazione SPDX
     (``SPDX-License-Identifier`` e ``SPDX-FileCopyrightText``);
  2. un file con un nome o un tipo vietato (chiavi, ``.env``, ``infra/dev_*.env``, stato di
     Terraform, documenti d'ufficio, immagini): del file si guarda solo il NOME, il contenuto
     non viene mai letto;
  3. un segreto evidente nel testo (blocco PEM di una chiave privata con contenuto, token
     JWT completo, chiavi di servizi cloud noti);
  4. un riferimento vietato (percorsi locali, caselle di posta personali, cartelle di lavoro
     temporanee e i termini riservati del file locale, vedi sotto);
  5. un file tracciato che le regole di ``.gitignore`` escludono;
  6. l'assenza di ``LICENSE``.

La configurazione è in ``tools/opensource/pubblicazione.toml`` (regole generiche,
pubblicabili). I termini riservati (nomi di persone e di aziende, titoli di documenti di terzi)
stanno nel file LOCALE ``tools/opensource/pubblicazione.locale.toml``, ignorato da git, con la
stessa struttura: le sue liste si aggiungono a quelle pubbliche.

Uso:
    python tools/opensource/controlla_pubblicazione.py                  # controllo, uscita 1/0
    python tools/opensource/controlla_pubblicazione.py --applica-intestazioni
        # aggiunge l'intestazione SPDX ai sorgenti che non l'hanno (poi rilanciare il controllo)

Solo libreria standard (Python 3.11 o successivo).
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
CONFIGURAZIONE = Path(__file__).with_name("pubblicazione.toml")
CONFIGURAZIONE_LOCALE = Path(__file__).with_name("pubblicazione.locale.toml")

ID_LICENZA = "SPDX-License-Identifier:"
ID_COPYRIGHT = "SPDX-FileCopyrightText:"
#: righe iniziali in cui cercare l'intestazione (dopo shebang e direttive)
RIGHE_INTESTAZIONE = 12
#: file più grandi si saltano nella ricerca di segreti e riferimenti (dati, non codice)
DIMENSIONE_MASSIMA = 2_000_000


# ── stile dei commenti per tipo di file ────────────────────────────────

def stile(rel: str) -> str | None:
    """Stile del commento d'intestazione per il file, o None se il tipo non la richiede."""
    nome = rel.rsplit("/", 1)[-1]
    if nome == "Dockerfile" or nome.startswith("Dockerfile."):
        return "#"
    if rel.startswith("infra/helm/templates/") and (nome.endswith((".yaml", ".yml", ".tpl"))):
        return "go-template"
    estensione = nome.rsplit(".", 1)[-1] if "." in nome else ""
    return {
        "py": "#", "sh": "#", "ps1": "#", "tf": "#", "mako": "#", "yaml": "#", "yml": "#",
        "ts": "//", "tsx": "//", "js": "//", "mjs": "//", "cjs": "//", "java": "//", "go": "//",
        "sql": "--",
    }.get(estensione)


def righe_intestazione(stile_commento: str, copyright_: str, licenza: str) -> list[str]:
    testo = [f"{ID_COPYRIGHT} {copyright_}", f"{ID_LICENZA} {licenza}"]
    if stile_commento == "go-template":
        return ["{{- /*", *testo, "*/ -}}"]
    return [f"{stile_commento} {riga}" for riga in testo]


def ha_intestazione(testo: str) -> bool:
    inizio = "\n".join(testo.splitlines()[:RIGHE_INTESTAZIONE])
    return ID_LICENZA in inizio and ID_COPYRIGHT in inizio


def _righe_da_lasciare_in_cima(righe: list[str], rel: str) -> int:
    """Righe che devono restare prima dell'intestazione: shebang, direttiva syntax dei
    Dockerfile, dichiarazione di codifica dei file Python."""
    n = 0
    if righe and righe[0].startswith("#!"):
        n = 1
    if rel.rsplit("/", 1)[-1].startswith("Dockerfile") and len(righe) > n \
            and re.match(r"#\s*(syntax|escape|check)\s*=", righe[n]):
        n += 1
    if rel.endswith(".py") and len(righe) > n and re.match(r"#.*coding[:=]", righe[n]):
        n += 1
    return n


def aggiungi_intestazione(testo: str, rel: str, copyright_: str, licenza: str) -> str:
    """Il testo con l'intestazione SPDX (se manca), con BOM e fine riga del file originale."""
    if ha_intestazione(testo):
        return testo
    s = stile(rel)
    if s is None:
        return testo
    bom = "﻿" if testo.startswith("﻿") else ""
    corpo = testo[len(bom):]
    fine_riga = "\r\n" if "\r\n" in corpo else "\n"
    righe = corpo.split(fine_riga)
    n = _righe_da_lasciare_in_cima(righe, rel)
    nuove = righe[:n] + righe_intestazione(s, copyright_, licenza) + righe[n:]
    if corpo == "":
        nuove = righe_intestazione(s, copyright_, licenza) + [""]
    return bom + fine_riga.join(nuove)


# ── configurazione ─────────────────────────────────────────────────────

@dataclass
class Configurazione:
    copyright: str
    licenza: str
    cartelle: list[str]
    file_radice: list[str]
    esclusi: list[str]
    file_vietati: list[str]
    file_ammessi: list[str]
    segreti: list[re.Pattern[str]]
    riferimenti: list[re.Pattern[str]]
    eccezioni: dict[str, list[str]] = field(default_factory=dict)


def leggi_configurazione(percorso: Path = CONFIGURAZIONE,
                         locale: Path | None = CONFIGURAZIONE_LOCALE) -> Configurazione:
    dati = tomllib.loads(percorso.read_text(encoding="utf-8"))
    extra: dict = {}
    if locale is not None and locale.is_file():
        extra = tomllib.loads(locale.read_text(encoding="utf-8"))

    def lista(sezione: str, chiave: str) -> list:
        return list(dati.get(sezione, {}).get(chiave, [])) + \
            list(extra.get(sezione, {}).get(chiave, []))

    eccezioni = dict(dati.get("riferimenti", {}).get("eccezioni", {}))
    for k, v in extra.get("riferimenti", {}).get("eccezioni", {}).items():
        eccezioni[k] = list(eccezioni.get(k, [])) + list(v)
    intest = dati["intestazioni"]
    return Configurazione(
        copyright=intest["copyright"], licenza=intest["licenza"],
        cartelle=lista("intestazioni", "cartelle"),
        file_radice=lista("intestazioni", "file_radice"),
        esclusi=lista("intestazioni", "esclusi"),
        file_vietati=lista("segreti", "file_vietati"),
        file_ammessi=lista("segreti", "file_ammessi"),
        segreti=[re.compile(m) for m in lista("segreti", "modelli")],
        riferimenti=[re.compile(m, re.IGNORECASE) for m in lista("riferimenti", "modelli")],
        eccezioni=eccezioni,
    )


# ── file tracciati ─────────────────────────────────────────────────────

def _git(radice: Path, *argomenti: str) -> list[str]:
    uscita = subprocess.run(["git", "-C", str(radice), *argomenti], check=True,  # noqa: S603,S607
                            capture_output=True).stdout
    return [p for p in uscita.decode("utf-8").split("\0") if p]


def file_tracciati(radice: Path) -> list[str]:
    """I file dell'indice di git (``git ls-files``), percorsi relativi con ``/``."""
    return sorted(p for p in _git(radice, "ls-files", "-z") if (radice / p).is_file())


def tracciati_ignorati(radice: Path) -> list[str]:
    """File tracciati che ``.gitignore`` esclude (``git ls-files -ci --exclude-standard``)."""
    return sorted(_git(radice, "ls-files", "-z", "-c", "-i", "--exclude-standard"))


def _corrisponde(rel: str, modelli: Iterable[str]) -> bool:
    for m in modelli:
        if fnmatch.fnmatchcase(rel, m):
            return True
        # «**/x» vale anche per x alla radice
        if m.startswith("**/") and fnmatch.fnmatchcase(rel, m[3:]):
            return True
    return False


def richiede_intestazione(rel: str, conf: Configurazione) -> bool:
    if stile(rel) is None or _corrisponde(rel, conf.esclusi):
        return False
    if rel in conf.file_radice:
        return True
    return any(rel.startswith(c.rstrip("/") + "/") for c in conf.cartelle)


# ── controlli ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Problema:
    file: str
    tipo: str
    dettaglio: str

    def __str__(self) -> str:
        return f"{self.file}: {self.tipo}: {self.dettaglio}"


def _leggi_testo(percorso: Path) -> str | None:
    if percorso.stat().st_size > DIMENSIONE_MASSIMA:
        return None
    dati = percorso.read_bytes()
    if b"\0" in dati[:8192]:
        return None  # binario
    return dati.decode("utf-8", errors="replace")


def _ammesso(rel: str, modello: re.Pattern[str], conf: Configurazione) -> bool:
    for percorso, ammessi in conf.eccezioni.items():
        if fnmatch.fnmatchcase(rel, percorso) and ("*" in ammessi or modello.pattern in ammessi):
            return True
    return False


def controlla(radice: Path = RADICE, conf: Configurazione | None = None,
              tracciati: list[str] | None = None,
              ignorati: list[str] | None = None) -> list[Problema]:
    conf = conf or leggi_configurazione()
    tracciati = file_tracciati(radice) if tracciati is None else tracciati
    ignorati = tracciati_ignorati(radice) if ignorati is None else ignorati
    problemi: list[Problema] = []
    if not (radice / "LICENSE").is_file():
        problemi.append(Problema("LICENSE", "licenza", "file LICENSE assente"))
    for rel in ignorati:
        problemi.append(Problema(rel, "file ignorato tracciato",
                                 "escluso da .gitignore ma presente nell'indice (git rm --cached)"))
    for rel in tracciati:
        if _corrisponde(rel, conf.file_vietati) and not _corrisponde(rel, conf.file_ammessi):
            # mai leggere il contenuto di un possibile file di segreti
            problemi.append(Problema(rel, "file vietato", "nome o tipo non pubblicabile "
                                     "(chiavi, segreti, documenti d'ufficio, immagini)"))
            continue
        testo = _leggi_testo(radice / rel)
        if testo is None:
            continue
        if richiede_intestazione(rel, conf) and not ha_intestazione(testo):
            problemi.append(Problema(rel, "intestazione SPDX assente",
                                     "aggiungerla con --applica-intestazioni"))
        for modello in conf.segreti:
            m = modello.search(testo)
            if m and not _ammesso(rel, modello, conf):
                riga = testo.count("\n", 0, m.start()) + 1
                problemi.append(Problema(f"{rel}:{riga}", "segreto evidente", modello.pattern[:40]))
        for modello in conf.riferimenti:
            m = modello.search(testo)
            if m and not _ammesso(rel, modello, conf):
                riga = testo.count("\n", 0, m.start()) + 1
                # il testo trovato non si stampa: potrebbe essere proprio il dato riservato
                problemi.append(Problema(f"{rel}:{riga}", "riferimento vietato",
                                         f"modello {conf.riferimenti.index(modello) + 1}"))
    return problemi


def applica_intestazioni(radice: Path = RADICE, conf: Configurazione | None = None,
                         tracciati: list[str] | None = None) -> list[str]:
    """Aggiunge l'intestazione ai sorgenti che non l'hanno; restituisce i file modificati."""
    conf = conf or leggi_configurazione()
    tracciati = file_tracciati(radice) if tracciati is None else tracciati
    modificati = []
    for rel in tracciati:
        if not richiede_intestazione(rel, conf) or _corrisponde(rel, conf.file_vietati):
            continue
        percorso = radice / rel
        testo = _leggi_testo(percorso)
        if testo is None or ha_intestazione(testo):
            continue
        nuovo = aggiungi_intestazione(testo, rel, conf.copyright, conf.licenza)
        if nuovo != testo:
            percorso.write_bytes(nuovo.encode("utf-8"))
            modificati.append(rel)
    return modificati


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--applica-intestazioni", action="store_true",
                        help="aggiunge l'intestazione SPDX ai sorgenti che non l'hanno")
    parser.add_argument("--radice", type=Path, default=RADICE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.applica_intestazioni:
        modificati = applica_intestazioni(args.radice)
        print(f"intestazione SPDX aggiunta a {len(modificati)} file")
        return 0
    problemi = controlla(args.radice)
    for p in problemi:
        print(p)
    if problemi:
        print(f"controllo della pubblicazione: {len(problemi)} problemi")
        return 1
    print("controllo della pubblicazione: nessun problema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
