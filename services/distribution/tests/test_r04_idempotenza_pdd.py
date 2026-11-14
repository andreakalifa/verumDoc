# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 servizi (VD-C-106) — Idempotency-Key su POST /api/v2/distribution/richiedi.

Stessa semantica del versamento: stessa chiave e stessa richiesta → lo STESSO
PdD (200, ``Idempotent-Replayed``), corpo diverso → 409 IDEM-409. PostgreSQL
reale; il motore di distribuzione è sostituito (provato in test_b4_pdd.py) per
contare le esecuzioni.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.test_g2_distribuzione import _aip, _client, _finta_distribuzione, _token

pytestmark = richiede_pg

URL = "/api/v2/distribution/richiedi"


def _corpo(aip, motivo: str = "richiesta ispettiva") -> dict:
    return {"aip_id": str(aip), "tipo": "ESIBIZIONE", "motivo": motivo}


async def test_richiedi_stessa_chiave_stesso_pdd_una_sola_esecuzione(db_url, monkeypatch) -> None:
    ricevute = _finta_distribuzione(monkeypatch)
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    h = {**_token(str(tenant), user_id=str(uuid.uuid4())), "Idempotency-Key": "pdd-prova-0001"}
    async with await _client() as c:
        primo = await c.post(URL, json=_corpo(aip), headers=h)
        secondo = await c.post(URL, json=_corpo(aip), headers=h)
    assert primo.status_code == secondo.status_code == 200, secondo.text
    assert primo.json()["pdd_id"] == secondo.json()["pdd_id"]
    assert "idempotent-replayed" not in primo.headers
    assert secondo.headers["idempotent-replayed"] == "true"
    assert len(ricevute) == 1
    assert len(await sql(db_url, "SELECT 1 FROM public.pacchetti_distribuzione")) == 1
    [riga] = await sql(db_url, "SELECT stato, risorsa_id FROM public.richieste_idempotenti")
    assert (riga["stato"], riga["risorsa_id"]) == ("COMPLETATA", primo.json()["pdd_id"])


async def test_richiedi_stessa_chiave_corpo_diverso_409(db_url, monkeypatch) -> None:
    _finta_distribuzione(monkeypatch)
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    h = {**_token(str(tenant), user_id=str(uuid.uuid4())), "Idempotency-Key": "pdd-prova-0002"}
    async with await _client() as c:
        assert (await c.post(URL, json=_corpo(aip), headers=h)).status_code == 200
        r = await c.post(URL, json=_corpo(aip, "altro motivo della richiesta"), headers=h)
    assert r.status_code == 409 and r.json()["codice"] == "IDEM-409"
    assert len(await sql(db_url, "SELECT 1 FROM public.pacchetti_distribuzione")) == 1


async def test_richiedi_chiave_malformata_400_e_senza_chiave_due_pdd(db_url, monkeypatch) -> None:
    ricevute = _finta_distribuzione(monkeypatch)
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    h = _token(str(tenant))
    async with await _client() as c:
        r = await c.post(URL, json=_corpo(aip), headers={**h, "Idempotency-Key": "corta"})
        assert r.status_code == 400 and r.json()["codice"] == "IDEM-400"
        a = await c.post(URL, json=_corpo(aip), headers=h)
        b = await c.post(URL, json=_corpo(aip), headers=h)
    assert a.json()["pdd_id"] != b.json()["pdd_id"] and len(ricevute) == 2


async def test_richiedi_fallita_la_chiave_resta_ritentabile(db_url, monkeypatch) -> None:
    """Un errore prima della registrazione non lascia la chiave prenotata."""
    from app.routers import distribution as modulo

    from libs.shared.errori import ErroreApi

    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)

    async def guasto(req, tid):
        raise ErroreApi("DST-503", "S3 non risponde")

    monkeypatch.setattr(modulo, "esegui_distribuzione", guasto)
    h = {**_token(str(tenant), user_id=str(uuid.uuid4())), "Idempotency-Key": "pdd-prova-0003"}
    async with await _client() as c:
        assert (await c.post(URL, json=_corpo(aip), headers=h)).status_code == 503
        assert await sql(db_url, "SELECT 1 FROM public.richieste_idempotenti") == []
        _finta_distribuzione(monkeypatch)
        r = await c.post(URL, json=_corpo(aip), headers=h)
    assert r.status_code == 200 and "idempotent-replayed" not in r.headers


async def test_richiedi_in_corso_409_ritentabile_poi_ripresa(db_url, monkeypatch) -> None:
    """Una richiesta identica ancora in corso → 409 IN-CORSO con Retry-After; una
    prenotazione abbandonata (processo morto) si riprende dopo IDEMPOTENZA_IN_CORSO_MAX."""
    from libs.shared.security.idempotenza import impronta_richiesta

    ricevute = _finta_distribuzione(monkeypatch)
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip(db_url, tenant, classe)
    utente = str(uuid.uuid4())
    impronta = impronta_richiesta({"richiesta": {**_corpo(aip), "file_richiesti": None,
                                                 "url_expiry_seconds": None, "consegna": None},
                                   "canale": "API", "richiedente": utente})
    adesso = datetime.now(UTC)
    await sql(db_url, "INSERT INTO public.richieste_idempotenti (tenant_id, ambito, chiave, "
                      "impronta, stato, aggiornata_il, scade_il) VALUES ($1, 'distribution.pdd', "
                      "'pdd-prova-0004', $2, 'IN_CORSO', $3, $4)",
              tenant, impronta, adesso, adesso + timedelta(hours=24))
    h = {**_token(str(tenant), user_id=utente), "Idempotency-Key": "pdd-prova-0004"}
    async with await _client() as c:
        r = await c.post(URL, json=_corpo(aip), headers=h)
        assert r.status_code == 409 and r.json()["codice"] == "IDEM-409-IN-CORSO"
        assert int(r.headers["Retry-After"]) >= 1 and r.json()["ritentabile"] is True
        await sql(db_url, "UPDATE public.richieste_idempotenti SET aggiornata_il = $1",
                  adesso - timedelta(minutes=10))
        ripresa = await c.post(URL, json=_corpo(aip), headers=h)
    assert ripresa.status_code == 200 and len(ricevute) == 1


async def test_richiedi_chiave_per_ente_e_scaduta_riusabile(db_url, monkeypatch) -> None:
    ricevute = _finta_distribuzione(monkeypatch)
    t1, classe = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    a1, a2 = await _aip(db_url, t1, classe), await _aip(db_url, t2, classe)
    async with await _client() as c:
        r1 = await c.post(URL, json=_corpo(a1), headers={**_token(str(t1)),
                                                          "Idempotency-Key": "pdd-condivisa-01"})
        r2 = await c.post(URL, json=_corpo(a2), headers={**_token(str(t2)),
                                                          "Idempotency-Key": "pdd-condivisa-01"})
        assert r1.status_code == r2.status_code == 200 and len(ricevute) == 2
        # chiave scaduta: la stessa chiave con un corpo diverso è una richiesta nuova
        await sql(db_url, "UPDATE public.richieste_idempotenti SET scade_il = now() - "
                          "interval '1 minute' WHERE tenant_id = $1", t1)
        r3 = await c.post(URL, json=_corpo(a1, "nuova richiesta ispettiva"),
                          headers={**_token(str(t1)), "Idempotency-Key": "pdd-condivisa-01"})
    assert r3.status_code == 200 and r3.json()["pdd_id"] != r1.json()["pdd_id"]
