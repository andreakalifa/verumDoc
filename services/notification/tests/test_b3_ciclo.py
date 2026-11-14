# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Ciclo del lavoratore delle consegne e info del servizio — contratto C3 (agente R03 b3).
"""

from __future__ import annotations

import asyncio

import pytest
from app.application import consegne
from app.application.consegne import ParametriConsegna, ciclo_consegne
from fastapi.testclient import TestClient


async def test_ciclo_consegne_continua_dopo_errori_e_non_dorme_a_lotto_pieno(monkeypatch):
    esiti = iter([RuntimeError("db giù"), {"CONSEGNATA": 2}, {"IN_CODA": 1}, {}])
    giri = []

    async def giro_finto(**kw):
        giri.append(1)
        esito = next(esiti)
        if isinstance(esito, Exception):
            raise esito
        return esito

    attese: list[float] = []

    async def attendi(secondi: float) -> None:
        attese.append(secondi)
        if len(attese) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(consegne, "esegui_giro", giro_finto)
    parametri = ParametriConsegna([60], lotto=2, concorrenza=1, lease_secondi=60,
                                  lunghezza_massima_url=2048, consenti_privati=False)
    with pytest.raises(asyncio.CancelledError):
        await ciclo_consegne(inviatore=None, cifratore=None, parametri=parametri,  # type: ignore[arg-type]
                             intervallo_secondi=7, attendi=attendi)
    # errore → pausa; lotto pieno → subito un altro giro; lotto parziale → pausa; vuoto → pausa
    assert len(giri) == 4 and attese == [7, 7, 7]


def test_info_dichiara_lo_stato_dei_webhook() -> None:
    from app.main import app

    corpo = TestClient(app).get("/api/v1/info").json()
    assert corpo["webhook"]["stato"] in ("configurati", "non configurati")
    assert "webhook" not in corpo["posta"]
