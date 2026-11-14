# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b4 — dashboard-api con chiave API (VD-C-099) e ricerca del portale sui
metadati Allegato 5 popolati (VD-C-093), su PostgreSQL reale.

Prima un sistema versante non poteva elencare i propri versamenti (solo
Bearer) e la ricerca nei metadati non trovava nulla (tabella mai popolata).
I metadati qui si scrivono come li scrive aip-builder (una riga per
documento con i minimi dell'Allegato 5).
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.revoca import svuota_cache_revoche
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

PEPPER = b"pepper-di-prova-dashboard-b4-0123456789abcdef"


@pytest.fixture(autouse=True)
def _pepper(monkeypatch):
    from app.auth import dipendenze

    monkeypatch.setattr(dipendenze, "_PEPPER", PEPPER)
    svuota_cache_revoche()
    yield
    svuota_cache_revoche()


async def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _ente_con_chiave(url: str) -> tuple[uuid.UUID, uuid.UUID, str]:
    tenant, classe = await crea_tenant_e_classe(url)
    utente = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, 'p@example.org', 'Sistema', 'Protocollo', 'OPERATORE')",
              utente, tenant)
    generata = genera_chiave_api(PEPPER)
    await sql(url, "INSERT INTO public.api_keys (id, tenant_id, user_id, nome, key_hash, "
                   "key_prefix, permessi) VALUES ($1, $2, $3, 'Protocollo', $4, $5, "
                   "CAST('[\"read\"]' AS jsonb))",
              uuid.uuid4(), tenant, utente, generata.impronta, generata.identificativo)
    return tenant, classe, generata.chiave


async def _pda_con_metadati(url: str, tenant: uuid.UUID, classe: uuid.UUID,
                            produttore: str) -> uuid.UUID:
    pdv, aip, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'fattura — doc.pdf', 'CONSERVATO', $2, $3, $4)",
              pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, s3_key) VALUES ($1, $2, 'doc.pdf', "
                   "'PRINCIPALE', 'application/pdf', 1, $3, 'k')", doc, aip, "b" * 64)
    await sql(url, "INSERT INTO public.metadati_allegato5 (documento_id, identificativo_documento, "
                   "modalita_formazione, tipologia_documentale, data_documento, "
                   "soggetto_produttore_id, soggetto_produttore_nome, oggetto, formato) VALUES "
                   "($1, 'FT-9', 'a', 'fattura', DATE '2026-03-19', '01234567890', $2, "
                   "'Fornitura di carta', 'application/pdf')", doc, produttore)
    return aip


async def test_chiave_api_elenca_versamenti_pacchetti_e_cerca(db_url, monkeypatch) -> None:
    from app.routers import dashboard

    from tests.distribution_finta import DistributionFinta

    finta = DistributionFinta()
    monkeypatch.setattr(dashboard, "TRASPORTO_DISTRIBUTION", finta.trasporto())
    tenant, classe, chiave = await _ente_con_chiave(db_url)
    aip = await _pda_con_metadati(db_url, tenant, classe, "Cartiera Rossi S.r.l.")
    altro, classe_altro, _ = await _ente_con_chiave(db_url)
    await _pda_con_metadati(db_url, altro, classe_altro, "Cartiera Rossi S.r.l.")
    h = {"X-Api-Key": chiave}
    async with await _client() as c:
        versamenti = await c.get("/api/dashboard/versamenti",
                                 params={"tenant_id": str(tenant)}, headers=h)
        assert versamenti.status_code == 200, versamenti.text
        assert len(versamenti.json()["items"]) == 1
        # R03 i1: con la chiave API l'ente viene dall'identità, tenant_id facoltativo
        senza_ente = await c.get("/api/dashboard/versamenti", headers=h)
        assert senza_ente.status_code == 200, senza_ente.text
        assert senza_ente.json()["items"] == versamenti.json()["items"]
        pacchetti = await c.get("/api/dashboard/aip", params={"tenant_id": str(tenant)},
                                headers=h)
        assert pacchetti.status_code == 200
        [voce] = pacchetti.json()["items"]
        assert voce["metadati_allegato5"][0]["metadati"]["soggetto_produttore_nome"] == \
            "Cartiera Rossi S.r.l."
        # R03 f2 (REV-06): la ricerca testuale va all'indice (distribution) con la
        # stessa chiave API del chiamante
        finta.risultati[str(tenant)] = [str(aip)]
        trovati = await c.post("/api/dashboard/search", json={"query": "cartiera rossi"},
                               headers=h)
        assert trovati.status_code == 200, trovati.text
        assert [i["aip_id"] for i in trovati.json()["items"]] == [str(aip)]
        assert finta.richieste[0][1]["x-api-key"] == chiave
        # un altro ente: la chiave non lo apre
        negato = await c.get("/api/dashboard/versamenti", params={"tenant_id": str(altro)},
                             headers=h)
        assert negato.status_code == 403


async def test_chiave_api_revocata_o_ente_sospeso_401(db_url) -> None:
    tenant, _, chiave = await _ente_con_chiave(db_url)
    async with await _client() as c:
        await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", tenant)
        r = await c.get("/api/dashboard/versamenti", params={"tenant_id": str(tenant)},
                        headers={"X-Api-Key": chiave})
        assert r.status_code == 401
        await sql(db_url, "UPDATE public.tenants SET stato = 'ATTIVO' WHERE id = $1", tenant)
        await sql(db_url, "UPDATE public.api_keys SET scadenza = now() - interval '1 day'")
        r = await c.get("/api/dashboard/versamenti", params={"tenant_id": str(tenant)},
                        headers={"X-Api-Key": chiave})
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-002"
        senza = await c.get("/api/dashboard/versamenti", params={"tenant_id": str(tenant)})
        assert senza.status_code == 401 and senza.json()["codice"] == "AUTH-000"


async def test_rotte_amministrative_restano_solo_bearer(db_url) -> None:
    """La chiave API non apre le funzioni del portale fuori dal perimetro di VD-C-099."""
    tenant, _, chiave = await _ente_con_chiave(db_url)
    async with await _client() as c:
        r = await c.get("/api/dashboard/tenants", headers={"X-Api-Key": chiave})
    assert r.status_code == 401
