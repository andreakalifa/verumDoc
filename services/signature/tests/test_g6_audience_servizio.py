# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza R2 (g6-ultime): fuori da ENV=test il servizio di firma
non parte se l'audience del token di servizio (JWT_AUDIENCE_SERVIZIO) coincide
con quella dei token utente (JWT_AUDIENCE).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.config import Settings


@pytest.mark.parametrize("env", ["production", "staging", "development"])
def test_audience_uguali_bloccano_l_avvio(monkeypatch, env: str) -> None:
    import app.main as main

    monkeypatch.setattr(main, "settings", Settings(
        env=env, jwt_audience="verumdoc-api", jwt_audience_servizio=" verumdoc-api "))
    with pytest.raises(RuntimeError, match="JWT_AUDIENCE_SERVIZIO coincide con JWT_AUDIENCE"):
        main.controlla_avvio()


def test_audience_diverse_avvio_consentito_fuori_produzione(monkeypatch) -> None:
    import app.main as main

    monkeypatch.setattr(main, "settings", Settings(
        env="development", jwt_audience="verumdoc-api",
        jwt_audience_servizio="verumdoc-signature"))
    main.controlla_avvio()   # nessuna eccezione


def test_in_ambiente_test_il_controllo_non_si_applica(monkeypatch) -> None:
    import app.main as main

    monkeypatch.setattr(main, "settings", Settings(
        env="test", jwt_audience="x", jwt_audience_servizio="x"))
    main.controlla_avvio()


def test_avvio_reale_rifiutato_in_un_processo_separato() -> None:
    """Lifespan reale dell'applicazione (ENV=development, audience uguali)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pubblica = chiave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    servizio = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ENV", "JWT_"))}
    env.update({"ENV": "development", "JWT_PUBLIC_KEY": pubblica,
                "JWT_AUDIENCE": "verumdoc-api", "JWT_AUDIENCE_SERVIZIO": "verumdoc-api",
                "PYTHONPATH": os.pathsep.join([str(servizio), str(servizio.parents[1])])})
    codice = (
        "import asyncio\n"
        "from app.main import app, lifespan\n"
        "async def avvia():\n"
        "    async with lifespan(app):\n"
        "        print('AVVIATO')\n"
        "asyncio.run(avvia())\n"
    )
    esito = subprocess.run(  # noqa: S603 - comando fisso
        [sys.executable, "-c", codice], cwd=servizio, env=env,
        capture_output=True, text=True, timeout=120)
    assert esito.returncode != 0
    assert "AVVIATO" not in esito.stdout
    assert "JWT_AUDIENCE_SERVIZIO coincide con JWT_AUDIENCE" in esito.stderr
