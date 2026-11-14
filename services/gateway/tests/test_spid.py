# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test F2.4 — SPID/CIE integration (mock mode).
Il mock esiste solo con il login di sviluppo attivo (fixture `login_sviluppo`);
lo state usa un Redis in memoria (fixture `redis_finto`).

R02 (g2-auth): il mock simula SOLO la verifica dell'identità; associazione
all'utente, codice monouso nel frammento e scambio via POST sono quelli del
login reale. Qui l'elenco degli utenti è simulato; su PostgreSQL reale vedi
test_g2_spid_accesso.py.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from app.auth import spid
from app.main import app
from httpx import ASGITransport, AsyncClient

UTENTE = spid.UtenteSpid(
    user_id="00000000-0000-0000-0000-0000000000a1",
    tenant_id="00000000-0000-0000-0000-000000000001",
    ruolo="OPERATORE", nome="Mario", cognome="Rossi",
    ragione_sociale="Comune di Prova", piano="business",
)


@pytest.fixture
def utenti_simulati(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[spid.UtenteSpid]]:
    elenco: dict[str, list[spid.UtenteSpid]] = {"FTZPRV00A01Z999V": [UTENTE]}

    async def _utenti(cf: str) -> list[spid.UtenteSpid]:
        return list(elenco.get(cf, []))

    async def _registra(user_id: str) -> None:
        return None

    monkeypatch.setattr(spid, "utenti_per_codice_fiscale", _utenti)
    monkeypatch.setattr(spid, "_registra_accesso", _registra)
    return elenco


def _frammento(location: str) -> dict[str, str]:
    url = urlparse(location)
    assert url.query == ""
    return {k: v[0] for k, v in parse_qs(url.fragment).items()}


async def _mock_login(c: AsyncClient, cf: str | None = None, provider: str = "spid") -> Any:
    r = await c.get("/api/v1/auth/spid/login",
                    params={"provider": provider, "redirect_uri": "http://localhost:5173/login",
                            "livello": 2})
    assert r.status_code in (302, 307)
    assert "mock-callback" in r.headers["location"]
    q = parse_qs(urlparse(r.headers["location"]).query)
    params: dict[str, Any] = {"state": q["state"][0], "provider": provider}
    if cf:
        params["cf"] = cf
    return await c.get("/api/v1/auth/spid/mock-callback", params=params)


class TestSpidEndpoints:
    async def test_list_providers(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/auth/spid/providers")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 7
        nomi = [p["id"] for p in body["providers"]]
        assert "poste" in nomi
        assert "cie" in nomi

    async def test_mock_callback_state_invalido(self, redis_finto, login_sviluppo):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/auth/spid/mock-callback",
                            params={"state": "state-inesistente"})
        # g4: ritorno alla pagina di accesso (BASE_URL/login), non JSON
        assert r.status_code == 303
        assert _frammento(r.headers["location"]) == {"spid_errore": "SPID-002"}

    async def test_mock_codice_nel_frammento_poi_token_via_post(
        self, redis_finto, login_sviluppo, utenti_simulati
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               follow_redirects=False) as c:
            r = await _mock_login(c, provider="cie")
            assert r.status_code == 303
            assert r.headers["location"].startswith("http://localhost:5173/login#")
            esito = _frammento(r.headers["location"])
            assert set(esito) == {"spid_codice"}
            assert "token" not in r.headers["location"].lower()

            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": esito["spid_codice"]})
            assert s.status_code == 200, s.text
            corpo = s.json()
            assert corpo["esito"] == "accesso"
            assert corpo["utente"]["ruolo"] == "OPERATORE"
            assert corpo["utente"]["ragione_sociale"] == "Comune di Prova"

            from app.auth.jwt_handler import verify_access_token
            # R05 (VD-C-079): token solo nel cookie HttpOnly della sessione
            assert "access_token" not in corpo
            payload = verify_access_token(s.cookies["vd_sessione"])
            assert payload["tenant_id"] == UTENTE.tenant_id
            assert payload["user_id"] == UTENTE.user_id
            assert payload["auth_method"] == "cie_mock"
            assert "codice_fiscale" not in payload  # minimizzazione (§19.9)

            # Il codice è monouso
            di_nuovo = await c.post("/api/v1/auth/spid/scambio",
                                    json={"codice": esito["spid_codice"]})
            assert di_nuovo.status_code == 400
            assert di_nuovo.json()["codice"] == "SPID-012"

    async def test_mock_codice_fiscale_senza_utente(
        self, redis_finto, login_sviluppo, utenti_simulati
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await _mock_login(c, cf="MRTMTT25D09F205Z")
        assert _frammento(r.headers["location"]) == {"spid_errore": "SPID-008"}
        assert redis_finto.dati == {}  # nessun codice emesso

    async def test_mock_codice_fiscale_non_valido(
        self, redis_finto, login_sviluppo, utenti_simulati
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await _mock_login(c, cf="RSSMRA80A01H501Z")
        assert _frammento(r.headers["location"]) == {"spid_errore": "SPID-007"}

    async def test_codice_usato_da_altro_browser_rifiutato(
        self, redis_finto, login_sviluppo, utenti_simulati
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await _mock_login(c)
            codice = _frammento(r.headers["location"])["spid_codice"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as altro:
            s = await altro.post("/api/v1/auth/spid/scambio", json={"codice": codice})
        assert s.status_code == 400
        assert s.json()["codice"] == "SPID-006"

    async def test_piu_enti_richiede_la_scelta(
        self, redis_finto, login_sviluppo, utenti_simulati
    ):
        secondo = spid.UtenteSpid(
            user_id="00000000-0000-0000-0000-0000000000b2",
            tenant_id="00000000-0000-0000-0000-000000000002",
            ruolo="ADMIN_TENANT", nome="Mario", cognome="Rossi",
            ragione_sociale="Unione dei Comuni", piano="starter",
        )
        utenti_simulati["FTZPRV00A01Z999V"] = [UTENTE, secondo]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await _mock_login(c)
            codice = _frammento(r.headers["location"])["spid_codice"]
            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})
            assert s.status_code == 200
            scelta = s.json()
            assert scelta["esito"] == "scelta_ente"
            assert "access_token" not in scelta
            assert [e["tenant_id"] for e in scelta["enti"]] == [UTENTE.tenant_id,
                                                                secondo.tenant_id]
            # il vecchio codice è consumato, il nuovo vale per la scelta
            vecchio = await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})
            assert vecchio.status_code == 400
            t = await c.post("/api/v1/auth/spid/scambio",
                             json={"codice": scelta["codice"], "tenant_id": secondo.tenant_id})
        assert t.status_code == 200, t.text
        assert t.json()["utente"]["ruolo"] == "ADMIN_TENANT"
        assert t.json()["utente"]["tenant_id"] == secondo.tenant_id
