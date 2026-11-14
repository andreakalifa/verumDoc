# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/repository.py — fatti dichiarati nel repository (R15, VD-C-177).

Raccoglie dal repository, senza eseguire codice dei servizi, i fatti che i documenti
citano: registro delle criticità e delle decisioni, catalogo dei permessi e ruoli modello
(``libs/verumdoc_rules/permessi.py``), registro delle impostazioni, catalogo dei formati e
profili dei metadati (``libs/verumdoc_rules/dati``), route e menu del frontend
(``frontend/src/App.tsx``, ``frontend/src/components/Layout.tsx``), infrastruttura
dichiarata (``infra/helm/values.yaml``, ``infra/terraform``), runbook, allarmi e documenti
di architettura. Ogni file letto entra in ``fonti`` con la sua impronta SHA-256.

La fotografia (``FOTOGRAFIA``) è il risultato salvato in JSON canonico: la console
(dashboard-api) non ha il repository e genera i documenti con la fotografia del rilascio
più i dati del database. ``tools/conformita/genera_documenti.py --controlla`` (in
``make verifiche``) fallisce se la fotografia non è allineata al repository.

La fotografia non contiene date né dati di esercizio: a parità di repository è identica.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from libs.conformita.fotografia import FORMATO, FOTOGRAFIA, leggi_fotografia, serializza

RADICE = Path(__file__).resolve().parents[2]

_RIGA_CRITICITA = re.compile(
    r"^\|\s*(VD-C-\d{3})\s*\|\s*(P\d)\s*\|\s*([^|]*)\|\s*((?:[^|\\]|\\\|)*)\|"
    r"\s*((?:[^|\\]|\\\|)*)\|\s*([^|]*)\|\s*([^|]*)\|", re.MULTILINE)
_RIGA_DECISIONE = re.compile(r"^\|\s*(VD-(?:P|N|DR)-\d{2,3})\s*\|", re.MULTILINE)
_DATA_CHANGELOG = re.compile(r"\((\d{2})/(\d{2})/(\d{4})\)")


def _impronta(dati: bytes) -> str:
    return hashlib.sha256(dati).hexdigest()


class _Lettore:
    """Legge i file del repository e ne registra l'impronta fra le fonti."""

    def __init__(self, radice: Path) -> None:
        self.radice = radice
        self.fonti: dict[str, str] = {}

    def testo(self, rel: str) -> str:
        dati = (self.radice / rel).read_bytes()
        self.fonti[rel] = _impronta(dati)
        return dati.decode("utf-8")

    def json(self, rel: str) -> Any:
        return json.loads(self.testo(rel))


# ── registri ───────────────────────────────────────────────────────────

def _criticita(lettore: _Lettore) -> list[dict[str, str]]:
    testo = lettore.testo("docs/registro/criticita.md")
    voci = []
    for codice, gravita, area, titolo, _dove, stato, rilascio in _RIGA_CRITICITA.findall(testo):
        voci.append({"codice": codice, "gravita": gravita, "area": area.strip(),
                     "titolo": re.sub(r"\s+", " ", titolo.replace("\\|", "|")).strip(),
                     "stato": stato.strip(), "rilascio": rilascio.strip()})
    return voci


def _decisioni(lettore: _Lettore) -> list[str]:
    codici: set[str] = set()
    for rel in ("docs/registro/decisioni.md", "docs/registro/DECISIONI_DA_PRENDERE.md"):
        if (lettore.radice / rel).exists():
            codici |= set(_RIGA_DECISIONE.findall(lettore.testo(rel)))
    return sorted(codici)


def _rilascio(lettore: _Lettore) -> dict[str, str]:
    """Ultimo changelog (per nome) e data dichiarata nella sua prima riga."""
    file = sorted((lettore.radice / "changelog").glob("CHANGELOG-*.md"))
    if not file:
        return {}
    rel = file[-1].relative_to(lettore.radice).as_posix()
    prima = lettore.testo(rel).splitlines()[0]
    data = _DATA_CHANGELOG.search(prima)
    return {"changelog": rel, "titolo": prima.lstrip("# ").strip(),
            "data": f"{data.group(3)}-{data.group(2)}-{data.group(1)}" if data else ""}


