#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Genera i file derivati del catalogo errori (VD-C-100) da
``libs/shared/errori/catalogo.yaml``:

- ``libs/shared/errori/_catalogo_dati.py``: copia Python letta dai servizi a
  runtime (niente parser YAML nelle immagini dei servizi);
- ``docs/catalogo/errori.md``: tabella per famiglia per i clienti.

Uso (dalla radice del repository)::

    python tools/docs/genera_catalogo_errori.py            # rigenera
    python tools/docs/genera_catalogo_errori.py --verifica # 1 se non aggiornati
    python tools/docs/genera_catalogo_errori.py --codici   # 1 se il codice usa
                                                           # codici fuori catalogo
                                                           # o dismessi

Richiede PyYAML (già usato dagli strumenti di ``infra/`` e ``tools/dev``).
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import pprint
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

RADICE = Path(__file__).resolve().parents[2]
YAML = RADICE / "libs/shared/errori/catalogo.yaml"
DATI_PY = RADICE / "libs/shared/errori/_catalogo_dati.py"
DOC_MD = RADICE / "docs/catalogo/errori.md"

URI_BASE = "https://docs.verumdoc.it/errori/"
_ESITI = {
    "NON_VALIDO": "Colpa della richiesta o del pacchetto: correggere e reinviare.",
    "RIFIUTATO": "Regola o policy: ritentare la stessa richiesta non serve.",
    "ERRORE": "Problema del servizio o temporaneo: ritentare solo se `ritentabile`.",
}


def leggi_yaml(percorso: Path = YAML) -> dict[str, Any]:
    with percorso.open(encoding="utf-8") as f:
        dati = yaml.safe_load(f)
    if not isinstance(dati, dict):
        raise SystemExit(f"{percorso}: il catalogo non è un oggetto YAML")
    return dati


def _valida(dati: Mapping[str, Any]) -> None:
    # schema.py si carica dal percorso: il pacchetto importerebbe i file derivati
    # che questo strumento deve ancora scrivere.
    spec = importlib.util.spec_from_file_location(
        "_schema_catalogo_errori", RADICE / "libs/shared/errori/schema.py")
    assert spec is not None and spec.loader is not None
    schema = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = schema  # richiesto da dataclass
    spec.loader.exec_module(schema)
    problemi = schema.valida_catalogo(dati)
    if problemi:
        raise SystemExit("catalogo errori non valido:\n- " + "\n- ".join(problemi))


def genera_dati_py(dati: Mapping[str, Any]) -> str:
    testo = pprint.pformat(dati, width=100, sort_dicts=False)
    return (
        # R18 (VD-P-190): intestazione SPDX come in ogni sorgente del repository
        "# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc\n"
        "# SPDX-License-Identifier: EUPL-1.2\n"
        '"""File GENERATO da tools/docs/genera_catalogo_errori.py a partire da\n'
        "catalogo.yaml: non modificarlo a mano (un test verifica che sia aggiornato).\"\"\"\n"
        "# ruff: noqa: E501\n"
        "from typing import Any\n\n"
        f"DATI: dict[str, Any] = {testo}\n"
    )


def _cella(testo: object) -> str:
    return str(testo).replace("|", "\\|").replace("\n", " ").strip()


