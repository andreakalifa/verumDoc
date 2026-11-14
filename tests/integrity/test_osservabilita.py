# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R14 (VD-C-033): osservabilità collegata e coerente su tutti i servizi.

  - ogni servizio con ``app/main.py`` è fra i bersagli di Prometheus dello stack locale;
  - ogni metrica registrata nel codice ha il prefisso ``verumdoc_`` (nessun nome fuori
    convenzione: le regole e il cruscotto li cercano così);
  - il cruscotto Grafana versionato è JSON valido e cita solo metriche registrate;
  - ogni allarme citato nella documentazione viva (runbook compresi) esiste nelle regole;
  - ogni allarme a pagina dice che cosa fare (descrizione non vuota).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parents[2]
OSS = RADICE / "infra" / "observability"


def _cataloghi():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "genera_cataloghi", RADICE / "tools" / "docs" / "genera_cataloghi.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _nomi_esposti() -> set[str]:
    nomi = set(_cataloghi().metriche_del_codice())
    return nomi | {f"{n}_{s}" for n in nomi for s in ("total", "bucket", "sum", "count")}


def test_tutti_i_servizi_fra_i_bersagli_di_prometheus() -> None:
    servizi = {p.parent.parent.name for p in (RADICE / "services").glob("*/app/main.py")}
    testo = (OSS / "prometheus.yml").read_text(encoding="utf-8")
    bersagli = set(re.findall(r"^\s*-\s*([a-z][a-z0-9-]*):\d+", testo, re.MULTILINE))
    assert servizi - bersagli == set(), f"servizi senza scrape: {sorted(servizi - bersagli)}"


def test_metriche_con_prefisso_verumdoc() -> None:
    fuori = sorted(n for n in _cataloghi().metriche_del_codice() if not n.startswith("verumdoc_"))
    assert fuori == [], f"metriche fuori convenzione: {fuori}"


def test_cruscotto_grafana_cita_solo_metriche_registrate() -> None:
    cruscotti = sorted((OSS / "grafana" / "cruscotti").glob("*.json"))
    assert cruscotti, "nessun cruscotto versionato"
    esposte = _nomi_esposti()
    for f in cruscotti:
        dati = json.loads(f.read_text(encoding="utf-8"))
        assert dati["uid"] and dati["title"] and dati["panels"]
        espressioni = [t["expr"] for p in dati["panels"] for t in p.get("targets", [])]
        assert espressioni
        mancanti = {n for e in espressioni for n in re.findall(r"\b(verumdoc_[a-z0-9_]+)", e)
                    } - esposte
        assert mancanti == set(), f"{f.name}: metriche inesistenti {sorted(mancanti)}"
    provisioning = yaml.safe_load((OSS / "grafana" / "provisioning" / "dashboards" /
                                   "verumdoc.yaml").read_text(encoding="utf-8"))
    assert provisioning["providers"][0]["options"]["path"] == "/var/lib/grafana/cruscotti"


def _allarmi() -> dict[str, dict]:
    return {r["alert"]: r for f in sorted((OSS / "regole").glob("*.yml"))
            for g in yaml.safe_load(f.read_text(encoding="utf-8"))["groups"]
            for r in g["rules"]}


_CITAZIONE = (re.compile(r"[Aa]llarm[ei]\b[^`\n]{0,60}?`([A-Z][a-z]+[A-Z][A-Za-z0-9]+)`"),
              re.compile(r"[Aa]llarm[ei] (?:\*\*)?`?([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b"))


def allarmi_citati(radice: Path = RADICE) -> dict[str, list[str]]:
    """{allarme: [documenti]} degli allarmi citati nei documenti vivi."""
    spec = importlib.util.spec_from_file_location(
        "verifica_documentazione", radice / "tools" / "docs" / "verifica_documentazione.py")
    assert spec is not None and spec.loader is not None
    v = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = v
    spec.loader.exec_module(v)
    citati: dict[str, list[str]] = {}
    for p in v.documenti(radice):
        testo = p.read_text(encoding="utf-8")
        for modello in _CITAZIONE:
            for nome in modello.findall(testo):
                citati.setdefault(nome, []).append(p.relative_to(radice).as_posix())
    return citati


def test_ogni_allarme_citato_nei_runbook_esiste() -> None:
    esistenti = set(_allarmi())
    citati = allarmi_citati()
    assert any(f.startswith("docs/runbooks/") for v in citati.values() for f in v)
    mancanti = {k: v for k, v in citati.items() if k not in esistenti}
    assert mancanti == {}, f"allarmi citati ma inesistenti: {mancanti}"


def test_allarmi_r14_presenti_e_descritti() -> None:
    allarmi = _allarmi()
    for nome in ("PraticaSospesa", "PostaCodaFerma", "PostaNonPartita", "PecSenzaRicevuta",
                 "PecCanaleNonUtilizzabile", "PecLavoratoreFermo", "PecRicevuteNonAutentiche",
                 "ArretratoIpdaScartati"):
        assert nome in allarmi, nome
    for nome, r in allarmi.items():
        if r["labels"]["severity"] == "pagina":
            assert len(r["annotations"].get("description", "")) > 40, nome
