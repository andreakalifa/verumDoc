# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza (g5-sicurezza) — SPID/CIE.

B3: il livello nel token (`spid_level`) è quello dell'`acr` dell'id_token
    VERIFICATO, non quello chiesto dal browser; sotto il minimo configurato
    (SPID_LIVELLO_MINIMO, almeno L2) l'accesso è rifiutato (SPID-009).
B4: SPID-010 (ritorno non ammesso) non rivela gli indirizzi ammessi.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from app.auth import spid
from app.config import settings
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.test_vdc006_spid_id_token import (  # noqa: F401 — fixture e aggregatore finto
    CLIENT_ID,
    AggregatoreFinto,
    _frammento,
    _login_e_callback,
    aggregatore,
)


@pytest.mark.parametrize("acr,atteso", [
    ("https://www.spid.gov.it/SpidL2", 2), ("https://www.spid.gov.it/SpidL3", 3),
    ("https://www.spid.gov.it/SpidL1", 1), (["https://www.spid.gov.it/SpidL3"], 3),
    (None, None), ("", None), ("urn:altro", None), (42, None),
])
def test_livello_da_acr(acr: Any, atteso: int | None) -> None:
    assert spid.livello_da_acr(acr) == atteso


class TestLivelloVerificato:
    @pytest.fixture
    def utente_associato(self, aggregatore, monkeypatch):
        """Persona associata a un utente: si arriva al codice monouso."""
        async def _un_utente(cf: str) -> list[Any]:
            return [spid.UtenteSpid(user_id="u1", tenant_id="t1", ruolo="OPERATORE",
                                    nome="Mario", cognome="Rossi", ragione_sociale="Ente",
                                    piano="starter")]
        async def _nessuna_scrittura(user_id: str) -> None:
            return None

        monkeypatch.setattr(spid, "utenti_per_codice_fiscale", _un_utente)
        monkeypatch.setattr(spid, "_registra_accesso", _nessuna_scrittura)
        return aggregatore

    @pytest.mark.parametrize("acr", ["https://www.spid.gov.it/SpidL1", None, "urn:sconosciuto"])
    async def test_acr_assente_o_sotto_il_minimo_spid_009(self, aggregatore, acr) -> None:
        aggregatore.claims_extra = {"acr": acr}
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-009"}
        assert aggregatore.cf_cercati == []          # nessuna ricerca dell'utente

    async def test_minimo_l3_configurabile(self, aggregatore, monkeypatch) -> None:
        monkeypatch.setattr(settings, "spid_livello_minimo", 3)
        r = await _login_e_callback(aggregatore)       # id_token con acr L2
        assert _frammento(r) == {"spid_errore": "SPID-009"}

    async def test_richiesta_l1_alzata_al_minimo(self, aggregatore) -> None:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="https://api.verumdoc.it") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb", "livello": 1})
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["client_id"] == [CLIENT_ID]
        assert q["acr_values"] == ["https://www.spid.gov.it/SpidL2"]

    async def test_spid_level_del_token_dall_acr(self, utente_associato, redis_finto) -> None:
        """Il browser chiede L2, il gestore autentica a L3: nel token c'è 3."""
        from app.auth.jwt_handler import verify_access_token

        utente_associato.claims_extra = {"acr": "https://www.spid.gov.it/SpidL3"}
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="https://api.verumdoc.it",
                               follow_redirects=False) as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb", "livello": 2})
            q = parse_qs(urlparse(r.headers["location"]).query)
            utente_associato.nonce = q["nonce"][0]
            r = await c.get("/api/v1/auth/spid/callback",
                            params={"code": "codice", "state": q["state"][0]})
            codice = _frammento(r)["spid_codice"]
            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})
        assert s.status_code == 200, s.text
        # R05 (VD-C-079): token nel cookie HttpOnly della sessione, non nel corpo
        assert verify_access_token(s.cookies["vd_sessione"])["spid_level"] == 3


async def test_mock_sotto_il_minimo_spid_009(redis_finto, login_sviluppo) -> None:
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://localhost:5173") as c:
        r = await c.get("/api/v1/auth/spid/login",
                        params={"provider": "spid", "redirect_uri": "http://localhost:5173/login"})
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "livello": 1})
    assert r.status_code == 303
    assert parse_qs(urlparse(r.headers["location"]).fragment) == {"spid_errore": ["SPID-009"]}


async def test_spid_010_non_rivela_gli_indirizzi_ammessi() -> None:
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="https://api.verumdoc.it") as c:
        r = await c.get("/api/v1/auth/spid/login",
                        params={"redirect_uri": "https://attaccante.example/cb"})
    testo = r.text + r.headers.get("location", "")
    assert "spid_errore=SPID-010" in testo
    for ammesso in spid._get_allowed_uris():
        assert ammesso not in testo
