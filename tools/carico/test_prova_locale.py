# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prove delle funzioni pure di tools/carico/prova_locale.py (statistiche e richieste)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prova_locale as pl  # noqa: E402


def test_percentile_interpolato() -> None:
    valori = [float(x) for x in range(1, 101)]
    assert pl.percentile(valori, 50) == pytest.approx(50.5)
    assert pl.percentile(valori, 95) == pytest.approx(95.05)
    assert pl.percentile([3.0], 99) == 3.0


def test_pdf_sintetico_dimensione_e_firma() -> None:
    dati = pl.pdf_sintetico(300 * 1024, seme=7)
    assert len(dati) == 300 * 1024
    assert dati.startswith(b"%PDF-1.7") and dati.endswith(b"%%EOF\n")
    assert dati != pl.pdf_sintetico(300 * 1024, seme=8)


def test_corpo_multipart_leggibile_da_starlette() -> None:
    """Il corpo preparato dal generatore è un multipart valido per il gateway."""
    import asyncio

    from starlette.requests import Request

    dati = pl.pdf_sintetico(4096, 1)
    f = pl.FilePronto(dati, hashlib.sha256(dati).hexdigest())
    indice = pl.indice_versamento("X-1", f, "a.pdf")
    corpo, tipo = pl.corpo_multipart(indice, f, "a.pdf")

    async def leggi():
        inviato = False

        async def ricevi():
            nonlocal inviato
            if inviato:
                return {"type": "http.disconnect"}
            inviato = True
            return {"type": "http.request", "body": corpo, "more_body": False}
        req = Request({"type": "http", "method": "POST", "headers": [
            (b"content-type", tipo.encode())]}, ricevi)
        modulo = await req.form()
        return json.loads(modulo["indice"]), await modulo["files"].read(), modulo["files"].filename
    letto, contenuto, nome = asyncio.run(leggi())
    assert letto["documenti"][0]["hash"]["impronta"] == hashlib.sha256(contenuto).hexdigest()
    assert contenuto == dati and nome == "a.pdf"
    assert letto["chiave"]["numero"] == "X-1"


def test_statistiche_esiti_e_throughput() -> None:
    esiti = [pl.Esito(i * 0.1, 0.2, 202) for i in range(90)] + \
        [pl.Esito(9.0 + i * 0.1, 0.01, 503, "UPL-503-OCCUPATO") for i in range(10)]
    st = pl.statistiche(esiti, ritmo=10, durata=10, ritardo=0.0)
    assert st["accettate_202"] == 90 and st["errori_pct"] == 10.0
    assert st["esiti"] == {"202": 90, "503 UPL-503-OCCUPATO": 10}
    assert st["latenza_s"]["p95"] == pytest.approx(0.2)
    assert st["throughput_202_s"] == pytest.approx(90 / (8.9 + 0.2), rel=1e-3)


def test_argomenti_predefiniti() -> None:
    a = pl.argomenti(["--database-url", "postgresql+asyncpg://x/y", "--uscita", "/tmp/x"])
    assert a.livelli == [10.0, 25.0, 50.0] and a.dimensione_kb == 300 and a.soglia_p95 == 2.0
