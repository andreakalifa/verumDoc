# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (contratto Q2, ciclo di vita): ``GET /api/v1/versamento/{pdv_id}`` mostra lo
stato ANNULLATO con la sua pratica e la catena di rettifica (``rettifica_di``
sul PdV di rettifica, ``rettificato_da`` sull'originale), solo dell'ente.
"""
from __future__ import annotations

import json
import uuid

import pytest
from app.auth.dependencies import get_current_user
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.identita_prova import dipendenza_utente
from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]


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


async def _pdv(url: str, tenant, classe, *, stato: str, meta: dict | None = None) -> uuid.UUID:
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "metadata_json) VALUES ($1, 'prova', $2, $3, $4, $5, CAST($6 AS jsonb))",
              pdv, stato, classe, uuid.uuid4(), tenant, json.dumps(meta or {}))
    return pdv


async def _pratica(url: str, tenant, *, tipo: str, stato: str, pdv, risultato=None) -> uuid.UUID:
    pratica = uuid.uuid4()
    await sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, pdv_id, "
                   "pdv_risultato_id) VALUES ($1, $2, $3, $4, 'Motivo della prova', $5, $6)",
              pratica, tenant, tipo, stato, pdv, risultato)
    return pratica


async def test_stato_annullato_con_pratica(db_url, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pratica = str(uuid.uuid4())
    pdv = await _pdv(db_url, tenant, classe, stato="ANNULLATO", meta={"annullamento": {
        "pratica_id": pratica, "il": "2026-10-23T10:00:00+00:00",
        "stato_precedente": "CONSERVATO"}})
    come_cliente(tenant)
    corpo = (await _get(f"/api/v1/versamento/{pdv}")).json()
    assert corpo["stato"] == "ANNULLATO"
    assert corpo["annullamento"] == {"pratica_id": pratica, "annullato_il":
                                     "2026-10-23T10:00:00+00:00",
                                     "stato_precedente": "CONSERVATO"}
    assert "rettifica" not in corpo  # assente se vuoto: SDK e client invariati


async def test_catena_di_rettifica_nei_due_versi(db_url, come_cliente):
    tenant, classe = await crea_tenant_e_classe(db_url)
    originale = await _pdv(db_url, tenant, classe, stato="CONSERVATO")
    rettifica = await _pdv(db_url, tenant, classe, stato="CONSERVATO")
    pratica = await _pratica(db_url, tenant, tipo="RETTIFICA", stato="COMPLETATA",
                             pdv=originale, risultato=rettifica)
    come_cliente(tenant)
    su_originale = (await _get(f"/api/v1/versamento/{originale}")).json()
    assert "annullamento" not in su_originale
    assert su_originale["rettifica"]["rettifica_di"] is None
    (voce,) = su_originale["rettifica"]["rettificato_da"]
    assert voce["pratica_id"] == str(pratica) and voce["pdv_rettifica_id"] == str(rettifica)
    assert voce["stato"] == "COMPLETATA"
    su_rettifica = (await _get(f"/api/v1/versamento/{rettifica}")).json()
    assert su_rettifica["rettifica"]["rettifica_di"] == {
        "pdv_id": str(originale), "pratica_id": str(pratica), "stato": "COMPLETATA"}
    assert su_rettifica["rettifica"]["rettificato_da"] == []
    # un altro ente non vede il PdV né la sua catena
    altro, _ = await crea_tenant_e_classe(db_url)
    come_cliente(altro)
    assert (await _get(f"/api/v1/versamento/{originale}")).status_code == 404
