# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 (D16) — porte dei webhook ammesse per ente.

La 443 è sempre ammessa; le altre porte le ammette solo il conservatore, per
singolo ente e con motivo, con l'impostazione della console
``WEBHOOK_PORTE_AGGIUNTIVE`` (CODICI, porte 1-65535). Vale sia alla creazione o
modifica della sottoscrizione sia a ogni consegna (la porta si ricontrolla:
un'impostazione tolta ferma le consegne verso quella porta; stessa funzione
``porte_ente``, provata qui).
"""
from __future__ import annotations

import json

import pytest
from app.application.webhook_service import porte_ente
from app.config import settings

from libs.shared.parametri import svuota_cache
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_b3_api import _ambiente, client, crea_ente, h  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]


async def _porte(url: str, livello: str, tenant, porte: list[str]) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ($1, $2, 'WEBHOOK_PORTE_AGGIUNTIVE', CAST($3 AS jsonb), "
                   "'sistema del cliente su porta dedicata')", livello, tenant, json.dumps(porte))


async def test_porte_dell_ente(db_url):  # noqa: F811
    a, b, c = [await crea_ente(db_url) for _ in range(3)]
    await _porte(db_url, "ENTE", a["tenant"], ["8443"])
    await _porte(db_url, "ENTE", c["tenant"], ["22x"])         # valore rotto: ignorato
    svuota_cache()
    assert await porte_ente(a["tenant"], frozenset({443})) == {443, 8443}
    assert await porte_ente(b["tenant"], frozenset({443})) == {443}
    assert await porte_ente(c["tenant"], frozenset({443})) == {443}
    # la 443 c'è sempre, anche se la variabile globale ne elenca altre
    assert await porte_ente(b["tenant"], frozenset({9443})) == {443, 9443}
    # variabile globale vuota (solo sviluppo): nessun limite di porta, come prima
    assert await porte_ente(a["tenant"], frozenset()) is None


async def test_creazione_rispetta_le_porte_dell_ente(client, db_url, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "webhook_porte_ammesse", [443])
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    await _porte(db_url, "ENTE", a["tenant"], ["8443"])
    svuota_cache()
    corpo = {"url": "https://hook.cliente-prova.it:8443/eventi", "tipi": ["versamento.ricevuto"]}
    assert (await client.post("/api/v1/webhook", json=corpo, headers=h(a))).status_code == 201
    r = await client.post("/api/v1/webhook", json=corpo, headers=h(b))
    assert r.status_code in (400, 422) and r.json()["codice"].startswith("WH-"), r.text
    assert "443" in r.text
