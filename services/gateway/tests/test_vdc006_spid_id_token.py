# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-006 — verifica dell'id_token SPID (firma via JWKS, iss, aud, exp, nonce di sessione).

Nessuna rete: l'aggregatore (endpoint /token e /jwks) è simulato con
httpx.MockTransport e una chiave RSA generata nel test.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from app.auth import spid
from app.config import settings
from app.main import app
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

AGGREGATORE = "https://aggregatore.test"
ISS = "https://aggregatore.test/oidc"
CLIENT_ID = "verumdoc-client"
KID = "chiave-1"


def _jwk_pubblica(chiave: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk: dict[str, Any] = jwt.algorithms.RSAAlgorithm.to_jwk(chiave.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


class AggregatoreFinto:
    """Simula /token e /jwks dell'aggregatore."""

    def __init__(self) -> None:
        self.chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwks: dict[str, Any] = {"keys": [_jwk_pubblica(self.chiave, KID)]}
        self.nonce = ""
        self.claims_extra: dict[str, Any] = {}
        self.chiave_firma: rsa.RSAPrivateKey | None = None
        self.kid_firma: str | None = KID
        self.alg = "RS256"
        self.richieste_jwks = 0
        self.id_token_fisso: str | None = None

    def id_token(self) -> str:
        if self.id_token_fisso is not None:
            return self.id_token_fisso
        ora = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISS, "aud": CLIENT_ID, "sub": "persona-1", "iat": ora, "exp": ora + 300,
            "nonce": self.nonce, "fiscal_code": "TINIT-FTZPRV00A01Z999V",
            # livello dichiarato dal gestore (revisione B3: letto dall'id_token)
            "acr": "https://www.spid.gov.it/SpidL2",
        }
        claims.update(self.claims_extra)
        headers = {"kid": self.kid_firma} if self.kid_firma else {}
        return jwt.encode(claims, self.chiave_firma or self.chiave, algorithm=self.alg,
                          headers=headers)

    def gestisci(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token" and request.method == "POST":
            return httpx.Response(200, json={"id_token": self.id_token(),
                                             "access_token": "x"})
        if request.url.path == "/jwks":
            self.richieste_jwks += 1
            return httpx.Response(200, json=self.jwks)
        return httpx.Response(404)


@pytest.fixture
def aggregatore(monkeypatch: pytest.MonkeyPatch, redis_finto: Any) -> AggregatoreFinto:
    finto = AggregatoreFinto()
    monkeypatch.setattr(spid, "_trasporto_http", httpx.MockTransport(finto.gestisci))
    monkeypatch.setattr(spid, "_cache_jwks", {})
    monkeypatch.setattr(settings, "verumdoc_dev_auth", False)
    monkeypatch.setattr(settings, "spid_aggregator_url", AGGREGATORE)
    monkeypatch.setattr(settings, "spid_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "spid_issuer", ISS)
    monkeypatch.setattr(settings, "spid_jwks_url", f"{AGGREGATORE}/jwks")
    monkeypatch.setattr(settings, "base_url", "https://api.verumdoc.it")
    finto.cf_cercati = []

    async def _nessun_utente(cf: str) -> list[Any]:
        # Associazione persona→utente provata su PostgreSQL in test_g2_spid_accesso.py
        finto.cf_cercati.append(cf)
        return []

    monkeypatch.setattr(spid, "utenti_per_codice_fiscale", _nessun_utente)
    return finto


def _frammento(r: httpx.Response) -> dict[str, str]:
    """Esito del ritorno al frontend: 303 con i parametri nel frammento (#)."""
    assert r.status_code == 303, r.text
    url = urlparse(r.headers["location"])
    assert url.query == ""  # niente nella query string
    return {k: v[0] for k, v in parse_qs(url.fragment).items()}


async def _login_e_callback(
    aggregatore: AggregatoreFinto, *, con_cookie: bool = True
) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.verumdoc.it",
                           follow_redirects=False) as c:
        r = await c.get("/api/v1/auth/spid/login",
                        params={"redirect_uri": "https://app.verumdoc.it/cb", "livello": 2})
        assert r.status_code in (302, 307), r.text
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["client_id"] == [CLIENT_ID]
        aggregatore.nonce = q["nonce"][0]
        if not con_cookie:
            c.cookies.clear()
        return await c.get("/api/v1/auth/spid/callback",
                           params={"code": "codice", "state": q["state"][0]})


class TestCallbackSpid:
    async def test_id_token_valido_verificato_ma_utente_non_associato(self, aggregatore):
        """Firma, iss, aud, exp e nonce corretti: verifica superata; nessun token
        emesso perché l'associazione persona→cliente non esiste (SPID-008)."""
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-008"}  # nessun token né codice
        assert aggregatore.cf_cercati == ["FTZPRV00A01Z999V"]  # TINIT- tolto, validato
        assert r.headers["location"].startswith("https://app.verumdoc.it/cb#")

    async def test_firma_di_altra_chiave(self, aggregatore):
        aggregatore.chiave_firma = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-005"}

    async def test_nonce_di_altra_sessione(self, aggregatore):
        aggregatore.claims_extra = {"nonce": "nonce-di-un-altro-login"}
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-005"}

    @pytest.mark.parametrize("campo,valore", [
        ("aud", "altro-client"),
        ("iss", "https://idp-falso.test"),
        ("exp", int(time.time()) - 3600),
    ])
    async def test_claim_non_validi(self, aggregatore, campo, valore):
        aggregatore.claims_extra = {campo: valore}
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-005"}

    async def test_kid_sconosciuto_riscarica_jwks_e_rifiuta(self, aggregatore):
        aggregatore.kid_firma = "chiave-sconosciuta"
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-005"}
        assert aggregatore.richieste_jwks == 2  # cache + nuovo scaricamento (rotazione)

    async def test_rotazione_chiave_accettata_dopo_riscaricamento(self, aggregatore):
        """Una chiave nuova pubblicata nel JWKS è trovata riscaricando il JWKS."""
        await spid._scarica_jwks(f"{AGGREGATORE}/jwks")  # JWKS vecchio in cache
        nuova = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        aggregatore.jwks = {"keys": [_jwk_pubblica(nuova, "chiave-2")]}
        aggregatore.chiave_firma, aggregatore.kid_firma = nuova, "chiave-2"
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-008"}  # verifica superata

    async def test_token_non_firmato(self, aggregatore):
        ora = int(time.time())
        aggregatore.id_token_fisso = jwt.encode(
            {"iss": ISS, "aud": CLIENT_ID, "sub": "x", "iat": ora, "exp": ora + 60,
             "nonce": "qualsiasi"}, key=None, algorithm="none")
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-005"}

    async def test_id_token_assente(self, aggregatore):
        aggregatore.id_token_fisso = ""
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-005"}

    async def test_state_senza_cookie_di_sessione(self, aggregatore):
        r = await _login_e_callback(aggregatore, con_cookie=False)
        assert _frammento(r) == {"spid_errore": "SPID-006"}

    async def test_state_riusato(self, aggregatore):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="https://api.verumdoc.it") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb"})
            q = parse_qs(urlparse(r.headers["location"]).query)
            aggregatore.nonce = q["nonce"][0]
            primo = await c.get("/api/v1/auth/spid/callback",
                                params={"code": "c", "state": q["state"][0]})
            secondo = await c.get("/api/v1/auth/spid/callback",
                                  params={"code": "c", "state": q["state"][0]})
        assert _frammento(primo) == {"spid_errore": "SPID-008"}
        # g4: state già usato → pagina di accesso del frontend con SPID-002
        assert _frammento(secondo) == {"spid_errore": "SPID-002"}
        assert secondo.headers["location"].startswith(f"{settings.base_url}/login#")

    async def test_aggregatore_non_configurato(self, aggregatore, monkeypatch):
        monkeypatch.setattr(settings, "spid_jwks_url", "")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb"})
        # Errore mostrato per esteso dall'interfaccia: ritorno con il codice
        assert _frammento(r) == {"spid_errore": "SPID-001"}


    async def test_codice_fiscale_assente_o_non_valido(self, aggregatore):
        aggregatore.claims_extra = {"fiscal_code": "TINIT-RSSMRA80A01H501Z"}  # controllo errato
        r = await _login_e_callback(aggregatore)
        assert _frammento(r) == {"spid_errore": "SPID-007"}
        assert aggregatore.cf_cercati == []

    async def test_annullato_presso_il_gestore(self, aggregatore):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="https://api.verumdoc.it") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb"})
            q = parse_qs(urlparse(r.headers["location"]).query)
            r = await c.get("/api/v1/auth/spid/callback",
                            params={"state": q["state"][0], "error": "access_denied"})
        assert _frammento(r) == {"spid_errore": "SPID-003"}

    async def test_provider_non_ammesso(self, aggregatore):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb",
                                    "provider": "altro"})
        assert _frammento(r) == {"spid_errore": "SPID-011"}  # g4: redirect, non 422


    @pytest.mark.parametrize("env,atteso", [("production", True), ("staging", True),
                                            ("test", False)])
    async def test_cookie_di_sessione_secure_fuori_da_sviluppo(
        self, aggregatore, monkeypatch, env, atteso
    ):
        monkeypatch.setattr(settings, "env", env)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://app.verumdoc.it/cb"})
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert ("secure" in cookie) is atteso


class TestVerificaIdToken:
    async def test_restituisce_i_claim_verificati(self, aggregatore):
        aggregatore.nonce = "n-123"
        claims = await spid.verifica_id_token(aggregatore.id_token(), "n-123")
        assert claims["sub"] == "persona-1"
        assert claims["aud"] == CLIENT_ID

    async def test_nonce_atteso_vuoto_rifiutato(self, aggregatore):
        aggregatore.nonce = ""
        with pytest.raises(spid.IdTokenNonValido):
            await spid.verifica_id_token(aggregatore.id_token(), "")


class TestMockSpidSoloInSviluppo:
    async def test_mock_callback_404_senza_flag(self, redis_finto):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": "x"})
        assert r.status_code == 404

    @pytest.mark.parametrize("env", ["staging", "production", "prod"])
    async def test_mock_404_con_flag_ma_ambiente_esposto(self, redis_finto, monkeypatch, env):
        monkeypatch.setattr(settings, "verumdoc_dev_auth", True)
        monkeypatch.setattr(settings, "env", env)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": "x"})
        assert r.status_code == 404
