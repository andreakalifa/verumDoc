# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/docs/verifica_documentazione.py — controllo statico della documentazione (R14, VD-C-082).

REGOLE_SVILUPPO §0.3 e §0.4: la documentazione si allinea a ogni rilascio. Questo controllo
fallisce (uscita 1) se un documento vivo:

  1. ha un collegamento Markdown interno ``[testo](percorso)`` a un file che non esiste;
  2. cita fra apici inversi un percorso del repository (``docs/…``, ``services/…``,
     ``libs/…``, ``tools/…``, ``infra/…``, ``frontend/…``, ``sdk/…``, ``changelog/…``) che
     non esiste (con i caratteri jolly ``*`` e ``<…>`` il controllo si salta);
  3. cita una criticità ``VD-C-NNN`` che non è nel registro (``docs/registro/criticita.md``)
     o una decisione ``VD-P-NNN`` (R15: anche ``VD-N-NN`` e ``VD-DR-NN``) che non è in
     ``docs/registro/decisioni.md`` né in ``docs/registro/DECISIONI_DA_PRENDERE.md``;
  4. R15 (VD-C-177): in un documento GENERATO da ``tools/conformita/genera_documenti.py``
     (riconosciuto dalla prima riga) compare un segnaposto non dichiarato. L'unico
     segnaposto ammesso è il blocco ``> **DA COMPILARE** — … Fornisce: <chi>. … Campo:
     `<campo>`.`` (con chi e campo non vuoti), richiamato nelle tabelle come «DA COMPILARE
     (vedi sotto)» e seguito dal blocco nella stessa sezione; un requisito non riscontrato è
     ``> *da verificare sul testo ufficiale* — <requisito>: <motivo>.``. Sono segnaposto non
     dichiarati: «[da compilare]», «TODO», «TBD», «XXX», «DA_IMPOSTARE», «lorem ipsum»,
     «???», «<da …>», un «DA COMPILARE» in altra forma.

