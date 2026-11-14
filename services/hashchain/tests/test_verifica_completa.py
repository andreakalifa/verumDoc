# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
G2 — stato della catena ed esecuzione della verifica completa, su PostgreSQL
reale migrato (blocchi sigillati dal codice vero del servizio).

Prima: /hashchain/status restituiva sempre ``integrita.integra = null`` e
nessun blocco riportava lo stato di firma; non esisteva una verifica completa
con esito registrato.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from app.services.block_sealer import sigilla_blocco
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

ORA = datetime(2026, 10, 20, 10, 30, tzinfo=UTC)


async def _client() -> AsyncClient:
    from app.routers.catena import router
    from fastapi import FastAPI

    from libs.shared.errori import installa_gestori_errori
    from tests.token_di_prova import intestazioni

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    app.include_router(router)
    # g3-backend: le API della catena richiedono un token verificato
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       headers=intestazioni("RSC_CONSERVATORE"))


async def _verifica(c: AsyncClient, **kw) -> dict:
    """g4: avvio asincrono (202) e attesa dell'esito con il polling dello stato."""
    from app.services.verifica_asincrona import attendi_verifiche

    r = await c.post("/api/v1/hashchain/verifica-completa", **kw)
    assert r.status_code == 202, r.text
    assert r.json()["stato"] == "IN_CORSO"
    await attendi_verifiche()
    stato = await c.get(f"/api/v1/hashchain/verifica-completa/{r.json()['verifica_id']}")
    assert stato.status_code == 200, stato.text
    return stato.json()


async def _aip_su_storage(url: str, tenant: uuid.UUID, classe: uuid.UUID) -> str:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'prova', 'IN_BUILD', $2, $3, $4)", pdv, classe, uuid.uuid4(),
                   tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'SU_STORAGE', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:test:{aip}", hashlib.sha256(aip.bytes).hexdigest())
    return str(aip)


async def _catena(url: str, blocchi: int = 2) -> list[str]:
    tenant, classe = await crea_tenant_e_classe(url)
    aips = []
    for i in range(blocchi):
        lotto = [await _aip_su_storage(url, tenant, classe) for _ in range(3)]
        assert await sigilla_blocco(lotto, ora=ORA + timedelta(minutes=i))
        aips += lotto
    return aips


async def _senza_trigger(url: str, istruzione: str, *argomenti) -> None:
    """Manomissione da superutente del DB (trigger append-only scavalcati)."""
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute("SET session_replication_role = replica")
        await conn.execute(istruzione, *argomenti)
    finally:
        await conn.close()


async def test_mai_verificata_poi_integra_con_esito_registrato(db_url) -> None:
    await _catena(db_url)
    async with await _client() as c:
        prima = await c.get("/api/v1/hashchain/status")
        assert prima.status_code == 200, prima.text
        corpo = prima.json()
        assert corpo["integrita"]["integra"] is None
        assert corpo["integrita"]["esito"] == "MAI_VERIFICATA"
        assert corpo["integrita"]["verificata_il"] is None
        assert corpo["totale_blocchi"] == 3 and corpo["ultimo_blocco"] == 2
        # Stato di firma di ogni blocco (colonne di be-pipeline)
        assert all("stato_firma" in b for b in corpo["blocchi_recenti"])
        assert corpo["blocchi_recenti"][0]["numero"] == 2
        assert sum(corpo["firma"]["conteggi"].values()) == 2   # genesi esclusa

        esito = await _verifica(c, json={"avviata_da": str(uuid.uuid4()),
                                         "ruolo": "RSC_CONSERVATORE"})
        assert esito["stato"] == "COMPLETATA"
        assert esito["esito"] == "INTEGRA" and esito["integra"] is True
        assert esito["blocchi_verificati"] == 3 and esito["ultimo_blocco_verificato"] == 2
        assert esito["blocchi_senza_firma_qualificata"] == 2

        dopo = (await c.get("/api/v1/hashchain/status")).json()["integrita"]
    assert dopo["integra"] is True and dopo["esito"] == "INTEGRA"
    assert dopo["verificata_il"] == esito["conclusa_il"]
    assert dopo["verifica_id"] == esito["verifica_id"]
    assert dopo["blocchi_aggiunti_dopo"] == 0
    righe = await sql(db_url, "SELECT evento, user_id FROM public.audit_global "
                              "WHERE id = $1", uuid.UUID(esito["verifica_id"]))
    assert righe[0]["evento"] == "hashchain.verifica_completa"


