# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza (g5-sicurezza) — distribution, su PostgreSQL reale.

M6  migrazione: id non UUID → 422; errore del database senza testo SQL al
    client; /status con token RSC/SUPER_ADMIN e solo per i job del proprio ente.
B7  fascicoli: il responsabile è un utente attivo dello stesso ente (422 uguale
    per inesistente e altrui: nessun oracolo).
B9  ricerca: OpenSearch riceve il TESTO cercato e il filtro dell'ente; senza
    ente nessuna ricerca; OpenSearch giù → 503 (non elenco vuoto).
B10 approva/rifiuta: id non UUID → 422; solo notifiche PDD_*; motivo testo.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

CONSERVATORE = "00000000-0000-0000-0000-0000000000c0"


def _h(tenant_id: str, ruolo: str = "ADMIN_TENANT", user_id: str | None = None) -> dict:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id, user_id=user_id or str(uuid.uuid4()), ruolo=ruolo)}


async def _client() -> AsyncClient:
    from app.routers.distribution import router
    from app.routers.fascicoli import router as fascicoli
    from app.routers.migration import router as migrazione

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    for r in (router, fascicoli, migrazione):
        app.include_router(r)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _aip(url: str, tenant: uuid.UUID, classe: uuid.UUID) -> uuid.UUID:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    return aip


# ── M6: migrazione ───────────────────────────────────────────────────

CORPO_MIGRAZIONE = {"conservatore_destinatario": "Altro Conservatore",
                    "motivo": "Cambio conservatore a fine contratto",
                    "richiedente_id": "rsc"}


@richiede_pg
async def test_migrazione_id_non_valido_422(db_url) -> None:
    h = _h(CONSERVATORE, "RSC_CONSERVATORE")
    async with await _client() as c:
        r = await c.post("/api/v2/migration/export/non-un-uuid", json=CORPO_MIGRAZIONE, headers=h)
        assert r.status_code == 422 and r.json()["codice"] == "MIG-422"
        r = await c.post("/api/v2/migration/export-batch", headers=h, json={
            "aip_ids": [str(uuid.uuid4()), "rotto"], "conservatore_destinatario": "X",
            "motivo": "m", "richiedente_id": "r"})
        assert r.status_code == 422
        assert r.json()["dettagli"]["non_validi"] == ["rotto"]


@richiede_pg
async def test_migrazione_errore_db_senza_testo_sql(db_url) -> None:
    await sql(db_url, "ALTER TABLE public.aip RENAME TO aip_altrove")
    h = _h(CONSERVATORE, "SUPER_ADMIN")
    async with await _client() as c:
        for r in (
            await c.post(f"/api/v2/migration/export/{uuid.uuid4()}", json=CORPO_MIGRAZIONE,
                         headers=h),
            await c.post("/api/v2/migration/export-batch", headers=h, json={
                "aip_ids": [str(uuid.uuid4())], "conservatore_destinatario": "X",
                "motivo": "m", "richiedente_id": "r"}),
        ):
            assert r.status_code == 503, r.text
            assert r.json()["id_richiesta"]
            testo = r.text.lower()
            assert "aip" not in testo.replace("mig-", "") and "select" not in testo
            assert "relation" not in testo and "errore" not in r.json()["dettagli"]


@richiede_pg
async def test_stato_job_con_token_e_solo_del_proprio_ente(db_url) -> None:
    t1, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, t1, classe)
    async with await _client() as c:
        r = await c.post(f"/api/v2/migration/export/{aip}", json=CORPO_MIGRAZIONE,
                         headers=_h(CONSERVATORE, "RSC_CONSERVATORE"))
        assert r.status_code == 200, r.text
        job = r.json()["export_id"]
        url = f"/api/v2/migration/status/{job}"
        assert (await c.get(url)).status_code == 401
        assert (await c.get(url, headers=_h(str(t1), "ADMIN_TENANT"))).status_code == 403
        # SUPER_ADMIN di un altro ente: come un job inesistente
        altro = str(uuid.uuid4())
        assert (await c.get(url, headers=_h(altro, "SUPER_ADMIN"))).status_code == 404
        mio = await c.get(url, headers=_h(CONSERVATORE, "SUPER_ADMIN"))
        assert mio.status_code == 200
        assert mio.json()["export_id"] == job and "_tenant_id" not in mio.json()
        assert (await c.get(f"/api/v2/migration/status/{uuid.uuid4()}",
                            headers=_h(CONSERVATORE, "SUPER_ADMIN"))).status_code == 404