Documenti vivi: i ``.md`` della radice e di ``docs/`` (tranne ``docs/storico``, le fotografie
datate di ``docs/audit`` e ``docs/design``) e i README dei servizi e degli strumenti. I
changelog sono storici (un percorso tolto dopo resta citato com'era) e non si controllano.

Uso:
    python tools/docs/verifica_documentazione.py            # elenco dei problemi, uscita 1/0
    python tools/docs/verifica_documentazione.py --sommario # solo i conteggi
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
ESCLUSI = ("docs/storico/", "docs/audit/", "docs/design/", "changelog/", "node_modules/")
PREFISSI = ("docs/", "services/", "libs/", "tools/", "infra/", "frontend/", "sdk/",
            "changelog/")
CRITICITA = RADICE / "docs" / "registro" / "criticita.md"
DECISIONI = (RADICE / "docs" / "registro" / "decisioni.md",
             RADICE / "docs" / "registro" / "DECISIONI_DA_PRENDERE.md")

#: percorsi generati o previsti, citati di proposito anche quando non ci sono: segreti di
#: sviluppo generati (``tools/dev/genera_segreti_dev.py``, ignorati da git), foto di
#: verifica grafica generate a richiesta (§25.13), rapporti di Playwright, manuale (Fase 5),
#: schema UNI da acquistare (VD-C-071), file locale dei termini riservati (R18)
AMMESSI = tuple(re.compile(m) for m in (
    r"^infra/dev_[a-z_]+\.env$", r"^docs/design/foto/?$", r"^frontend/playwright-report/?$",
    r"^frontend/test-results/?$", r"^frontend/dist/?$", r"^docs/manuale/?$",
    r"^libs/sincro/schemas/uni11386-2020/?$",
    # R18 (VD-P-190): termini riservati del controllo della pubblicazione, solo locale
    r"^tools/opensource/pubblicazione\.locale\.toml$",
))
#: una riga che dice che un percorso è stato tolto lo cita di proposito
_TOLTO = re.compile(r"rimoss|rimozione|tolt[oaie]\b|eliminat|sostituit|non esiste|dismess",
                    re.IGNORECASE)
#: registri storici: i percorsi citati nelle voci chiuse possono non esistere più
STORICI = ("docs/registro/",)

_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
_CODICE = re.compile(r"`([^`\n]+)`")
_VDC = re.compile(r"\bVD-C-(\d{3})\b")
_VDP = re.compile(r"\bVD-P-(\d{2,3})\b")
_RIGA_VDC = re.compile(r"^\|\s*VD-C-(\d{3})\s*\|", re.MULTILINE)
_RIGA_VDP = re.compile(r"^\|\s*VD-P-(\d{2,3})\s*\|", re.MULTILINE)
#: R15: decisioni normative e di design
_VDND = re.compile(r"\b(VD-(?:N|DR)-\d{2,3})\b")
_RIGA_VDND = re.compile(r"^\|\s*(VD-(?:N|DR)-\d{2,3})\s*\|", re.MULTILINE)

#: R15 (VD-C-177): documenti generati e loro segnaposto
MARCA_GENERATO = "<!-- File generato da tools/conformita/genera_documenti.py"
_DICHIARATO = re.compile(
    r"^> \*\*DA COMPILARE\*\* — (?P<cosa>.+?)\. Fornisce: (?P<chi>[^.]+?)\.(?: Dove: .+?\.)? "
    r"Campo: `(?P<campo>[a-z0-9_.\-/A-Z]+)`\.$")
_DA_VERIFICARE = re.compile(r"^> \*da verificare sul testo ufficiale\* — (.+?): (.+)\.$")
_CELLA = "DA COMPILARE (vedi sotto)"
_SEGNAPOSTO = re.compile(
    r"\[da compilare\]|\bTODO\b|\bTBD\b|\bXXX\b|DA_IMPOSTARE|lorem ipsum|\?\?\?|<da [^>]*>",
    re.IGNORECASE)


@dataclass(frozen=True)
class Problema:
    file: str
    riga: int
    tipo: str
    dettaglio: str

    def __str__(self) -> str:
        return f"{self.file}:{self.riga}: {self.tipo}: {self.dettaglio}"


def documenti(radice: Path = RADICE) -> list[Path]:
    """I documenti vivi da controllare (vedi modulo)."""
    candidati = list(radice.glob("*.md")) + list((radice / "docs").rglob("*.md"))
    for cartella in ("services", "tools", "libs", "infra", "sdk"):
        candidati += list((radice / cartella).rglob("README*.md"))
    vivi = []
    for p in sorted(set(candidati)):
        rel = p.relative_to(radice).as_posix()
        if not any(rel.startswith(e) or f"/{e}" in rel for e in ESCLUSI):
            vivi.append(p)
    return vivi


def codici_registrati(radice: Path = RADICE) -> tuple[set[str], set[str]]:
    vdc = set(_RIGA_VDC.findall((radice / CRITICITA.relative_to(RADICE)).read_text("utf-8")))
    vdp: set[str] = set()
    for f in DECISIONI:
        percorso = radice / f.relative_to(RADICE)
        if percorso.exists():
            testo = percorso.read_text("utf-8")
            vdp |= {str(int(n)) for n in _RIGA_VDP.findall(testo)}
            vdp |= set(_RIGA_VDND.findall(testo))
    return vdc, vdp


def controlla_generato(rel: str, testo: str) -> list[Problema]:
    """R15: segnaposto di un documento generato (vedi punto 4 del modulo)."""
    problemi: list[Problema] = []
    in_attesa: list[int] = []          # righe di tabella «vedi sotto» della sezione corrente
    dichiarati_sezione = 0

    def chiudi_sezione() -> None:
        if in_attesa and dichiarati_sezione == 0:
            for riga in in_attesa:
                problemi.append(Problema(rel, riga, "segnaposto non dichiarato",
                                         "«DA COMPILARE (vedi sotto)» senza blocco nella sezione"))

    for n, riga in enumerate(testo.splitlines(), start=1):
        if riga.startswith("#"):
            chiudi_sezione()
            in_attesa, dichiarati_sezione = [], 0
        if riga.startswith("> **DA COMPILARE**"):
            m = _DICHIARATO.match(riga)
            if not m or not m.group("chi").strip() or not m.group("cosa").strip():
                problemi.append(Problema(rel, n, "segnaposto non dichiarato",
                                         "DA COMPILARE senza «Fornisce: <chi>» o senza campo"))
            dichiarati_sezione += 1
            continue
        if riga.startswith("> *da verificare sul testo ufficiale*"):
            if not _DA_VERIFICARE.match(riga):
                problemi.append(Problema(rel, n, "segnaposto non dichiarato",
                                         "da verificare senza requisito o motivo"))
            continue
        resto = riga.replace(_CELLA, "")
        if _CELLA in riga:
            in_attesa.append(n)
        if "DA COMPILARE" in resto:
            problemi.append(Problema(rel, n, "segnaposto non dichiarato",
                                     "«DA COMPILARE» fuori dal blocco dichiarato"))
        for m in _SEGNAPOSTO.finditer(resto):
            problemi.append(Problema(rel, n, "segnaposto non dichiarato", m.group(0)))
    chiudi_sezione()
    return problemi


def _percorso_citato(testo: str) -> str | None:
    """Il percorso di repository citato fra apici inversi, se è uno."""
    t = testo.strip().split()[0] if testo.strip() else ""
    t = t.rstrip(".,;:)")
    if not t.startswith(PREFISSI) or any(c in t for c in "*<>{}$|"):
        return None
    t = re.sub(r":\d+(?:[-–,]\d+)*$", "", t)     # file.py:123, file.py:40,91
    t = t.split("#", 1)[0].split("::", 1)[0]
    return t or None


def esiste(percorso: str, radice: Path = RADICE) -> bool:
    """Il percorso esiste, è ammesso, è un prefisso di file esistenti (``docs/adr/ADR-001``)
    o un nome Python puntato (``libs/shared/parametri.orizzonte_permanente_anni``)."""
    if any(m.match(percorso) for m in AMMESSI) or (radice / percorso).exists():
        return True
    parti = percorso.rstrip("/").split("/")
    if parti[0] == "services" and len(parti) > 1 and not (radice / "services" / parti[1]).is_dir():
        return True          # «services/coda.py» relativo all'app di un servizio, non al repo
    cartella, nome = (radice / percorso).parent, parti[-1]
    if cartella.is_dir() and any(f.name.startswith(nome) for f in cartella.iterdir()):
        return True
    if "." in nome:
        modulo = "/".join(parti[:-1] + [nome.split(".", 1)[0]])
        return (radice / f"{modulo}.py").exists() or (radice / modulo).is_dir()
    return False


def _righe_senza_blocchi(testo: str) -> Iterable[tuple[int, str]]:
    """Righe fuori dai blocchi di codice recintati (``` … ```)."""
    dentro = False
    for n, riga in enumerate(testo.splitlines(), start=1):
        if riga.lstrip().startswith("```"):
            dentro = not dentro
            continue
        if not dentro:
            yield n, riga


def controlla_file(p: Path, vdc: set[str], vdp: set[str],
                   radice: Path = RADICE) -> list[Problema]:
    rel = p.relative_to(radice).as_posix()
    storico = rel.startswith(STORICI)
    problemi: list[Problema] = []
    testo = p.read_text(encoding="utf-8")
    if testo.startswith(MARCA_GENERATO):
        problemi += controlla_generato(rel, testo)
    for n, riga in _righe_senza_blocchi(testo):
        for destinazione in _LINK.findall(riga):
            if re.match(r"^[a-z][a-z0-9+.-]*:", destinazione) or destinazione.startswith("#"):
                continue                                   # http:, mailto:, ancora interna
            bersaglio = destinazione.split("#", 1)[0]
            if not bersaglio:
                continue
            if not (p.parent / bersaglio).exists() and not (radice / bersaglio).exists():
                problemi.append(Problema(rel, n, "collegamento rotto", destinazione))
        for citato in ([] if storico or _TOLTO.search(riga) else _CODICE.findall(riga)):
            percorso = _percorso_citato(citato)
            if percorso and not esiste(percorso, radice):
                problemi.append(Problema(rel, n, "percorso inesistente", percorso))
        for codice in _VDC.findall(riga):
            if codice not in vdc:
                problemi.append(Problema(rel, n, "criticità non registrata", f"VD-C-{codice}"))
        for codice in _VDP.findall(riga):
            if str(int(codice)) not in vdp:
                problemi.append(Problema(rel, n, "decisione non registrata", f"VD-P-{codice}"))
        for codice in _VDND.findall(riga):
            if codice not in vdp and not storico:
                problemi.append(Problema(rel, n, "decisione non registrata", codice))
    return problemi


def verifica(radice: Path = RADICE) -> list[Problema]:
    vdc, vdp = codici_registrati(radice)
    problemi: list[Problema] = []
    for p in documenti(radice):
        problemi += controlla_file(p, vdc, vdp, radice)
    return problemi


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sommario", action="store_true", help="solo i conteggi per tipo")
    a = ap.parse_args(argv)
    problemi = verifica()
    if a.sommario:
        conteggi: dict[str, int] = {}
        for pr in problemi:
            conteggi[pr.tipo] = conteggi.get(pr.tipo, 0) + 1
        print(conteggi or "nessun problema")
    else:
        for pr in problemi:
            print(pr)
    print(f"documentazione: {len(documenti())} documenti, {len(problemi)} problemi",
          file=sys.stderr)
    return 1 if problemi else 0


if __name__ == "__main__":
    raise SystemExit(main())