def genera_markdown(dati: Mapping[str, Any]) -> str:
    famiglie: Mapping[str, Any] = dati["famiglie"]
    codici: Mapping[str, Any] = dati["codici"]
    per_famiglia: dict[str, list[tuple[str, Mapping[str, Any]]]] = {f: [] for f in famiglie}
    for codice, v in codici.items():
        per_famiglia[v["famiglia"]].append((codice, v))

    righe = [
        "# Catalogo dei codici d'errore",
        "",
        "<!-- File GENERATO da tools/docs/genera_catalogo_errori.py a partire da",
        "     libs/shared/errori/catalogo.yaml: non modificarlo a mano. -->",
        "",
        f"Versione del catalogo: **{dati['versione']}** — {len(codici)} codici "
        f"in {len(famiglie)} famiglie.",
        "",
        "## Formato della risposta",
        "",
        "Tutti i servizi rispondono agli errori con `application/problem+json` (RFC 9457):",
        "",
        "```json",
        "{",
        f'  "type": "{URI_BASE}VRS-102",',
        '  "title": "Impronta del file errata",',
        '  "status": 400,',
        '  "detail": "Atteso: ab12… | Calcolato: cd34…",',
        '  "instance": "/api/v1/versamento/ud",',
        '  "codice": "VRS-102",',
        '  "esito": "NON_VALIDO",',
        '  "ritentabile": false,',
        '  "dettagli": {},',
        '  "campi": [],',
        '  "id_richiesta": "5f0c2d…",',
        '  "messaggio": "Impronta del file errata"',
        "}",
        "```",
        "",
        "- `codice` è stabile e va usato dai programmi; `title` e `detail` sono per le persone.",
        "- `campi` elenca gli errori per campo (`campo`, `posizione`, `tipo`, `messaggio`).",
        "- `id_richiesta` è anche nell'intestazione `X-Request-ID`: citarlo nelle segnalazioni.",
        "- `Retry-After` accompagna gli errori temporanei che prevedono un'attesa.",
        "- `messaggio` ripete `title` per i client del formato precedente.",
        "",
        "### Esiti",
        "",
        "| Esito | Significato |",
        "|---|---|",
        *[f"| `{e}` | {s} |" for e, s in _ESITI.items()],
        "",
        "I codici con contesto *asincrono* non sono risposte HTTP: compaiono in `errori[]`",
        "dello stato del PdV; lo stato HTTP indicato è solo di classificazione.",
        "",
        "I codici *dismessi* non sono più emessi: restano elencati perché un codice",
        "pubblicato non cambia significato e non si riusa; la voce indica i sostituti.",
        "",
        "## Famiglie",
        "",
        "| Famiglia | Descrizione | Codici |",
        "|---|---|---|",
        *[f"| [{s}](#{s.lower()}) | {_cella(f['descrizione'])} | {len(per_famiglia[s])} |"
          for s, f in famiglie.items()],
        "",
    ]
    for sigla, fam in famiglie.items():
        righe += [f"## {sigla}", "", _cella(fam["descrizione"]), ""]
        voci = per_famiglia[sigla]
        if not voci:
            righe += ["*Nessun codice ancora definito.*", ""]
            continue
        righe += [
            "| Codice | HTTP | Esito | Ritentabile | Titolo | Significato | Che cosa fare |",
            "|---|---|---|---|---|---|---|",
        ]
        for codice, v in voci:
            ritenta = "sì" if v["ritentabile"] else "no"
            if v.get("retry_after"):
                ritenta += f" (dopo {v['retry_after']} s)"
            descr = _cella(v["descrizione"])
            if v.get("contesto") == "asincrono":
                descr = f"*Asincrono.* {descr}"
            if v.get("dismesso"):
                vedi = ", ".join(f"`{c}`" for c in v.get("sostituito_da") or ())
                descr = ("*Dismesso: non più emesso" + (f", vedi {vedi}" if vedi else "")
                         + ".* " + descr)
            if v.get("alias"):
                descr += " Vecchi codici: " + ", ".join(f"`{a}`" for a in v["alias"]) + "."
            righe.append(
                f"| `{codice}` | {v['status']} | {v['esito']} | {ritenta} | "
                f"{_cella(v['titolo'])} | {descr} | {_cella(v['azione'])} |"
            )
        righe.append("")
    return "\n".join(righe).rstrip() + "\n"


def file_attesi(dati: Mapping[str, Any] | None = None) -> dict[Path, str]:
    dati = dati if dati is not None else leggi_yaml()
    _valida(dati)
    return {DATI_PY: genera_dati_py(dati), DOC_MD: genera_markdown(dati)}


def non_aggiornati(dati: Mapping[str, Any] | None = None) -> list[Path]:
    """File derivati assenti o diversi da quelli che il catalogo produrrebbe."""
    return [p for p, testo in file_attesi(dati).items()
            if not p.exists() or p.read_text(encoding="utf-8") != testo]


# ── Coerenza col codice: codici usati ma assenti dal catalogo ────────

_FORMA = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
#: Funzioni/classi che ricevono un codice d'errore come argomento posizionale.
_CHIAMATE_CON_CODICE = {
    "ErroreApi", "errore_registrato", "RifiutoVersamento", "ErroreTokenNonValido",
    "ErrorePermanente", "errore_interno", "errore_db", "_errore", "_non_autenticato",
    "_errore_spid", "_ritorno_login", "_problema",
}
_CHIAVI_CODICE = {"type", "codice", "spid_errore"}


