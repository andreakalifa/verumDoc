# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Oracolo esterno (REGOLE_SVILUPPO §23.5): libs/catena contro il pacchetto Go
``golang.org/x/mod/sumdb`` (``note`` e ``tlog``), implementazione di riferimento
delle note firmate e degli alberi RFC 6962 usata da sum.golang.org.

Si esegue quando sono disponibili il comando ``go`` e una copia locale del
modulo ``golang.org/x/mod`` (``VD_ORACOLO_GO_MOD=/percorso/di/mod``, per esempio
``git clone --branch v0.20.0 https://github.com/golang/mod``); altrimenti si
salta con il motivo. Nessun accesso alla rete durante il test.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from libs.catena import nota
from libs.catena.rfc6962 import (
    esadecimale,
    hash_foglia,
    prova_consistenza,
    prova_inclusione,
    radice,
)

MODULO_GO = os.environ.get("VD_ORACOLO_GO_MOD", "")
CARTELLA = Path(__file__).resolve().parent / "oracolo_go"

pytestmark = pytest.mark.skipif(
    not (MODULO_GO and Path(MODULO_GO, "go.mod").is_file() and shutil.which("go")),
    reason="oracolo Go non disponibile: servono `go` e VD_ORACOLO_GO_MOD "
           "(copia di golang.org/x/mod)",
)


def _esegui_go(ingresso: dict, tmp_path: Path) -> dict:
    lavoro = tmp_path / "oracolo"
    lavoro.mkdir()
    shutil.copy(CARTELLA / "main.go", lavoro / "main.go")
    (lavoro / "go.mod").write_text(
        "module oracolo\n\ngo 1.21\n\nrequire golang.org/x/mod v0.20.0\n\n"
        f"replace golang.org/x/mod => {MODULO_GO}\n", encoding="utf-8")
    ambiente = {**os.environ, "GOFLAGS": "-mod=mod", "GOPROXY": "off", "GONOSUMDB": "*",
                "GOSUMDB": "off", "GOWORK": "off"}
    go = shutil.which("go") or "go"
    esito = subprocess.run(  # noqa: S603 — comando fisso «go run .», argomenti nostri
        [go, "run", "."], cwd=lavoro, input=json.dumps(ingresso),
        capture_output=True, text=True, timeout=300, env=ambiente)
    assert esito.returncode == 0, esito.stderr[-3000:]
    return json.loads(esito.stdout)


def test_note_e_alberi_interoperabili_con_go(tmp_path: Path) -> None:
    dati = [f"foglia-{i}".encode() for i in range(23)]
    foglie = [hash_foglia(d) for d in dati]
    privata, pubblica = nota.genera_chiave("verumdoc-registro")
    corpo = nota.formatta(nota.Checkpoint("verumdoc.it/registro/v1/prova", 23, radice(foglie)))
    firmata = nota.componi(corpo, [nota.analizza_firmatario(privata).firma(corpo)])
    privata_go, pubblica_go = nota.genera_chiave("verumdoc-testimone")
    inclusioni = [(i, n) for n in (1, 2, 7, 8, 13, 23) for i in range(0, n, 3)]
    consistenze = [(m, n) for n in (2, 8, 13, 23) for m in range(1, n + 1, 2)]
    uscita = _esegui_go({
        "foglie": [d.hex() for d in dati],
        "verificatore": pubblica,
        "nota": firmata,
        "privata_go": privata_go,
        "testo_go": corpo,
        "prove_inclusione": [{"i": i, "n": n, "percorso": esadecimale(
            prova_inclusione(foglie[:n], i))} for i, n in inclusioni],
        "prove_consistenza": [{"da": m, "a": n, "percorso": esadecimale(
            prova_consistenza(foglie, m, n))} for m, n in consistenze],
    }, tmp_path)

    # Go verifica la nostra nota; noi verifichiamo la firma Go (stessa riga: Ed25519)
    assert uscita["nota_valida"] is True and uscita["firme"] == ["verumdoc-registro"]
    assert [f.nome for f in nota.verifica(uscita["nota_go"], [pubblica_go])] == \
        ["verumdoc-testimone"]
    assert uscita["nota_go"] == nota.componi(
        corpo, [nota.analizza_firmatario(privata_go).firma(corpo)])
    # Stesse radici, prove accettate da Go e identiche alle prove prodotte da Go
    assert uscita["radici"] == [radice(foglie[:n]).hex() for n in range(24)]
    assert all(uscita["inclusione_ok"]) and all(uscita["consistenza_ok"])
    assert uscita["inclusione_go"] == [esadecimale(prova_inclusione(foglie[:n], i))
                                       for i, n in inclusioni]
    assert uscita["consistenza_go"] == [esadecimale(prova_consistenza(foglie, m, n))
                                        for m, n in consistenze]