# ── registri del codice (moduli puri di libs/verumdoc_rules) ───────────

def _permessi(lettore: _Lettore) -> dict[str, Any]:
    lettore.testo("libs/verumdoc_rules/permessi.py")
    from libs.verumdoc_rules import permessi as p

    return {"permessi": dict(p.PERMESSI), "mai_delegabili": sorted(p.MAI_DELEGABILI),
            "modelli": {r: sorted(v) for r, v in p.MODELLI.items()},
            "ruoli_base": list(p.RUOLI_BASE), "concedibili": list(p.CONCEDIBILI_POSSIBILI),
            "ruoli_conservatore": sorted(p.RUOLI_CONSERVATORE)}


def _valore(v: Any) -> Any:
    if isinstance(v, tuple | list | frozenset | set):
        return [str(x) for x in (sorted(v) if isinstance(v, frozenset | set) else v)]
    if v is None or isinstance(v, bool | int | str):
        return v
    return str(v)


def _impostazioni(lettore: _Lettore) -> list[dict[str, Any]]:
    lettore.testo("libs/verumdoc_rules/parametri_servizio.py")
    from libs.verumdoc_rules.parametri_servizio import IMPOSTAZIONI_SERVIZIO

    voci = []
    for v in sorted(IMPOSTAZIONI_SERVIZIO, key=lambda x: x.codice):
        voci.append({"codice": v.codice, "gruppo": v.gruppo, "tipo": str(v.tipo.value),
                     "predefinito": _valore(v.predefinito), "descrizione": v.descrizione,
                     "norm_ref": v.norm_ref, "applicata_da": v.applicata_da,
                     "riservata": bool(v.riservata_conservatore or v.locked)})
    return voci


def _formati(lettore: _Lettore) -> dict[str, Any]:
    rel = "libs/verumdoc_rules/dati/formati/allegato2.v1.json"
    d = lettore.json(rel)
    return {"versione": str(d.get("versione")), "valido_dal": d.get("valido_dal"),
            "norm_ref": d.get("norm_ref"), "fonte": rel,
            "formati": [{"id": f["id"], "nome": f["nome"], "famiglia": f.get("famiglia", ""),
                         "mime": list(f.get("mime") or []), "esito": f.get("esito", "")}
                        for f in d.get("formati", [])]}


def _profili(lettore: _Lettore) -> list[dict[str, Any]]:
    voci = []
    for f in sorted((lettore.radice / "libs/verumdoc_rules/dati/profili").glob("*.v*.json")):
        d = lettore.json(f.relative_to(lettore.radice).as_posix())
        campi = d.get("campi") or []
        voci.append({"codice": d.get("codice"), "nome": d.get("nome"),
                     "versione": str(d.get("versione")), "valido_dal": d.get("valido_dal"),
                     "estende": d.get("estende"), "norm_ref": d.get("norm_ref"),
                     "origine": d.get("origine"), "campi": len(campi),
                     "obbligatori": sum(1 for c in campi if c.get("obbligatorio")),
                     "da_riscontrare": sum(1 for c in campi if c.get("da_riscontrare"))})
    return voci


# ── frontend: route e menu ─────────────────────────────────────────────

