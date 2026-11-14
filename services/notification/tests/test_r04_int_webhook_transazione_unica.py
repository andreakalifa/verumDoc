# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — POST /api/v1/webhook con Idempotency-Key: la sottoscrizione e il
completamento della chiave stanno nella STESSA transazione (richiesta 6 di
r04-servizi). Se il completamento fallisce il webhook non esiste (503, chiave
rilasciata e riutilizzabile); se riesce, chiave COMPLETATA con l'id del webhook.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from libs.shared.security import idempotenza
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401 — fixture
from tests.test_b3_api import URL_OK, _ambiente, client, crea_ente, h  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_completamento_fallito_annulla_anche_il_webhook(client, db_url, monkeypatch):
    ente = await crea_ente(db_url)
    originale = idempotenza.completa
    sessioni: list[object] = []

    async def guasto(sessione, *a, **k):
        sessioni.append(sessione)
        raise OperationalError("UPDATE richieste_idempotenti", {}, Exception("connessione persa"))

    monkeypatch.setattr(idempotenza, "completa", guasto)
    corpo = {"url": URL_OK, "tipi": ["pdd.pronto"]}
    r = await client.post("/api/v1/webhook", json=corpo,
                          headers={**h(ente), "Idempotency-Key": "webhook-tx-0001"})
    assert r.status_code == 503 and r.json()["codice"] == "WH-503"
    assert sessioni and sessioni[0] is not None  # chiamata DENTRO la transazione
    assert await sql(db_url, "SELECT 1 FROM public.webhook_sottoscrizioni") == []
    assert await sql(db_url, "SELECT 1 FROM public.richieste_idempotenti") == []

    monkeypatch.setattr(idempotenza, "completa", originale)
    ok = await client.post("/api/v1/webhook", json=corpo,
                           headers={**h(ente), "Idempotency-Key": "webhook-tx-0001"})
    assert ok.status_code == 201, ok.text
    chiave = await sql(db_url, "SELECT stato, risorsa_id FROM public.richieste_idempotenti")
    assert [(c["stato"], c["risorsa_id"]) for c in chiave] == [("COMPLETATA", ok.json()["id"])]