def _nome_chiamata(nodo: ast.Call) -> str:
    f = nodo.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _stringa(nodo: ast.AST | None) -> str | None:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    return None


def codici_nel_sorgente(testo: str) -> set[str]:
    """Codici d'errore letterali usati in contesti d'errore di un sorgente Python."""
    trovati: set[str] = set()
    for nodo in ast.walk(ast.parse(testo)):
        candidati: list[str | None] = []
        if isinstance(nodo, ast.Dict):
            for k, v in zip(nodo.keys, nodo.values, strict=True):
                if _stringa(k) in _CHIAVI_CODICE:
                    candidati.append(_stringa(v))
        elif isinstance(nodo, ast.Call):
            if _nome_chiamata(nodo) in _CHIAMATE_CON_CODICE:
                candidati += [_stringa(a) for a in nodo.args]
            candidati += [_stringa(k.value) for k in nodo.keywords if k.arg in _CHIAVI_CODICE]
        elif isinstance(nodo, ast.Assign | ast.AnnAssign):
            bersagli = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            if any(isinstance(t, ast.Name) and t.id == "codice" for t in bersagli):
                candidati.append(_stringa(nodo.value))
        trovati |= {c for c in candidati if c and _FORMA.fullmatch(c)}
    return trovati


def _sorgenti(radice: Path) -> Iterable[Path]:
    for base in (radice / "services", radice / "libs/shared"):
        for p in base.rglob("*.py"):
            parti = set(p.relative_to(radice).parts)
            if "tests" in parti or "__pycache__" in parti or "alembic" in parti:
                continue
            yield p


def codici_fuori_catalogo(radice: Path = RADICE,
                          dati: Mapping[str, Any] | None = None) -> dict[str, list[str]]:
    """{codice: [file, ...]} per i codici usati nel codice e assenti dal catalogo."""
    dati = dati if dati is not None else leggi_yaml()
    noti = set(dati["codici"])
    for v in dati["codici"].values():
        noti |= set(v.get("alias") or ())
    mancanti: dict[str, list[str]] = {}
    for p in _sorgenti(radice):
        for c in codici_nel_sorgente(p.read_text(encoding="utf-8")) - noti:
            mancanti.setdefault(c, []).append(str(p.relative_to(radice)))
    return mancanti


def codici_dismessi_usati(radice: Path = RADICE,
                          dati: Mapping[str, Any] | None = None) -> dict[str, list[str]]:
    """{codice: [file, ...]} per i codici marcati ``dismesso`` ma ancora usati nel codice."""
    dati = dati if dati is not None else leggi_yaml()
    dismessi = {c for c, v in dati["codici"].items() if v.get("dismesso")}
    usati: dict[str, list[str]] = {}
    if not dismessi:
        return usati
    for p in _sorgenti(radice):
        for c in codici_nel_sorgente(p.read_text(encoding="utf-8")) & dismessi:
            usati.setdefault(c, []).append(str(p.relative_to(radice)))
    return usati


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--verifica", action="store_true",
                    help="non scrive: esce con 1 se i file derivati non sono aggiornati")
    ap.add_argument("--codici", action="store_true",
                    help="esce con 1 se il codice dei servizi usa codici fuori catalogo "
                         "o dismessi")
    a = ap.parse_args(argv)
    if a.codici:
        mancanti = codici_fuori_catalogo()
        for codice, file in sorted(mancanti.items()):
            sys.stdout.write(f"{codice}: {', '.join(sorted(file))}" + "\n")
        dismessi = codici_dismessi_usati()
        for codice, file in sorted(dismessi.items()):
            sys.stdout.write(f"{codice} (dismesso): {', '.join(sorted(file))}" + "\n")
        return 1 if mancanti or dismessi else 0
    if a.verifica:
        vecchi = non_aggiornati()
        for p in vecchi:
            sys.stdout.write(f"da rigenerare: {p.relative_to(RADICE)}" + "\n")
        return 1 if vecchi else 0
    for p, testo in file_attesi().items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(testo, encoding="utf-8")
        sys.stdout.write(f"scritto {p.relative_to(RADICE)}" + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
