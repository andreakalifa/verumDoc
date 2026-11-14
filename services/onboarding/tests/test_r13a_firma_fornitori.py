# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13a — registro dei fornitori di firma e marca nella console (onboarding), su
PostgreSQL vero: solo i ruoli fissi del conservatore (permesso mai delegabile),
nessun segreto, niente SVILUPPO in produzione, versione letta, ruolo e tipo
immutabili, audit di ogni modifica e di ogni prova, stato e prova inoltrati al
servizio di firma con il token della persona.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_g5_gestione_utenti import _chiama, _ente, _tok, _utente

pytestmark = [pytest.mark.integration, richiede_pg]
URL = "/api/v1/onboarding/firma/fornitori"
CSC = {"ruolo": "FIRMA", "tipo": "CSC", "nome": "Firma remota principale", "attivo": True,
       "priorita": 1, "configurazione": {"url_base": "https://rssp.esempio.it/csc/v2",
                                         "url_token": "https://rssp.esempio.it/oauth2/token",
                                         "client_id": "vd"}}


async def _rsc(url: str) -> str:
    tid = await _ente(url)
    return _tok(tid, await _utente(url, tid, "RSC_CONSERVATORE"), "RSC_CONSERVATORE")


async def _eventi(url: str, evento: str) -> list[dict]:
    righe = await sql(url, "SELECT dettagli::text AS d FROM public.audit_global WHERE evento = $1 "
                           "ORDER BY created_at", evento)
    return [json.loads(r["d"]) for r in righe]


async def test_registra_modifica_con_versione_e_audit(db_url) -> None:  # noqa: F811
    tok = await _rsc(db_url)
    r = await _chiama("PUT", f"{URL}/csc-principale", tok, {**CSC, "versione": 0})
    assert r.status_code == 200, r.text
    assert r.json()["versione"] == 1 and r.json()["configurazione"]["timeout_secondi"] == 30
    r = await _chiama("PUT", f"{URL}/csc-principale", tok, {**CSC, "versione": 0})
    assert r.status_code == 409
    r = await _chiama("PUT", f"{URL}/csc-principale", tok,
                      {**CSC, "priorita": 2, "versione": 1})
    assert r.status_code == 200 and r.json()["priorita"] == 2
    r = await _chiama("PUT", f"{URL}/csc-principale", tok,
                      {**CSC, "tipo": "PKCS11", "versione": 2})
    assert r.status_code == 422
    elenco = await _chiama("GET", URL, tok)
    assert [i["codice"] for i in elenco.json()["items"]] == ["csc-principale"]
    assert {s["nome"] for s in elenco.json()["segreti"]["CSC"]} >= {"client_secret", "pin"}
    eventi = await _eventi(db_url, "firma.fornitore_modificato")
    assert len(eventi) == 2 and eventi[0]["prima"] is None and eventi[1]["dopo"]["priorita"] == 2


async def test_nessun_segreto_e_niente_sviluppo_in_produzione(db_url, monkeypatch) -> None:  # noqa: F811
    tok = await _rsc(db_url)
    corpo = {**CSC, "configurazione": {**CSC["configurazione"], "client_secret": "x"}}
    r = await _chiama("PUT", f"{URL}/csc-x", tok, corpo)
    assert r.status_code == 422 and r.json()["codice"] == "FIRMA-422-CONFIGURAZIONE"
    monkeypatch.setenv("ENV", "production")
    r = await _chiama("PUT", f"{URL}/finto", tok, {"ruolo": "FIRMA", "tipo": "SVILUPPO",
                                                  "nome": "Sviluppo", "attivo": True})
    assert r.status_code == 422 and "SVILUPPO" in r.text
    assert await sql(db_url, "SELECT 1 FROM public.fornitori_firma") == []


