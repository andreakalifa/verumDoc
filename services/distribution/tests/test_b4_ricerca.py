# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b4 — indicizzazione e ricerca (VD-C-093), su PostgreSQL vero e OpenSearch
finto (vero server HTTP, tests/opensearch_finto.py).

Prima: ``index_aip()`` mai chiamata, ricerca sempre vuota; ``metadati_allegato5``
mai popolata. Ora la coda ``indicizzazione_aip`` (scritta da aip-builder col PdA)
è consumata a lotti con la bulk API; la ricerca filtra per ente, pagina a
cursore e riporta lo stato attuale dal database.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.pda_reale import crea_pda, s3  # noqa: F401

pytestmark = richiede_pg

ADESSO = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def opensearch(monkeypatch):
    from app.config import settings

    from tests.opensearch_finto import OpenSearchFinto

    finto = OpenSearchFinto().avvia()
    monkeypatch.setattr(settings, "opensearch_url", finto.url)
    monkeypatch.setattr(settings, "indicizzazione_ritardi_secondi", [60, 300])
    yield finto
    finto.ferma()


def _h(tenant, ruolo: str = "VIEWER") -> dict:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(tenant), user_id=str(uuid.uuid4()), ruolo=ruolo)}


async def _client() -> AsyncClient:
    from app.routers.distribution import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _in_coda(url: str, pda: dict) -> None:
    await sql(url, "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id, prossimo_tentativo) "
                   "VALUES ($1, $2, $3)", pda["aip"], pda["tenant"], ADESSO - timedelta(seconds=1))


async def _coda(url: str) -> dict[str, dict]:
    righe = await sql(url, "SELECT aip_id, stato, tentativi, prossimo_tentativo, ultimo_errore "
                           "FROM public.indicizzazione_aip")
    return {str(r["aip_id"]): dict(r) for r in righe}


async def test_elabora_lotto_indicizza_con_metadati(db_url, s3, opensearch) -> None:
    from app.services.indicizzazione import elabora_lotto

    pda = await crea_pda(db_url, s3, metadati={"metadati_extra": {"cig": "Z123"}})
    await _in_coda(db_url, pda)
    esito = await elabora_lotto(adesso=ADESSO)
    assert esito == {"indicizzati": 1, "falliti": 0, "in_errore": 0}
    doc = opensearch.documenti[str(pda["aip"])]
    assert doc["tenant_id"] == str(pda["tenant"]) and doc["classe_documentale"] == "fattura"
    assert doc["numero"] == "42" and doc["registro"] == "DELIBERE" and doc["anno"] == "2026"
    assert doc["identificativi_documento"] == ["DEL-42"]
    assert doc["numeri_registrazione"] == ["PG/2026/42"]
    assert "Comune di Prova" in doc["soggetti_produttori"] and "Z123" in doc["testo_completo"]
    assert doc["nomi_file"] == ["doc.pdf"] and doc["data_documento"] == "2026-03-19"
    assert opensearch.routing[str(pda["aip"])] == str(pda["tenant"])
    assert (await _coda(db_url))[str(pda["aip"])]["stato"] == "INDICIZZATO"
    # niente di nuovo da fare; un nuovo invio dello stesso PdA è idempotente
    assert (await elabora_lotto(adesso=ADESSO))["indicizzati"] == 0
    await sql(db_url, "UPDATE public.indicizzazione_aip SET stato = 'IN_CODA'")
    await elabora_lotto(adesso=ADESSO)
    assert len(opensearch.documenti) == 1


async def test_metadati_sensibili_del_profilo_fuori_dall_indice(db_url, s3, opensearch) -> None:
    from app.services.indicizzazione import elabora_lotto

    classe = (await sql(db_url, "SELECT id FROM public.classi_documentali WHERE codice = 'fattura' "
                                "AND tenant_id IS NULL"))[0]["id"]
    profilo = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.profili_metadati (id, classe_id, versione, valido_dal, "
                      "campi) VALUES ($1, $2, 1, DATE '2026-01-01', CAST($3 AS jsonb))",
              profilo, classe, json.dumps([
                  {"nome": "codice_fiscale_paziente", "tipo": "testo", "sensibile": True},
                  {"nome": "reparto", "tipo": "testo"}]))
    pda = await crea_pda(
        db_url, s3, metadati={"metadati_extra": {"codice_fiscale_paziente": "FTZPRV00A01Z999V",
                                                 "reparto": "Cardiologia"}},
        regole_applicate={"profilo_metadati": {"id": str(profilo), "versione": 1}})
    await _in_coda(db_url, pda)
    await elabora_lotto(adesso=ADESSO)
    testo = json.dumps(opensearch.documenti[str(pda["aip"])])
    assert "Cardiologia" in testo and "FTZPRV00A01Z999V" not in testo


