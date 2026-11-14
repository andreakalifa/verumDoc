# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-002 — il dev-token esiste solo con VERUMDOC_DEV_AUTH=1 E ENV development/test.
VD-C-004 — il gateway non parte senza chiave privata fuori dall'ambiente di test.

I test di avvio importano app.main in un processo separato con l'ambiente
desiderato: è esattamente ciò che fa uvicorn all'avvio del servizio.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.config import Settings, login_sviluppo_consentito
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

GATEWAY = Path(__file__).resolve().parents[1]
RADICE = GATEWAY.parents[1]
ROTTA = "/api/v1/auth/dev-token"


@pytest.fixture(scope="module")
def chiavi_env() -> dict[str, str]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    # Formato su una riga con \n letterali, come in docker-compose/.env
    return {"JWT_PRIVATE_KEY": priv.replace("\n", "\\n"),
            "JWT_PUBLIC_KEY": pub.replace("\n", "\\n")}


def _avvia(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Importa app.main con l'ambiente dato e stampa le rotte registrate."""
    base = {k: v for k, v in os.environ.items()
            if k.upper() not in {"ENV", "VERUMDOC_DEV_AUTH", "JWT_PRIVATE_KEY",
                                 "JWT_PUBLIC_KEY"}}
    base["PYTHONPATH"] = os.pathsep.join([str(GATEWAY), str(RADICE)])
    # Pepper delle chiavi API (obbligatorio fuori da development/test, R02 g2-auth):
    # qui si prova il login di sviluppo, non le chiavi API.
    base["API_KEY_PEPPER"] = "pepper-di-prova-per-l-avvio-0123456789abcdef"
    base.update(env)
    codice = "import app.main as m; print('\\n'.join(m.app.openapi()['paths']))"
    return subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [sys.executable, "-c", codice], cwd=GATEWAY, env=base,
        capture_output=True, text=True, timeout=120,
    )


class TestRegolaLoginSviluppo:
    @pytest.mark.parametrize("env,flag,atteso", [
        ("development", True, True),
        ("test", True, True),
        ("Development", True, True),
        ("development", False, False),
        ("test", False, False),
        ("staging", True, False),
        ("production", True, False),
        ("Production", True, False),
        ("prod", True, False),
        ("", True, False),
        ("sviluppo", True, False),
    ])
    def test_matrice(self, env: str, flag: bool, atteso: bool) -> None:
        s = Settings(_env_file=None, env=env, verumdoc_dev_auth=flag)
        assert s.login_sviluppo_abilitato() is atteso

    def test_flag_letto_da_variabile_ambiente(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERUMDOC_DEV_AUTH", "1")
        monkeypatch.setenv("ENV", "development")
        assert Settings(_env_file=None).login_sviluppo_abilitato() is True

    def test_ambienti_consentiti(self) -> None:
        assert login_sviluppo_consentito("development")
        assert not login_sviluppo_consentito("staging")


class TestRottaNonRegistrata:
    def test_suite_senza_flag_rotta_assente(self) -> None:
        from app.main import app

        assert ROTTA not in app.openapi()["paths"]
        r = TestClient(app).post(ROTTA, json={"ruolo": "SUPER_ADMIN"})
        assert r.status_code in (404, 405)

    @pytest.mark.parametrize("env", ["staging", "PRODUCTION", "prod"])
    def test_flag_attivo_ma_ambiente_esposto(self, chiavi_env: dict[str, str], env: str) -> None:
        esito = _avvia({**chiavi_env, "ENV": env, "VERUMDOC_DEV_AUTH": "1"})
        assert esito.returncode == 0, esito.stderr[-2000:]
        assert ROTTA not in esito.stdout.split()

    def test_sviluppo_senza_flag(self, chiavi_env: dict[str, str]) -> None:
        esito = _avvia({**chiavi_env, "ENV": "development"})
        assert esito.returncode == 0, esito.stderr[-2000:]
        assert ROTTA not in esito.stdout.split()

    def test_sviluppo_con_flag_rotta_presente(self, chiavi_env: dict[str, str]) -> None:
        esito = _avvia({**chiavi_env, "ENV": "development", "VERUMDOC_DEV_AUTH": "1"})
        assert esito.returncode == 0, esito.stderr[-2000:]
        assert ROTTA in esito.stdout.split()


class TestDifesaInProfondita:
    async def test_endpoint_404_se_regola_non_soddisfatta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import settings
        from app.routers.dev_auth import DevTokenRequest, dev_token

        monkeypatch.setattr(settings, "verumdoc_dev_auth", True)
        monkeypatch.setattr(settings, "env", "staging")
        with pytest.raises(HTTPException) as e:
            await dev_token(DevTokenRequest(), _richiesta())
        assert e.value.status_code == 404

    async def test_endpoint_attivo_emette_token_verificabile(self, login_sviluppo: None) -> None:
        from app.auth.jwt_handler import verify_access_token
        from app.routers.dev_auth import DevTokenRequest, dev_token

        r = await dev_token(DevTokenRequest(ruolo="VIEWER"), _richiesta())
        assert verify_access_token(json.loads(r.body)["access_token"])["ruolo"] == "VIEWER"
        # R05 (VD-C-079): il portale di sviluppo riceve la sessione in cookie HttpOnly
        cookie = r.headers.getlist("set-cookie")
        assert any(c.startswith("vd_sessione=") and "HttpOnly" in c for c in cookie)


def _richiesta():
    from starlette.requests import Request

    return Request({"type": "http", "scheme": "http", "method": "POST", "path": "/",
                    "headers": [], "server": ("localhost", 8000)})


class TestAvvioSenzaChiavi:
    """VD-C-004: fuori da ENV=test, senza chiave privata il gateway non parte."""

    @pytest.mark.parametrize("env", ["development", "production"])
    def test_senza_chiavi_non_parte(self, env: str) -> None:
        esito = _avvia({"ENV": env})
        assert esito.returncode != 0
        assert "ErroreChiaviJwt" in esito.stderr
        assert "JWT_PUBLIC_KEY mancante" in esito.stderr

    def test_solo_pubblica_non_parte(self, chiavi_env: dict[str, str]) -> None:
        esito = _avvia({"ENV": "production", "JWT_PUBLIC_KEY": chiavi_env["JWT_PUBLIC_KEY"]})
        assert esito.returncode != 0
        assert "JWT_PRIVATE_KEY mancante" in esito.stderr

    def test_con_chiavi_da_ambiente_parte(self, chiavi_env: dict[str, str]) -> None:
        esito = _avvia({**chiavi_env, "ENV": "production"})
        assert esito.returncode == 0, esito.stderr[-2000:]
        assert "BEGIN" not in esito.stdout + esito.stderr  # nessuna chiave stampata