# ── B7: responsabile del fascicolo ───────────────────────────────────

@richiede_pg
async def test_responsabile_fascicolo_dello_stesso_ente(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    mio, altrui, spento = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for uid, tenant, attivo in ((mio, t1, True), (altrui, t2, True), (spento, t1, False)):
        await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, attivo) "
                          "VALUES ($1, $2, $3, 'N', 'C', $4)", uid, tenant, f"{uid.hex[:8]}@x.it",
                  attivo)
    h = _h(str(t1), "OPERATORE")
    async with await _client() as c:
        risposte = {}
        for nome, uid in (("altrui", altrui), ("inesistente", uuid.uuid4()), ("spento", spento)):
            r = await c.post("/api/v2/fascicoli", headers=h,
                             json={"titolo": f"Fascicolo {nome}", "responsabile_id": str(uid)})
            risposte[nome] = (r.status_code, r.json()["codice"], r.json()["title"])
        # stessa risposta per l'utente di un altro ente e per uno inesistente
        assert risposte["altrui"] == risposte["inesistente"] == risposte["spento"]
        assert risposte["altrui"][0] == 422
        r = await c.post("/api/v2/fascicoli", headers=h,
                         json={"titolo": "Fascicolo buono", "responsabile_id": str(mio)})
        assert r.status_code == 201, r.text
    righe = await sql(db_url, "SELECT responsabile_id FROM public.fascicoli WHERE tenant_id = $1",
                      t1)
    assert [r["responsabile_id"] for r in righe] == [mio]


# ── B9: ricerca ──────────────────────────────────────────────────────

@pytest.fixture
def opensearch(monkeypatch):
    """OpenSearch finto: vero server HTTP (tests/opensearch_finto.py)."""
    from app.config import settings

    from tests.opensearch_finto import OpenSearchFinto

    finto = OpenSearchFinto().avvia()
    monkeypatch.setattr(settings, "opensearch_url", finto.url)
    yield finto
    finto.ferma()


@pytest.mark.asyncio
async def test_ricerca_passa_testo_e_filtro_ente(opensearch) -> None:
    """R03 b4: la ricerca arriva via HTTP con testo, filtro ed instradamento
    dell'ENTE DEL TOKEN (non quello del corpo). Nessun PdA indicizzato: vuoto."""
    ente = str(uuid.uuid4())
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/search", headers=_h(ente, "VIEWER"),
                         json={"query": "delibera 42", "anno": "2026",
                               "tenant_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    assert {k: v for k, v in r.json().items() if k != "campi_testo"} == {
        "total": 0, "total_esatto": True, "items": [], "cursore": None, "altri": False}
    [(corpo, parametri)] = opensearch.ricerche
    # R03 f2 (REV-06): nessuna paginazione per posizione, conteggio limitato
    assert "from" not in corpo and corpo["track_total_hits"] == 1000
    assert corpo["query"]["bool"]["must"][0]["multi_match"]["query"] == "delibera 42"
    filtri = corpo["query"]["bool"]["filter"]
    # l'ente è quello del token, non quello scritto nel corpo
    assert {"term": {"tenant_id": ente}} in filtri and {"term": {"anno": "2026"}} in filtri
    assert parametri == {"routing": ente}


@pytest.mark.asyncio
async def test_ricerca_opensearch_giu_503_senza_dettagli(opensearch) -> None:
    opensearch.guasto = True
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/search", headers=_h(str(uuid.uuid4())),
                         json={"query": "x"})
    assert r.status_code == 503
    assert "opensearch" not in r.text.lower() and r.json()["id_richiesta"]


