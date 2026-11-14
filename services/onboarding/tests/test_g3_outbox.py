# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g3-backend — eventi di ciclo di vita dell'ente nell'outbox (REGOLE §12.3), su
PostgreSQL reale.

Prima: dopo il commit il servizio pubblicava direttamente su Kafka; con Kafka
giù l'evento era solo registrato nel log e PERSO. Ora l'evento si scrive in
``outbox_eventi`` nella stessa transazione del cambio di stato: con Kafka giù
l'operazione riesce e l'evento resta in coda per il relay; se l'outbox non è
scrivibile, nemmeno il cambio di stato avviene.
"""
from __future__ import annotations

import json
import uuid

import pytest
from app.services.onboarding_service import attiva_tenant, cambia_stato_tenant, crea_tenant
from sqlalchemy.exc import DBAPIError

import libs.shared.kafka as kafka_condiviso
from libs.shared.outbox import pubblica_in_sospeso
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


async def _eventi(url: str) -> list[dict]:
    righe = await sql(url, "SELECT tenant_id, topic, chiave, payload, pubblicato_il, tentativi "
                           "FROM public.outbox_eventi ORDER BY sequenza")
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in righe]


def _cf() -> str:
    return str(uuid.uuid4().int)[:11]


async def test_kafka_giu_ciclo_di_vita_riesce_ed_eventi_in_ordine(
        db_url, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(kafka_condiviso, "_producer", None)
    tenant = await crea_tenant("Comune di Prova", _cf(), "ref@example.org")
    tid = str(tenant.id)
    await attiva_tenant(tid)
    await cambia_stato_tenant(tid, "SOSPESO", motivo="morosità")

    stato = await sql(db_url, "SELECT stato FROM public.tenants WHERE id = $1", tenant.id)
    assert stato[0]["stato"] == "SOSPESO"
    eventi = await _eventi(db_url)
    assert [e["payload"]["evento"] for e in eventi] == [
        "tenant.creato", "tenant.attivato", "tenant.stato_cambiato"]
    assert all(e["topic"] == "lifecycle.event" and e["chiave"] == tid for e in eventi)
    assert all(e["tenant_id"] == tenant.id for e in eventi)
    assert all(e["pubblicato_il"] is None for e in eventi)
    assert eventi[2]["payload"]["da"] == "ATTIVO" and eventi[2]["payload"]["a"] == "SOSPESO"

    # Kafka torna: il relay li pubblica nell'ordine in cui sono stati scritti
    inviati: list[str] = []

    async def invia(topic, chiave, valore):
        inviati.append(valore["evento"])

    assert await pubblica_in_sospeso(invia) == 3
    assert inviati == ["tenant.creato", "tenant.attivato", "tenant.stato_cambiato"]
    assert all(e["pubblicato_il"] is not None for e in await _eventi(db_url))


async def test_kafka_attivo_evento_pubblicato_subito(db_url, monkeypatch) -> None:  # noqa: F811
    inviati: list[tuple] = []

    class ProducerFinto:
        async def send_and_wait(self, topic, value=None, key=None):
            inviati.append((topic, key, value["evento"]))

    monkeypatch.setattr(kafka_condiviso, "_producer", ProducerFinto())
    tenant = await crea_tenant("Comune di Prova", _cf(), "ref@example.org")
    assert inviati == [("lifecycle.event", str(tenant.id), "tenant.creato")]
    assert (await _eventi(db_url))[0]["pubblicato_il"] is not None


async def test_outbox_non_scrivibile_nessun_cambio_di_stato(
        db_url, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(kafka_condiviso, "_producer", None)
    tenant = await crea_tenant("Comune di Prova", _cf(), "ref@example.org")
    await sql(db_url, "ALTER TABLE public.outbox_eventi RENAME TO outbox_altrove")
    with pytest.raises(DBAPIError):
        await attiva_tenant(str(tenant.id))
    with pytest.raises(DBAPIError):
        await crea_tenant("Altro Comune", _cf(), "ref@example.org")
    stati = await sql(db_url, "SELECT stato FROM public.tenants")
    assert [s["stato"] for s in stati] == ["TRIAL"]   # né attivato, né il secondo creato