async def test_errori_per_voce_ritentativi_poi_errore_e_rielabora(db_url, s3, opensearch) -> None:
    from app.services.indicizzazione import elabora_lotto, rimetti_in_coda

    buono, cattivo = await crea_pda(db_url, s3), await crea_pda(db_url, s3)
    for p in (buono, cattivo):
        await _in_coda(db_url, p)
    opensearch.rifiuta_ids = {str(cattivo["aip"])}
    assert await elabora_lotto(adesso=ADESSO) == {"indicizzati": 1, "falliti": 1, "in_errore": 0}
    coda = await _coda(db_url)
    riga = coda[str(cattivo["aip"])]
    assert riga["stato"] == "IN_CODA" and riga["tentativi"] == 1
    assert riga["prossimo_tentativo"] == ADESSO + timedelta(seconds=60)
    assert "mapper_parsing_exception" in riga["ultimo_errore"]
    # prima del ritardo non si riprova
    assert (await elabora_lotto(adesso=ADESSO))["falliti"] == 0
    await elabora_lotto(adesso=ADESSO + timedelta(seconds=61))
    esito = await elabora_lotto(adesso=ADESSO + timedelta(seconds=400))
    assert esito == {"indicizzati": 0, "falliti": 1, "in_errore": 1}
    assert (await _coda(db_url))[str(cattivo["aip"])]["stato"] == "ERRORE"
    # «Rielabora» dopo la correzione
    opensearch.rifiuta_ids = set()
    assert await rimetti_in_coda([cattivo["aip"]]) == 1
    await sql(db_url, "UPDATE public.indicizzazione_aip SET prossimo_tentativo = $1", ADESSO)
    assert (await elabora_lotto(adesso=ADESSO))["indicizzati"] == 1


async def test_opensearch_giu_tutto_il_lotto_si_ritenta(db_url, s3, opensearch) -> None:
    from app.services.indicizzazione import elabora_lotto

    pda = await crea_pda(db_url, s3)
    await _in_coda(db_url, pda)
    opensearch.guasto = True
    assert (await elabora_lotto(adesso=ADESSO))["falliti"] == 1
    riga = (await _coda(db_url))[str(pda["aip"])]
    assert riga["stato"] == "IN_CODA" and "non disponibile" in riga["ultimo_errore"]


async def test_query_costanti_per_lotto(db_url, s3, opensearch) -> None:
    """REGOLE §23.9: 2 o 12 PdA nel lotto, stesso numero di query."""
    from app.services.indicizzazione import elabora_lotto

    from libs.shared.db import get_engine

    conteggi = []
    for quanti in (2, 12):
        await sql(db_url, "UPDATE public.indicizzazione_aip SET stato = 'INDICIZZATO'")
        for _ in range(quanti):
            await _in_coda(db_url, await crea_pda(db_url, s3))
        query: list[str] = []

        def _conta(conn, cursor, statement, *a, query=query):  # noqa: ANN001
            query.append(statement)

        motore = get_engine().sync_engine
        event.listen(motore, "before_cursor_execute", _conta)
        try:
            assert (await elabora_lotto(adesso=ADESSO))["indicizzati"] == quanti
        finally:
            event.remove(motore, "before_cursor_execute", _conta)
        conteggi.append(len(query))
    assert conteggi[0] == conteggi[1]


async def test_ricerca_cursore_ente_e_stato_dal_db(db_url, s3, opensearch) -> None:
    from app.services.indicizzazione import elabora_lotto

    primo = await crea_pda(db_url, s3, data_versamento=ADESSO - timedelta(days=3))
    tenant = primo["tenant"]
    altri = [await crea_pda(db_url, s3, tenant=tenant, data_versamento=ADESSO - timedelta(days=d))
             for d in (2, 1)]
    estraneo = await crea_pda(db_url, s3, oggetto="Delibera di giunta")
    for p in (primo, *altri, estraneo):
        await _in_coda(db_url, p)
    await elabora_lotto(adesso=ADESSO)

    async with await _client() as c:
        p1 = await c.post("/api/v2/distribution/search", headers=_h(tenant),
                          json={"query": "delibera giunta", "limit": 2})
        assert p1.status_code == 200, p1.text
        assert p1.json()["altri"] is True and p1.json()["cursore"]
        assert [i["aip_id"] for i in p1.json()["items"]] == [str(altri[1]["aip"]),
                                                               str(altri[0]["aip"])]
        # stato attuale dal database, non quello dell'indice
        await sql(db_url, "UPDATE public.aip SET stato = 'ESIBIZIONE' WHERE id = $1",
                  primo["aip"])
        p2 = await c.post("/api/v2/distribution/search", headers=_h(tenant),
                          json={"query": "delibera giunta", "limit": 2,
                                "cursore": p1.json()["cursore"]})
        assert p2.status_code == 200
        assert [(i["aip_id"], i["stato"]) for i in p2.json()["items"]] == [
            (str(primo["aip"]), "ESIBIZIONE")]
        assert p2.json()["altri"] is False and p2.json()["cursore"] is None
        # filtro per data di versamento
        p3 = await c.post("/api/v2/distribution/search", headers=_h(tenant), json={
            "data_da": (ADESSO - timedelta(days=1, hours=1)).isoformat()})
        assert [i["aip_id"] for i in p3.json()["items"]] == [str(altri[1]["aip"])]
        # cursore alterato
        r = await c.post("/api/v2/distribution/search", headers=_h(tenant),
                         json={"cursore": "non-un-cursore"})
        assert r.status_code == 422 and r.json()["codice"] == "DST-104"
    # l'ente estraneo non compare mai
    assert all(r[1]["routing"] == str(tenant) for r in opensearch.ricerche)


async def test_ricerca_scarta_risultati_non_dell_ente_nel_db(db_url, s3, opensearch) -> None:
    """Seconda difesa: un documento dell'indice con l'ente sbagliato (indice
    corrotto o vecchio) non si mostra se nel database il PdA è di un altro ente."""
    pda = await crea_pda(db_url, s3)
    intruso = str(uuid.uuid4())
    opensearch.documenti[str(pda["aip"])] = {
        "aip_id": str(pda["aip"]), "tenant_id": intruso, "data_versamento": "2026-10-01"}
    opensearch.routing[str(pda["aip"])] = intruso
    async with await _client() as c:
        r = await c.post("/api/v2/distribution/search", headers=_h(intruso), json={})
    assert r.status_code == 200 and r.json()["items"] == []
