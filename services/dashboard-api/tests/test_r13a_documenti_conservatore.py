# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13a — manuale e piano: «Pubblica» mette la versione fra i documenti da sigillare
(stato DA_FIRMARE, contenuto canonico senza la data di generazione), con audit;
la stessa versione due volte non si duplica; solo il responsabile; la busta si
scarica solo quando è FIRMATO.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg
BASE = "/api/dashboard/documenti-conservatore"


def _token(ruolo: str) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id="00000000-0000-0000-0000-0000000000c0", user_id=str(uuid.uuid4()),
        ruolo=ruolo)}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_pubblica_manuale_da_sigillare_e_idempotente(db_url) -> None:  # noqa: F811
    async with _client() as c:
        r = await c.post(f"{BASE}/manuale/pubblica", headers=_token("RSC_CONSERVATORE"))
        assert r.status_code == 200, r.text
        prima = r.json()
        assert prima["stato"] == "DA_FIRMARE" and prima["nuova_versione"]
        r2 = await c.post(f"{BASE}/manuale/pubblica", headers=_token("RSC_CONSERVATORE"))
        assert r2.json()["id"] == prima["id"] and not r2.json()["nuova_versione"]
        elenco = await c.get(BASE, headers=_token("SUPER_ADMIN"))
        assert [i["id"] for i in elenco.json()["items"]] == [prima["id"]]
        assert (await c.get(f"{BASE}/{prima['id']}/busta",
                            headers=_token("RSC_CONSERVATORE"))).status_code == 404
    riga = (await sql(db_url, "SELECT contenuto, impronta_sha256, tenant_id FROM "
                              "public.firme_documenti WHERE id = $1", uuid.UUID(prima["id"])))[0]
    assert riga["tenant_id"] is None
    assert hashlib.sha256(bytes(riga["contenuto"])).hexdigest() == riga["impronta_sha256"]
    corpo = json.loads(bytes(riga["contenuto"]))
    assert corpo["tipo"] == "MANUALE" and "meta" not in corpo["documento"]
    audit = await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                              "'conservatore.documento_pubblicato'")
    assert len(audit) == 1


async def test_busta_scaricabile_quando_firmato(db_url) -> None:  # noqa: F811
    async with _client() as c:
        r = await c.post(f"{BASE}/piano/pubblica", headers=_token("SUPER_ADMIN"))
        ident = r.json()["id"]
        await sql(db_url, "UPDATE public.firme_documenti SET stato = 'FIRMATO', busta = $1, "
                          "busta_sha256 = $2, firmato_il = now() WHERE id = $3",
                  b"P7M", "00" * 32, uuid.UUID(ident))
        b = await c.get(f"{BASE}/{ident}/busta", headers=_token("RSC_CONSERVATORE"))
        assert b.status_code == 200 and b.content == b"P7M"
        assert b.headers["content-type"] == "application/pkcs7-mime"


@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "OPERATORE", "VIEWER"])
async def test_solo_il_responsabile(db_url, ruolo) -> None:  # noqa: F811
    async with _client() as c:
        r = await c.post(f"{BASE}/manuale/pubblica", headers=_token(ruolo))
        assert r.status_code == 403
