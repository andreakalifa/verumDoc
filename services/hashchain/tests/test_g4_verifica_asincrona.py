# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g4-chiusura — verifica completa ASINCRONA (decisione del coordinatore).

POST avvia e risponde subito 202; GET /verifica-completa/{id} dà lo stato
(IN_CORSO / COMPLETATA / FALLITA / INTERROTTA). Una sola verifica alla volta
(409 con l'id di quella in corso). Tempo massimo → INTERROTTA. Verifiche
rimaste IN_CORSO dopo un arresto → INTERROTTA all'avvio e alla lettura.
Tutto su PostgreSQL reale migrato; i blocchi li sigilla il codice vero.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import app.services.verifica_asincrona as va
import pytest
from app.config import settings
from httpx import ASGITransport, AsyncClient

from tests import infra_reale
from tests.infra_reale import richiede_pg, sql
from tests.token_di_prova import intestazioni

# Fixture condivise (tests/infra_reale.py), registrate in questo modulo
db_url = infra_reale.db_url

pytestmark = [pytest.mark.integration, richiede_pg]

VERIFICA = "/api/v1/hashchain/verifica-completa"


async def _client(ruolo: str = "RSC_CONSERVATORE", **kw) -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       headers=intestazioni(ruolo, **kw))


@pytest.fixture
def verifica_trattenuta(monkeypatch):
    """La verifica vera parte solo quando il test apre il cancello."""
    cancello = asyncio.Event()
    vera = va.esegui_verifica_completa

    async def trattenuta(**kw):
        await cancello.wait()
        return await vera(**kw)

    monkeypatch.setattr(va, "esegui_verifica_completa", trattenuta)
    return cancello


