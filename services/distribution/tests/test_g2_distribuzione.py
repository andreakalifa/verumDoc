# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
G2 — esibizioni (distribution) e fascicoli su PostgreSQL reale.

Prima:
- lo storico restituiva {"total": 0, "items": []} su qualsiasi errore e
  total = len(righe); non riportava il canale;
- il richiedente registrato era quello scritto dal client ("dashboard-user");
- un errore nel registro della distribuzione veniva solo loggato e i link
  venivano consegnati comunque;
- un VIEWER poteva creare fascicoli e aggiungervi pacchetti.
"""
from __future__ import annotations

import json
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.pda_reale import s3  # noqa: F401

pytestmark = richiede_pg


# g3-test: il gateway non emette MAI token senza ente: anche SUPER_ADMIN e
# RSC_CONSERVATORE portano l'ente del conservatore. La verifica JWT condivisa
# (libs/shared/security/jwt_rs256.py) rifiuta un tenant_id vuoto con 401
# (fail-closed, REGOLE §14.3). Prima questi test firmavano tenant_id="" per i
# profili globali e fallivano con 401. Ora ``None`` = «ente del conservatore».
TENANT_CONSERVATORE = "00000000-0000-0000-0000-0000000000c0"


def _token(tenant_id: str | None, ruolo: str = "ADMIN_TENANT",
           user_id: str | None = None) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id or TENANT_CONSERVATORE, user_id=user_id or str(uuid.uuid4()),
        ruolo=ruolo,
    )}


def _token_senza_ente(ruolo: str) -> dict[str, str]:
    """Token firmato con tenant_id vuoto (mai emesso dal gateway)."""
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id="", user_id=str(uuid.uuid4()), ruolo=ruolo)}


async def _client() -> AsyncClient:
    from app.routers.distribution import router
    from app.routers.fascicoli import router as fascicoli

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    app.include_router(router)
    app.include_router(fascicoli)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _aip(url: str, tenant: uuid.UUID, classe: uuid.UUID) -> uuid.UUID:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'Prova', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    return aip


def _finta_distribuzione(monkeypatch) -> list:
    from app.models.distribution import RispostaDistribuzione
    from app.routers import distribution as modulo

    ricevute: list = []

    async def finta(req, tid):
        ricevute.append(req)
        return RispostaDistribuzione(
            pdd_id=str(uuid.uuid4()), aip_id=req.aip_id, tipo=req.tipo.value, documenti=[],
            sincro_xml_incluso=False, hash_pdd="0" * 64, created_at="2026-10-20T00:00:00Z",
        )

    monkeypatch.setattr(modulo, "esegui_distribuzione", finta)
    return ricevute


async def test_richiedente_dal_token_e_canale(db_url, monkeypatch) -> None:
    ricevute = _finta_distribuzione(monkeypatch)
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    utente = str(uuid.uuid4())
    corpo = {"aip_id": str(aip), "tipo": "ESIBIZIONE", "motivo": "richiesta ispettiva",
             "richiedente_id": "dashboard-user"}
    async with await _client() as c:
        web = await c.post("/api/v2/distribution/richiedi", json=corpo,
                           headers={**_token(str(tenant), user_id=utente),
                                    "X-VerumDoc-Canale": "WEB"})
        assert web.status_code == 200, web.text
        api = await c.post("/api/v2/distribution/richiedi", json={**corpo, "richiedente_id": None},
                           headers=_token(str(tenant), user_id=utente))
        assert api.status_code == 200, api.text
        strano = await c.post("/api/v2/distribution/richiedi", json=corpo,
                              headers={**_token(str(tenant), user_id=utente),
                                       "X-VerumDoc-Canale": "<script>"})
        assert strano.status_code == 200
        storico = await c.get("/api/v2/distribution/storico", headers=_token(str(tenant)))

    assert [r.richiedente_id for r in ricevute] == [utente] * 3   # valore del client ignorato
    righe = await sql(db_url, "SELECT id, canale, destinatario, payload_json "
                              "FROM public.notifications ORDER BY created_at")
    assert [r["canale"] for r in righe] == ["WEB", "API", "API"]
    assert {r["destinatario"] for r in righe} == {utente}
    # La riga del registro ha l'id del PdD: approva/rifiuta lo trovano
    assert str(righe[0]["id"]) == web.json()["pdd_id"]
    assert json.loads(righe[0]["payload_json"])["richiedente_id"] == utente
    # Lo storico mostra anche le richieste fatte dal portale
    assert storico.status_code == 200
    assert len(storico.json()["items"]) == 3 and storico.json()["altri"] is False
    assert {i["canale"] for i in storico.json()["items"]} == {"WEB", "API"}
    assert all(i["motivo"] == "richiesta ispettiva" for i in storico.json()["items"])


async def test_registro_non_disponibile_niente_link(db_url, monkeypatch) -> None:
    _finta_distribuzione(monkeypatch)
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    await sql(db_url, "ALTER TABLE public.notifications RENAME TO notifiche_altrove")
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/richiedi", json={
            "aip_id": str(aip), "tipo": "ESIBIZIONE", "motivo": "richiesta ispettiva",
        }, headers=_token(str(tenant)))
        s = await c.get("/api/v2/distribution/storico", headers=_token(str(tenant)))
    for risposta in (r, s):
        assert risposta.status_code == 503, risposta.text
        assert "documenti" not in risposta.json()
        assert risposta.json()["id_richiesta"] == risposta.headers["x-request-id"]
        assert "notifications" not in risposta.text and "relation" not in risposta.text


async def test_storico_a_cursore_e_profili_globali(db_url) -> None:
    """R04 (VD-C-102): a cursore, senza totale né skip."""
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    for i in range(5):
        await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, "
                          "destinatario, stato) VALUES ($1, 'PDD_ESIBIZIONE', $2, 'u', 'INVIATO')",
                  t1, "WEB" if i % 2 else "API")
    # Una notifica e-mail non è una distribuzione
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario) "
                      "VALUES ($1, 'pdv.invalid', 'email', 'a@example.org')", t1)
    async with await _client() as c:
        skip = await c.get("/api/v2/distribution/storico?limit=2&skip=2",
                           headers=_token(str(t1)))
        assert skip.status_code == 422 and skip.json()["codice"] == "DST-422"
        visti, cursore = [], None
        for _ in range(3):
            url = "/api/v2/distribution/storico?limit=2" + (f"&cursore={cursore}" if cursore
                                                           else "")
            pag = (await c.get(url, headers=_token(str(t1)))).json()
            assert "total" not in pag
            visti += [i["id"] for i in pag["items"]]
            cursore = pag["cursore"]
        assert len(visti) == 5 == len(set(visti)) and cursore is None and not pag["altri"]
        cattivo = await c.get("/api/v2/distribution/storico?cursore=abc", headers=_token(str(t1)))
        assert cattivo.status_code == 422 and cattivo.json()["codice"] == "DST-422"
        glob = await c.get(f"/api/v2/distribution/storico?tenant_id={t1}",
                           headers=_token(None, "RSC_CONSERVATORE"))
        assert glob.status_code == 200 and len(glob.json()["items"]) == 5
        altro = await c.get(f"/api/v2/distribution/storico?tenant_id={t1}",
                            headers=_token(str(t2)))
        assert altro.status_code == 403
        # token senza ente: rifiutato dalla verifica JWT condivisa (401, g3-test)
        senza = await c.get("/api/v2/distribution/storico",
                            headers=_token_senza_ente("SUPER_ADMIN"))
        assert senza.status_code == 401
        # profilo globale senza tenant_id: solo l'ente del proprio token
        proprio = await c.get("/api/v2/distribution/storico", headers=_token(None, "SUPER_ADMIN"))
        assert proprio.status_code == 200 and proprio.json()["items"] == []


async def test_viewer_non_crea_ne_modifica_fascicoli(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    corpo = {"titolo": "Gara 2026", "codice": "G-1"}
    async with await _client() as c:
        no = await c.post("/api/v2/fascicoli", json=corpo, headers=_token(str(tenant), "VIEWER"))
        assert no.status_code == 403
        si = await c.post("/api/v2/fascicoli", json=corpo, headers=_token(str(tenant), "OPERATORE"))
        assert si.status_code == 201, si.text
        fid = si.json()["id"]
        no = await c.post(f"/api/v2/fascicoli/{fid}/aip", json={"aip_id": str(aip)},
                          headers=_token(str(tenant), "VIEWER"))
        assert no.status_code == 403
        # Il VIEWER legge
        letto = await c.get(f"/api/v2/fascicoli/{fid}", headers=_token(str(tenant), "VIEWER"))
        assert letto.status_code == 200
    assert not await sql(db_url, "SELECT 1 FROM public.fascicoli_aip")
    assert len(await sql(db_url, "SELECT 1 FROM public.fascicoli")) == 1


async def test_esibizione_reale_con_impronta_del_pdd(db_url, s3) -> None:
    """Motore vero su PostgreSQL e S3 veri: prima compute_pdd_hash riceveva una
    lista e ogni PdD dava 500; R03 b4: i collegamenti puntano ai file REALI
    del PdA (documenti, IPdA, manifesto) con impronte verificate."""
    from tests.pda_reale import crea_pda

    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/richiedi", json={
            "aip_id": str(pda["aip"]), "tipo": "ESIBIZIONE", "motivo": "richiesta ispettiva",
        }, headers=_token(str(pda["tenant"])))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert len(corpo["hash_pdd"]) == 64 and corpo["stato"] == "PRONTO"
    assert [d["nome_file"] for d in corpo["documenti"]] == [
        "doc.pdf", "IndiceAIP_SInCRO.xml", "manifest.json"]
    assert all(d["signed_url"].startswith("http") and d["impronta_verificata"]
               for d in corpo["documenti"])
    assert all(d["s3_key"] is None for d in corpo["documenti"])
    righe = await sql(db_url, "SELECT id FROM public.notifications")
    assert [str(x["id"]) for x in righe] == [corpo["pdd_id"]]
