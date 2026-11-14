# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R02 (g2-auth) — chiavi API: una chiave generata da ONBOARDING autentica davvero
sul GATEWAY (prima mai: sha256+8 caratteri contro bcrypt+10 caratteri).

Prova end-to-end su PostgreSQL reale (tests/infra_reale.py):
  1. il codice di onboarding (services/onboarding, eseguito in un processo a
     parte con il suo pacchetto `app`) genera la chiave con POST
     /api/v1/onboarding/tenant/{id}/api-keys, autenticato con un token del gateway;
  2. il gateway accetta quella chiave (X-Api-Key, oppure X-Api-Key-Id +
     X-Api-Key-Secret come l'SDK) su un endpoint protetto e su require_scope
     («versamento:ud»), registra ultimo_uso;
  3. la stessa chiave è rifiutata dopo la revoca, con un pepper diverso, con un
     identificativo di un'altra chiave, se scaduta o con il cliente sospeso.

L'interprete per onboarding è VD_ONBOARDING_PYTHON se impostata (es. il venv
di onboarding), altrimenti quello dei test del gateway.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.auth import api_key
from app.auth.dependencies import get_redis, require_scope
from app.auth.jwt_handler import chiavi_jwt, create_access_token
from app.main import app
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

PEPPER = "pepper-condiviso-onboarding-gateway-0123456789abcdef"
SERVIZI = Path(__file__).resolve().parents[2]
RADICE = SERVIZI.parent

_SCRIPT_ONBOARDING = """
import asyncio, json, os, sys
from httpx import ASGITransport, AsyncClient
from app.main import app
from libs.shared.db import close_db, init_db

async def principale():
    await init_db(os.environ["DATABASE_URL"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://onboarding") as c:
            r = await c.post(
                f"/api/v1/onboarding/tenant/{os.environ['VD_TENANT']}/api-keys",
                json={"descrizione": "Protocollo informatico"},
                headers={"Authorization": "Bearer " + os.environ["VD_TOKEN"]},
            )
        print("VD_ESITO " + json.dumps({"status": r.status_code, "corpo": r.json()}))
    finally:
        await close_db()

asyncio.run(principale())
"""


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api_key, "_PEPPER", PEPPER.encode())
    app.dependency_overrides[get_redis] = lambda: None  # rate limit fuori dalla prova
    yield
    app.dependency_overrides.pop(get_redis, None)


async def _ente_con_responsabile(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    await sql(url,
              "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "stato, email_referente) VALUES ($1, $2, 'Comune', $3, 'ATTIVO', 'e@example.org')",
              tid, tid.hex[:16], f"t_{tid.hex[:20]}")
    await sql(url,
              "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
              "VALUES ($1, $2, 'admin@example.org', 'Anna', 'Bianchi', 'ADMIN_TENANT')",
              uid, tid)
    return tid, uid


def _genera_con_onboarding(url: str, tenant: uuid.UUID, user: uuid.UUID) -> dict[str, Any]:
    """Esegue il VERO endpoint di onboarding in un processo separato."""
    token = create_access_token(tenant_id=str(tenant), user_id=str(user), ruolo="ADMIN_TENANT",
                                scopes=["admin:tenant"])
    servizio = SERVIZI / "onboarding"
    ambiente = {k: v for k, v in os.environ.items()
                if k.upper() not in {"JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY", "API_KEY_PEPPER"}}
    ambiente.update({
        "ENV": "test",
        "PYTHONPATH": os.pathsep.join([str(servizio), str(RADICE)]),
        "DATABASE_URL": url,
        "JWT_PUBLIC_KEY": chiavi_jwt().pubblica_pem,
        "API_KEY_PEPPER": PEPPER,
        "VD_TENANT": str(tenant),
        "VD_TOKEN": token,
    })
    interprete = os.environ.get("VD_ONBOARDING_PYTHON") or sys.executable
    esito = subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [interprete, "-c", _SCRIPT_ONBOARDING], cwd=servizio, env=ambiente,
        capture_output=True, text=True, timeout=120,
    )
    assert esito.returncode == 0, esito.stderr[-3000:]
    riga = [x for x in esito.stdout.splitlines() if x.startswith("VD_ESITO ")]
    assert riga, esito.stdout[-2000:] + esito.stderr[-2000:]
    risultato: dict[str, Any] = json.loads(riga[-1].removeprefix("VD_ESITO "))
    assert risultato["status"] == 201, risultato
    return risultato["corpo"]


