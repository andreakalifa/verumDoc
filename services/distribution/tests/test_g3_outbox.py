# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g3-backend — eventi di distribuzione nell'outbox (REGOLE §12.3), PostgreSQL reale.

Prima: l'evento «aip.distribuito» si pubblicava direttamente su Kafka; con Kafka
giù era solo registrato nel log e PERSO. Ora si scrive in ``outbox_eventi``
nella stessa transazione del registro delle distribuzioni: la richiesta di
esibizione riesce con Kafka giù e l'evento parte quando il relay lo pubblica.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import libs.shared.kafka as kafka_condiviso
from libs.shared.outbox import pubblica_in_sospeso
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


def _token(tenant: uuid.UUID, utente: str) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=utente, ruolo="ADMIN_TENANT")}


async def _client() -> AsyncClient:
    from app.routers.distribution import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _aip(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant, classe = await crea_tenant_e_classe(url)
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    return tenant, aip


async def _richiedi(tenant: uuid.UUID, aip: uuid.UUID, utente: str):
    async with await _client() as c:
        # R03 b4: consegna ZIP (preparata dopo, fuori dalla richiesta): la prova
        # riguarda il registro e l'outbox, non la lettura dei file sullo storage.
        return await c.post("/api/v2/distribution/richiedi", json={
            "aip_id": str(aip), "tipo": "ESIBIZIONE", "motivo": "richiesta ispettiva",
            "consegna": "ZIP",
        }, headers=_token(tenant, utente))


async def test_kafka_giu_esibizione_riesce_e_evento_resta_in_outbox(
        db_url, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(kafka_condiviso, "_producer", None)   # Kafka non disponibile
    tenant, aip = await _aip(db_url)
    utente = str(uuid.uuid4())

    r = await _richiedi(tenant, aip, utente)
    assert r.status_code == 200, r.text
    pdd_id = r.json()["pdd_id"]

    righe = await sql(db_url, "SELECT tenant_id, topic, chiave, payload, pubblicato_il, "
                              "tentativi, ultimo_errore FROM public.outbox_eventi")
    assert len(righe) == 1
    riga = righe[0]
    payload = json.loads(riga["payload"])
    assert riga["topic"] == "lifecycle.event" and riga["chiave"] == str(aip)
    assert riga["tenant_id"] == tenant
    assert payload["evento"] == "aip.distribuito" and payload["pdd_id"] == pdd_id
    assert payload["richiedente_id"] == utente and payload["tipo_pdd"] == "ESIBIZIONE"
    # R04 integrazione: con il producer non attivo il cortocircuito dell'outbox non
    # tocca la riga (nessun tentativo segnato, un avviso nel log e una metrica):
    # l'evento NON risulta pubblicato e resta per il relay
    assert riga["pubblicato_il"] is None and riga["tentativi"] == 0
    assert riga["ultimo_errore"] is None

    # Kafka torna: il relay (stessa funzione del gateway) lo pubblica una volta sola
    inviati: list[tuple] = []

    async def invia(topic, chiave, valore):
        inviati.append((topic, chiave, valore["pdd_id"]))

    assert await pubblica_in_sospeso(invia) == 1
    assert await pubblica_in_sospeso(invia) == 0
    assert inviati == [("lifecycle.event", str(aip), pdd_id)]
    dopo = await sql(db_url, "SELECT pubblicato_il FROM public.outbox_eventi")
    assert dopo[0]["pubblicato_il"] is not None


async def test_kafka_attivo_evento_pubblicato_subito(db_url, monkeypatch) -> None:  # noqa: F811
    inviati: list[tuple] = []

    class ProducerFinto:
        async def send_and_wait(self, topic, value=None, key=None):
            inviati.append((topic, key, value["evento"]))

    monkeypatch.setattr(kafka_condiviso, "_producer", ProducerFinto())
    tenant, aip = await _aip(db_url)
    r = await _richiedi(tenant, aip, str(uuid.uuid4()))
    assert r.status_code == 200, r.text
    assert inviati == [("lifecycle.event", str(aip), "aip.distribuito")]
    riga = (await sql(db_url, "SELECT pubblicato_il FROM public.outbox_eventi"))[0]
    assert riga["pubblicato_il"] is not None


@pytest.mark.parametrize("tabella,rinominata", [
    ("notifications", "notifiche_altrove"),
    ("outbox_eventi", "outbox_altrove"),
])
async def test_registro_ed_evento_insieme_o_niente(
        db_url, monkeypatch, tabella, rinominata) -> None:  # noqa: F811
    """Se il registro o l'outbox non sono scrivibili: 503, nessun link, e
    nessuna delle due righe (stessa transazione)."""
    monkeypatch.setattr(kafka_condiviso, "_producer", None)
    tenant, aip = await _aip(db_url)
    await sql(db_url, f"ALTER TABLE public.{tabella} RENAME TO {rinominata}")
    r = await _richiedi(tenant, aip, str(uuid.uuid4()))
    assert r.status_code == 503, r.text
    assert "documenti" not in r.json()
    assert tabella not in r.text
    await sql(db_url, f"ALTER TABLE public.{rinominata} RENAME TO {tabella}")
    assert await sql(db_url, "SELECT 1 FROM public.notifications") == []
    assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi") == []
