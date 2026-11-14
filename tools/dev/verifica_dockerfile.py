# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Controllo statico dei Dockerfile dei servizi (la costruzione delle immagini non
è eseguibile ovunque: qui si verifica senza Docker ciò che la farebbe fallire).

Per ogni services/<svc>/Dockerfile:
  1. import: si seguono gli import di app/ (test esclusi) e, ricorsivamente, dei
     moduli di libs/ che usa. Se usa libs/ il Dockerfile deve copiare libs/ e
     impostare PYTHONPATH=/app;
  2. dipendenze: ogni pacchetto di terze parti importato deve stare fra le
     dipendenze di runtime del pyproject (non fra quelle [dev]). Gli import
     facoltativi (protetti da try/except ImportError nel codice) sono elencati
     in OPZIONALI e solo segnalati;
  3. COPY: ogni sorgente copiata esiste nel repository;
  4. HEALTHCHECK: usa solo la libreria standard (httpx/curl non sono garantiti
     nell'immagine) e interroga il percorso «live» dichiarato in
     infra/helm/values.yaml per quel servizio.

R08: un servizio Java (``pom.xml``, es. verifica-firme con EU DSS) non ha
import Python: si controllano le COPY e che l'HEALTHCHECK sia la classe Java
dell'immagine (``salute/Salute.java``) che interroga il percorso «live» dei values.

Uso:  python tools/dev/verifica_dockerfile.py      (esce con 1 se trova errori)
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parents[2]
STDLIB = set(sys.stdlib_module_names) | {"__future__"}

#: nome del modulo importato → nome della distribuzione pip
DISTRIBUZIONI = {
    "jwt": "pyjwt", "jose": "python-jose", "yaml": "pyyaml", "dateutil": "python-dateutil",
    "pydantic_settings": "pydantic-settings", "prometheus_client": "prometheus-client",
    "pyhanko": "pyhanko", "pyhanko_certvalidator": "pyhanko-certvalidator",
    "pkcs11": "python-pkcs11",
    "multipart": "python-multipart", "botocore": "boto3", "starlette": "fastapi",
    "typing_extensions": "pydantic", "opensearchpy": "opensearch-py",
    # R04: token IAM per Kafka su MSK (libs/shared/kafka/connessione.py)
    "aws_msk_iam_sasl_signer": "aws-msk-iam-sasl-signer-python",
    # R16 (VD-C-176): tracce distribuite (libs/shared/observability/tracciamento.py)
    "opentelemetry": "opentelemetry-sdk",
}
#: import facoltativi: il codice li protegge e funziona (degradato) senza
OPZIONALI = {
    "opentelemetry": "libs/shared/observability: tracing disattivato se assente",
    "opensearchpy": "distribution/app/services/search_index.py: indicizzazione saltata con avviso",
}


def _moduli_importati(file: Path) -> list[str]:
    albero = ast.parse(file.read_text(encoding="utf-8"), str(file))
    nomi: list[str] = []
    for n in ast.walk(albero):
        if isinstance(n, ast.Import):
            nomi += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            nomi.append(n.module)
            nomi += [f"{n.module}.{a.name}" for a in n.names]
    return nomi


def _file_di_libs(modulo: str, radice: Path) -> Path | None:
    percorso = radice.joinpath(*modulo.split("."))
    if percorso.with_suffix(".py").is_file():
        return percorso.with_suffix(".py")
    if (percorso / "__init__.py").is_file():
        return percorso / "__init__.py"
    return None


def analizza_import(app: Path, radice: Path = RADICE) -> tuple[set[str], set[str]]:
    """(file di libs/ usati, moduli di terze parti) di app/ e della sua chiusura in libs/."""
    da_vedere = [p for p in app.rglob("*.py") if "tests" not in p.parts]
    visti: set[Path] = set()
    libs: set[str] = set()
    terze: set[str] = set()
    while da_vedere:
        file = da_vedere.pop()
        if file in visti:
            continue
        visti.add(file)
        for modulo in _moduli_importati(file):
            primo = modulo.split(".")[0]
            if primo == "libs":
                parti = modulo.split(".")
                for i in range(1, len(parti) + 1):
                    trovato = _file_di_libs(".".join(parti[:i]), radice)
                    if trovato is not None:
                        libs.add(str(trovato.relative_to(radice)))
                        da_vedere.append(trovato)
            elif primo not in STDLIB and primo not in ("app", "tests", "conftest"):
                terze.add(primo)
    return libs, terze


def _nome_distribuzione(requisito: str) -> str:
    return re.split(r"[\[<>=!~ ;]", requisito, maxsplit=1)[0].lower().replace("_", "-")


def verifica_servizio_java(svc: Path, values: dict, radice: Path = RADICE
                           ) -> tuple[list[str], list[str]]:
    """R08: Dockerfile di un servizio Java (multi-stadio Maven)."""
    errori: list[str] = []
    nome = svc.name
    testo = (svc / "Dockerfile").read_text(encoding="utf-8")
    for sorgente, _ in re.findall(r"^COPY\s+(?!--from)(\S+)\s+(\S+)", testo, re.M):
        if not (radice / sorgente).exists():
            errori.append(f"{nome}: COPY di un percorso inesistente: {sorgente}")
    if not re.search(r"^USER\s+(?!root)\S+", testo, re.M):
        errori.append(f"{nome}: l'immagine deve girare con un utente non root")
    controllo = re.search(r"^HEALTHCHECK.*?CMD\s+(.+?)$", testo, re.M | re.S)
    salute = svc / "salute" / "Salute.java"
    live = ((values.get("services") or {}).get(nome) or {}).get("health", {}).get("live")
    if controllo is None or "Salute" not in controllo.group(1):
        errori.append(f"{nome}: HEALTHCHECK deve usare la classe Java salute/Salute")
    elif not salute.is_file() or (live and f"{live}\"" not in salute.read_text(encoding="utf-8")):
        errori.append(
            f"{nome}: salute/Salute.java non interroga {live} (health.live in values.yaml)")
    return errori, []


def verifica_servizio(
    svc: Path, values: dict, radice: Path = RADICE,
) -> tuple[list[str], list[str]]:
    if (svc / "pom.xml").is_file() and not (svc / "pyproject.toml").is_file():
        return verifica_servizio_java(svc, values, radice)
    errori: list[str] = []
    avvisi: list[str] = []
    nome = svc.name
    testo = (svc / "Dockerfile").read_text(encoding="utf-8")
    copie = re.findall(r"^COPY\s+(?!--from)(\S+)\s+(\S+)", testo, re.M)

    libs, terze = analizza_import(svc / "app", radice)
    if libs:
        if not any(sorgente.rstrip("/") == "libs" for sorgente, _ in copie):
            errori.append(f"{nome}: importa libs/ ({len(libs)} moduli) "
                          "ma il Dockerfile non copia libs/")
        if not re.search(r"^ENV\s+PYTHONPATH=/app\b", testo, re.M):
            errori.append(f"{nome}: importa libs/ ma manca ENV PYTHONPATH=/app")

    progetto = tomllib.loads((svc / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime = {_nome_distribuzione(r) for r in progetto.get("dependencies", [])}
    for modulo in sorted(terze):
        dist = DISTRIBUZIONI.get(modulo, modulo).lower().replace("_", "-")
        if dist in runtime:
            continue
        if modulo in OPZIONALI:
            avvisi.append(f"{nome}: import facoltativo {modulo} non installato "
                          f"({OPZIONALI[modulo]})")
        else:
            errori.append(f"{nome}: importa {modulo} ma {dist} "
                          "non è fra le dipendenze di runtime")

    for sorgente, _ in copie:
        if not (radice / sorgente).exists():
            errori.append(f"{nome}: COPY di un percorso inesistente: {sorgente}")

    controllo = re.search(r"^HEALTHCHECK.*?CMD\s+(.+?)$", testo, re.M | re.S)
    if controllo is None:
        avvisi.append(f"{nome}: nessun HEALTHCHECK")
    else:
        comando = controllo.group(1)
        moduli = set(re.findall(r"import\s+([A-Za-z_][\w.]*)", comando))
        non_std = sorted(m for m in moduli if m.split(".")[0] not in STDLIB)
        if non_std or "curl" in comando or "wget" in comando:
            errori.append(f"{nome}: HEALTHCHECK usa strumenti non garantiti nell'immagine: "
                          f"{non_std or comando.strip()}")
        live = ((values.get("services") or {}).get(nome) or {}).get("health", {}).get("live")
        interroga = (f"localhost:8000{live}'" in comando or f'localhost:8000{live}"' in comando)
        if live and not interroga:
            errori.append(f"{nome}: HEALTHCHECK non interroga {live} (health.live in values.yaml)")
    return errori, avvisi


def main() -> int:
    values = yaml.safe_load((RADICE / "infra" / "helm" / "values.yaml").read_text(encoding="utf-8"))
    errori: list[str] = []
    servizi = sorted(p for p in (RADICE / "services").iterdir() if (p / "Dockerfile").is_file())
    for svc in servizi:
        e, a = verifica_servizio(svc, values)
        errori += e
        for avviso in a:
            print("AVVISO:", avviso)
    for e in errori:
        print("ERRORE:", e)
    if errori:
        return 1
    print(f"OK: {len(servizi)} Dockerfile coerenti con gli import e le dipendenze dei servizi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
