# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/docs/genera_cataloghi.py — cataloghi generati dai registri del codice (R14, VD-C-082).

REGOLE_SVILUPPO §28.1: i cataloghi in ``docs/catalogo/`` si generano, mai a mano. Questo
comando rigenera (o, con ``--controlla``, verifica senza scrivere) tutti i cataloghi:

  endpoint.md, openapi.yaml   da ``tools/docs/genera_openapi.py`` (R03)
  errori.md                   da ``tools/docs/genera_catalogo_errori.py`` (R03)
  impostazioni.md             dal registro ``libs.verumdoc_rules.parametri_servizio``
                              (codice, gruppo, tipo, predefinito, limiti, livelli, verso
                              «più sicuro», chi la applica, riferimento normativo)
  topic.md                    da ``infra/kafka/topics.yaml`` (produttori, consumatori,
                              partizioni, retention, chiave)
  metriche.md                 dalle metriche registrate nel codice (Counter, Gauge, Histogram
                              e raccoglitori propri) e dalle regole d'allarme di
                              ``infra/observability/regole`` (allarme, gravità, metriche)

R15 (VD-C-177): route del frontend (``route.md``) e matrice ruoli × permessi
(``permessi.md``) li genera ``tools/conformita/genera_documenti.py`` insieme al manuale.
Non ancora generati (§28.1): catalogo delle regole, classificazione dei modelli.

Uso:
    python tools/docs/genera_cataloghi.py               # rigenera
    python tools/docs/genera_cataloghi.py --controlla   # uscita 1 se un catalogo è vecchio
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
CATALOGO = RADICE / "docs" / "catalogo"
INTESTAZIONE = ("<!-- File generato da tools/docs/genera_cataloghi.py: non modificare a mano "
                "(REGOLE_SVILUPPO §28). -->\n\n")


def _cella(valore: object) -> str:
    testo = "" if valore is None else str(valore)
    return testo.replace("|", "\\|").replace("\n", " ").strip()


def _valore(v: object) -> str:
    if isinstance(v, tuple | list):
        return ", ".join(str(x) for x in v) if v else "(vuoto)"
    if isinstance(v, bool):
        return "sì" if v else "no"
    return str(v)


# ── impostazioni ───────────────────────────────────────────────────────

def impostazioni_md() -> str:
    if str(RADICE) not in sys.path:
        sys.path.insert(0, str(RADICE))
    from libs.verumdoc_rules.parametri_servizio import IMPOSTAZIONI_SERVIZIO

    voci = sorted(IMPOSTAZIONI_SERVIZIO, key=lambda v: (v.gruppo or "", v.codice))
    righe = [INTESTAZIONE, "# Catalogo delle impostazioni\n\n",
             f"{len(voci)} voci del registro `libs/verumdoc_rules/parametri_servizio.py` "
             "(console «Impostazioni»; catena di risoluzione in `libs/shared/parametri.py`).\n\n",
             "| Codice | Gruppo | Tipo | Predefinito | Limiti | Livelli | Verso più sicuro | "
             "Applicata da | Riferimento |\n|---|---|---|---|---|---|---|---|---|\n"]
    for v in voci:
        limiti = ""
        if v.minimo is not None or v.massimo is not None:
            minimo = "" if v.minimo is None else v.minimo
            massimo = "" if v.massimo is None else v.massimo
            limiti = f"{minimo}–{massimo}"
        elif v.scelte:
            limiti = " / ".join(v.scelte)
        livelli = ", ".join(sorted(str(getattr(x, "value", x)) for x in v.ambito_ammesso)) \
            if v.ambito_ammesso else "tutti"
        if v.riservata_conservatore:
            livelli += " (riservata al conservatore)"
        verso = getattr(v.verso_sicuro, "value", v.verso_sicuro)
        righe.append("| " + " | ".join(_cella(x) for x in (
            f"`{v.codice}`", v.gruppo, getattr(v.tipo, "value", v.tipo), _valore(v.predefinito),
            limiti, livelli, verso, v.applicata_da or "non collegata", v.norm_ref or "")) + " |\n")
    return "".join(righe)


# ── topic ──────────────────────────────────────────────────────────────

def topic_md() -> str:
    import yaml

    dati = yaml.safe_load((RADICE / "infra" / "kafka" / "topics.yaml").read_text("utf-8"))
    topic = sorted(dati["topics"], key=lambda t: t["name"])
    righe = [INTESTAZIONE, "# Catalogo dei topic Kafka\n\n",
             f"{len(topic)} topic da `infra/kafka/topics.yaml` (unica fonte, anche dei permessi "
             "IAM/ACL per servizio).\n\n",
             "| Topic | Partizioni | Retention | Produttori | Consumatori | Chiave |\n"
             "|---|---|---|---|---|---|\n"]
    for t in topic:
        ms = int(t["retention_ms"])
        retention = "senza scadenza" if ms < 0 else (
            f"{ms // 86_400_000} giorni" if ms % 86_400_000 == 0 else f"{ms // 3_600_000} h")
        righe.append("| " + " | ".join(_cella(x) for x in (
            f"`{t['name']}`", t["partitions"], retention, ", ".join(t.get("producers") or []),
            ", ".join(t.get("consumers") or []), t.get("key") or "")) + " |\n")
    return "".join(righe)


