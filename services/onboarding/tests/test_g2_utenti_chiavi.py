# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R02 (g2-auth) — utenti con codice fiscale e chiavi API nel formato unico, su
PostgreSQL reale (tests/infra_reale.py, VD_TEST_DATABASE_URL).

Verifica:
  - creazione: 201 con l'utente davvero salvato; email o codice fiscale già
    presenti nell'ente → 409 (prima 201 senza creare); stesso codice fiscale in
    un altro ente → ammesso (unicità per cliente, REGOLE §14.12);
  - codice fiscale validato (carattere di controllo) e normalizzato;
  - modifica del codice fiscale, anche con conflitto 409;
  - chiave API: impronta HMAC con pepper e identificativo di 10 caratteri,
    permessi di versamento, responsabile obbligatorio, 503 senza pepper;
  - errore del database: 503 con riferimento, mai il testo SQL al client.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient

from libs.shared.security.chiavi_api import identificativo_da_chiave, verifica_chiave
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

PEPPER = b"pepper-di-prova-" * 4
CF = "FTZPRV00A01Z999V"


async def _ente(url: str, nome: str = "Comune di Prova") -> str:
    tid = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "stato, email_referente) VALUES ($1, $2, $3, $4, 'ATTIVO', 'e@example.org')",
              tid, tid.hex[:16], nome, f"t_{tid.hex[:20]}")
    return str(tid)


def _tok(tenant_id: str, ruolo: str = "ADMIN_TENANT", user_id: str = "u") -> str:
    return create_access_token(tenant_id=tenant_id, user_id=user_id, ruolo=ruolo,
                               scopes=["admin:tenant"])


async def _chiama(metodo: str, url: str, tok: str, corpo: Any = None) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(metodo, url, json=corpo,
                               headers={"Authorization": f"Bearer {tok}"})


def _utente(**extra: Any) -> dict[str, Any]:
    corpo = {"email": "Mario.Rossi@Example.org", "nome": "Mario", "cognome": "Rossi",
             "ruolo": "OPERATORE", "codice_fiscale": "tinit-ftzprv00a01z999v"}
    corpo.update(extra)
    return corpo


class TestUtenti:
    async def test_creazione_salva_davvero_e_lista_completa(self, db_url) -> None:
        tid = await _ente(db_url)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti", _tok(tid), _utente())
        assert r.status_code == 201, r.text
        creato = r.json()
        assert creato["email"] == "mario.rossi@example.org"
        assert creato["codice_fiscale"] == CF  # normalizzato
        righe = await sql(db_url, "SELECT email, nome, cognome, codice_fiscale, ruolo::text "
                                  "FROM public.users WHERE tenant_id = $1", uuid.UUID(tid))
        assert [tuple(r) for r in righe] == [
            ("mario.rossi@example.org", "Mario", "Rossi", CF, "OPERATORE")]

        lista = await _chiama("GET", f"/api/v1/onboarding/tenant/{tid}/utenti", _tok(tid))
        assert lista.status_code == 200
        voce = lista.json()["items"][0]
        assert {"id", "email", "nome", "cognome", "codice_fiscale", "ruolo", "attivo",
                "created_at"} <= set(voce)
        assert "nota" not in lista.json()

    async def test_email_doppia_nell_ente_409(self, db_url) -> None:
        tid = await _ente(db_url)
        url = f"/api/v1/onboarding/tenant/{tid}/utenti"
        assert (await _chiama("POST", url, _tok(tid), _utente())).status_code == 201
        r = await _chiama("POST", url, _tok(tid), _utente(codice_fiscale=None))
        assert r.status_code == 409
        assert r.json()["codice"] == "ONB-409"
        assert "email" in r.json()["title"]
        assert len(await sql(db_url, "SELECT 1 FROM public.users WHERE tenant_id = $1",
                             uuid.UUID(tid))) == 1

    async def test_codice_fiscale_doppio_nell_ente_409(self, db_url) -> None:
        tid = await _ente(db_url)
        url = f"/api/v1/onboarding/tenant/{tid}/utenti"
        assert (await _chiama("POST", url, _tok(tid), _utente())).status_code == 201
        r = await _chiama("POST", url, _tok(tid), _utente(email="altro@example.org"))
        assert r.status_code == 409
        assert "codice fiscale" in r.json()["title"]

    async def test_stesso_codice_fiscale_in_altro_ente_ammesso(self, db_url) -> None:
        a, b = await _ente(db_url, "Comune A"), await _ente(db_url, "Comune B")
        sa = _tok(a, "SUPER_ADMIN")
        for tid in (a, b):
            r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti", sa, _utente())
            assert r.status_code == 201, r.text

    @pytest.mark.parametrize("cf", ["RSSMRA80A01H501X", "12345678901", "RSSMRA80A01H50"])
    async def test_codice_fiscale_non_valido_422(self, db_url, cf: str) -> None:
        tid = await _ente(db_url)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti", _tok(tid),
                          _utente(codice_fiscale=cf))
        assert r.status_code == 422
        assert not await sql(db_url, "SELECT 1 FROM public.users WHERE tenant_id = $1",
                             uuid.UUID(tid))

    async def test_ente_inesistente_404(self, db_url) -> None:
        tid = str(uuid.uuid4())
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti",
                          _tok(tid, "SUPER_ADMIN"), _utente())
        assert r.status_code == 404

    async def test_modifica_codice_fiscale_e_conflitto(self, db_url) -> None:
        tid = await _ente(db_url)
        url = f"/api/v1/onboarding/tenant/{tid}/utenti"
        primo = (await _chiama("POST", url, _tok(tid), _utente())).json()
        secondo = (await _chiama("POST", url, _tok(tid),
                                 _utente(email="b@example.org", codice_fiscale=None))).json()
        assert secondo["codice_fiscale"] is None

        # il codice fiscale di un utente esistente lo cambia solo il SUPER_ADMIN
        # (revisione di sicurezza A1; i casi negativi in test_g5_gestione_utenti.py)
        sa = _tok(tid, "SUPER_ADMIN")
        r = await _chiama("PUT", f"{url}/{secondo['id']}", _tok(tid),
                          {"codice_fiscale": "MRTMTT25D09F205Z"})
        assert r.status_code == 403
        r = await _chiama("PUT", f"{url}/{secondo['id']}", sa,
                          {"codice_fiscale": "MRTMTT25D09F205Z"})
        assert r.status_code == 200
        r = await _chiama("PUT", f"{url}/{secondo['id']}", sa, {"codice_fiscale": CF})
        assert r.status_code == 409
        r = await _chiama("PUT", f"{url}/{primo['id']}", sa, {"codice_fiscale": None})
        assert r.status_code == 200
        righe = await sql(db_url, "SELECT email, codice_fiscale FROM public.users "
                                  "WHERE tenant_id = $1 ORDER BY email", uuid.UUID(tid))
        assert [tuple(x) for x in righe] == [("b@example.org", "MRTMTT25D09F205Z"),
                                             ("mario.rossi@example.org", None)]

    async def test_modifica_utente_di_altro_ente_404(self, db_url) -> None:
        a, b = await _ente(db_url), await _ente(db_url)
        creato = (await _chiama("POST", f"/api/v1/onboarding/tenant/{b}/utenti",
                                _tok(b), _utente())).json()
        r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{a}/utenti/{creato['id']}",
                          _tok(a), {"nome": "Altro"})
        assert r.status_code == 404


