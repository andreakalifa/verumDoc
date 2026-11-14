# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R10 (V3) — «Rielabora» dei versamenti rifiutati (ruolo base OPERATORE):
con un ruolo personalizzato serve il permesso di versare.
"""
from __future__ import annotations

import uuid

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg  # noqa: F401
from tests.test_r04_scarti import _client
from tests.test_r07_rielabora_ente import _percorso, _scartato

pytestmark = richiede_pg


def _h(tenant, permessi: list[str]) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=str(uuid.uuid4()), ruolo="OPERATORE",
        rp=str(uuid.uuid4()), pv=1, permessi=permessi)}


async def test_rielabora_con_ruolo_personalizzato_serve_versare(db_url) -> None:  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _scartato(db_url, tenant, classe, 301)
    async with _client() as c:
        r = await c.post(_percorso(pdv, "anteprima"), headers=_h(tenant, ["archivio:consulta"]))
        assert r.status_code == 403 and r.json()["codice"] == "AUTH-010", r.text
        r = await c.post(_percorso(pdv, "anteprima"),
                         headers=_h(tenant, ["archivio:consulta", "versamento:esegui"]))
        assert r.status_code == 200, r.text