_COSTANTE = re.compile(r"const\s+([A-Z_]+)\s*:\s*Ruolo\[\]\s*=\s*\[([^\]]*)\]")
_ROUTE = re.compile(r"<Route\s+path=\"([^\"]+)\"")
_ELEMENTO_PAGINA = re.compile(r"<([A-Z][A-Za-z]+Page)\b")
_GUARDIA_RUOLI = re.compile(r"roles=\{\[([^\]]*)\]\}")
_GUARDIA_COSTANTE = re.compile(r"conGuardia\(\s*([A-Z_]+|\[[^\]]*\])\s*,")
_SCOPE = re.compile(r"ScopeGuard\s+scope=\"([^\"]+)\"")
#: revisione R17 (R17-12/13): guardia e voci sul PERMESSO dell'azione
_PERMESSO_GUARDIA = re.compile(r"PermessoGuard\s+permesso=\"([^\"]+)\"")
_PERMESSO_VOCE = re.compile(r"permesso:\s*([A-Z_]+|\"[^\"]+\")")
_PERMESSO_COSTANTE = re.compile(r"export const ([A-Z_]+) = \"([a-z_]+:[a-z_]+)\";")
_AREA = re.compile(r"\{\s*label:\s*\"([^\"]+)\",\s*voci:\s*\[")
_VOCE = re.compile(r"\{\s*to:\s*\"([^\"]+)\",\s*label:\s*\"([^\"]+)\",\s*ruoli:\s*"
                   r"([A-Z_]+|\[[^\]]*\]|null)((?:,\s*(?:permesso|scope):\s*(?:[A-Z_]+|\"[^\"]+\"))*)")
_SCOPE_VOCE = re.compile(r"scope:\s*\"([^\"]+)\"")
_NOME_RUOLO = re.compile(r"^\s*([A-Z_]+):\s*\"([^\"]+)\",?$", re.MULTILINE)


def _elenco_ruoli(testo: str) -> list[str]:
    return re.findall(r"\"([A-Z_]+)\"", testo)


def _ruoli(espressione: str, costanti: dict[str, list[str]]) -> list[str] | None:
    espressione = espressione.strip()
    if espressione == "null":
        return None
    if espressione.startswith("["):
        return _elenco_ruoli(espressione)
    return list(costanti[espressione])


def _con_permesso(ruoli: list[str] | None, permesso: str | None, nomi: dict[str, str]
                  ) -> list[str] | None:
    """Ruoli FISSI che hanno il permesso (modelli di ``libs/verumdoc_rules/permessi.py``),
    intersecati con quelli della guardia di ruolo se c'è."""
    if permesso is None:
        return ruoli
    from libs.verumdoc_rules.permessi import MODELLI

    base = ruoli if ruoli is not None else [r for r in nomi if r != "API_SYSTEM"]
    return [r for r in base if permesso in MODELLI.get(r, frozenset())]


def _divergenze(route: list[dict[str, Any]], nomi: dict[str, str]) -> list[str]:
    """Revisione R17 (R17-13): pagina e voce di menu devono ammettere gli stessi ruoli
    (stesso scope): altrimenti un ruolo vede una voce che lo rimanda indietro, o apre
    dall'indirizzo una pagina che il menu non gli mostra."""
    tutti = sorted(x for x in nomi if x != "API_SYSTEM")   # nessuna guardia = ogni persona
    difetti = []
    for r in route:
        voce = r["menu"]
        if not voce:
            continue
        pagina = sorted(r["ruoli"]) if r["ruoli"] is not None else tutti
        menu = sorted(voce["ruoli"]) if voce["ruoli"] is not None else tutti
        if pagina != menu:
            difetti.append(f"{r['percorso']}: pagina {pagina} ≠ menu {menu}")
    return difetti


