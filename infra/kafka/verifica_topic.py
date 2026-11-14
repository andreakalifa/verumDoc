# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Confronta infra/kafka/topics.yaml con i topic usati dal codice (VD-C-035).

Raccoglie i nomi dei topic dal codice dei servizi:
  - campi ``kafka_topic_*`` delle Settings (services/*/app/config.py);
  - costanti passate come ``topic=`` (kafka_produce) o come primo argomento di
    ``send_and_wait`` / ``AIOKafkaConsumer``;
  - costanti nelle liste assegnate a una variabile ``topics`` o nelle tuple/liste
    assegnate a costanti ``TOPIC*``;
  - (R03) stringhe assegnate a costanti ``TOPIC*`` (topic dell'outbox, es.
    ``TOPIC_COMPLETATO = "caricamento.completato"``) e chiavi delle mappe
    topic → tipo nelle Settings (campi ``*_topic`` con un dizionario, es.
    ``eventi_topic`` di notification, a cui il consumer si sottoscrive);
  - code di scarto: ``kafka_topic_in`` + ``suffisso_dlq`` delle Settings.
Esce con codice 1 se le due liste non coincidono.

Uso:  python infra/kafka/verifica_topic.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
LISTA = RADICE / "infra" / "kafka" / "topics.yaml"


def topic_dichiarati(percorso: Path = LISTA) -> set[str]:
    nomi = set()
    for riga in percorso.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*-\s*name:\s*([^\s#]+)", riga)
        if m:
            nomi.add(m.group(1))
    return nomi


def _costante(nodo: ast.AST) -> str | None:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    return None


def topic_nel_codice(radice: Path = RADICE) -> dict[str, set[str]]:
    trovati: dict[str, set[str]] = {}

    def aggiungi(nome: str | None, dove: str) -> None:
        if nome:
            trovati.setdefault(nome, set()).add(dove)

    for file in sorted((radice / "services").glob("*/app/**/*.py")):
        if "__pycache__" in file.parts:
            continue
        dove = str(file.relative_to(radice))
        albero = ast.parse(file.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
                if nodo.target.id.startswith("kafka_topic") and nodo.value is not None:
                    aggiungi(_costante(nodo.value), dove)
            elif isinstance(nodo, ast.Call):
                for kw in nodo.keywords:
                    if kw.arg == "topic":
                        aggiungi(_costante(kw.value), dove)
                nome_f = getattr(nodo.func, "attr", getattr(nodo.func, "id", ""))
                if nome_f in ("send_and_wait", "send", "AIOKafkaConsumer") and nodo.args:
                    aggiungi(_costante(nodo.args[0]), dove)
            elif isinstance(nodo, ast.Assign) and isinstance(nodo.value, (ast.List, ast.Tuple)):
                if any(isinstance(t, ast.Name) and (t.id == "topics" or t.id.startswith("TOPIC"))
                       for t in nodo.targets):
                    for el in nodo.value.elts:
                        aggiungi(_costante(el), dove)
            elif isinstance(nodo, ast.Assign) and _costante(nodo.value):
                if any(isinstance(t, ast.Name) and t.id.startswith("TOPIC") for t in nodo.targets):
                    aggiungi(_costante(nodo.value), dove)
            if (isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name)
                    and nodo.target.id.endswith("_topic") and isinstance(nodo.value, ast.Dict)):
                for chiave in nodo.value.keys:
                    aggiungi(_costante(chiave) if chiave is not None else None, dove + " (mappa)")
        if file.name == "config.py":
            campi = {
                n.target.id: _costante(n.value)
                for n in ast.walk(albero)
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value
            }
            if campi.get("kafka_topic_in") and campi.get("suffisso_dlq"):
                aggiungi(f"{campi['kafka_topic_in']}{campi['suffisso_dlq']}", dove + " (DLQ)")
    return trovati


def main() -> int:
    dichiarati = topic_dichiarati()
    codice = topic_nel_codice()
    mancanti = sorted(set(codice) - dichiarati)
    inutili = sorted(dichiarati - set(codice))
    for nome in mancanti:
        print(f"MANCANTE in topics.yaml: {nome} (usato in {', '.join(sorted(codice[nome]))})")
    for nome in inutili:
        print(f"NON USATO dal codice: {nome}")
    if mancanti or inutili:
        return 1
    print(f"OK: {len(dichiarati)} topic, lista coerente con il codice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
