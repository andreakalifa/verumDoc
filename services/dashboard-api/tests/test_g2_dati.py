# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
G2 — API della dashboard rese veritiere, su PostgreSQL reale migrato (e S3
moto per gli scaricamenti).

Prima:
- POST /search ignorava testo e classe (filtrava l'anno con un LIKE sulla
  segnatura), un ADMIN_TENANT senza ente nel token cercava in tutti gli enti;
- /versamenti dava total = len(righe) e nessun motivo di rifiuto;
- /aip non restituiva i metadati dell'Allegato 5;
- gli scaricamenti dei metadati erano impossibili per i profili globali;
- /audit restituiva 200 con il testo dell'eccezione SQL (e il filtro per ente
  falliva sempre: uuid = testo);
- /sla/report restituiva 99.95 e 99.98 scritti nel codice.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import (  # noqa: F401
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)

pytestmark = richiede_pg

BUCKET = "verumdoc-aip-g2"
PDF = b"%PDF-1.4 contenuto"


# g3-test: il gateway non emette MAI token senza ente: anche SUPER_ADMIN e
# RSC_CONSERVATORE portano l'ente del conservatore. La verifica JWT condivisa
# (libs/shared/security/jwt_rs256.py) rifiuta un tenant_id vuoto con 401
# (fail-closed, REGOLE §14.3). Prima questi test firmavano tenant_id="" per i
# profili globali e fallivano con 401. Ora ``None`` = «ente del conservatore».
TENANT_CONSERVATORE = "00000000-0000-0000-0000-0000000000c0"


def _token(tenant_id: str | None, ruolo: str = "OPERATORE",
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
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _pdv(url, tenant, classe, *, oggetto="Prova", stato="CONSERVATO", errori=None,
               ricevuto: datetime | None = None, protocollo: str | None = None) -> uuid.UUID:
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "errori_validazione, data_ricezione, numero_protocollo) VALUES ($1, $2, $3, $4, "
                   "$5, $6, CAST($7 AS jsonb), $8, $9)",
              pdv, oggetto, stato, classe, uuid.uuid4(), tenant, json.dumps(errori or []),
              ricevuto or datetime.now(UTC), protocollo)
    return pdv


async def _aip(url, tenant, classe, *, oggetto="Prova", urn=None, versato: datetime | None = None,
               stato="CONSERVATO", nome_file="doc.pdf", metadati: dict | None = None,
               conservato: datetime | None = None, ricevuto: datetime | None = None,
               s3=None, meta_pdv: dict | None = None) -> uuid.UUID:
    pdv = await _pdv(url, tenant, classe, oggetto=oggetto, ricevuto=ricevuto)
    if meta_pdv:
        await sql(url, "UPDATE public.pdv SET metadata_json = CAST($2 AS jsonb) WHERE id = $1",
                  pdv, json.dumps(meta_pdv))
    aip, doc = uuid.uuid4(), uuid.uuid4()
    chiave = f"{tenant}/{aip}"
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "sincro_xml_key, hash_sha256, data_versamento, data_conservazione) "
                   "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
              aip, pdv, tenant, urn or f"urn:prova:{aip}", stato, f"{chiave}/manifest.json",
              BUCKET, f"{chiave}/IndiceAIP.xml", "a" * 64, versato or datetime.now(UTC),
              conservato)
    await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, s3_key) VALUES ($1, $2, $3, 'PRINCIPALE', "
                   "'application/pdf', $4, $5, $6)",
              doc, aip, nome_file, len(PDF), hashlib.sha256(PDF).hexdigest(), f"{chiave}/doc.pdf")
    if metadati is not None:
        campi = {"identificativo_documento": "DOC-1", "modalita_formazione": "a",
                 "tipologia_documentale": "Fattura", "data_documento": datetime(2026, 1, 5).date(),
                 "soggetto_produttore_id": "P1", "soggetto_produttore_nome": "Comune di Prova",
                 "oggetto": "Oggetto del documento", "formato": "application/pdf", **metadati}
        await sql(url, "INSERT INTO public.metadati_allegato5 (documento_id, "
                       + ", ".join(campi) + ") VALUES ($1, "
                       + ", ".join(f"${i + 2}" for i in range(len(campi))) + ")",
                  doc, *campi.values())
    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=f"{chiave}/manifest.json", Body=b'{"m":1}')
        s3.put_object(Bucket=BUCKET, Key=f"{chiave}/IndiceAIP.xml", Body=b"<PIndex/>")
        s3.put_object(Bucket=BUCKET, Key=f"{chiave}/doc.pdf", Body=PDF)
    return aip


