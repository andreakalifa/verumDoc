# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fascicoli informatici — distribution su PostgreSQL reale (VD-C-026).

Prima: la creazione passava al modello campi inesistenti (tipo, anno) e
ometteva codice e data di apertura obbligatori (500); il dettaglio usava una
relazione inesistente (aip_list); la tabella non aveva tenant_id e nessuna
lettura era filtrata per cliente.
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


def _token(tenant_id: str, ruolo: str = "OPERATORE") -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id, user_id=str(uuid.uuid4()), ruolo=ruolo,
    )}


async def _client() -> AsyncClient:
    from app.routers.fascicoli import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _aip(url: str, tenant: uuid.UUID, classe: uuid.UUID) -> uuid.UUID:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    return aip


async def test_crea_dettaglio_aggiungi_e_lista(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    h = _token(str(tenant))
    async with await _client() as c:
        r = await c.post("/api/v2/fascicoli", json={
            "titolo": "Gara servizi 2026", "tipo": "PROCEDIMENTO", "anno": 2026,
            "codice": "GARA-2026-01",
        }, headers=h)
        assert r.status_code == 201, r.text
        f = r.json()
        assert f["codice"] == "GARA-2026-01" and f["stato"] == "APERTO" and f["num_aip"] == 0

        r = await c.post(f"/api/v2/fascicoli/{f['id']}/aip",
                         json={"aip_id": str(aip), "ruolo": "ALLEGATO"}, headers=h)
        assert r.status_code == 200, r.text
        # Lo stesso AIP una seconda volta: 409, non 500
        r = await c.post(f"/api/v2/fascicoli/{f['id']}/aip", json={"aip_id": str(aip)}, headers=h)
        assert r.status_code == 409

        d = await c.get(f"/api/v2/fascicoli/{f['id']}", headers=h)
        assert d.status_code == 200, d.text
        assert d.json()["num_aip"] == 1
        lista = await c.get(f"/api/v2/fascicoli/{f['id']}/aip", headers=h)
        assert lista.json()["items"] == [
            {
                "aip_id": str(aip), "urn": f"urn:prova:{aip}", "stato": "IN_BUILD",
                "ruolo": "ALLEGATO"},
        ]
    riga = await sql(db_url, "SELECT tenant_id FROM public.fascicoli WHERE id = $1",
                     uuid.UUID(f["id"]))
    assert riga[0]["tenant_id"] == tenant


async def test_codice_generato_e_unico_per_cliente(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        r = await c.post("/api/v2/fascicoli", json={"titolo": "Senza codice"},
                         headers=_token(str(t1)))
        assert r.status_code == 201 and r.json()["codice"].startswith("FASC-")
        corpo = {"titolo": "Con codice", "codice": "F-1"}
        assert (await c.post("/api/v2/fascicoli", json=corpo,
                             headers=_token(str(t1)))).status_code == 201
        assert (await c.post("/api/v2/fascicoli", json=corpo,
                             headers=_token(str(t1)))).status_code == 409
        # Stesso codice in un altro cliente: ammesso
        assert (await c.post("/api/v2/fascicoli", json=corpo,
                             headers=_token(str(t2)))).status_code == 201


async def test_isolamento_fra_clienti(db_url) -> None:
    t1, classe = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    aip_t2 = await _aip(db_url, t2, classe)
    async with await _client() as c:
        f = (await c.post("/api/v2/fascicoli", json={"titolo": "Di t1"},
                          headers=_token(str(t1)))).json()
        estraneo = _token(str(t2))
        assert (await c.get(f"/api/v2/fascicoli/{f['id']}", headers=estraneo)).status_code == 404
        assert (await c.get(f"/api/v2/fascicoli/{f['id']}/aip",
                            headers=estraneo)).status_code == 404
        assert (await c.post(f"/api/v2/fascicoli/{f['id']}/aip", json={"aip_id": str(aip_t2)},
                             headers=estraneo)).status_code == 404
        # AIP di un altro cliente nel proprio fascicolo: 404 (§14.6)
        r = await c.post(f"/api/v2/fascicoli/{f['id']}/aip", json={"aip_id": str(aip_t2)},
                         headers=_token(str(t1)))
        assert r.status_code == 404


async def test_id_non_validi_e_token_senza_cliente(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        assert (await c.get("/api/v2/fascicoli/abc", headers=_token(str(t1)))).status_code == 404
        r = await c.post("/api/v2/fascicoli", json={"titolo": "Prova"}, headers=_token("nessuno"))
        assert r.status_code == 403
        assert (await c.post("/api/v2/fascicoli", json={"titolo": "Prova"})).status_code == 401
        r = await c.post("/api/v2/fascicoli", json={"titolo": "Tipo errato", "tipo": "ALTRO"},
                         headers=_token(str(t1)))
        assert r.status_code == 422


async def test_fascicolo_chiuso_non_accetta_aip(db_url) -> None:
    t1, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, t1, classe)
    async with await _client() as c:
        f = (await c.post("/api/v2/fascicoli", json={"titolo": "Da chiudere"},
                          headers=_token(str(t1)))).json()
        await sql(db_url, "UPDATE public.fascicoli SET stato = 'CHIUSO' WHERE id = $1",
                  uuid.UUID(f["id"]))
        r = await c.post(f"/api/v2/fascicoli/{f['id']}/aip", json={"aip_id": str(aip)},
                         headers=_token(str(t1)))
    assert r.status_code == 409


# ── R05 (VD-C-087): elenco a cursore e chiusura ─────────────────────────────

async def test_elenco_fascicoli_cursore_e_perimetro(db_url) -> None:
    """Elenco del solo ente, dal più recente, a pagine senza salti né doppioni;
    filtri; cursore non valido → 400 FSC-400-CURSORE."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    altro, _ = await crea_tenant_e_classe(db_url)
    h = _token(str(tenant))
    creati = []
    async with await _client() as c:
        for i in range(5):
            r = await c.post("/api/v2/fascicoli", json={"titolo": f"Fascicolo {i}", "anno": 2026,
                                                        "codice": f"F-{i}"}, headers=h)
            creati.append(r.json()["id"])
        await c.post("/api/v2/fascicoli", json={"titolo": "Di un altro ente"},
                     headers=_token(str(altro)))
        visti, cursore = [], None
        while True:
            url = "/api/v2/fascicoli?limite=2" + (f"&cursore={cursore}" if cursore else "")
            pagina = (await c.get(url, headers=h)).json()
            visti += [f["id"] for f in pagina["items"]]
            cursore = pagina["cursore"]
            if not pagina["altri"]:
                break
        assert sorted(visti) == sorted(creati) and len(visti) == 5
        assert visti == list(reversed(creati))
        r = await c.get("/api/v2/fascicoli?stato=CHIUSO", headers=h)
        assert r.json()["items"] == []
        r = await c.get("/api/v2/fascicoli?cursore=xyz", headers=h)
        assert r.status_code == 400 and r.json()["codice"] == "FSC-400-CURSORE"
        r = await c.get("/api/v2/fascicoli")
        assert r.status_code == 401


async def test_chiusura_fascicolo(db_url) -> None:
    """Chiusura con data (predefinita oggi), poi nessun PdA aggiunto; date non
    valide → 422; doppia chiusura → 409; VIEWER non chiude; altro ente → 404."""
    from datetime import date, timedelta

    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    h = _token(str(tenant))
    async with await _client() as c:
        f = (await c.post("/api/v2/fascicoli", json={
            "titolo": "Procedimento concluso", "data_apertura": "2020-01-10"}, headers=h)).json()
        fid = f["id"]
        corpo = {"motivo": "Procedimento concluso con provvedimento finale"}
        r = await c.post(f"/api/v2/fascicoli/{fid}/chiudi", headers=_token(str(tenant), "VIEWER"),
                         json=corpo)
        assert r.status_code == 403
        altro, _ = await crea_tenant_e_classe(db_url)
        r = await c.post(f"/api/v2/fascicoli/{fid}/chiudi", headers=_token(str(altro)), json=corpo)
        assert r.status_code == 404
        for data in ("2019-12-31", (date.today() + timedelta(days=2)).isoformat()):
            r = await c.post(f"/api/v2/fascicoli/{fid}/chiudi", headers=h,
                             json={**corpo, "data_chiusura": data})
            assert r.status_code == 422 and r.json()["codice"] == "FSC-422-CHIUSURA", data
        r = await c.post(f"/api/v2/fascicoli/{fid}/chiudi", headers=h,
                         json={**corpo, "stato_atteso": "CHIUSO"})
        assert r.status_code == 409
        r = await c.post(f"/api/v2/fascicoli/{fid}/chiudi", headers=h,
                         json={**corpo, "data_chiusura": "2025-06-30"})
        assert r.status_code == 200, r.text
        assert r.json() == {"id": fid, "stato": "CHIUSO", "data_chiusura": "2025-06-30"}
        r = await c.post(f"/api/v2/fascicoli/{fid}/chiudi", headers=h, json=corpo)
        assert r.status_code == 409 and r.json()["codice"] == "FSC-409"
        r = await c.post(f"/api/v2/fascicoli/{fid}/aip", headers=h, json={"aip_id": str(aip)})
        assert r.status_code == 409
        dettaglio = (await c.get(f"/api/v2/fascicoli/{fid}", headers=h)).json()
        assert dettaglio["stato"] == "CHIUSO" and dettaglio["data_chiusura"] == "2025-06-30"
        elenco = (await c.get("/api/v2/fascicoli?stato=CHIUSO", headers=h)).json()
        assert [x["id"] for x in elenco["items"]] == [fid]
    voci = await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                             "'fascicolo.chiuso' AND tenant_id = $1", tenant)
    assert len(voci) == 1


def test_motivo_blocco_chiusura_puro() -> None:
    from datetime import date

    from app.services.fascicoli import motivo_blocco_chiusura

    oggi = date(2026, 10, 23)
    assert motivo_blocco_chiusura("APERTO", date(2026, 1, 1), oggi, oggi) is None
    assert motivo_blocco_chiusura("CHIUSO", date(2026, 1, 1), oggi, oggi)[0] == "FSC-409"
    assert motivo_blocco_chiusura("APERTO", date(2026, 1, 1), date(2025, 12, 31),
                                  oggi)[0] == "FSC-422-CHIUSURA"