def _route(lettore: _Lettore) -> dict[str, Any]:
    app = lettore.testo("frontend/src/App.tsx")
    layout = lettore.testo("frontend/src/components/Layout.tsx")
    ruoli_ts = lettore.testo("frontend/src/auth/ruoli.ts")
    permessi_ts = lettore.testo("frontend/src/auth/permessi.ts")
    costanti_permesso = dict(_PERMESSO_COSTANTE.findall(permessi_ts))
    nomi = dict(_NOME_RUOLO.findall(ruoli_ts))
    costanti_app = {n: _elenco_ruoli(v) for n, v in _COSTANTE.findall(app)}
    costanti_menu = {n: _elenco_ruoli(v) for n, v in _COSTANTE.findall(layout)}
    menu: dict[str, dict[str, Any]] = {}
    inizio_aree = layout.find("const AREE")
    fine_aree = layout.find("];", inizio_aree)
    aree = list(_AREA.finditer(layout, inizio_aree, fine_aree))
    for i, m in enumerate(aree):
        area = m.group(1)
        corpo = layout[m.end():aree[i + 1].start() if i + 1 < len(aree) else fine_aree]
        for percorso, etichetta, ruoli, coda in _VOCE.findall(corpo):
            scope = _SCOPE_VOCE.search(coda)
            permesso = _PERMESSO_VOCE.search(coda)
            codice = None
            if permesso is not None:
                grezzo = permesso.group(1)
                codice = grezzo.strip('"') if grezzo.startswith('"') else costanti_permesso[grezzo]
            menu[percorso] = {"area": area, "voce": etichetta,
                              "ruoli": _con_permesso(_ruoli(ruoli, costanti_menu), codice, nomi),
                              "scope": scope.group(1) if scope else None, "permesso": codice}
    posizioni = [(m.start(), m.group(1)) for m in _ROUTE.finditer(app)]
    route = []
    for i, (inizio, percorso) in enumerate(posizioni):
        fine = posizioni[i + 1][0] if i + 1 < len(posizioni) else len(app)
        tratto = app[inizio:fine]
        pagina = _ELEMENTO_PAGINA.search(tratto)
        ruoli: list[str] | None = None
        if (m := _GUARDIA_RUOLI.search(tratto)) is not None:
            ruoli = _elenco_ruoli(m.group(1))
        elif (m := _GUARDIA_COSTANTE.search(tratto)) is not None:
            ruoli = _ruoli(m.group(1), costanti_app)
        scope = _SCOPE.search(tratto)
        permesso = _PERMESSO_GUARDIA.search(tratto)
        ruoli = _con_permesso(ruoli, permesso.group(1) if permesso else None, nomi)
        assoluto = percorso if percorso.startswith("/") else "/" + percorso
        route.append({"percorso": assoluto, "pagina": pagina.group(1) if pagina else None,
                      "ruoli": ruoli, "scope": scope.group(1) if scope else None,
                      "permesso": permesso.group(1) if permesso else None,
                      "menu": menu.get(assoluto)})
    difetti = _divergenze(route, nomi)
    if difetti:
        raise ValueError("pagine e menu ammettono ruoli diversi (revisione R17, R17-13): "
                         + "; ".join(difetti))
    return {"route": route, "nomi_ruoli": nomi}


# ── infrastruttura dichiarata ──────────────────────────────────────────

_TF = {
    "regione": r"aws_region\s*=\s*\"([^\"]+)\"",
    "regione_replica": r"replication_region\s*=\s*\"([^\"]+)\"",
    "retention_predefinita_anni": r"default_retention_years\s*=\s*(\d+)",
    "backup_giorni": r"backup_retention_days\s*=\s*(\d+)",
    "postgresql": r"engine_version\s*=\s*\"(\d+\.\d+)\"",
}