class TestChiaviApi:
    @pytest.fixture(autouse=True)
    def _pepper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.routers import utenti

        monkeypatch.setattr(utenti, "_PEPPER", PEPPER)

    async def _responsabile(self, url: str, tid: str) -> str:
        # ADMIN_TENANT (creato dal SUPER_ADMIN): i test usano un token
        # ADMIN_TENANT con questo user_id, e la revoca (N2) rifiuta un token il
        # cui ruolo differisce da quello dell'utente nel database.
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti",
                          _tok(tid, "SUPER_ADMIN"), _utente(ruolo="ADMIN_TENANT"))
        return str(r.json()["id"])

    async def test_generata_con_impronta_hmac_e_identificativo(self, db_url) -> None:
        tid = await _ente(db_url)
        uid = await self._responsabile(db_url, tid)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/api-keys",
                          _tok(tid, user_id=uid), {"descrizione": "Protocollo"})
        assert r.status_code == 201, r.text
        corpo = r.json()
        chiave = corpo["api_key"]
        assert identificativo_da_chiave(chiave) == corpo["key_prefix"]
        assert corpo["api_key_id"] == f"vd_{corpo['key_prefix']}"
        riga = (await sql(db_url, "SELECT key_prefix, key_hash, permessi::text, nome, user_id, "
                                  "attiva FROM public.api_keys WHERE id = $1",
                          uuid.UUID(corpo["id"])))[0]
        assert riga["key_prefix"] == corpo["key_prefix"] and len(riga["key_prefix"]) == 10
        assert riga["key_hash"].startswith("hmac-sha256$")
        assert chiave not in riga["key_hash"]
        assert verifica_chiave(chiave, riga["key_hash"], PEPPER)
        assert "versamento:ud" in riga["permessi"]
        assert riga["nome"] == "Protocollo" and str(riga["user_id"]) == uid and riga["attiva"]

        lista = (await _chiama("GET", f"/api/v1/onboarding/tenant/{tid}/api-keys",
                               _tok(tid))).json()
        assert lista["items"][0]["descrizione"] == "Protocollo"
        assert "api_key" not in lista["items"][0]

    async def test_chiamante_non_registrato_deve_indicare_il_responsabile(self, db_url) -> None:
        tid = await _ente(db_url)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/api-keys",
                          _tok(tid, user_id=str(uuid.uuid4())), {"descrizione": "x"})
        assert r.status_code == 422
        uid = await self._responsabile(db_url, tid)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/api-keys",
                          _tok(tid, user_id=str(uuid.uuid4())), {"user_id": uid})
        assert r.status_code == 201

    async def test_responsabile_di_altro_ente_404(self, db_url) -> None:
        a, b = await _ente(db_url), await _ente(db_url)
        uid_b = await self._responsabile(db_url, b)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{a}/api-keys",
                          _tok(a, "SUPER_ADMIN"), {"user_id": uid_b})
        assert r.status_code == 404

    async def test_senza_pepper_503_esplicito(self, db_url, monkeypatch) -> None:
        from app.routers import utenti

        monkeypatch.setattr(utenti, "_PEPPER", None)
        tid = await _ente(db_url)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/api-keys", _tok(tid), {})
        assert r.status_code == 503
        assert r.json()["codice"] == "ONB-530"


class TestErroriDatabase:
    async def test_errore_sql_503_con_riferimento_senza_testo_sql(self, db_url) -> None:
        tid = await _ente(db_url)
        await sql(db_url, "ALTER TABLE public.users RENAME COLUMN cognome TO cognome_x")
        r = await _chiama("GET", f"/api/v1/onboarding/tenant/{tid}/utenti", _tok(tid))
        assert r.status_code == 503
        corpo = r.json()
        assert corpo["codice"] == "ONB-503" and corpo["id_richiesta"] == r.headers["x-request-id"]
        assert "cognome" not in r.text and "SELECT" not in r.text.upper()