async def _usa(intestazioni: dict[str, str]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway") as c:
        return await c.get(f"/api/v1/versamento/{uuid.uuid4()}", headers=intestazioni)


class TestChiaveDaOnboardingAlGateway:
    async def test_end_to_end(self, db_url) -> None:
        tenant, user = await _ente_con_responsabile(db_url)
        generata = _genera_con_onboarding(db_url, tenant, user)
        chiave, ident = generata["api_key"], generata["api_key_id"]

        # Autenticata: l'endpoint risponde 404 «PdV non trovato per questo
        # cliente», cioè ha riconosciuto il cliente della chiave (non 401).
        r = await _usa({"X-Api-Key": chiave})
        assert r.status_code == 404, r.text
        r = await _usa({"X-Api-Key-Id": ident, "X-Api-Key-Secret": chiave})
        assert r.status_code == 404, r.text
        segreto = chiave.split("_", 2)[2]
        r = await _usa({"X-Api-Key-Id": ident, "X-Api-Key-Secret": segreto})
        assert r.status_code == 404, r.text

        # Il token interno porta cliente, ruolo e permessi della chiave
        token = await api_key.validate_api_key(None, chiave)
        from app.auth.jwt_handler import verify_access_token
        payload = verify_access_token(token)
        assert payload["tenant_id"] == str(tenant) and payload["user_id"] == str(user)
        assert payload["ruolo"] == "API_SYSTEM" and payload["api_key_id"] == generata["id"]
        assert "versamento:ud" in payload["scopes"]

        # require_scope("versamento:ud") passa con la sola chiave
        prova = FastAPI()

        @prova.get("/versa")
        async def _versa(u: dict = Depends(require_scope("versamento:ud"))) -> dict:  # noqa: B008
            return {"tenant": u["tenant_id"]}

        prova.dependency_overrides[get_redis] = lambda: None
        async with AsyncClient(transport=ASGITransport(app=prova), base_url="http://p") as c:
            v = await c.get("/versa", headers={"X-Api-Key": chiave})
        assert v.status_code == 200 and v.json() == {"tenant": str(tenant)}

        uso = await sql(db_url, "SELECT ultimo_uso FROM public.api_keys WHERE id = $1",
                        uuid.UUID(generata["id"]))
        assert uso[0]["ultimo_uso"] is not None

    async def test_rifiuti(self, db_url, monkeypatch) -> None:
        tenant, user = await _ente_con_responsabile(db_url)
        generata = _genera_con_onboarding(db_url, tenant, user)
        chiave = generata["api_key"]
        altra = _genera_con_onboarding(db_url, tenant, user)

        # identificativo di un'altra chiave
        r = await _usa({"X-Api-Key-Id": altra["api_key_id"], "X-Api-Key-Secret": chiave})
        assert r.status_code == 401
        # segreto alterato / forma non valida / nessuna credenziale
        assert (await _usa({"X-Api-Key": chiave[:-2] + "zz"})).status_code == 401
        assert (await _usa({"X-Api-Key": "sk_test"})).status_code == 401
        assert (await _usa({})).status_code == 401
        # pepper diverso nel gateway
        monkeypatch.setattr(api_key, "_PEPPER", b"x" * 64)
        assert (await _usa({"X-Api-Key": chiave})).status_code == 401
        monkeypatch.setattr(api_key, "_PEPPER", PEPPER.encode())
        assert (await _usa({"X-Api-Key": chiave})).status_code == 404

        # scaduta
        await sql(db_url, "UPDATE public.api_keys SET scadenza = now() - interval '1 day' "
                          "WHERE id = $1", uuid.UUID(generata["id"]))
        r = await _usa({"X-Api-Key": chiave})
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-002"
        await sql(db_url, "UPDATE public.api_keys SET scadenza = NULL WHERE id = $1",
                  uuid.UUID(generata["id"]))
        # cliente sospeso
        await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", tenant)
        assert (await _usa({"X-Api-Key": chiave})).status_code == 401
        await sql(db_url, "UPDATE public.tenants SET stato = 'ATTIVO' WHERE id = $1", tenant)
        # revocata
        await sql(db_url, "UPDATE public.api_keys SET attiva = false WHERE id = $1",
                  uuid.UUID(generata["id"]))
        assert (await _usa({"X-Api-Key": chiave})).status_code == 401
        # l'altra chiave resta valida
        assert (await _usa({"X-Api-Key": altra["api_key"]})).status_code == 404

    async def test_senza_pepper_503_esplicito(self, db_url, monkeypatch) -> None:
        monkeypatch.setattr(api_key, "_PEPPER", None)
        r = await _usa({"X-Api-Key": "vd_abcdefghij_" + "a" * 43})
        assert r.status_code == 503
        assert r.json()["codice"] == "AUTH-030"


class TestAvvioGateway:
    @staticmethod
    def _avvia(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
        servizio = SERVIZI / "gateway"
        ambiente = {k: v for k, v in os.environ.items()
                    if k.upper() not in {"API_KEY_PEPPER", "ENV", "JWT_PRIVATE_KEY",
                                         "JWT_PUBLIC_KEY"}}
        ambiente.update({"ENV": "production", "JWT_PRIVATE_KEY": priv,
                         "PYTHONPATH": os.pathsep.join([str(servizio), str(RADICE)])})
        ambiente.update(extra)
        return subprocess.run(  # noqa: S603 - comando fisso
            [sys.executable, "-c", "import app.auth.dependencies"], cwd=servizio,
            env=ambiente, capture_output=True, text=True, timeout=120,
        )

    def test_produzione_senza_pepper_non_parte(self) -> None:
        esito = self._avvia({})
        assert esito.returncode != 0
        assert "API_KEY_PEPPER mancante" in esito.stderr

    def test_produzione_con_pepper_parte(self) -> None:
        esito = self._avvia({"API_KEY_PEPPER": PEPPER})
        assert esito.returncode == 0, esito.stderr[-2000:]