def _infrastruttura(lettore: _Lettore) -> dict[str, Any]:
    import yaml

    valori = yaml.safe_load(lettore.testo("infra/helm/values.yaml"))
    servizi = []
    for nome, s in sorted((valori.get("services") or {}).items()):
        if not isinstance(s, dict) or not s.get("enabled", True):
            continue
        servizi.append({"nome": nome, "repliche": s.get("replicas"),
                        "esposto": bool(s.get("exposed")),
                        "lavoratori": sorted((s.get("workers") or {}).keys())
                        if isinstance(s.get("workers"), dict) else [],
                        "cronjob": sorted((s.get("cronJobs") or {}).keys())
                        if isinstance(s.get("cronJobs"), dict) else []})
    segnaposto = sorted({k for k, v in _piatto(valori).items()
                         if isinstance(v, str) and v.startswith("DA_IMPOSTARE")})
    prod = lettore.testo("infra/terraform/environments/prod/main.tf")
    s3 = lettore.testo("infra/terraform/modules/s3/main.tf")
    rds = lettore.testo("infra/terraform/modules/rds/main.tf")
    terraform: dict[str, Any] = {}
    for chiave, espressione in _TF.items():
        m = re.search(espressione, prod)
        terraform[chiave] = m.group(1) if m else None
    modo = re.search(r"default_retention\s*\{\s*mode\s*=\s*\"([A-Z]+)\"", s3)
    terraform["object_lock"] = modo.group(1) if modo else None
    terraform["rds_cifrato"] = bool(re.search(r"storage_encrypted\s*=\s*true", rds))
    terraform["rds_protezione_eliminazione"] = bool(
        re.search(r"deletion_protection\s*=\s*true", rds))
    ambienti = sorted(p.name for p in (lettore.radice / "infra/terraform/environments").iterdir()
                      if p.is_dir())
    moduli = sorted(p.name for p in (lettore.radice / "infra/terraform/modules").iterdir()
                    if p.is_dir())
    return {"helm": {"servizi": servizi, "segnaposto": segnaposto,
                     "ambiente": (valori.get("global") or {}).get("environment")},
            "terraform": {**terraform, "ambienti": ambienti, "moduli": moduli}}


def _piatto(d: Any, prefisso: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        risultato: dict[str, Any] = {}
        for k, v in d.items():
            risultato.update(_piatto(v, f"{prefisso}.{k}" if prefisso else str(k)))
        return risultato
    return {prefisso: d}


# ── documenti di esercizio ─────────────────────────────────────────────

def _titoli(lettore: _Lettore, cartella: str) -> list[dict[str, str]]:
    voci = []
    for f in sorted((lettore.radice / cartella).glob("*.md")):
        rel = f.relative_to(lettore.radice).as_posix()
        titolo = next((r.lstrip("# ").strip() for r in lettore.testo(rel).splitlines()
                       if r.startswith("# ")), f.stem)
        voci.append({"file": rel, "titolo": titolo})
    return voci


def _allarmi(lettore: _Lettore) -> list[dict[str, str]]:
    import yaml

    voci = []
    for f in sorted((lettore.radice / "infra/observability/regole").glob("*.yml")):
        dati = yaml.safe_load(lettore.testo(f.relative_to(lettore.radice).as_posix()))
        for gruppo in dati.get("groups", []):
            for r in gruppo.get("rules", []):
                if "alert" in r:
                    voci.append({"nome": r["alert"], "gravita": r["labels"]["severity"],
                                 "sintesi": r["annotations"]["summary"]})
    return sorted(voci, key=lambda v: v["nome"])


def raccogli(radice: Path = RADICE) -> dict[str, Any]:
    """Fatti del repository (dizionario serializzabile, senza date né dati di esercizio)."""
    if str(radice) not in sys.path:
        sys.path.insert(0, str(radice))
    lettore = _Lettore(radice)
    fatti: dict[str, Any] = {
        "formato": FORMATO,
        "rilascio": _rilascio(lettore),
        "criticita": _criticita(lettore),
        "decisioni": _decisioni(lettore),
        "permessi": _permessi(lettore),
        "impostazioni": _impostazioni(lettore),
        "formati": _formati(lettore),
        "profili": _profili(lettore),
        "frontend": _route(lettore),
        "infrastruttura": _infrastruttura(lettore),
        "runbook": _titoli(lettore, "docs/runbooks"),
        "architettura": _titoli(lettore, "docs/architettura"),
        "allarmi": _allarmi(lettore),
    }
    # fonti citate dai documenti senza estrarne dati: requisiti normativi (ricerca interna)
    # e contratto delle API (OpenAPI generata)
    for rel in ("docs/audit/allegati/ricerca_normativa.md", "docs/catalogo/openapi.yaml"):
        lettore.testo(rel)
    fatti["fonti"] = dict(sorted(lettore.fonti.items()))
    return fatti


__all__ = ["FORMATO", "FOTOGRAFIA", "RADICE", "leggi_fotografia", "raccogli", "serializza"]
