# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R10 (V3) — i permessi dei ruoli personalizzati valgono anche sugli
endpoint di distribution che autorizzano per ruolo base: scrittura dei fascicoli
(versamento:esegui) e rigenerazione di un PdD (esibizione:richiedi).
"""
from __future__ import annotations

import uuid

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg  # noqa: F401
from tests.test_fascicoli import _client

pytestmark = richiede_pg


def _h(tenant, ruolo: str = "OPERATORE", permessi: list[str] | None = None) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    extra = {} if permessi is None else {"rp": str(uuid.uuid4()), "pv": 1, "permessi": permessi}
    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=str(uuid.uuid4()), ruolo=ruolo, **extra)}


async def test_fascicoli_con_ruolo_personalizzato_serve_versare(db_url) -> None:  # noqa: F811
    tenant, _classe = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        solo_lettura = _h(tenant, permessi=["archivio:consulta"])
        r = await c.post("/api/v2/fascicoli", json={"titolo": "Prova"}, headers=solo_lettura)
        assert r.status_code == 403 and r.json()["codice"] == "FSC-403-RUOLO", r.text
        versa = _h(tenant, permessi=["archivio:consulta", "versamento:esegui"])
        r = await c.post("/api/v2/fascicoli", json={"titolo": "Prova"}, headers=versa)
        assert r.status_code == 201, r.text
        assert (await c.post("/api/v2/fascicoli", json={"titolo": "Altro"},
                             headers=_h(tenant))).status_code == 201   # ruolo fisso: come prima


async def test_rigenera_pdd_con_ruolo_personalizzato_serve_l_esibizione(db_url) -> None:  # noqa: F811
    from app.routers.distribution import router
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(router)
    tenant, _classe = await crea_tenant_e_classe(db_url)
    percorso = f"/api/v2/distribution/pdd/{uuid.uuid4()}/rigenera"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(percorso, headers=_h(tenant, permessi=["archivio:consulta"]))
        assert r.status_code == 403 and r.json()["codice"] == "DST-403", r.text
        r = await c.post(percorso, headers=_h(tenant, permessi=["esibizione:richiedi"]))
        assert r.status_code == 404   # permesso presente: si arriva al PdD (inesistente)


async def test_consultazione_con_ruolo_fisso_non_chiede_esibizioni(db_url) -> None:  # noqa: F811
    """Revisione R17 (audit R17-02): la Consultazione (VIEWER) non ha «esibizione:richiedi»;
    l'operatore sì (R17-01)."""
    from app.routers.distribution import router
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(router)
    tenant, _classe = await crea_tenant_e_classe(db_url)
    percorso = f"/api/v2/distribution/pdd/{uuid.uuid4()}/rigenera"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(percorso, headers=_h(tenant, "VIEWER"))
        assert r.status_code == 403 and r.json()["codice"] == "DST-403", r.text
        r = await c.post("/api/v2/distribution/richiedi", headers=_h(tenant, "VIEWER"),
                         json={"aip_id": str(uuid.uuid4()), "tipo": "COMPLETO",
                               "motivo": "Esibizione di prova"})
        assert r.status_code == 403 and r.json()["codice"] == "DST-403", r.text
        for ruolo in ("OPERATORE", "ADMIN_TENANT", "API_SYSTEM"):
            r = await c.post(percorso, headers=_h(tenant, ruolo))
            assert r.status_code == 404, (ruolo, r.text)
