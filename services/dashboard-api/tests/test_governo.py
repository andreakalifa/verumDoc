# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Contratti, nomine del responsabile della conservazione e notifiche —
dashboard-api su PostgreSQL reale (VD-C-022, VD-C-025).

Prima: le letture usavano colonne inesistenti (data_inizio, stato, nome,
cognome…) e ``:tid::uuid``; l'errore veniva nascosto e la pagina mostrava
«nessun contratto/nessuna nomina». Non esisteva un percorso di creazione.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

CF = "FTZPRV00A01Z999V"


def _token(tenant_id: str, ruolo: str) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id, user_id=str(uuid.uuid4()), ruolo=ruolo,
    )}


async def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _contratto(tenant: uuid.UUID, numero: str = "CTR-2026-001", **altro) -> dict:
    return {"tenant_id": str(tenant), "numero_contratto": numero,
            "data_stipula": "2026-01-15", "data_scadenza": "2036-01-14",
            "piano": "enterprise", "valore_annuo_eur": 1200.0, **altro}


def _nomina(tenant: uuid.UUID, **altro) -> dict:
    return {"tenant_id": str(tenant), "nome_rsc": "Mario Rossi", "codice_fiscale_rsc": CF,
            "email_rsc": "mario.rossi@example.org", "data_nomina": "2026-02-01", **altro}


# ── Contratti ────────────────────────────────────────────────────────