@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "OPERATORE", "VIEWER"])
async def test_solo_il_conservatore(db_url, ruolo) -> None:  # noqa: F811
    tid = await _ente(db_url)
    tok = _tok(tid, await _utente(db_url, tid, ruolo), ruolo)
    r = await _chiama("PUT", f"{URL}/csc-principale", tok, CSC)
    assert r.status_code == 403 and r.json()["codice"] == "FIRMA-403-FORNITORI"
    assert (await _chiama("GET", URL, tok)).status_code == 403


async def test_ruolo_personalizzato_mai_anche_sul_conservatore(db_url) -> None:  # noqa: F811
    from app.auth.jwt_handler import create_access_token

    tid = await _ente(db_url)
    uid = await _utente(db_url, tid, "RSC_CONSERVATORE")
    tok = create_access_token(tenant_id=str(tid), user_id=str(uid), ruolo="RSC_CONSERVATORE",
                              rp=str(uuid.uuid4()), permessi=["firma_fornitori:gestisci"])
    r = await _chiama("GET", URL, tok)
    assert r.status_code in (401, 403)


async def test_stato_e_prova_inoltrati_con_audit(db_url, monkeypatch) -> None:  # noqa: F811
    from app.routers import firma_fornitori

    ricevute: list[httpx.Request] = []

    def firma(request: httpx.Request) -> httpx.Response:
        ricevute.append(request)
        if request.url.path.endswith("/prova"):
            if "ignoto" in request.url.path:
                return httpx.Response(404, json={"codice": "FIRMA-404-FORNITORE",
                                                 "detail": "fornitore non attivo"})
            return httpx.Response(200, json={"codice": "csc-principale", "esito": "OK",
                                             "verificata": True})
        return httpx.Response(200, json={"fornitori": [], "production_ready": False})

    monkeypatch.setattr(firma_fornitori, "TRASPORTO", httpx.MockTransport(firma))
    tok = await _rsc(db_url)
    assert (await _chiama("GET", f"{URL}/stato", tok)).json()["production_ready"] is False
    r = await _chiama("POST", f"{URL}/csc-principale/prova", tok)
    assert r.status_code == 200 and r.json()["verificata"]
    r = await _chiama("POST", f"{URL}/ignoto/prova", tok)
    assert r.status_code == 404 and r.json()["codice"] == "FIRMA-404-FORNITORE"
    assert all(q.headers["authorization"] == f"Bearer {tok}" for q in ricevute)
    eventi = await _eventi(db_url, "firma.fornitore_provato")
    assert [e["esito"] for e in eventi] == ["OK", "ERRORE"]


async def test_permesso_mai_delegabile_anche_nel_vincolo_del_database(db_url) -> None:  # noqa: F811
    import asyncpg

    tid = await _ente(db_url)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await sql(db_url, "INSERT INTO public.ruoli_personalizzati (id, tenant_id, nome, "
                          "ruolo_base, permessi) VALUES ($1, $2, 'x', 'OPERATORE', $3)",
                  uuid.uuid4(), tid, ["firma_fornitori:gestisci"])


def test_controllo_del_permesso_mai_delegabile_isolato() -> None:
    """La dipendenza della console nega il permesso a un token con ruolo
    personalizzato anche se il ruolo è del conservatore (seconda difesa, isolata dalla
    revoca)."""
    from app.auth.jwt_handler import create_access_token
    from app.routers.firma_fornitori import conservatore_firma

    from libs.shared.errori import ErroreApi

    tid = str(uuid.uuid4())
    buono = create_access_token(tenant_id=tid, user_id=str(uuid.uuid4()),
                                ruolo="RSC_CONSERVATORE")
    assert conservatore_firma(f"Bearer {buono}")["ruolo"] == "RSC_CONSERVATORE"
    for extra in ({"rp": "r1", "permessi": ["firma_fornitori:gestisci"]},
                  {"auth_method": "api_key"}):
        tok = create_access_token(tenant_id=tid, user_id=str(uuid.uuid4()),
                                  ruolo="RSC_CONSERVATORE", **extra)
        with pytest.raises(ErroreApi) as errore:
            conservatore_firma(f"Bearer {tok}")
        assert errore.value.codice == "FIRMA-403-FORNITORI"
