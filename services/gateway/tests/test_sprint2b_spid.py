# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/tests/test_sprint2b_spid.py
Sprint 2B FIX-016: SPID state in Redis (non più in-memory dict).
Sprint 2B FIX-017: redirect_uri whitelist (Open Redirect prevention).
"""
from __future__ import annotations

import pytest


class TestSPIDRedisState:
    """FIX-016: state SPID in Redis, monouso (GETDEL atomico)."""

    async def test_state_salvato_e_letto_una_sola_volta(self, redis_finto):
        from app.auth.spid import _get_state, _set_state

        await _set_state("s1", {"nonce": "n1"})
        assert await _get_state("s1") == {"nonce": "n1"}
        assert await _get_state("s1") is None

    async def test_state_inesistente(self, redis_finto):
        from app.auth.spid import _get_state

        assert await _get_state("mai-visto") is None

    def test_state_prefix_defined(self):
        """Deve avere un prefix per le chiavi Redis."""
        from app.auth.spid import _STATE_PREFIX
        assert _STATE_PREFIX.startswith("spid:")

    def test_state_ttl_defined(self):
        """Deve avere un TTL ragionevole (< 10 minuti)."""
        from app.auth.spid import _STATE_TTL
        assert 60 <= _STATE_TTL <= 600

    async def test_login_salva_state_con_nonce(self, redis_finto, login_sviluppo):
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "http://localhost:5173/cb"})
        assert r.status_code in (302, 307)
        assert len(redis_finto.dati) == 1
        import json
        dati = json.loads(next(iter(redis_finto.dati.values())))
        assert len(dati["nonce"]) >= 32
        assert dati["redirect_uri"] == "http://localhost:5173/cb"


class TestSPIDRedirectWhitelist:
    """FIX-017: redirect_uri deve essere nella whitelist."""

    def test_whitelist_dev_exists(self):
        """Deve esistere una whitelist per dev."""
        from app.auth.spid import ALLOWED_REDIRECT_URIS_DEV
        assert len(ALLOWED_REDIRECT_URIS_DEV) > 0

    def test_whitelist_prod_exists(self):
        """Deve esistere una whitelist per produzione."""
        from app.auth.spid import ALLOWED_REDIRECT_URIS_PROD
        assert len(ALLOWED_REDIRECT_URIS_PROD) > 0

    def test_whitelist_prod_is_https(self):
        """In produzione tutti gli URI devono essere HTTPS."""
        from app.auth.spid import ALLOWED_REDIRECT_URIS_PROD
        for uri in ALLOWED_REDIRECT_URIS_PROD:
            assert uri.startswith("https://"), (
                f"URI produzione non HTTPS: {uri}"
            )

    def test_whitelist_dev_is_localhost(self):
        """In dev tutti gli URI devono essere localhost."""
        from app.auth.spid import ALLOWED_REDIRECT_URIS_DEV
        for uri in ALLOWED_REDIRECT_URIS_DEV:
            assert "localhost" in uri, (
                f"URI dev non localhost: {uri}"
            )

    def test_validate_redirect_rejects_unknown(self):
        """URI non in whitelist deve essere rifiutato."""
        from app.auth.spid import RitornoNonAmmesso, _validate_redirect_uri
        with pytest.raises(RitornoNonAmmesso):  # g4: diventa redirect SPID-010
            _validate_redirect_uri("https://evil.com/steal-token")

    def test_validate_redirect_accepts_localhost(self):
        """URI localhost dev deve essere accettato."""
        from app.auth.spid import _validate_redirect_uri
        # Non deve lanciare eccezione
        _validate_redirect_uri("http://localhost:5173/cb")

    def test_validate_redirect_rejects_similar(self):
        """URI simile ma non identico deve essere rifiutato."""
        from app.auth.spid import RitornoNonAmmesso, _validate_redirect_uri
        with pytest.raises(RitornoNonAmmesso):
            _validate_redirect_uri("http://localhost:5173/callback")

    def test_validate_redirect_rejects_javascript(self):
        """URI javascript: deve essere rifiutato."""
        from app.auth.spid import RitornoNonAmmesso, _validate_redirect_uri
        with pytest.raises(RitornoNonAmmesso):
            _validate_redirect_uri("javascript:alert(1)")

    async def test_login_rifiuta_redirect_esterno(self, redis_finto):
        """Il login valida redirect_uri prima di qualsiasi altra operazione."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/auth/spid/login",
                            params={"redirect_uri": "https://evil.com/steal-token"})
        # g4: ritorno alla pagina di accesso (BASE_URL/login#spid_errore=SPID-010)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/login#spid_errore=SPID-010")
        assert "evil.com" not in r.headers["location"]
        assert redis_finto.dati == {}

    @pytest.mark.parametrize("env", ["production", "PRODUCTION", "staging", "prod"])
    def test_localhost_non_ammesso_fuori_da_sviluppo(self, monkeypatch, env):
        """Gli URI localhost valgono solo in development/test (case-insensitive)."""
        from app.auth.spid import RitornoNonAmmesso, _validate_redirect_uri
        from app.config import settings

        monkeypatch.setattr(settings, "env", env)
        with pytest.raises(RitornoNonAmmesso):
            _validate_redirect_uri("http://localhost:5173/cb")
        _validate_redirect_uri("https://app.verumdoc.it/cb")