async def test_contratto_creato_e_letto(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    sa = _token(str(uuid.uuid4()), "SUPER_ADMIN")
    async with await _client() as c:
        r = await c.post("/api/dashboard/contratti", json=_contratto(tenant), headers=sa)
        assert r.status_code == 201, r.text
        assert r.json()["stato"] == "ATTIVO"

        # L'amministratore del cliente legge i contratti del proprio cliente
        lista = await c.get("/api/dashboard/contratti",
                            headers=_token(str(tenant), "ADMIN_TENANT"))
    assert lista.status_code == 200, lista.text
    corpo = lista.json()
    assert corpo["total"] == 1
    voce = corpo["items"][0]
    assert voce["numero_contratto"] == "CTR-2026-001"
    assert voce["data_inizio"] == "2026-01-15"
    assert voce["data_fine"] == "2036-01-14"
    assert voce["tipo_servizio"] == "enterprise"
    assert voce["ragione_sociale"] == "Cliente di prova"
    assert voce["stato"] == "ATTIVO"


async def test_contratto_scaduto_e_revocato(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    ieri = date.today() - timedelta(days=2)
    await sql(db_url, "INSERT INTO public.contracts (tenant_id, numero_contratto, data_stipula, "
                      "data_scadenza, piano) VALUES ($1, 'S-1', $2, $3, 'base')",
              tenant, ieri - timedelta(days=365), ieri)
    await sql(db_url, "INSERT INTO public.contracts (tenant_id, numero_contratto, data_stipula, "
                      "piano, attivo) VALUES ($1, 'R-1', $2, 'base', false)", tenant, ieri)
    async with await _client() as c:
        r = await c.get("/api/dashboard/contratti", headers=_token(str(tenant), "VIEWER"))
    stati = {v["numero_contratto"]: v["stato"] for v in r.json()["items"]}
    assert stati == {"S-1": "SCADUTO", "R-1": "REVOCATO"}


async def test_contratti_di_altro_cliente_non_visibili(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "INSERT INTO public.contracts (tenant_id, numero_contratto, data_stipula, "
                      "piano) VALUES ($1, 'X-1', CURRENT_DATE, 'base')", t1)
    async with await _client() as c:
        # Chi non è SUPER_ADMIN/RSC non può chiedere un altro cliente: vede il suo
        r = await c.get("/api/dashboard/contratti", params={"tenant_id": str(t1)},
                        headers=_token(str(t2), "ADMIN_TENANT"))
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.parametrize("modifica, atteso", [
    ({"data_scadenza": "2025-01-01"}, 422),          # scadenza prima della stipula
    ({"numero_contratto": ""}, 422),
    ({"valore_annuo_eur": -1}, 422),
    ({"campo_ignoto": 1}, 422),
])
async def test_contratto_validazione(db_url, modifica, atteso) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        r = await c.post("/api/dashboard/contratti", json=_contratto(tenant, **modifica),
                         headers=_token(str(uuid.uuid4()), "SUPER_ADMIN"))
    assert r.status_code == atteso, r.text


async def test_contratto_permessi_duplicati_cliente_inesistente(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    sa = _token(str(uuid.uuid4()), "SUPER_ADMIN")
    async with await _client() as c:
        r = await c.post("/api/dashboard/contratti", json=_contratto(tenant),
                         headers=_token(str(tenant), "ADMIN_TENANT"))
        assert r.status_code == 403
        assert (await c.post("/api/dashboard/contratti", json=_contratto(tenant),
                             headers=sa)).status_code == 201
        assert (await c.post("/api/dashboard/contratti", json=_contratto(tenant),
                             headers=sa)).status_code == 409
        r = await c.post("/api/dashboard/contratti", json=_contratto(uuid.uuid4(), "N-2"),
                         headers=sa)
        assert r.status_code == 404


# ── Nomine ───────────────────────────────────────────────────────────

async def test_nomina_creata_revoca_la_precedente(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    sa = _token(str(uuid.uuid4()), "SUPER_ADMIN")
    async with await _client() as c:
        prima = await c.post("/api/dashboard/nomine-rsc", json=_nomina(tenant), headers=sa)
        assert prima.status_code == 201, prima.text
        seconda = await c.post(
            "/api/dashboard/nomine-rsc",
            json=_nomina(tenant, nome_rsc="Anna Bianchi", codice_fiscale_rsc="BNCNNA85M41F205X",
                         data_nomina="2026-06-01"),
            headers=sa,
        )
        assert seconda.status_code == 201, seconda.text
        assert seconda.json()["revocate"] == [prima.json()["id"]]

        r = await c.get("/api/dashboard/nomine-rsc",
                        headers=_token(str(tenant), "RSC_CONSERVATORE"))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["nomina_corrente"]["nome"] == "Anna Bianchi"
    assert corpo["nomina_corrente"]["codice_fiscale"] == "BNCNNA85M41F205X"
    assert len(corpo["storico"]) == 1
    assert corpo["storico"][0]["data_revoca"] == "2026-06-01"
    assert corpo["storico"][0]["attiva"] is False


async def test_nomine_filtrate_per_cliente(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    sa = _token(str(uuid.uuid4()), "SUPER_ADMIN")
    async with await _client() as c:
        await c.post("/api/dashboard/nomine-rsc", json=_nomina(t1), headers=sa)
        r = await c.get("/api/dashboard/nomine-rsc",
                        headers=_token(str(t2), "RSC_CONSERVATORE"))
        assert r.json()["nomina_corrente"] is None
        r = await c.get("/api/dashboard/nomine-rsc", params={"tenant_id": str(t1)}, headers=sa)
        assert r.json()["nomina_corrente"]["nome"] == "Mario Rossi"
        # Ruoli non ammessi alla lettura (permessi invariati)
        r = await c.get("/api/dashboard/nomine-rsc", headers=_token(str(t1), "ADMIN_TENANT"))
        assert r.status_code == 403


@pytest.mark.parametrize("modifica", [
    {"codice_fiscale_rsc": "ftzprv00a01z999v"},   # minuscole
    {"codice_fiscale_rsc": "12345678901"},         # partita IVA, non persona fisica
    {"email_rsc": "non-una-email"},
    {"data_scadenza": "2026-01-01"},               # prima della nomina
])
async def test_nomina_validazione(db_url, modifica) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        r = await c.post("/api/dashboard/nomine-rsc", json=_nomina(tenant, **modifica),
                         headers=_token(str(uuid.uuid4()), "SUPER_ADMIN"))
    assert r.status_code == 422, r.text


async def test_nomina_solo_super_admin(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        for ruolo in ("ADMIN_TENANT", "RSC_CONSERVATORE", "OPERATORE"):
            r = await c.post("/api/dashboard/nomine-rsc", json=_nomina(tenant),
                             headers=_token(str(tenant), ruolo))
            assert r.status_code == 403, ruolo


# ── Notifiche ────────────────────────────────────────────────────────

async def test_notifiche_filtrate_per_tenant_id(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    # Il destinatario della notifica di t2 contiene l'id di t1: col vecchio
    # filtro LIKE sul destinatario t1 l'avrebbe vista.
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario) "
                      "VALUES ($1, 'PDD_ESIBIZIONE', 'API', 'utente-a')", t1)
    await sql(db_url, "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario) "
                      "VALUES ($1, 'PDD_ESIBIZIONE', 'API', $2)", t2, f"x-{t1}")
    async with await _client() as c:
        r = await c.get("/api/dashboard/notifiche", params={"tenant_id": str(t1)},
                        headers=_token(str(t1), "ADMIN_TENANT"))
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1   # R04 (VD-C-102): a cursore, senza totale
    assert r.json()["items"][0]["destinatario"] == "utente-a"
