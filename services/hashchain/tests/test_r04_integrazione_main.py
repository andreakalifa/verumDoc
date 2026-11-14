# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — collegamenti di ``app.main`` della hashchain.

1. Il router della verifica continua è montato sull'app VERA (senza, le rotte
   rispondono 404; con il router e senza token rispondono 401).
2. Il lifespan avvia il relay dell'outbox con origine ``hashchain`` (gli eventi
   ``integrita.anomalia`` del lavoratore e ``aip.conserved`` / ``catena.checkpoint``
   del registro escono da lì) e lo ferma all'arresto.
"""
from __future__ import annotations

import asyncio
import uuid

import app.main as principale
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.unit
async def test_rotte_della_verifica_continua_montate_sull_app_reale():
    async with AsyncClient(transport=ASGITransport(app=principale.app),
                           base_url="http://test") as c:
        for metodo, percorso in (("POST", "/api/v1/hashchain/verifiche"),
                                 ("GET", "/api/v1/hashchain/verifiche/registro"),
                                 ("GET", "/api/v1/hashchain/verifiche/riepilogo"),
                                 ("GET", f"/api/v1/hashchain/verifiche/{uuid.uuid4()}")):
            r = await c.request(metodo, percorso, json={})
            assert r.status_code == 401, (percorso, r.status_code)
            assert r.headers["content-type"].startswith("application/problem+json")
    percorsi = set(principale.app.openapi()["paths"])
    assert "/api/v1/hashchain/verifiche/{richiesta_id}" in percorsi
    assert "/api/v1/hashchain/checkpoint" in percorsi  # il registro resta


@pytest.mark.unit
async def test_lifespan_avvia_e_ferma_il_relay_con_origine_hashchain(monkeypatch):
    chiamate: list[tuple] = []
    relay = object()

    async def finto_avvia(origine, **kw):
        chiamate.append(("avvia", origine, kw.get("bootstrap")))
        return relay

    async def finto_ferma(r):
        chiamate.append(("ferma", r))

    async def nulla(*_a, **_k):
        return 0

    class CicloFinto:
        async def esegui(self, fine: asyncio.Event) -> None:
            chiamate.append(("ciclo",))
            await fine.wait()

    monkeypatch.setattr(principale, "verifica_configurazione", lambda _s: None)
    monkeypatch.setattr(principale, "init_db", nulla)
    monkeypatch.setattr(principale, "close_db", nulla)
    monkeypatch.setattr(principale, "chiudi_verifiche_orfane", nulla)
    monkeypatch.setattr(principale, "arresta_verifiche", nulla)
    monkeypatch.setattr(principale, "avvia_relay_servizio", finto_avvia)
    monkeypatch.setattr(principale, "ferma_relay_servizio", finto_ferma)
    monkeypatch.setattr(principale, "ciclo_predefinito", lambda: CicloFinto())

    async with principale.lifespan(principale.app):
        await asyncio.sleep(0)
        assert principale.app.state.relay_outbox is relay
    assert chiamate[0] == ("avvia", "hashchain", principale.settings.kafka_bootstrap)
    assert ("ciclo",) in chiamate
    assert chiamate[-1] == ("ferma", relay)
