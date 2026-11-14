# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-033: ogni servizio espone GET /metrics con le metriche HTTP condivise.

Ogni servizio ha il proprio pacchetto ``app``: si importa il suo ``app.main``
in un processo separato (PYTHONPATH = radice + cartella del servizio), si fa
una richiesta e si legge /metrics. Il lifespan NON parte (niente DB/Kafka):
si prova il cablaggio di ``installa_metriche`` nel main.py reale.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[4]
SERVIZI = sorted(
    p.parent.parent.name for p in (RADICE / "services").glob("*/app/main.py")
)

_SONDA = r"""
import json, sys
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)          # senza «with»: il lifespan non parte
client.get("/rotta-inesistente-per-metriche")
r = client.get("/metrics")
print(json.dumps({"stato": r.status_code, "tipo": r.headers.get("content-type", ""),
                  "testo": r.text}))
"""


def test_tutti_i_servizi_censiti() -> None:
    assert set(SERVIZI) >= {
        "aip-builder", "dashboard-api", "distribution", "gateway", "hashchain",
        "notification", "onboarding", "signature", "validation",
    }


@pytest.mark.parametrize("servizio", SERVIZI)
def test_metrics_esposto(servizio: str) -> None:
    cartella = RADICE / "services" / servizio
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(RADICE), str(cartella)]),
        "ENV": "test",
    }
    esito = subprocess.run(
        [sys.executable, "-c", _SONDA], cwd=cartella, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert esito.returncode == 0, f"{servizio}: {esito.stderr[-2500:]}"
    risposta = json.loads(esito.stdout.strip().splitlines()[-1])
    assert risposta["stato"] == 200
    assert risposta["tipo"].startswith("text/plain")
    testo = risposta["testo"]
    assert f'servizio="{servizio}"' in testo
    assert "verumdoc_http_richieste_totali" in testo
    # La richiesta non instradata è contata con l'etichetta fissa, mai col percorso
    assert 'rotta="non_instradata"' in testo
    assert "rotta-inesistente-per-metriche" not in testo
