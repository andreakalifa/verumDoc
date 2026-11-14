# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (integrazione, contratto Q2) — API di migrazione chiamata dal servizio ciclo-vita.

Riversamento in uscita e restituzione chiedono i PdD MIGRAZIONE a distribution
con un TOKEN DI SERVIZIO (aud ``verumdoc-distribution``, scope
``migrazione:servizio``), per conto di un ente indicato nella richiesta:
  - ``export-batch`` con ``ente_richiedente`` obbligatorio; i PdA di un altro
    ente finiscono negli ``errori`` (RLS nell'ambito dell'ente + controllo);
  - ``status/{id}?ente=`` al posto del tenant del token;
  - token di servizio senza lo scope → 403; con l'audience di un altro servizio
    → 401; l'export singolo resta delle persone RSC/SUPER_ADMIN (403).
Le persone continuano a usare la stessa API come prima (test_b4_pdd).
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.pda_reale import crea_pda, s3  # noqa: F401

pytestmark = richiede_pg

CORPO = {"conservatore_destinatario": "Nuovo Conservatore", "motivo": "riversamento in uscita"}


def _servizio(*, pubblico: str = "verumdoc-distribution",
              scopes: tuple[str, ...] = ("migrazione:servizio",)) -> dict[str, str]:
    from app.auth.jwt_handler import chiavi_jwt
    from app.config import settings

    from libs.shared.security.jwt_rs256 import firma_token_servizio

    token = firma_token_servizio("ciclo-vita", chiave_privata_pem=chiavi_jwt().privata_pem,
                                 emittente=settings.jwt_issuer, durata_secondi=300,
                                 pubblico=pubblico, scopes=scopes)
    return {"Authorization": f"Bearer {token}"}


async def _client() -> AsyncClient:
    from app.routers.migration import router as migrazione

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(migrazione)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_servizio_esporta_per_l_ente_e_legge_lo_stato(db_url, s3) -> None:  # noqa: F811
    from app.services.pdd_zip import elabora_un_pdd

    pda = await crea_pda(db_url, s3)
    altro = await crea_pda(db_url, s3)
    ente = str(pda["tenant"])
    async with await _client() as c:
        r = await c.post("/api/v2/migration/export-batch", headers=_servizio(), json={
            **CORPO, "aip_ids": [str(pda["aip"]), str(altro["aip"])], "ente_richiedente": ente})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["esportati"] == 1 and corpo["errori_count"] == 1
        assert corpo["errori"][0]["aip_id"] == str(altro["aip"])
        pdd = corpo["results"][0]["pdd_id"]
        assert await elabora_un_pdd() == "PRONTO"
        stato = await c.get(f"/api/v2/migration/status/{pdd}", params={"ente": ente},
                            headers=_servizio())
        assert stato.status_code == 200, stato.text
        assert stato.json()["stato"] == "PRONTO" and stato.json()["zip"]["hash_sha256"]
        # un altro ente non vede il PdD; senza ente la richiesta non è valida
        r = await c.get(f"/api/v2/migration/status/{pdd}",
                        params={"ente": str(altro["tenant"])}, headers=_servizio())
        assert r.status_code == 404
        r = await c.get(f"/api/v2/migration/status/{pdd}", headers=_servizio())
        assert r.status_code == 422
    righe = await sql(db_url, "SELECT opzioni, richiedente_id FROM public.pacchetti_distribuzione "
                              "WHERE id = $1", uuid.UUID(pdd))
    assert json.loads(righe[0]["opzioni"])["ente_richiedente"] == ente
    audit = await sql(db_url, "SELECT user_id, dettagli FROM public.audit_global "
                              "WHERE evento = 'migrazione.richiesta'")
    assert len(audit) == 1 and audit[0]["user_id"] is None
    assert json.loads(audit[0]["dettagli"])["servizio"] == "ciclo-vita"


async def test_servizio_senza_ente_richiedente_422(db_url, s3) -> None:  # noqa: F811
    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post("/api/v2/migration/export-batch", headers=_servizio(),
                         json={**CORPO, "aip_ids": [str(pda["aip"])]})
    assert r.status_code == 422
    assert await sql(db_url, "SELECT 1 FROM public.pacchetti_distribuzione") == []


@pytest.mark.parametrize(("intestazioni", "atteso"), [
    ({"scopes": ("registro:leggi",)}, 403),
    ({"pubblico": "verumdoc-registro"}, 401),
])
async def test_token_di_servizio_non_ammesso(db_url, s3, intestazioni, atteso) -> None:  # noqa: F811
    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post("/api/v2/migration/export-batch", headers=_servizio(**intestazioni),
                         json={**CORPO, "aip_ids": [str(pda["aip"])],
                               "ente_richiedente": str(pda["tenant"])})
    assert r.status_code == atteso, r.text
    assert await sql(db_url, "SELECT 1 FROM public.pacchetti_distribuzione") == []


async def test_export_singolo_solo_persone(db_url, s3) -> None:  # noqa: F811
    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post(f"/api/v2/migration/export/{pda['aip']}", headers=_servizio(),
                         json=CORPO)
    assert r.status_code == 403
