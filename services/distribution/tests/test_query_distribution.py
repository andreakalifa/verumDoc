# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Query del router distribution su PostgreSQL reale (VD-C-022, R03).

Solo le query: ``CAST(:id AS uuid)`` al posto di ``:id::uuid`` (che
SQLAlchemy non riconosce come parametro: GET /aip/{id} rispondeva sempre
500), storico filtrato per tenant_id invece che con LIKE sul destinatario,
approvazione/rifiuto con id e JSON convertiti esplicitamente.
L'autorizzazione per cliente su questi endpoint è di be-sicurezza.
"""
from __future__ import annotations

import json
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


def _token(tenant_id: str, ruolo: str = "ADMIN_TENANT") -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id, user_id=str(uuid.uuid4()), ruolo=ruolo,
    )}


async def _client() -> AsyncClient:
    from app.routers.distribution import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_info_aip_query_funzionante(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                      "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(db_url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, "
                      "s3_bucket, hash_sha256) VALUES ($1, $2, $3, 'urn:x', 'CONSERVATO', 'k', "
                      "'b', $4)", aip, pdv, tenant, "c" * 64)
    async with await _client() as c:
        r = await c.get(f"/api/v2/distribution/aip/{aip}", headers=_token(str(tenant)))
    assert r.status_code == 200, r.text
    assert r.json()["aip_id"] == str(aip)
    assert r.json()["distribuibile"] is True


async def test_storico_filtrato_per_tenant_id(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario) "
                      "VALUES ($1, 'PDD_COMPLETO', 'API', 'richiedente-1')", t1)
    # Destinatario che contiene l'id di t1 ma appartiene a t2
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario) "
                      "VALUES ($1, 'PDD_COMPLETO', 'API', $2)", t2, f"{t1}")
    async with await _client() as c:
        r = await c.get("/api/v2/distribution/storico", headers=_token(str(t1)))
    assert r.status_code == 200, r.text
    assert [v["richiedente"] for v in r.json()["items"]] == ["richiedente-1"]


async def test_approva_e_rifiuta(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    ids = []
    for _ in range(2):
        riga = await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, "
                                 "destinatario, stato) VALUES ($1, 'PDD_ESIBIZIONE', 'API', 'r', "
                                 "'PENDING') RETURNING id", tenant)
        ids.append(riga[0]["id"])
    h = _token(str(tenant))
    async with await _client() as c:
        a = await c.post(f"/api/v2/distribution/{ids[0]}/approva", headers=h)
        assert a.status_code == 200, a.text
        r = await c.post(f"/api/v2/distribution/{ids[1]}/rifiuta", json={"motivo": "doppione"},
                         headers=h)
        assert r.status_code == 200, r.text
    righe = await sql(db_url, "SELECT id, stato, payload_json FROM public.notifications "
                              "WHERE id = ANY($1::uuid[])", ids)
    per_id = {x["id"]: x for x in righe}
    assert per_id[ids[0]]["stato"] == "APPROVATA"
    assert per_id[ids[1]]["stato"] == "RIFIUTATA"
    assert json.loads(per_id[ids[1]]["payload_json"])["motivo_rifiuto"] == "doppione"


async def test_richiesta_registrata_con_tenant_id(db_url, monkeypatch) -> None:
    """La richiesta di PdD si registra in notifications con tenant_id (ora
    obbligatorio): prima l'INSERT lo ometteva."""
    from app.models.distribution import RispostaDistribuzione
    from app.routers import distribution as modulo

    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                      "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    # PdA CONSERVATO: registra_pdd ricontrolla lo stato con il PdA bloccato (revisione R05, R10)
    await sql(db_url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, s3_key, s3_bucket, "
                      "hash_sha256, stato) VALUES ($1, $2, $3, 'urn:y', 'k', 'b', $4, "
                      "'CONSERVATO')",
              aip, pdv, tenant, "d" * 64)
    aip_id = str(aip)

    async def finta_distribuzione(req, tid):
        return RispostaDistribuzione(
            pdd_id="pdd-1", aip_id=req.aip_id, tipo=req.tipo.value, documenti=[],
            sincro_xml_incluso=False, hash_pdd="0" * 64, created_at="2026-10-20T00:00:00Z",
        )

    monkeypatch.setattr(modulo, "esegui_distribuzione", finta_distribuzione)
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/richiedi", json={
            "aip_id": aip_id, "tipo": "COMPLETO", "motivo": "verifica interna",
            "richiedente_id": "utente-1",
        }, headers=_token(str(tenant)))
    assert r.status_code == 200, r.text
    righe = await sql(db_url, "SELECT tenant_id, tipo, payload_json FROM public.notifications")
    assert len(righe) == 1
    assert righe[0]["tenant_id"] == tenant
    assert righe[0]["tipo"] == "PDD_COMPLETO"
    assert json.loads(righe[0]["payload_json"])["motivo"] == "verifica interna"
