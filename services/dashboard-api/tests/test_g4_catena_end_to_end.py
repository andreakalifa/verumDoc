# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
g4-chiusura: la pagina «Catena» passa davvero attraverso il proxy della dashboard
fino al servizio hashchain VERO, che verifica il token (g3-backend).

Il servizio hashchain gira in un sottoprocesso (uvicorn, ENV=development, solo
JWT_PUBLIC_KEY = chiave pubblica di questa dashboard di test, stesso database di
prova). Prima della correzione il proxy non inoltrava il token: hashchain
rispondeva 401 e la dashboard lo restituiva al frontend (oppure 502).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests import infra_reale
from tests.dati_prova import client, token
from tests.infra_reale import _porta_libera, richiede_pg, sql

pytestmark = richiede_pg

# Fixture condivise (tests/infra_reale.py), registrate in questo modulo
db_url = infra_reale.db_url

RADICE = Path(__file__).resolve().parents[3]
HASHCHAIN = RADICE / "services" / "hashchain"

_AVVIO = """
import asyncio, sys
import uvicorn
from app.main import app
from libs.shared.db import init_db

async def principale():
    await init_db(sys.argv[1])
    # lifespan disattivato: il consumer Kafka non serve alle API della catena
    cfg = uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[2]),
                         lifespan="off", log_level="warning")
    await uvicorn.Server(cfg).serve()

asyncio.run(principale())
"""


@pytest.fixture
async def hashchain_vero(db_url, monkeypatch):
    """Avvia hashchain con la chiave pubblica della dashboard; restituisce l'URL."""
    from app.auth.jwt_handler import chiavi_jwt
    from app.config import settings

    porta = _porta_libera()
    ambiente = {k: v for k, v in os.environ.items() if not k.startswith(("JWT_", "VD_"))}
    ambiente.update({
        "ENV": "development",
        "JWT_PUBLIC_KEY": chiavi_jwt().pubblica_pem,
        "JWT_ISSUER": settings.jwt_issuer,
        "JWT_AUDIENCE": settings.jwt_audience,
        "DATABASE_URL": db_url,
        "PYTHONPATH": f"{HASHCHAIN}{os.pathsep}{RADICE}",
    })
    proc = subprocess.Popen([sys.executable, "-c", _AVVIO, db_url, str(porta)],
                            cwd=HASHCHAIN, env=ambiente,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{porta}"
    try:
        scadenza = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                uscita = proc.stdout.read().decode(errors="replace")
                pytest.fail(f"hashchain non è partito:\n{uscita[-2000:]}")
            try:
                if httpx.get(f"{url}/api/v1/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > scadenza:
                pytest.fail("hashchain non risponde entro 30 s")
            time.sleep(0.2)
        monkeypatch.setattr(settings, "hashchain_url", url)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


async def test_pagina_catena_con_utente_valido(hashchain_vero, db_url) -> None:
    """Stato della catena: 200 dal servizio vero (né 401 né 502)."""
    async with client() as c:
        r = await c.get("/api/dashboard/hashchain?limit=3", headers=token("VIEWER"))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert {"totale_blocchi", "firma", "integrita", "blocchi_recenti"} <= corpo.keys()


async def test_verifica_completa_attraverso_il_proxy(hashchain_vero, db_url) -> None:
    """Verifica completa asincrona da RSC: 202 subito, polling fino a COMPLETATA;
    l'esecutore registrato è quello del token. Un secondo avvio mentre la prima
    è in corso darebbe 409 (provato in hashchain/tests/test_g4_verifica_asincrona.py)."""
    import asyncio

    utente = str(uuid.uuid4())
    h = token("RSC_CONSERVATORE", user_id=utente)
    async with client() as c:
        r = await c.post("/api/dashboard/hashchain/verifica-completa", headers=h)
        assert r.status_code == 202, r.text
        avvio = r.json()
        assert set(avvio) == {"verifica_id", "stato", "avviata_il"}
        for _ in range(200):
            s = await c.get(f"/api/dashboard/hashchain/verifica-completa/{avvio['verifica_id']}",
                            headers=h)
            assert s.status_code == 200, s.text
            if s.json()["stato"] != "IN_CORSO":
                break
            await asyncio.sleep(0.05)
        negato = await c.get(f"/api/dashboard/hashchain/verifica-completa/{avvio['verifica_id']}",
                             headers=token("VIEWER"))
        stato = await c.get("/api/dashboard/hashchain", headers=token("VIEWER"))
    fine = s.json()
    assert fine["stato"] == "COMPLETATA" and fine["avviata_da"] == utente
    assert negato.status_code == 403
    assert stato.status_code == 200
    assert stato.json()["integrita"]["verifica_id"] == avvio["verifica_id"]
    righe = await sql(db_url, "SELECT user_id, dettagli->>'ruolo' AS ruolo "
                              "FROM public.audit_global "
                              "WHERE evento = 'hashchain.verifica_completa'")
    assert [(str(x["user_id"]), x["ruolo"]) for x in righe] == [(utente, "RSC_CONSERVATORE")]


async def test_token_non_valido_fermato_dalla_dashboard(hashchain_vero) -> None:
    """Senza token la richiesta non arriva a hashchain: 401 della dashboard."""
    async with client() as c:
        r = await c.get("/api/dashboard/hashchain")
    assert r.status_code == 401


async def test_hashchain_rifiuta_un_token_non_suo(hashchain_vero) -> None:
    """Prova che hashchain verifica davvero: un token firmato con un'altra chiave
    ricevuto direttamente è respinto con 401."""
    from app.config import settings
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from libs.shared.security.jwt_rs256 import firma_token

    altra = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    falso = firma_token({"tenant_id": str(uuid.uuid4()), "user_id": "x", "ruolo": "SUPER_ADMIN"},
                        chiave_privata_pem=altra, emittente=settings.jwt_issuer,
                        pubblico=settings.jwt_audience, durata_secondi=60)
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{hashchain_vero}/api/v1/hashchain/status",
                        headers={"Authorization": f"Bearer {falso}"})
    assert r.status_code == 401
