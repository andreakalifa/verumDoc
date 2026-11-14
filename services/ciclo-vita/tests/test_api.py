# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API del ciclo di vita: autenticazione, perimetri fra enti, elenchi a cursore,
errori del catalogo, salute (REGOLE §14, §18, §19.1, §23.4).
"""
from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    PASSATO,
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pda,
    crea_pdv,
    crea_utente,
    intestazioni,
    token,
)

ROTTE = [("get", "/api/v1/ciclo-vita/pratiche"),
         ("get", f"/api/v1/ciclo-vita/pratiche/{uuid.uuid4()}"),
         ("get", f"/api/v1/ciclo-vita/pratiche/{uuid.uuid4()}/oggetti"),
         ("post", f"/api/v1/ciclo-vita/pratiche/{uuid.uuid4()}/transizione"),
         ("post", "/api/v1/ciclo-vita/pratiche/rettifica"),
         ("post", "/api/v1/ciclo-vita/pratiche/annullamento"),
         ("post", "/api/v1/ciclo-vita/pratiche/scarto/proposta"),
         ("post", "/api/v1/ciclo-vita/pratiche/riversamento-uscita"),
         ("post", "/api/v1/ciclo-vita/pratiche/riversamento-ingresso"),
         ("post", "/api/v1/ciclo-vita/pratiche/restituzione")]


@pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
async def test_rotte_senza_token_401(metodo, percorso) -> None:
    async with client_app() as c:
        r = await getattr(c, metodo)(percorso)
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["codice"] == "AUTH-000"


@pytest.mark.parametrize(("metodo", "percorso"), ROTTE[:3])
async def test_rotte_token_altra_chiave_401(metodo, percorso) -> None:
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
    falso = token(uuid.uuid4(), "ADMIN_TENANT", chiave_privata=pem)
    async with client_app() as c:
        r = await getattr(c, metodo)(percorso, headers={"Authorization": f"Bearer {falso}"})
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-001"


async def test_salute_e_metriche() -> None:
    async with client_app() as c:
        assert (await c.get("/api/v1/health/live")).json()["status"] == "alive"
        assert (await c.get("/metrics")).status_code == 200


def test_avvio_in_produzione_senza_configurazione_rifiutato(monkeypatch) -> None:
    """REGOLE §19.4: in produzione al LAVORATORE mancano token del registro,
    chiave del canale, token di distribution, Redis → elenco dei problemi."""
    from app.config import settings
    from app.main import problemi_avvio

    monkeypatch.setattr(settings, "env", "production")
    problemi = " ".join(problemi_avvio(lavoratore=True))
    for atteso in ("HASHCHAIN_TOKEN_REGISTRO", "CICLO_VITA_TOKEN_CHIAVE_PRIVATA",
                   "DISTRIBUTION_TOKEN_SERVIZIO", "REDIS_URL"):
        assert atteso in problemi


@pytest.mark.integration
@richiede_pg
async def test_perimetro_fra_enti_e_cursore(db_url, s3_ciclo) -> None:
    """§14.4: la pratica di un altro ente risponde 404 (lettura, oggetti,
    transizione); l'elenco dell'ente non mostra le pratiche altrui; il
    conservatore vede tutti gli enti e filtra per ente; cursore senza salti."""
    bucket_worm(s3_ciclo)
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    admin_a, admin_b = await crea_utente(db_url, a, "ADMIN_TENANT"), await crea_utente(
        db_url, b, "ADMIN_TENANT")
    classe = await crea_classe(db_url, a)
    pratiche = []
    async with client_app() as c:
        for _ in range(3):
            pdv = await crea_pdv(db_url, a, classe, admin_a, stato="RICEVUTO")
            r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                             headers=intestazioni(a, "ADMIN_TENANT", admin_a),
                             json={"pdv_id": str(pdv), "motivo": "Versato per errore, prova"})
            assert r.status_code == 201, r.text
            pratiche.append(r.json()["id"])
        chi_b = intestazioni(b, "ADMIN_TENANT", admin_b)
        for percorso in (f"/api/v1/ciclo-vita/pratiche/{pratiche[0]}",
                         f"/api/v1/ciclo-vita/pratiche/{pratiche[0]}/oggetti"):
            r = await c.get(percorso, headers=chi_b)
            assert r.status_code == 404 and r.json()["codice"] == "LC-404"
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pratiche[0]}/transizione", headers=chi_b,
                         json={"stato_atteso": "ESEGUITA", "nuovo_stato": "RESPINTA",
                               "motivo": "Tentativo da un altro ente"})
        assert r.status_code == 404
        r = await c.get("/api/v1/ciclo-vita/pratiche", headers=chi_b)
        assert r.json()["items"] == []
        r = await c.get(f"/api/v1/ciclo-vita/pratiche?ente={a}", headers=chi_b)
        assert r.json()["items"] == []
        # annullamento di un PdV di un altro ente → 404 (nessun oracolo)
        pdv_a = await crea_pdv(db_url, a, classe, admin_a, stato="RICEVUTO")
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi_b,
                         json={"pdv_id": str(pdv_a), "motivo": "Tentativo da un altro ente"})
        assert r.status_code == 404 and r.json()["codice"] == "LC-404-PDV"
        # cursore: 3 pratiche a pagine di 2, nessun salto né doppione
        chi_a = intestazioni(a, "VIEWER")
        p1 = (await c.get("/api/v1/ciclo-vita/pratiche?limite=2", headers=chi_a)).json()
        assert len(p1["items"]) == 2 and p1["altri"] is True
        p2 = (await c.get(f"/api/v1/ciclo-vita/pratiche?limite=2&cursore={p1['cursore']}",
                          headers=chi_a)).json()
        assert p2["altri"] is False
        visti = [x["id"] for x in p1["items"] + p2["items"]]
        assert sorted(visti) == sorted(pratiche)
        r = await c.get("/api/v1/ciclo-vita/pratiche?cursore=sbagliato", headers=chi_a)
        assert r.status_code == 400 and r.json()["codice"] == "LC-400-CURSORE"
        # il conservatore vede tutti gli enti e filtra per ente
        rsc = intestazioni(await crea_ente(db_url), "RSC_CONSERVATORE")
        r = await c.get(f"/api/v1/ciclo-vita/pratiche?ente={a}&tipo=ANNULLAMENTO", headers=rsc)
        assert {x["id"] for x in r.json()["items"]} == set(pratiche)
        r = await c.get(f"/api/v1/ciclo-vita/pratiche/{pratiche[0]}", headers=rsc)
        assert r.status_code == 200
        # identificativi non validi: 404/422, mai 500
        assert (await c.get("/api/v1/ciclo-vita/pratiche/abc", headers=chi_a)).status_code == 404
        r = await c.get("/api/v1/ciclo-vita/pratiche?limite=-1", headers=chi_a)
        assert r.status_code == 422


@pytest.mark.integration
@richiede_pg
async def test_proposta_scarto_permessi_ed_ente(db_url, s3_ciclo) -> None:
    a = await crea_ente(db_url)
    altro = await crea_ente(db_url)
    corpo = {"ente": str(a), "criteri": {"anni": [2015]}, "motivo": "Scarto dei documenti 2015"}
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                         headers=intestazioni(a, "OPERATORE"), json=corpo)
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-RUOLO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                         headers=intestazioni(altro, "ADMIN_TENANT"), json=corpo)
        assert r.status_code == 404 and r.json()["codice"] == "LC-404-ENTE"
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                         headers=intestazioni(a, "ADMIN_TENANT"),
                         json={
                             **corpo, "criteri": {"anni": [2015], "scadenza_entro": "2026-01-01"}})
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-CRITERI"
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                         headers=intestazioni(a, "ADMIN_TENANT"),
                         json={**corpo, "impronta_piano": "0" * 64})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-PIANO-CAMBIATO"
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                         headers=intestazioni(a, "ADMIN_TENANT"), json={**corpo, "motivo": "corto"})
        assert r.status_code == 422 and r.json()["codice"] == "VAL-422"


@pytest.mark.integration
@richiede_pg
async def test_scarto_fascicolo_aperto_e_termine_dalla_chiusura(db_url, s3_ciclo) -> None:
    """VD-C-087: il fascicolo aperto blocca lo scarto; chiuso, il termine si
    calcola dalla data di chiusura (anni della classe)."""
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant, anni=10)
    pda = []
    for _ in range(2):
        pdv = await crea_pdv(db_url, tenant, classe, admin)
        pda.append(await crea_pda(db_url, tenant, pdv, scadenza=PASSATO))
    aperto, chiuso = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.fascicoli (id, tenant_id, codice, titolo, stato) "
                      "VALUES ($1, $2, 'F-A', 'Aperto', 'APERTO'), ($3, $2, 'F-C', 'Chiuso', "
                      "'CHIUSO')", aperto, tenant, chiuso)
    await sql(db_url, "UPDATE public.fascicoli SET data_chiusura = current_date - 365 "
                      "WHERE id = $1", chiuso)
    await sql(db_url, "INSERT INTO public.fascicoli_aip (fascicolo_id, aip_id) VALUES ($1, $2), "
                      "($3, $4)", aperto, pda[0][0], chiuso, pda[1][0])
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                         headers=intestazioni(tenant, "ADMIN_TENANT", admin),
                         json={"ente": str(tenant), "criteri": {"anni": [2015]},
                               "motivo": "Scarto con fascicoli della prova"})
    assert r.json()["bloccati"] == {"FASCICOLO_APERTO": 1, "TERMINE_FASCICOLO_NON_SCADUTO": 1}
