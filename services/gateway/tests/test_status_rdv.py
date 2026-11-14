# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
GET /api/v1/versamento/{pdv_id} e /rdv (VD-C-012, VD-C-020): il RdV
definitivo si restituisce quando il riferimento esiste; 409 solo se non è
ancora emesso; impronta verificata alla lettura.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from app.auth.dependencies import get_current_user
from app.config import settings
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.identita_prova import dipendenza_utente
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

RDV = b"<?xml version='1.0'?><RicevutaVersamento tipo='DEFINITIVA'/>"


@pytest.fixture
def s3(s3_server, monkeypatch):
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    client = client_s3(s3_server)
    client.create_bucket(Bucket="aip-rdv")
    return client


@pytest.fixture
def come_cliente():
    def _imposta(tenant) -> None:
        app.dependency_overrides[get_current_user] = dipendenza_utente({
            "tenant_id": str(tenant), "user_id": str(uuid.uuid4()), "ruolo": "OPERATORE",
            "scopes": ["read"]})
    yield _imposta
    app.dependency_overrides.pop(get_current_user, None)


async def _get(percorso: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(percorso)


async def _pdv(url: str, tenant, classe, *, stato: str, meta: dict) -> uuid.UUID:
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "metadata_json) VALUES ($1, 'prova', $2, $3, $4, $5, CAST($6 AS jsonb))",
              pdv, stato, classe, uuid.uuid4(), tenant, json.dumps(meta))
    return pdv


async def test_rdv_conservato_restituisce_xml(db_url, s3, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    s3.put_object(Bucket="aip-rdv", Key="t/a/RdV_Definitiva.xml", Body=RDV)
    pdv = await _pdv(db_url, tenant, classe, stato="CONSERVATO", meta={"rdv_definitiva": {
        "s3_bucket": "aip-rdv", "s3_key": "t/a/RdV_Definitiva.xml",
        "impronta_sha256": hashlib.sha256(RDV).hexdigest()}})
    come_cliente(tenant)
    resp = await _get(f"/api/v1/versamento/{pdv}/rdv")
    assert resp.status_code == 200
    assert resp.content == RDV
    assert resp.headers["content-type"].startswith("application/xml")
    assert resp.headers["X-Impronta-SHA256"] == hashlib.sha256(RDV).hexdigest()
    # RdV emesso prima di R04: formato 1, nessun checkpoint
    assert resp.headers["X-Formato-RdV"] == "verumdoc-rdv-1"
    assert "X-Checkpoint-Dimensione" not in resp.headers


async def test_rdv_formato_2_intestazioni_formato_e_checkpoint(db_url, s3, come_cliente):
    """R04 (K5): il RdV v2 si scarica dallo stesso endpoint; le intestazioni dicono
    formato e checkpoint di riferimento delle prove."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    rdv2 = b"<?xml version='1.0'?><RapportoVersamento versione='2'/>"
    s3.put_object(Bucket="aip-rdv", Key="t/a/RdV_Definitivo_v2.xml", Body=rdv2)
    pdv = await _pdv(db_url, tenant, classe, stato="CONSERVATO", meta={"rdv_definitiva": {
        "s3_bucket": "aip-rdv", "s3_key": "t/a/RdV_Definitivo_v2.xml",
        "impronta_sha256": hashlib.sha256(rdv2).hexdigest(),
        "formato": "verumdoc-rdv-definitivo-2", "checkpoint_dimensione": 42}})
    come_cliente(tenant)
    resp = await _get(f"/api/v1/versamento/{pdv}/rdv")
    assert resp.status_code == 200 and resp.content == rdv2
    assert resp.headers["X-Formato-RdV"] == "verumdoc-rdv-definitivo-2"
    assert resp.headers["X-Checkpoint-Dimensione"] == "42"


async def test_rdv_non_emesso_409_con_stato(db_url, s3, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv(db_url, tenant, classe, stato="IN_BUILD", meta={})
    come_cliente(tenant)
    resp = await _get(f"/api/v1/versamento/{pdv}/rdv")
    assert resp.status_code == 409
    assert resp.json()["codice"] == "VRS-409" and resp.json()["dettagli"]["stato"] == "IN_BUILD"


async def test_rdv_impronta_diversa_502(db_url, s3, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    s3.put_object(Bucket="aip-rdv", Key="t/b/RdV_Definitiva.xml", Body=RDV + b"alterato")
    pdv = await _pdv(db_url, tenant, classe, stato="CONSERVATO", meta={"rdv_definitiva": {
        "s3_bucket": "aip-rdv", "s3_key": "t/b/RdV_Definitiva.xml",
        "impronta_sha256": hashlib.sha256(RDV).hexdigest()}})
    come_cliente(tenant)
    assert (await _get(f"/api/v1/versamento/{pdv}/rdv")).status_code == 502


async def test_rdv_storage_irraggiungibile_503(db_url, s3, come_cliente, monkeypatch):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv(db_url, tenant, classe, stato="CONSERVATO", meta={"rdv_definitiva": {
        "s3_bucket": "bucket-inesistente", "s3_key": "x", "impronta_sha256": "0" * 64}})
    come_cliente(tenant)
    assert (await _get(f"/api/v1/versamento/{pdv}/rdv")).status_code == 503


async def test_rdv_altro_cliente_404(db_url, s3, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    pdv = await _pdv(db_url, tenant, classe, stato="CONSERVATO", meta={})
    come_cliente(altro)
    assert (await _get(f"/api/v1/versamento/{pdv}/rdv")).status_code == 404
    assert (await _get(f"/api/v1/versamento/{pdv}")).status_code == 404


async def test_stato_pdv_tenant_non_uuid_404_non_500(db_url, s3, come_cliente):
    come_cliente("non-un-uuid")
    assert (await _get(f"/api/v1/versamento/{uuid.uuid4()}")).status_code == 404
    assert (await _get(f"/api/v1/versamento/{uuid.uuid4()}/rdv")).status_code == 404


async def test_stato_pdv_riporta_stato_errori_e_rdv(db_url, s3, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    rif = {"s3_bucket": "aip-rdv", "s3_key": "k", "impronta_sha256": "0" * 64}
    pdv = await _pdv(db_url, tenant, classe, stato="CONSERVATO",
                     meta={"rdv_preliminare": "<x/>", "rdv_definitiva": rif})
    come_cliente(tenant)
    corpo = (await _get(f"/api/v1/versamento/{pdv}")).json()
    assert corpo["stato"] == "CONSERVATO"
    assert corpo["classe_documentale"] == "fattura"
    assert corpo["rdv_definitivo"] == rif and corpo["rdv_preliminare"] == "<x/>"