async def _stato(c: AsyncClient, verifica_id: str) -> dict:
    r = await c.get(f"{VERIFICA}/{verifica_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def _attendi_fine(c: AsyncClient, verifica_id: str, secondi: float = 10) -> dict:
    """Polling come farà il frontend."""
    scadenza = asyncio.get_running_loop().time() + secondi
    while True:
        stato = await _stato(c, verifica_id)
        if stato["stato"] != "IN_CORSO":
            return stato
        assert asyncio.get_running_loop().time() < scadenza, "verifica mai conclusa"
        await asyncio.sleep(0.05)


async def test_202_poi_polling_fino_a_completata(db_url) -> None:
    utente = str(uuid.uuid4())
    async with await _client(user_id=utente) as c:
        r = await c.post(VERIFICA)
        assert r.status_code == 202, r.text
        avvio = r.json()
        assert set(avvio) == {"verifica_id", "stato", "avviata_il"}
        assert avvio["stato"] == "IN_CORSO"
        fine = await _attendi_fine(c, avvio["verifica_id"])
        catena = (await c.get("/api/v1/hashchain/status")).json()["integrita"]
    assert fine["stato"] == "COMPLETATA"
    assert fine["esito"] == "INTEGRA" and fine["integra"] is True  # solo genesi
    assert fine["avviata_il"] == avvio["avviata_il"] and fine["conclusa_il"]
    assert fine["avviata_da"] == utente and fine["ruolo"] == "RSC_CONSERVATORE"
    assert fine["blocchi_verificati"] == 1
    assert catena["verifica_id"] == avvio["verifica_id"]
    eventi = await sql(db_url, "SELECT evento FROM public.audit_global "
                               "WHERE dettagli->>'verifica_id' = $1 ORDER BY created_at",
                       avvio["verifica_id"])
    assert [e["evento"] for e in eventi] == ["hashchain.verifica_avviata",
                                             "hashchain.verifica_completa"]


async def test_seconda_richiesta_409_con_id_in_corso(db_url, verifica_trattenuta) -> None:
    async with await _client() as c, await _client("SUPER_ADMIN") as altro:
        prima = await c.post(VERIFICA)
        assert prima.status_code == 202
        vid = prima.json()["verifica_id"]
        seconda = await altro.post(VERIFICA)
        assert seconda.status_code == 409, seconda.text
        corpo = seconda.json()
        assert (corpo["codice"], corpo["title"], corpo["dettagli"]) == (
            "HC-409", "Una verifica completa è già in corso", {"verifica_id": vid})
        assert (await _stato(c, vid))["stato"] == "IN_CORSO"
        assert (await _stato(c, vid))["conclusa_il"] is None
        verifica_trattenuta.set()
        fine = await _attendi_fine(c, vid)
        # Finita la prima, se ne può avviare un'altra
        terza = await c.post(VERIFICA)
        assert terza.status_code == 202
        await _attendi_fine(c, terza.json()["verifica_id"])
    assert fine["stato"] == "COMPLETATA"
    avvii = await sql(db_url, "SELECT count(*) AS n FROM public.audit_global "
                              "WHERE evento = 'hashchain.verifica_avviata'")
    assert avvii[0]["n"] == 2  # la richiesta respinta non lascia tracce


async def test_tempo_massimo_superato_interrotta(db_url, monkeypatch, verifica_trattenuta) -> None:
    monkeypatch.setattr(settings, "hashchain_verifica_timeout_seconds", 0.3)
    async with await _client() as c:
        # Una verifica COMPLETATA prima, per vedere che lo stato della catena la conserva
        verifica_trattenuta.set()
        buona = (await c.post(VERIFICA)).json()["verifica_id"]
        await _attendi_fine(c, buona)
        verifica_trattenuta.clear()
        lenta = (await c.post(VERIFICA)).json()["verifica_id"]
        fine = await _attendi_fine(c, lenta)
        catena = (await c.get("/api/v1/hashchain/status")).json()["integrita"]
    assert fine["stato"] == "INTERROTTA" and fine["esito"] is None
    assert "0.3 s" in fine["messaggio"]
    assert catena["verifica_id"] == buona  # resta l'ultima COMPLETATA


async def test_errore_durante_la_verifica_fallita_senza_dettagli(db_url, monkeypatch) -> None:
    async def guasta(**_):
        raise RuntimeError("psycopg: password authentication failed for user vd_segreto")

    monkeypatch.setattr(va, "esegui_verifica_completa", guasta)
    async with await _client() as c:
        vid = (await c.post(VERIFICA)).json()["verifica_id"]
        fine = await _attendi_fine(c, vid)
    assert fine["stato"] == "FALLITA" and fine["integra"] is None
    assert "vd_segreto" not in json.dumps(fine)
    righe = await sql(db_url, "SELECT dettagli::text AS d FROM public.audit_global "
                              "WHERE evento = 'hashchain.verifica_completa'")
    assert "vd_segreto" not in righe[0]["d"]


async def test_riavvio_durante_la_verifica_chiude_come_interrotta(db_url) -> None:
    """Un avvio registrato da un processo poi morto (nessun lock attivo, nessun
    esito): all'avvio del servizio si chiude come INTERROTTA."""
    vid = str(uuid.uuid4())
    avviata = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    await sql(db_url, "INSERT INTO public.audit_global (id, evento, dettagli, created_at) "
                      "VALUES ($1, 'hashchain.verifica_avviata', CAST($2 AS jsonb), now())",
              uuid.uuid4(), json.dumps({"verifica_id": vid, "stato": "IN_CORSO",
                                        "avviata_il": avviata, "avviata_da": "u",
                                        "ruolo": "SUPER_ADMIN"}))
    assert await va.chiudi_verifiche_orfane() == 1   # ciò che fa il lifespan all'avvio
    assert await va.chiudi_verifiche_orfane() == 0   # idempotente
    async with await _client() as c:
        stato = await _stato(c, vid)
    assert stato["stato"] == "INTERROTTA" and stato["avviata_il"] == avviata
    assert "riavviato" in stato["messaggio"]


async def test_orfana_chiusa_anche_alla_lettura(db_url) -> None:
    vid = str(uuid.uuid4())
    await sql(db_url, "INSERT INTO public.audit_global (id, evento, dettagli, created_at) "
                      "VALUES ($1, 'hashchain.verifica_avviata', CAST($2 AS jsonb), now())",
              uuid.uuid4(), json.dumps({"verifica_id": vid, "stato": "IN_CORSO",
                                        "avviata_il": datetime.now(UTC).isoformat()}))
    async with await _client() as c:
        assert (await _stato(c, vid))["stato"] == "INTERROTTA"


async def test_verifica_in_esecuzione_non_chiusa_come_orfana(db_url, verifica_trattenuta) -> None:
    async with await _client() as c:
        vid = (await c.post(VERIFICA)).json()["verifica_id"]
        assert await va.chiudi_verifiche_orfane() == 0
        assert (await _stato(c, vid))["stato"] == "IN_CORSO"
        verifica_trattenuta.set()
        assert (await _attendi_fine(c, vid))["stato"] == "COMPLETATA"


async def test_arresto_del_servizio_registra_interrotta(db_url, verifica_trattenuta) -> None:
    async with await _client() as c:
        vid = (await c.post(VERIFICA)).json()["verifica_id"]
        await asyncio.sleep(0.05)
        await va.arresta_verifiche()          # ciò che fa il lifespan allo spegnimento
        stato = await _stato(c, vid)
    assert stato["stato"] == "INTERROTTA" and "arrestato" in stato["messaggio"]


@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "OPERATORE", "VIEWER", "API_SYSTEM"])
async def test_stato_della_verifica_negato_agli_altri_ruoli(db_url, ruolo) -> None:
    async with await _client() as c:
        vid = (await c.post(VERIFICA)).json()["verifica_id"]
        await va.attendi_verifiche()
    async with await _client(ruolo) as c:
        r = await c.get(f"{VERIFICA}/{vid}")
        avvio = await c.post(VERIFICA)
    assert r.status_code == 403 and r.json()["codice"] == "HC-403"
    assert avvio.status_code == 403


async def test_id_sconosciuto_404_e_id_non_valido_422(db_url) -> None:
    async with await _client() as c:
        assert (await c.get(f"{VERIFICA}/{uuid.uuid4()}")).status_code == 404
        assert (await c.get(f"{VERIFICA}/non-un-uuid")).status_code == 422