# ── metriche e allarmi ─────────────────────────────────────────────────

_METRICA = re.compile(
    r"\b(Counter|Gauge|Histogram|Summary)\(\s*[\"']([a-z_:][a-z0-9_:]*)[\"']\s*,\s*"
    r"(?:\(\s*)?[\"']([^\"']*)[\"']")
_CHIAVE = re.compile(r"[\"'](verumdoc_[a-z0-9_]+)[\"']\s*:\s*\(?\s*[\"']([^\"']*)[\"']")


def metriche_del_codice(radice: Path = RADICE) -> dict[str, tuple[str, str, str]]:
    """{nome: (tipo, descrizione, file)} delle metriche registrate nel codice."""
    trovate: dict[str, tuple[str, str, str]] = {}
    for cartella in ("services", "libs"):
        for f in sorted((radice / cartella).rglob("*.py")):
            if "tests" in f.parts or "__pycache__" in f.parts:
                continue
            testo = f.read_text(encoding="utf-8")
            rel = f.relative_to(radice).as_posix()
            for tipo, nome, descr in _METRICA.findall(testo):
                trovate.setdefault(nome, (tipo, descr, rel))
            for nome, descr in _CHIAVE.findall(testo):
                trovate.setdefault(nome, ("Gauge", descr, rel))
    return trovate


def allarmi(radice: Path = RADICE) -> list[dict[str, object]]:
    import yaml

    elenco = []
    for f in sorted((radice / "infra" / "observability" / "regole").glob("*.yml")):
        for gruppo in yaml.safe_load(f.read_text("utf-8"))["groups"]:
            for r in gruppo["rules"]:
                elenco.append({**r, "_file": f.name})
    return elenco


def metriche_md() -> str:
    metriche = metriche_del_codice()
    regole = allarmi()
    usate: dict[str, list[str]] = {}
    for r in regole:
        for nome in re.findall(r"\b(verumdoc_[a-z0-9_]+)\b", str(r["expr"])):
            base = re.sub(r"_(total|bucket|sum|count)$", "", nome)
            usate.setdefault(base, []).append(str(r["alert"]))
    righe = [INTESTAZIONE, "# Catalogo delle metriche e degli allarmi\n\n",
             f"{len(metriche)} metriche registrate nel codice, {len(regole)} allarmi in "
             "`infra/observability/regole/`; cruscotto in "
             "`infra/observability/grafana/cruscotti/`.\n\n## Metriche\n\n",
             "| Metrica | Tipo | Descrizione | Dove | Allarmi |\n|---|---|---|---|---|\n"]
    for nome in sorted(metriche):
        tipo, descr, dove = metriche[nome]
        righe.append("| " + " | ".join(_cella(x) for x in (
            f"`{nome}`", tipo, descr, f"`{dove}`",
            ", ".join(sorted(set(usate.get(nome, []))))) ) + " |\n")
    righe.append("\n## Allarmi\n\n| Allarme | Gravità | Attesa | Sintesi | File |\n"
                 "|---|---|---|---|---|\n")
    for r in sorted(regole, key=lambda x: str(x["alert"])):
        righe.append("| " + " | ".join(_cella(x) for x in (
            f"`{r['alert']}`", r["labels"]["severity"], r.get("for", "0m"),  # type: ignore[index]
            r["annotations"]["summary"], r["_file"])) + " |\n")  # type: ignore[index]
    return "".join(righe)


GENERATORI: dict[str, Callable[[], str]] = {
    "impostazioni.md": impostazioni_md,
    "topic.md": topic_md,
    "metriche.md": metriche_md,
}


def _esterni(controlla: bool) -> list[str]:
    """Generatori di R03 (OpenAPI ed endpoint, catalogo errori) nello stesso comando."""
    falliti = []
    for script in ("genera_openapi.py", "genera_catalogo_errori.py"):
        argomenti = [sys.executable, str(RADICE / "tools" / "docs" / script)]
        if controlla:
            argomenti.append("--verifica")
        if subprocess.run(argomenti, cwd=RADICE, check=False).returncode != 0:  # noqa: S603
            falliti.append(script)
    return falliti


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--controlla", "--verifica", action="store_true", dest="controlla",
                    help="non scrive; uscita 1 se un catalogo non è allineato")
    ap.add_argument("--solo-nuovi", action="store_true",
                    help="solo i cataloghi di questo comando (senza OpenAPI ed errori)")
    a = ap.parse_args(argv)
    vecchi: list[str] = []
    for nome, genera in GENERATORI.items():
        atteso = genera()
        percorso = CATALOGO / nome
        attuale = percorso.read_text(encoding="utf-8") if percorso.exists() else None
        if attuale == atteso:
            continue
        if a.controlla:
            vecchi.append(f"docs/catalogo/{nome}")
        else:
            percorso.write_text(atteso, encoding="utf-8")
            print(f"scritto docs/catalogo/{nome}")
    if not a.solo_nuovi:
        vecchi += _esterni(a.controlla)
    if vecchi:
        print("cataloghi non allineati (rigenerare con python tools/docs/genera_cataloghi.py): "
              + ", ".join(vecchi), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
