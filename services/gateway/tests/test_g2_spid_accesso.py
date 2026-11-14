# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R02 (g2-auth) — accesso SPID/CIE reale: identità verificata → utente → token,
su PostgreSQL reale (tests/infra_reale.py). L'aggregatore è il mock di sviluppo
(che salta SOLO la verifica dell'id_token, provata in test_vdc006_spid_id_token.py);
associazione, codice monouso e scambio sono quelli del login reale.

Casi: utente attivo → token con tenant/ruolo/scope dal database e ultimo_login
aggiornato; utente disattivato, cliente SOSPESO o DISATTIVATO, utente API_SYSTEM
→ SPID-008; stessa persona in due enti → scelta esplicita (§14.12) e un
tenant_id non tra i suoi → 403 senza dettagli; il token emesso è accettato da
un endpoint protetto del gateway.
"""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from app.auth.dependencies import get_redis
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

CF = "FTZPRV00A01Z999V"


@pytest.fixture(autouse=True)
def _senza_rate_limit():
    """Il limitatore di richieste usa Redis: fuori da questa prova."""
    app.dependency_overrides[get_redis] = lambda: None
    yield
    app.dependency_overrides.pop(get_redis, None)


async def _ente(url: str, nome: str, stato: str = "ATTIVO") -> uuid.UUID:
    tid = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "stato, piano, email_referente) VALUES ($1, $2, $3, $4, "
              "CAST($5 AS public.tenant_stato), 'business', 'e@example.org')",
              tid, tid.hex[:16], nome, f"t_{tid.hex[:20]}", stato)
    return tid


async def _utente(url: str, tenant: uuid.UUID, *, ruolo: str = "OPERATORE",
                  attivo: bool = True, cf: str = CF) -> uuid.UUID:
    uid = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo, "
              "codice_fiscale) VALUES ($1, $2, $3, 'Mario', 'Rossi', "
              "CAST($4 AS public.user_ruolo), $5, $6)",
              uid, tenant, f"{uid.hex[:8]}@example.org", ruolo, attivo, cf)
    return uid


async def _accedi(c: AsyncClient, cf: str = CF) -> dict[str, str]:
    r = await c.get("/api/v1/auth/spid/login",
                    params={"provider": "spid", "redirect_uri": "http://localhost:5173/login"})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "cf": cf})
    assert r.status_code == 303
    return {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).fragment).items()}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:5173")


class TestAccessoSpidReale:
    async def test_utente_attivo_riceve_token_con_dati_dal_database(
        self, db_url, redis_finto, login_sviluppo
    ):
        ente = await _ente(db_url, "Comune di Prova")
        uid = await _utente(db_url, ente, ruolo="ADMIN_TENANT")
        async with _client() as c:
            esito = await _accedi(c)
            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": esito["spid_codice"]})
            assert s.status_code == 200, s.text
            corpo = s.json()
            # R05 (VD-C-079): il token NON è nel corpo ma nel cookie HttpOnly, che
            # il browser rimanda da solo: apre un endpoint protetto del gateway
            assert "access_token" not in corpo and corpo["sessione"] == "cookie"
            assert "httponly" in s.headers["set-cookie"].lower()
            protetto = await c.get("/api/v1/lifecycle/scadenze")
        utente = corpo["utente"]
        assert utente["tenant_id"] == str(ente) and utente["user_id"] == str(uid)
        assert utente["ruolo"] == "ADMIN_TENANT" and "admin:tenant" in utente["scopes"]
        assert utente["ragione_sociale"] == "Comune di Prova" and utente["piano"] == "business"
        assert protetto.status_code not in (401, 403), protetto.text
        riga = await sql(db_url, "SELECT ultimo_login FROM public.users WHERE id = $1", uid)
        assert riga[0]["ultimo_login"] is not None

    async def test_utente_disattivato_cliente_sospeso_o_sistema_rifiutati(
        self, db_url, redis_finto, login_sviluppo
    ):
        attivo = await _ente(db_url, "Ente attivo")
        await _utente(db_url, attivo, attivo=False)
        await _utente(db_url, attivo, ruolo="API_SYSTEM", cf="MRTMTT25D09F205Z")
        for stato in ("SOSPESO", "DISATTIVATO"):
            await _utente(db_url, await _ente(db_url, f"Ente {stato}", stato))
        async with _client() as c:
            assert await _accedi(c) == {"spid_errore": "SPID-008"}
            assert await _accedi(c, "MRTMTT25D09F205Z") == {"spid_errore": "SPID-008"}

    async def test_cliente_in_prova_ammesso(self, db_url, redis_finto, login_sviluppo):
        await _utente(db_url, await _ente(db_url, "Ente in prova", "TRIAL"))
        async with _client() as c:
            assert "spid_codice" in await _accedi(c)

    async def test_codice_fiscale_sconosciuto(self, db_url, redis_finto, login_sviluppo):
        async with _client() as c:
            assert await _accedi(c, "MRTMTT25D09F205Z") == {"spid_errore": "SPID-008"}

    async def test_due_enti_scelta_esplicita_e_ente_estraneo(
        self, db_url, redis_finto, login_sviluppo
    ):
        a = await _ente(db_url, "Comune A")
        b = await _ente(db_url, "Comune B")
        estraneo = await _ente(db_url, "Comune C")
        await _utente(db_url, a)
        uid_b = await _utente(db_url, b, ruolo="VIEWER")
        async with _client() as c:
            esito = await _accedi(c)
            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": esito["spid_codice"]})
            scelta = s.json()
            assert scelta["esito"] == "scelta_ente"
            assert [e["ragione_sociale"] for e in scelta["enti"]] == ["Comune A", "Comune B"]
            # un ente in cui la persona non è utente: 403 senza dire se esiste
            r = await c.post("/api/v1/auth/spid/scambio",
                             json={"codice": scelta["codice"], "tenant_id": str(estraneo)})
            assert r.status_code == 403
            assert "Comune C" not in r.text
            # il codice è stato consumato anche dal tentativo sbagliato
            esito = await _accedi(c)
            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": esito["spid_codice"]})
            t = await c.post("/api/v1/auth/spid/scambio",
                             json={"codice": s.json()["codice"], "tenant_id": str(b)})
        assert t.status_code == 200
        assert t.json()["utente"]["user_id"] == str(uid_b)
        assert t.json()["utente"]["scopes"] == ["read"]