@pytest.mark.asyncio
async def test_ricerca_opensearch_irraggiungibile_503(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "opensearch_url", "http://127.0.0.1:9")
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/search", headers=_h(str(uuid.uuid4())),
                         json={"query": "x"})
    assert r.status_code == 503 and r.json()["codice"] == "DST-503"


@pytest.mark.asyncio
@pytest.mark.parametrize("ente", ["", None, "   "])
async def test_search_aip_senza_ente_errore(opensearch, ente) -> None:
    from app.services import search_index

    with pytest.raises(ValueError):
        await search_index.search_aip("x", ente)
    assert opensearch.ricerche == []


# ── B10: approva / rifiuta ───────────────────────────────────────────

async def _notifica(url: str, tenant: uuid.UUID, tipo: str) -> uuid.UUID:
    nid = uuid.uuid4()
    await sql(url, "INSERT INTO public.notifications (id, tenant_id, tipo, canale, destinatario, "
                   "stato) VALUES ($1, $2, $3, 'WEB', $4, 'PENDING')", nid, tenant, tipo,
              nid.hex[:8])
    return nid


@richiede_pg
async def test_approva_rifiuta_id_non_valido_e_tipo(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    email = await _notifica(db_url, t1, "pdv.invalid")
    pdd = await _notifica(db_url, t1, "PDD_ESIBIZIONE")
    h = _h(str(t1))
    async with await _client() as c:
        for azione in ("approva", "rifiuta"):
            r = await c.post(f"/api/v2/distribution/non-un-uuid/{azione}", headers=h, json={})
            assert r.status_code == 422, (azione, r.text)
            r = await c.post(f"/api/v2/distribution/{email}/{azione}", headers=h, json={})
            assert r.status_code == 404, (azione, r.text)
        r = await c.post(f"/api/v2/distribution/{pdd}/rifiuta", headers=h,
                         json={"motivo": {"non": "testo"}})
        assert r.status_code == 422
        r = await c.post(f"/api/v2/distribution/{pdd}/rifiuta", headers=h,
                         json={"motivo": "Documento non pertinente"})
        assert r.status_code == 200 and r.json()["stato"] == "RIFIUTATA"
    stati = {r["tipo"]: r["stato"] for r in await sql(
        db_url, "SELECT tipo, stato FROM public.notifications WHERE tenant_id = $1", t1)}
    assert stati == {"pdv.invalid": "PENDING", "PDD_ESIBIZIONE": "RIFIUTATA"}


@richiede_pg
async def test_approva_solo_pdd(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    pdd = await _notifica(db_url, t1, "PDD_ESIBIZIONE")
    async with await _client() as c:
        r = await c.post(f"/api/v2/distribution/{pdd}/approva", headers=_h(str(t1)))
    assert r.status_code == 200 and r.json()["stato"] == "APPROVATA"


# ── B5: revoca ───────────────────────────────────────────────────────

@richiede_pg
async def test_token_di_ente_sospeso_o_utente_disattivato_401(db_url) -> None:
    from libs.shared.security.revoca import svuota_cache_revoche

    svuota_cache_revoche()
    t1, _ = await crea_tenant_e_classe(db_url)
    uid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, attivo) "
                      "VALUES ($1, $2, 'op@x.it', 'N', 'C', true)", uid, t1)
    h = _h(str(t1), "OPERATORE", user_id=str(uid))
    async with await _client() as c:
        assert (await c.post("/api/v2/fascicoli", headers=h,
                             json={"titolo": "Prima"})).status_code == 201
        await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", uid)
        svuota_cache_revoche()
        r = await c.post("/api/v2/fascicoli", headers=h, json={"titolo": "Dopo"})
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-003"
        await sql(db_url, "UPDATE public.users SET attivo = true WHERE id = $1", uid)
        await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", t1)
        svuota_cache_revoche()
        r = await c.get("/api/v2/distribution/storico", headers=h)
        assert r.status_code == 401
    svuota_cache_revoche()
    assert [x["titolo"] for x in await sql(db_url, "SELECT titolo FROM public.fascicoli")] == \
        ["Prima"]
