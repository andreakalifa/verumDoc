# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 servizi (VD-C-106) — Idempotency-Key su POST /api/v1/webhook, sull'app reale e
su PostgreSQL vero. Stessa semantica del versamento: stessa chiave e stessa
richiesta → stessa sottoscrizione con lo stesso segreto (200); corpo diverso → 409.
"""

from __future__ import annotations

import pytest

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401 — fixture
from tests.test_b3_api import URL_OK, _ambiente, client, crea_ente, h  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


def _con_chiave(ente, chiave: str) -> dict:
    return {**h(ente), "Idempotency-Key": chiave}


async def test_crea_webhook_ripetuto_stessa_sottoscrizione_e_segreto(client, db_url):
    ente = await crea_ente(db_url)
    corpo = {"url": URL_OK, "tipi": ["versamento.ricevuto"]}
    primo = await client.post("/api/v1/webhook", json=corpo,
                              headers=_con_chiave(ente, "webhook-prova-001"))
    secondo = await client.post("/api/v1/webhook", json=corpo,
                                headers=_con_chiave(ente, "webhook-prova-001"))
    assert primo.status_code == 201, primo.text
    assert secondo.status_code == 200, secondo.text
    assert secondo.headers["idempotent-replayed"] == "true"
    assert secondo.json()["id"] == primo.json()["id"]
    assert secondo.json()["segreto"] == primo.json()["segreto"]
    assert len(await sql(db_url, "SELECT 1 FROM public.webhook_sottoscrizioni")) == 1
    # una sola voce di audit: la ripetizione non crea nulla
    audit = await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = 'webhook.creato'")
    assert len(audit) == 1


async def test_crea_webhook_stessa_chiave_corpo_diverso_409(client, db_url):
    ente = await crea_ente(db_url)
    assert (await client.post("/api/v1/webhook", json={"url": URL_OK, "tipi": ["pdd.pronto"]},
                              headers=_con_chiave(ente, "webhook-prova-002"))).status_code == 201
    r = await client.post("/api/v1/webhook", json={"url": URL_OK, "tipi": ["versamento.ricevuto"]},
                          headers=_con_chiave(ente, "webhook-prova-002"))
    assert r.status_code == 409 and r.json()["codice"] == "IDEM-409"
    assert len(await sql(db_url, "SELECT 1 FROM public.webhook_sottoscrizioni")) == 1


async def test_crea_webhook_chiave_malformata_e_rifiuto_non_prenota(client, db_url):
    ente = await crea_ente(db_url)
    r = await client.post("/api/v1/webhook", json={"url": URL_OK, "tipi": ["pdd.pronto"]},
                          headers=_con_chiave(ente, "a b"))
    assert r.status_code == 400 and r.json()["codice"] == "IDEM-400"
    # URL rifiutato (http): nessuna chiave resta prenotata, la stessa chiave si riusa
    rifiuto = await client.post("/api/v1/webhook",
                                json={"url": "http://hook.example.org", "tipi": ["pdd.pronto"]},
                                headers=_con_chiave(ente, "webhook-prova-003"))
    assert rifiuto.status_code == 422
    assert await sql(db_url, "SELECT 1 FROM public.richieste_idempotenti") == []
    ok = await client.post("/api/v1/webhook", json={"url": URL_OK, "tipi": ["pdd.pronto"]},
                           headers=_con_chiave(ente, "webhook-prova-003"))
    assert ok.status_code == 201, ok.text


async def test_crea_webhook_ripetuto_dopo_eliminazione_404(client, db_url):
    ente = await crea_ente(db_url)
    corpo = {"url": URL_OK, "tipi": ["pdd.pronto"]}
    creato = (await client.post("/api/v1/webhook", json=corpo,
                                headers=_con_chiave(ente, "webhook-prova-004"))).json()
    assert (await client.delete(f"/api/v1/webhook/{creato['id']}",
                                headers=h(ente))).status_code == 204
    r = await client.post("/api/v1/webhook", json=corpo,
                          headers=_con_chiave(ente, "webhook-prova-004"))
    assert r.status_code == 404 and r.json()["codice"] == "WH-404"


async def test_crea_webhook_chiave_di_un_altro_ente_indipendente(client, db_url):
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    corpo = {"url": URL_OK, "tipi": ["pdd.pronto"]}
    ra = await client.post(
        "/api/v1/webhook", json=corpo, headers=_con_chiave(a, "webhook-comune-01"))
    rb = await client.post(
        "/api/v1/webhook", json=corpo, headers=_con_chiave(b, "webhook-comune-01"))
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]