async def test_blocco_aggiunto_dopo_la_verifica_e_dichiarato(db_url) -> None:
    await _catena(db_url, blocchi=1)
    async with await _client() as c:
        assert (await _verifica(c))["stato"] == "COMPLETATA"
        tenant, classe = await crea_tenant_e_classe(db_url)
        assert await sigilla_blocco([await _aip_su_storage(db_url, tenant, classe)],
                                    ora=ORA + timedelta(hours=1))
        stato = (await c.get("/api/v1/hashchain/status")).json()["integrita"]
    assert stato["ultimo_blocco_verificato"] == 1
    assert stato["blocchi_aggiunti_dopo"] == 1


async def test_radice_di_merkle_alterata_compromessa(db_url) -> None:
    aips = await _catena(db_url)
    # Impronta di un AIP del blocco 1 cambiata: la radice ricalcolata non torna
    await _senza_trigger(db_url, "UPDATE public.aip SET hash_sha256 = $1 WHERE id = $2",
                         "f" * 64, uuid.UUID(aips[0]))
    async with await _client() as c:
        v = await _verifica(c)
        stato = (await c.get("/api/v1/hashchain/status")).json()["integrita"]
    assert v["esito"] == "COMPROMESSA" and v["integra"] is False
    assert "Merkle" in v["messaggio"]
    assert stato["integra"] is False and stato["esito"] == "COMPROMESSA"


async def test_collegamento_aip_blocco_alterato(db_url) -> None:
    aips = await _catena(db_url)
    blocchi = await sql(db_url, "SELECT id FROM public.hashchain_blocks ORDER BY numero_blocco")
    # L'AIP del blocco 1 viene fatto puntare al blocco 2: impronte e radici restano uguali
    await _senza_trigger(db_url, "UPDATE public.aip SET hashchain_block_id = $1 WHERE id = $2",
                         blocchi[2]["id"], uuid.UUID(aips[0]))
    async with await _client() as c:
        v = await _verifica(c)
    assert v["esito"] == "COMPROMESSA"
    assert "non è collegato" in v["messaggio"]


async def test_genesi_alterata(db_url) -> None:
    await _catena(db_url, blocchi=1)
    await _senza_trigger(db_url, "UPDATE public.hashchain_blocks SET merkle_root = $1 "
                                 "WHERE numero_blocco = 0", "1" * 64)
    async with await _client() as c:
        v = await _verifica(c)
    assert v["esito"] == "COMPROMESSA" and "genesi" in v["messaggio"]


async def test_catena_vuota_non_e_compromessa(db_url) -> None:
    await _senza_trigger(db_url, "DELETE FROM public.hashchain_blocks")
    async with await _client() as c:
        v = await _verifica(c)
        stato = (await c.get("/api/v1/hashchain/status")).json()
    assert v["esito"] == "NESSUN_BLOCCO" and v["integra"] is None
    assert stato["totale_blocchi"] == 0 and stato["integrita"]["integra"] is None


async def test_database_non_raggiungibile_503_senza_testo_sql(db_url) -> None:
    await sql(db_url, "ALTER TABLE public.hashchain_blocks RENAME TO hb_altrove")
    async with await _client() as c:
        r = await c.get("/api/v1/hashchain/status")
        # g4: l'avvio riesce (registro in audit_global); la verifica FALLISCE
        # in fondo, senza esito sulla catena e senza testo SQL
        v = await _verifica(c)
    assert r.status_code == 503
    corpo = r.json()
    assert corpo["codice"] == "HC-503" and corpo["id_richiesta"] == r.headers["x-request-id"]
    assert "hashchain_blocks" not in r.text and "relation" not in r.text
    assert v["stato"] == "FALLITA" and v["esito"] is None and v["conclusa_il"]
    assert "hashchain_blocks" not in str(v) and "relation" not in str(v)


async def test_avvio_con_registro_non_scrivibile_503(db_url) -> None:
    # Registro non scrivibile simulato: rinominare un registro protetto è rifiutato
    # dall'event trigger (migrazione 20261023_033), qui sospeso SOLO nella copia
    # del database di questo test (superutente)
    await sql(db_url, "ALTER EVENT TRIGGER vd_protezione_registri_stato DISABLE")
    await sql(db_url, "ALTER TABLE public.audit_global RENAME TO ag_altrove")
    async with await _client() as c:
        v = await c.post("/api/v1/hashchain/verifica-completa")
    assert v.status_code == 503
    assert v.json()["codice"] == "HC-503"
    assert "audit_global" not in v.text and "relation" not in v.text
