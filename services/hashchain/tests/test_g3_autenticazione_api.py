# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g3-backend — le API della catena richiedono un token verificato (§19.1, §14.3).

Prima: ``GET /api/v1/hashchain/status`` e ``POST /api/v1/hashchain/verifica-completa``
rispondevano a chiunque raggiungesse il servizio, e ``avviata_da``/``ruolo``
della verifica erano presi dal corpo della richiesta.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.token_di_prova import intestazioni, token

SERVIZIO = Path(__file__).resolve().parents[1]
RADICE = SERVIZIO.parents[1]
STATO = "/api/v1/hashchain/status"
VERIFICA = "/api/v1/hashchain/verifica-completa"


def _altra_chiave_privata() -> str:
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()


async def _client(headers: dict[str, str] | None = None) -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       headers=headers or {})


@pytest.mark.parametrize("percorso,metodo", [(STATO, "GET"), (VERIFICA, "POST"),
                                             (f"{VERIFICA}/{uuid.uuid4()}", "GET")])
@pytest.mark.parametrize("caso", ["assente", "non_bearer", "altra_chiave", "scaduto",
                                  "pubblico_sbagliato", "alg_none", "senza_tenant"])
async def test_token_mancante_o_non_valido_401(percorso, metodo, caso) -> None:
    if caso == "assente":
        headers = {}
    elif caso == "non_bearer":
        headers = {"Authorization": f"Basic {token('SUPER_ADMIN')}"}
    elif caso == "altra_chiave":
        headers = intestazioni("SUPER_ADMIN", chiave_privata=_altra_chiave_privata())
    elif caso == "scaduto":
        headers = intestazioni("SUPER_ADMIN", durata=-120)
    elif caso == "pubblico_sbagliato":
        import jwt
        from app.auth.jwt_handler import chiavi_jwt
        tok = jwt.encode({"tenant_id": "t", "ruolo": "SUPER_ADMIN", "iss": "verumdoc.it",
                          "aud": "altro", "iat": 1, "exp": 4_000_000_000},
                         chiavi_jwt().privata_pem, algorithm="RS256")
        headers = {"Authorization": f"Bearer {tok}"}
    elif caso == "alg_none":
        import jwt
        tok = jwt.encode({"tenant_id": "t", "ruolo": "SUPER_ADMIN", "iss": "verumdoc.it",
                          "aud": "verumdoc-api", "iat": 1, "exp": 4_000_000_000},
                         None, algorithm="none")
        headers = {"Authorization": f"Bearer {tok}"}
    else:  # tenant vuoto: negato (fail-closed)
        headers = intestazioni("SUPER_ADMIN", tenant_id="")
    async with await _client(headers) as c:
        r = await c.request(metodo, percorso)
    assert r.status_code == 401, r.text
    assert r.json()["codice"] in ("AUTH-001", "AUTH-002")
    assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "OPERATORE", "VIEWER", "API_SYSTEM"])
async def test_verifica_completa_negata_agli_altri_ruoli(ruolo) -> None:
    """403 prima di toccare il database (nessun esito registrato)."""
    async with await _client(intestazioni(ruolo)) as c:
        r = await c.post(VERIFICA, json={"avviata_da": "x", "ruolo": "SUPER_ADMIN"})
    assert r.status_code == 403, r.text
    assert r.json()["codice"] == "HC-403"


@richiede_pg
async def test_stato_con_token_valido_qualsiasi_ruolo(db_url) -> None:  # noqa: F811
    async with await _client(intestazioni("VIEWER")) as c:
        r = await c.get(STATO)
    assert r.status_code == 200, r.text
    assert r.json()["integrita"]["esito"] == "MAI_VERIFICATA"


@richiede_pg
@pytest.mark.parametrize("ruolo", ["SUPER_ADMIN", "RSC_CONSERVATORE"])
async def test_verifica_registra_chi_avvia_dal_token_non_dal_corpo(db_url, ruolo) -> None:  # noqa: F811
    utente = str(uuid.uuid4())
    from app.services.verifica_asincrona import attendi_verifiche

    async with await _client(intestazioni(ruolo, user_id=utente)) as c:
        r = await c.post(VERIFICA, json={"avviata_da": "falsificato", "ruolo": "SUPER_ADMIN"})
        assert r.status_code == 202, r.text  # g4: avvio asincrono
        await attendi_verifiche()
        esito = (await c.get(f"{VERIFICA}/{r.json()['verifica_id']}")).json()
    assert esito["avviata_da"] == utente and esito["ruolo"] == ruolo
    righe = await sql(db_url, "SELECT dettagli::text AS d FROM public.audit_global WHERE id = $1",
                      uuid.UUID(esito["verifica_id"]))
    assert "falsificato" not in righe[0]["d"]


def _avvio(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    ambiente = {k: v for k, v in os.environ.items()
                if k not in ("ENV", "JWT_PUBLIC_KEY", "JWT_PRIVATE_KEY")}
    ambiente.update(env)
    ambiente["PYTHONPATH"] = os.pathsep.join([str(SERVIZIO), str(RADICE)])
    return subprocess.run([sys.executable, "-c", "import app.main"], cwd=SERVIZIO, env=ambiente,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("ambiente", ["development", "staging", "production"])
def test_senza_chiave_pubblica_il_servizio_non_parte(ambiente) -> None:
    esito = _avvio({"ENV": ambiente})
    assert esito.returncode != 0
    assert "JWT_PUBLIC_KEY mancante" in esito.stderr


def test_con_la_sola_chiave_pubblica_parte() -> None:
    privata = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pubblica = privata.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    esito = _avvio({"ENV": "production", "JWT_PUBLIC_KEY": pubblica.replace("\n", "\\n")})
    assert esito.returncode == 0, esito.stderr