@pytest.fixture
def s3(s3_server, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    client = client_s3(s3_server)
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    return client


def _senza_testo_sql(risposta) -> None:
    corpo = risposta.json()
    assert corpo["id_richiesta"] == risposta.headers["x-request-id"]
    for parola in ("relation", "SELECT", "asyncpg", "does not exist", "notifications_x"):
        assert parola not in risposta.text


# ── 1. Ricerca ──────────────────────────────────────────────────────

@pytest.fixture
def distribution(monkeypatch):
    """R03 f2 (REV-06): la ricerca testuale va sull'indice tramite distribution."""
    from app.routers import dashboard

    from tests.distribution_finta import DistributionFinta

    finta = DistributionFinta()
    monkeypatch.setattr(dashboard, "TRASPORTO_DISTRIBUTION", finta.trasporto())
    return finta


async def test_ricerca_senza_testo_a_cursore_con_anno_e_stato(db_url) -> None:
    """R03 f2 (REV-06): senza testo la ricerca va sul database a cursore
    (data di versamento, id): anno come intervallo di date, stato, nessun
    totale né offset."""
    tenant, fattura = await crea_tenant_e_classe(db_url, codice_classe="fattura")
    a1 = await _aip(db_url, tenant, fattura, versato=datetime(2025, 6, 1, tzinfo=UTC))
    a2 = await _aip(db_url, tenant, fattura, versato=datetime(2026, 2, 1, tzinfo=UTC))
    a3 = await _aip(db_url, tenant, fattura, versato=datetime(2026, 3, 1, tzinfo=UTC),
                    stato="IN_CATENA")
    # 31/12/2025 alle 23:30 UTC è già il 2026 in Italia
    a4 = await _aip(db_url, tenant, fattura, versato=datetime(2025, 12, 31, 23, 30, tzinfo=UTC))
    altro, _ = await crea_tenant_e_classe(db_url)
    await _aip(db_url, altro, fattura, versato=datetime(2026, 2, 2, tzinfo=UTC))
    h = _token(str(tenant))
    async with await _client() as c:
        async def cerca(**corpo):
            r = await c.post("/api/dashboard/search", json={"tenant_id": str(tenant), **corpo},
                             headers=h)
            assert r.status_code == 200, r.text
            return r.json()

        tutti = await cerca()
        assert [i["aip_id"] for i in tutti["items"]] == [str(a3), str(a2), str(a4), str(a1)]
        assert "total" not in tutti and tutti["motore"] == "database"
        assert {i["aip_id"] for i in (await cerca(anno="2026"))["items"]} == {
            str(a2), str(a3), str(a4)}
        assert [i["aip_id"] for i in (await cerca(stato="IN_CATENA"))["items"]] == [str(a3)]
        # pagine a cursore: nessuna voce ripetuta o persa
        visti, cursore = [], None
        while True:
            pagina = await cerca(limit=1, **({"cursore": cursore} if cursore else {}))
            visti += [i["aip_id"] for i in pagina["items"]]
            if not pagina["altri"]:
                assert pagina["cursore"] is None
                break
            cursore = pagina["cursore"]
        assert visti == [i["aip_id"] for i in tutti["items"]]

        r = await c.post("/api/dashboard/search", json={"anno": "26"}, headers=h)
        assert r.status_code == 422
        assert (await c.post("/api/dashboard/search", json={"stato": "BOH"},
                             headers=h)).status_code == 422
        skip = await c.post("/api/dashboard/search", json={"skip": 10}, headers=h)
        assert skip.status_code == 422 and skip.json()["codice"] == "DSH-422"
        alterato = await c.post("/api/dashboard/search", json={"cursore": "xyz"}, headers=h)
        assert alterato.status_code == 422 and alterato.json()["codice"] == "DSH-104"


async def test_ricerca_testuale_sull_indice_poi_dati_dal_database(db_url, distribution) -> None:
    """R03 f2 (REV-06): con testo o classe la ricerca va all'indice (distribution,
    con le credenziali del chiamante); i PdA trovati si rileggono dal database:
    stato attuale, solo dell'ente, filtro di stato applicato al dato attuale."""
    tenant, fattura = await crea_tenant_e_classe(db_url, codice_classe="fattura")
    a1 = await _aip(db_url, tenant, fattura, oggetto="Fattura energia elettrica")
    a2 = await _aip(db_url, tenant, fattura, oggetto="Contratto energia", stato="IN_CATENA")
    altro, _ = await crea_tenant_e_classe(db_url)
    estraneo = await _aip(db_url, altro, fattura, oggetto="Fattura energia elettrica")
    # l'indice (finto) risponde anche con un PdA di un altro ente: scartato
    distribution.risultati[str(tenant)] = [str(a2), str(estraneo), str(a1)]
    h = _token(str(tenant))
    async with await _client() as c:
        r = await c.post("/api/dashboard/search", headers=h,
                         json={"query": "energia", "anno": "2026", "limit": 2,
                               "classe_documentale": str(fattura)})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["motore"] == "indice" and corpo["campi_testo"]
        assert [i["aip_id"] for i in corpo["items"]] == [str(a2)]  # estraneo scartato
        assert corpo["items"][0]["classe_documentale"]["codice"] == "fattura"
        assert corpo["altri"] is True and corpo["cursore"]
        inviato, intestazioni = distribution.richieste[0]
        assert inviato["tenant_id"] == str(tenant) and inviato["query"] == "energia"
        assert inviato["classe_documentale"] == "fattura"     # id tradotto nel codice
        assert inviato["data_da"].startswith("2026-01-01T00:00:00+01:00")
        assert inviato["data_a"].startswith("2026-12-31T23:59:59.999999+01:00")
        assert intestazioni["authorization"] == h["Authorization"]
        # pagina successiva: il cursore dell'indice torna a distribution
        r2 = await c.post("/api/dashboard/search", headers=h,
                          json={"query": "energia", "limit": 2, "cursore": corpo["cursore"]})
        assert distribution.richieste[-1][0]["cursore"] == "p2"
        assert [i["aip_id"] for i in r2.json()["items"]] == [str(a1)]
        assert r2.json()["altri"] is False and r2.json()["cursore"] is None
        # stato: sul dato attuale del database
        solo = await c.post("/api/dashboard/search", headers=h,
                            json={"query": "energia", "stato": "CONSERVATO", "limit": 10})
        assert [i["aip_id"] for i in solo.json()["items"]] == [str(a1)]
        # un cursore del database non vale per l'indice (e viceversa)
        db = await c.post("/api/dashboard/search", headers=h, json={"limit": 1})
        misto = await c.post("/api/dashboard/search", headers=h,
                             json={"query": "energia", "cursore": db.json()["cursore"]})
        assert misto.status_code == 422 and misto.json()["codice"] == "DSH-104"


async def test_ricerca_indice_errori_di_distribution(db_url, distribution) -> None:
    """Cursore rifiutato dall'indice → DSH-104; quota di ricerca → 429 QTA-001 con
    Retry-After; distribution in errore → 502 senza testo interno."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    h = _token(str(tenant))
    async with await _client() as c:
        distribution.errore = (422, {"type": "x", "codice": "DST-104", "title": "Cursore"}, {})
        r = await c.post("/api/dashboard/search", headers=h, json={"query": "x"})
        assert r.status_code == 422 and r.json()["codice"] == "DSH-104"
        distribution.errore = (429, {"type": "x", "codice": "QTA-001", "title": "Q"},
                               {"Retry-After": "17"})
        r = await c.post("/api/dashboard/search", headers=h, json={"query": "x"})
        assert r.status_code == 429 and r.json()["codice"] == "QTA-001"
        assert r.headers["Retry-After"] == "17"
        distribution.errore = (503, {"type": "x", "codice": "DST-503", "title": "giù"}, {})
        r = await c.post("/api/dashboard/search", headers=h, json={"query": "x"})
        assert r.status_code == 502 and r.json()["codice"] == "DSH-502"
        assert "giù" not in r.text


async def test_ricerca_e_elenco_senza_count_ne_offset(db_url, distribution) -> None:
    """R03 f2 (REV-06): nessuna istruzione con COUNT o OFFSET, stesso numero di
    istruzioni con 3 o 30 PdA (il costo non cresce con l'archivio)."""
    from sqlalchemy import event

    from libs.shared import db as modulo_db

    tenant, classe = await crea_tenant_e_classe(db_url)
    istruzioni: list[str] = []

    def registra(conn, cursor, statement, *a) -> None:
        if "public.aip" in statement or " aip" in statement:
            istruzioni.append(statement)

    async def conta(n: int) -> list[str]:
        for _ in range(n):
            await _aip(db_url, tenant, classe)
        istruzioni.clear()
        async with await _client() as c:
            h = _token(str(tenant))
            assert (await c.post("/api/dashboard/search", json={"limit": 2},
                                 headers=h)).status_code == 200
            assert (await c.post("/api/dashboard/search", json={"query": "x"},
                                 headers=h)).status_code == 200
            assert (await c.get("/api/dashboard/aip?limit=2", headers=h)).status_code == 200
        return list(istruzioni)

    motore = modulo_db.get_engine().sync_engine
    event.listen(motore, "before_cursor_execute", registra)
    try:
        poche, molte = await conta(3), await conta(27)
    finally:
        event.remove(motore, "before_cursor_execute", registra)
    assert len(poche) == len(molte) >= 2
    assert not any("count(" in s.lower() for s in poche + molte)
    assert not any("offset" in s.lower() for s in poche + molte)
    assert not any("ilike" in s.lower() for s in poche + molte)


async def test_ricerca_isolamento_tra_enti(db_url) -> None:
    t1, classe = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    await _aip(db_url, t1, classe)
    async with await _client() as c:
        # Altro ente: 403 per un profilo dell'ente
        r = await c.post("/api/dashboard/search", json={"tenant_id": str(t1)},
                         headers=_token(str(t2), "ADMIN_TENANT"))
        assert r.status_code == 403
        # Token senza ente (prima cercava in tutti gli enti): rifiutato già dalla
        # verifica JWT condivisa, per ogni profilo → 401 (g3-test)
        for ruolo in ("ADMIN_TENANT", "SUPER_ADMIN"):
            r = await c.post("/api/dashboard/search", json={}, headers=_token_senza_ente(ruolo))
            assert r.status_code == 401, (ruolo, r.text)
        # Profilo globale senza tenant_id: legge l'ente del proprio token (il
        # conservatore), mai tutti gli enti
        r = await c.post("/api/dashboard/search", json={}, headers=_token(None, "SUPER_ADMIN"))
        assert r.status_code == 200, r.text
        assert r.json()["items"] == []
        assert r.json()["filtri_applicati"]["tenant_id"] == TENANT_CONSERVATORE
        r = await c.post("/api/dashboard/search", json={"tenant_id": str(t1)},
                         headers=_token(None, "RSC_CONSERVATORE"))
        assert r.status_code == 200 and len(r.json()["items"]) == 1


# ── 2. Versamenti ───────────────────────────────────────────────────

async def test_versamenti_a_cursore_e_motivo_del_rifiuto(db_url) -> None:
    """R03 i1: elenco a cursore (niente COUNT né offset); il motivo del rifiuto resta."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    base = datetime(2026, 10, 1, tzinfo=UTC)
    conservati = [await _pdv(db_url, tenant, classe, ricevuto=base + timedelta(hours=i))
                  for i in range(4)]
    rifiutato = await _pdv(db_url, tenant, classe, stato="ERRORE",
                           ricevuto=base + timedelta(days=1),
                           errori=[{"step": 6, "nome": "metadati_allegato5", "codice": "VLD-601",
                                    "messaggio": "Manca la tipologia documentale",
                                    "tipo": "verifica"}])
    tecnico = await _pdv(db_url, tenant, classe, stato="ERRORE", ricevuto=base + timedelta(days=2),
                         errori=[{"codice": "TECNICO", "tipo": "tecnico",
                                  "messaggio": "ProgrammingError: relation x does not exist"}])
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/versamenti?tenant_id={tenant}&limit=2",
                        headers=_token(str(tenant)))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert "total" not in corpo and corpo["altri"] is True and corpo["cursore"]
        # dal più recente: i due in ERRORE
        assert [i["pdv_id"] for i in corpo["items"]] == [str(tecnico), str(rifiutato)]
        per_id = {i["pdv_id"]: i for i in corpo["items"]}
        assert per_id[str(rifiutato)]["motivo_rifiuto"] == "Manca la tipologia documentale"
        assert per_id[str(rifiutato)]["motivi_rifiuto"][0]["codice"] == "VLD-601"
        # Un errore tecnico non porta il testo dell'eccezione al client
        assert "relation" not in json.dumps(per_id[str(tecnico)])
        assert per_id[str(tecnico)]["motivo_rifiuto"].startswith("Errore tecnico")
        # tutte le pagine: ogni PdV una volta, in ordine, poi altri=false
        visti, cursore = [i["pdv_id"] for i in corpo["items"]], corpo["cursore"]
        while cursore:
            pagina = (await c.get("/api/dashboard/versamenti",
                                  params={"tenant_id": str(tenant), "limit": 2,
                                          "cursore": cursore},
                                  headers=_token(str(tenant)))).json()
            visti += [i["pdv_id"] for i in pagina["items"]]
            cursore = pagina["cursore"]
            assert (cursore is None) == (pagina["altri"] is False)
        assert visti == [str(tecnico), str(rifiutato)] + [str(p) for p in reversed(conservati)]

        solo_errori = (await c.get(f"/api/dashboard/versamenti?tenant_id={tenant}&stato=ERRORE",
                                   headers=_token(str(tenant)))).json()
        assert len(solo_errori["items"]) == 2 and solo_errori["altri"] is False
        ok = (await c.get(f"/api/dashboard/versamenti?tenant_id={tenant}&stato=CONSERVATO"
                          "&limit=3", headers=_token(str(tenant)))).json()
        assert len(ok["items"]) == 3 and ok["altri"] is True
        ultimo = (await c.get("/api/dashboard/versamenti", params={
            "tenant_id": str(tenant), "stato": "CONSERVATO", "limit": 3,
            "cursore": ok["cursore"]}, headers=_token(str(tenant)))).json()
        assert [i["pdv_id"] for i in ultimo["items"]] == [str(conservati[0])]
        assert ultimo["items"][0]["motivo_rifiuto"] is None
        assert ultimo["items"][0]["motivi_rifiuto"] == []


async def test_versamenti_ente_dal_token_skip_e_cursore_non_validi(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    altro, classe_altro = await crea_tenant_e_classe(db_url)
    mio = await _pdv(db_url, tenant, classe)
    await _pdv(db_url, altro, classe_altro)
    async with await _client() as c:
        # senza tenant_id: l'ente del token
        r = await c.get("/api/dashboard/versamenti", headers=_token(str(tenant)))
        assert r.status_code == 200, r.text
        assert [i["pdv_id"] for i in r.json()["items"]] == [str(mio)]
        skip = await c.get("/api/dashboard/versamenti?skip=20", headers=_token(str(tenant)))
        assert skip.status_code == 422 and skip.json()["codice"] == "DSH-422"
        for alterato in ("abc", "eyJkIjoxfQ", "x" * 50):
            r = await c.get("/api/dashboard/versamenti", params={"cursore": alterato},
                            headers=_token(str(tenant)))
            assert r.status_code == 422 and r.json()["codice"] == "DSH-104", (alterato, r.text)


async def test_versamenti_pagina_senza_count_e_con_query_costanti(db_url) -> None:
    """Nessun COUNT e lo stesso numero di istruzioni con 3 o 30 PdV (a cursore)."""
    from sqlalchemy import event

    from libs.shared import db as modulo_db

    tenant, classe = await crea_tenant_e_classe(db_url)
    istruzioni: list[str] = []

    def registra(conn, cursor, statement, *a, **k):
        if "public.pdv" in statement or " pdv" in statement:
            istruzioni.append(statement)

    async def conta(n_pdv: int) -> list[str]:
        for _ in range(n_pdv):
            await _pdv(db_url, tenant, classe)
        istruzioni.clear()
        async with await _client() as c:
            r = await c.get("/api/dashboard/versamenti?limit=2", headers=_token(str(tenant)))
        assert r.status_code == 200
        return list(istruzioni)

    motore = modulo_db.get_engine().sync_engine
    event.listen(motore, "before_cursor_execute", registra)
    try:
        poche, molte = await conta(3), await conta(27)
    finally:
        event.remove(motore, "before_cursor_execute", registra)
    assert len(poche) == len(molte) >= 1
    assert not any("count(" in s.lower() for s in poche + molte)
    assert not any("offset" in s.lower() for s in poche + molte)


# ── 3. Pacchetti con metadati Allegato 5 ────────────────────────────

async def test_elenco_pacchetti_con_metadati_allegato5(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    con = await _aip(db_url, tenant, classe, metadati={"numero_registrazione": "PROT-7"},
                     versato=datetime(2026, 5, 1, tzinfo=UTC))
    senza = await _aip(db_url, tenant, classe, versato=datetime(2026, 4, 1, tzinfo=UTC))
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/aip?tenant_id={tenant}", headers=_token(str(tenant)))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert "total" not in corpo and corpo["altri"] is False and len(corpo["items"]) == 2
    per_id = {i["aip_id"]: i for i in corpo["items"]}
    doc = per_id[str(con)]["metadati_allegato5"][0]
    assert doc["nome_file"] == "doc.pdf"
    assert doc["metadati"]["modalita_formazione"] == "a"
    assert doc["metadati"]["numero_registrazione"] == "PROT-7"
    assert doc["metadati"]["data_documento"] == "2026-01-05"
    assert set(doc["metadati"]) == set(corpo["campi_allegato5"])
    # Documento senza metadati registrati: dichiarato, non inventato
    assert per_id[str(senza)]["metadati_allegato5"][0]["metadati"] is None
    assert per_id[str(con)]["classe_documentale"]["codice"] == "fattura"


# ── 4. Scaricamenti per profili globali (VD-C-005) ─────────────────

async def test_profilo_globale_legge_metadati_ma_non_contenuto(db_url, s3) -> None:
    ente, classe = await crea_tenant_e_classe(db_url)
    proprio, _ = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, ente, classe, s3=s3, meta_pdv={"rdv_preliminare": "<RdV/>"})
    sa = _token(str(proprio), "SUPER_ADMIN")
    q = f"?tenant_id={ente}"
    async with await _client() as c:
        files = await c.get(f"/api/dashboard/aip/{aip}/files{q}", headers=sa)
        assert files.status_code == 200, files.text
        assert files.json()["contenuto_accessibile"] is False
        assert files.json()["download_completo_url"] is None
        assert all(f["download_url"].endswith(q) for f in files.json()["files"])
        for percorso in ("manifest", "sincro", "rdv"):
            r = await c.get(f"/api/dashboard/aip/{aip}/download/{percorso}{q}", headers=sa)
            assert r.status_code == 200, (percorso, r.text)
        # Contenuto di un altro ente: 403 anche per SUPER_ADMIN, 404 senza indicare l'ente
        r = await c.get(f"/api/dashboard/aip/{aip}/download/completo{q}", headers=sa)
        assert r.status_code == 403
        r = await c.get(f"/api/dashboard/aip/{aip}/download/completo", headers=sa)
        assert r.status_code == 404
        # Un profilo d'ente non può indicare un altro ente
        r = await c.get(f"/api/dashboard/aip/{aip}/download/manifest{q}",
                        headers=_token(str(proprio), "ADMIN_TENANT"))
        assert r.status_code == 403
        # Il proprio ente scarica anche il contenuto
        r = await c.get(f"/api/dashboard/aip/{aip}/download/completo?tenant_id={ente}",
                        headers=_token(str(ente)))
        assert r.status_code == 200


async def test_rdv_definitivo_da_s3_con_impronta(db_url, s3) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    xml = b"<RdV definitivo='1'/>"
    s3.put_object(Bucket=BUCKET, Key="rdv/ok.xml", Body=xml)
    s3.put_object(Bucket=BUCKET, Key="rdv/alterato.xml", Body=b"<RdV alterato/>")
    buono = await _aip(db_url, tenant, classe, s3=s3, meta_pdv={"rdv_definitiva": {
        "s3_bucket": BUCKET, "s3_key": "rdv/ok.xml",
        "impronta_sha256": hashlib.sha256(xml).hexdigest()}})
    cattivo = await _aip(db_url, tenant, classe, s3=s3, meta_pdv={"rdv_definitiva": {
        "s3_bucket": BUCKET, "s3_key": "rdv/alterato.xml",
        "impronta_sha256": hashlib.sha256(xml).hexdigest()}})
    h = _token(str(tenant))
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/aip/{buono}/download/rdv", headers=h)
        assert r.status_code == 200 and r.content == xml
        assert r.headers["x-rdv-tipo"] == "definitivo"
        r = await c.get(f"/api/dashboard/aip/{cattivo}/download/rdv", headers=h)
        assert r.status_code == 502
        _senza_testo_sql(r)


async def test_zip_non_prodotto_se_un_file_manca_o_e_alterato(db_url, s3) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe, s3=s3)
    s3.put_object(Bucket=BUCKET, Key=f"{tenant}/{aip}/doc.pdf", Body=b"%PDF alterato")
    h = _token(str(tenant))
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/aip/{aip}/download/completo", headers=h)
        assert r.status_code == 502 and "doc.pdf" in r.json()["title"]
        s3.delete_object(Bucket=BUCKET, Key=f"{tenant}/{aip}/manifest.json")
        r = await c.get(f"/api/dashboard/aip/{aip}/download/manifest", headers=h)
    assert r.status_code == 502
    _senza_testo_sql(r)
    assert "NoSuchKey" not in r.text


# ── 6. Notifiche ────────────────────────────────────────────────────

async def test_notifiche_con_errore_e_tentativi(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario, "
                      "stato, tentativi, errore, payload_json) VALUES ($1, 'pdv.invalid', 'email', "
                      "'a@example.org', 'NON_PARTITA', 3, '550 casella inesistente', "
                      "CAST($2 AS jsonb))", tenant, json.dumps({"oggetto": "PdV rifiutato"}))
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/notifiche?tenant_id={tenant}", headers=_token(str(tenant)))
    assert r.status_code == 200, r.text
    voce = r.json()["items"][0]
    assert voce["tentativi"] == 3 and voce["errore"] == "550 casella inesistente"
    # R04 (VD-C-102): elenco a cursore, niente totale
    assert voce["oggetto"] == "PdV rifiutato" and len(r.json()["items"]) == 1
    assert "total" not in r.json() and r.json()["altri"] is False


async def test_notifiche_e_registro_errore_db_503(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "ALTER TABLE public.notifications RENAME TO notifications_x")
    # rinominare un registro protetto è rifiutato dall'event trigger (migrazione
    # 20261023_033): qui sospeso SOLO nella copia del database di questo test
    await sql(db_url, "ALTER EVENT TRIGGER vd_protezione_registri_stato DISABLE")
    await sql(db_url, "ALTER TABLE public.audit_global RENAME TO audit_x")
    async with await _client() as c:
        n = await c.get(f"/api/dashboard/notifiche?tenant_id={tenant}", headers=_token(str(tenant)))
        a = await c.get("/api/dashboard/audit", headers=_token(str(tenant), "ADMIN_TENANT"))
    for r in (n, a):
        assert r.status_code == 503, r.text
        assert "error" not in r.json()
        _senza_testo_sql(r)


# ── 9. Registro operazioni ──────────────────────────────────────────

async def test_registro_filtrato_per_ente_e_evento_letterale(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    for tid, evento in ((t1, "contratto.registrato"), (t1, "distribuzione.richiesta"),
                        (t2, "contratto.registrato")):
        await sql(db_url, "INSERT INTO public.audit_global (tenant_id, evento) VALUES ($1, $2)",
                  tid, evento)
    async with await _client() as c:
        r = await c.get("/api/dashboard/audit", headers=_token(str(t1), "ADMIN_TENANT"))
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 2 and "total" not in r.json()
        assert {i["tenant_id"] for i in r.json()["items"]} == {str(t1)}
        r = await c.get(f"/api/dashboard/audit?tenant_id={t2}&evento=contratto",
                        headers=_token(None, "RSC_CONSERVATORE"))
        assert len(r.json()["items"]) == 1
        # «_» è letterale: non trova «contratto.registrato»
        r = await c.get("/api/dashboard/audit?evento=contratto_registrato",
                        headers=_token(None, "SUPER_ADMIN"))
        assert r.json()["items"] == []
        r = await c.get("/api/dashboard/audit?evento=contratto.registrato",
                        headers=_token(None, "SUPER_ADMIN"))
        assert len(r.json()["items"]) == 2   # profilo globale: tutti gli enti


# ── 8. SLA ──────────────────────────────────────────────────────────

async def test_sla_senza_percentuali_inventate(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    adesso = datetime.now(UTC)
    for minuti in (1, 2, 3):
        await _aip(db_url, tenant, classe, ricevuto=adesso - timedelta(minutes=10),
                   conservato=adesso - timedelta(minutes=10 - minuti))
    async with await _client() as c:
        r = await c.get("/api/dashboard/sla/report", headers=_token(None, "SUPER_ADMIN"))
        assert r.status_code == 200, r.text
        corpo = r.json()
        conf = corpo["conformita"]
        assert conf["uptime_pct"] is None and conf["disponibilita_pct"] is None
        assert conf["versamento_p95_ms"] is None and conf["fonte"] is None
        assert corpo["misure"]["uptime_pct"]["fonte"] is None
        assert corpo["misure"]["uptime_pct"]["raggiunto"] is None
        # Misura reale dal database: tra 2 e 3 minuti, con la fonte dichiarata
        p99 = conf["conservazione_p99_ms"]
        assert 120_000 <= p99 <= 180_000
        assert corpo["misure"]["conservazione_p99_ms"]["campioni"] == 3
        assert conf["fonti"]["conservazione_p99_ms"].startswith("database")
        assert conf["raggiunto"]["conservazione_p99_ms"] is False      # obiettivo 30 s
        assert corpo["sla_targets"]["uptime_pct"] == 99.9
        assert corpo["note"] is None
        assert (await c.get("/api/dashboard/sla/report?mese=2026-13",
                            headers=_token(None, "SUPER_ADMIN"))).status_code == 422


# ── 10. Contratti e nomine: 503 su errore del database ──────────────

async def test_contratti_e_nomine_503_con_id_di_correlazione(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "ALTER TABLE public.contracts RENAME TO contracts_x")
    await sql(db_url, "ALTER TABLE public.rsc_nominations RENAME TO nomine_x")
    async with await _client() as c:
        a = await c.get("/api/dashboard/contratti", headers=_token(str(tenant), "ADMIN_TENANT"))
        b = await c.get(f"/api/dashboard/nomine-rsc?tenant_id={tenant}",
                        headers=_token(None, "SUPER_ADMIN"))
    for r in (a, b):
        assert r.status_code == 503
        _senza_testo_sql(r)


# ── 7. Proxy della catena (verso il servizio hashchain) ─────────────

_ASYNC_CLIENT_VERO = httpx.AsyncClient


def _finto_hashchain(monkeypatch, gestore) -> list[httpx.Request]:
    from app.routers import dashboard

    richieste: list[httpx.Request] = []
    vero = _ASYNC_CLIENT_VERO

    def gestisci(req: httpx.Request) -> httpx.Response:
        richieste.append(req)
        return gestore(req)

    monkeypatch.setattr(dashboard.httpx, "AsyncClient",
                        lambda **kw: vero(transport=httpx.MockTransport(gestisci), **kw))
    return richieste


async def test_verifica_completa_solo_ruoli_globali_e_inoltra_chi_avvia(monkeypatch) -> None:
    utente = str(uuid.uuid4())
    vid = str(uuid.uuid4())
    richieste = _finto_hashchain(monkeypatch, lambda req: httpx.Response(
        202, json={
            "verifica_id": vid, "stato": "IN_CORSO", "avviata_il": "2026-10-20T10:00:00+00:00"}))
    async with await _client() as c:
        no = await c.post("/api/dashboard/hashchain/verifica-completa",
                          headers=_token(str(uuid.uuid4()), "ADMIN_TENANT"))
        si = await c.post("/api/dashboard/hashchain/verifica-completa",
                          headers=_token(None, "RSC_CONSERVATORE", user_id=utente))
    assert no.status_code == 403
    assert si.status_code == 202 and si.json()["verifica_id"] == vid  # g4: asincrona
    assert len(richieste) == 1
    assert richieste[0].url.path == "/api/v1/hashchain/verifica-completa"
    assert json.loads(richieste[0].content)["avviata_da"] == utente
    assert richieste[0].headers["authorization"].startswith("Bearer ")


async def test_errori_del_servizio_a_valle(monkeypatch) -> None:
    risposte = iter([
        httpx.Response(409, json={"detail": {"type": "HC-409", "title": "in corso"}}),
        httpx.Response(500, text="Traceback: psycopg errore segreto"),
    ])
    _finto_hashchain(monkeypatch, lambda req: next(risposte))
    h = _token(None, "SUPER_ADMIN")
    async with await _client() as c:
        a = await c.post("/api/dashboard/hashchain/verifica-completa", headers=h)
        b = await c.get("/api/dashboard/hashchain", headers=h)
    assert a.status_code == 409 and a.json()["codice"] == "HC-409"
    assert b.status_code == 502 and "segreto" not in b.text
    assert b.json()["id_richiesta"]

    def irraggiungibile(req):
        raise httpx.ConnectError("connessione rifiutata 10.0.0.7")

    _finto_hashchain(monkeypatch, irraggiungibile)
    async with await _client() as c:
        r = await c.get("/api/dashboard/hashchain", headers=h)
    assert r.status_code == 502 and "10.0.0.7" not in r.text


async def test_proxy_distribuzione_dichiara_il_canale_web(monkeypatch) -> None:
    richieste = _finto_hashchain(monkeypatch, lambda req: httpx.Response(200, json={"ok": 1}))
    async with await _client() as c:
        r = await c.post("/api/dashboard/distribution", json={"aip_id": "x"},
                         headers=_token(str(uuid.uuid4())))
    assert r.status_code == 200
    assert richieste[0].headers["x-verumdoc-canale"] == "WEB"
    assert richieste[0].headers["authorization"].startswith("Bearer ")
