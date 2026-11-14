# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Avvio del servizio (lifespan): server in ascolto, prontezza, metriche, avvio
bloccato con impostazioni pericolose."""
from __future__ import annotations

import asyncssh
import httpx
import pytest
from app import main
from app.config import settings

from tests.conftest import crea_account
from tests.infra_reale import _porta_libera, richiede_pg


@pytest.fixture
def impostazioni(monkeypatch, radice, db_url):
    porta = _porta_libera()
    monkeypatch.setattr(settings, "sftp_porta", porta)
    monkeypatch.setattr(settings, "sftp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "sftp_radice", str(radice))
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "lavoratore_intervallo_secondi", 3600)
    return porta


@richiede_pg
async def test_avvio_pronto_e_accesso_sftp(impostazioni, db_url) -> None:
    porta = impostazioni
    account = await crea_account(db_url)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://t") as c:
            pronto = await c.get("/api/v1/health/ready")
            assert pronto.status_code == 200, pronto.text
            assert pronto.json()["checks"] == {"database": "ok", "volume": "ok",
                                               "server_sftp": "ok"}
            assert (await c.get("/api/v1/health/live")).status_code == 200
            async with asyncssh.connect("127.0.0.1", porta, username=account.nome_utente,
                                        client_keys=[account.chiave], known_hosts=None,
                                        agent_path=None) as conn:
                async with conn.start_sftp_client() as sftp:
                    assert "in" in await sftp.listdir("/")
            metriche = (await c.get("/metrics")).text
            assert 'verumdoc_sftp_accessi_totali_total{esito="ok"} 1.0' in metriche
            assert "verumdoc_sftp_giri_scansione_totali" in metriche
            info = (await c.get("/api/v1/info")).json()
            assert info["componenti"] == ["server", "lavoratore"]
    assert main._stato["server"] is None


async def test_avvio_bloccato_in_produzione_senza_segreti(monkeypatch) -> None:
    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(RuntimeError, match="SFTP_CHIAVE_HOST"):
        async with main.app.router.lifespan_context(main.app):
            pass


async def test_prontezza_senza_avvio_non_pronto(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "sftp_radice", str(tmp_path / "manca"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://t") as c:
        r = await c.get("/api/v1/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["volume"] == "volume_non_scrivibile"
    assert r.json()["checks"]["server_sftp"] == "server_non_avviato"
