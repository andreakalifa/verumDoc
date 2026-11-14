# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Controlli d'avvio (REGOLE §19.4), salute, metriche, migrazioni e comandi
dell'operatore del testimone."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib

import pytest
from app.avvio import carica_contesto, problemi_configurazione
from app.config import Settings
from app.domain.checkpoint import ConfigurazioneNonValida
from app.main import app
from app.osservabilita import MetricheTestimone
from httpx import ASGITransport, AsyncClient

from libs.catena import nota as nota_lib
from tests.conftest import ORIGINE, SERVIZIO, migra, richiede_pg, sql


def test_problemi_configurazione_completa_nessuno(configurazione) -> None:
    assert problemi_configurazione(configurazione) == []


@pytest.mark.parametrize(
    "modifica, frammento",
    [
        ({"chiave_privata": ""}, "TESTIMONE_CHIAVE_PRIVATA non impostata"),
        ({"chiave_privata": "PRIVATE+KEY+x+00000000+AAAA"}, "TESTIMONE_CHIAVE_PRIVATA non valida"),
        ({"chiavi_registro": []}, "TESTIMONE_CHIAVI_REGISTRO non impostata"),
        (
            {"chiavi_registro": ["verumdoc-registro+00000000+AAAA"]},
            "Chiave del registro non valida",
        ),
        ({"origini": []}, "ORIGINI_AMMESSE vuota"),
        ({"origini": ["con spazio"]}, "Origine non valida"),
        ({"impronte_token": []}, "IMPRONTE_TOKEN_REGISTRO non impostata"),
        ({"impronte_token": ["abc"]}, "SHA-256"),
        ({"max_righe_prova": 3}, "Limiti"),
    ],
)
def test_problemi_configurazione_incompleta_elencata(configurazione, modifica, frammento) -> None:
    problemi = problemi_configurazione(dataclasses.replace(configurazione, **modifica))
    assert any(frammento in p for p in problemi), problemi


def test_problemi_configurazione_chiave_testimone_uguale_al_registro(configurazione) -> None:
    """Stessa chiave per registro e testimone = nessuna indipendenza: non parte."""
    priv = nota_lib.analizza_firmatario(configurazione.chiave_privata)
    come_registro = nota_lib.chiave_verificatore(
        "verumdoc-registro", priv.chiave_privata.public_key()
    )
    c = dataclasses.replace(configurazione, chiavi_registro=[come_registro])
    assert any("nessuna indipendenza" in p for p in problemi_configurazione(c))


def test_problemi_configurazione_produzione_db_di_sviluppo(configurazione) -> None:
    c = dataclasses.replace(
        configurazione,
        env="production",
        database_url="postgresql+asyncpg://u:vd_dev_only@localhost/x",
    )
    assert any("DATABASE_URL" in p for p in problemi_configurazione(c))


def test_carica_contesto_incompleto_non_parte(configurazione) -> None:
    with pytest.raises(ConfigurazioneNonValida, match="non parte"):
        carica_contesto(dataclasses.replace(configurazione, chiave_privata=""), MetricheTestimone())


def test_settings_elenchi_separati_da_virgola(monkeypatch) -> None:
    monkeypatch.setenv("TESTIMONE_ORIGINI_AMMESSE", "a/b, c/d ,")
    monkeypatch.setenv("TESTIMONE_IMPRONTE_TOKEN_REGISTRO", "AA" * 32 + "," + "bb" * 32)
    s = Settings()
    assert s.origini_ammesse == ["a/b", "c/d"]
    assert s.impronte_token_registro == ["aa" * 32, "bb" * 32]


async def test_liveness_200_readiness_senza_configurazione_503() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/v1/health/live")).json()["service"] == "testimone"
        r = await c.get("/api/v1/health/ready")
        assert r.status_code == 503
        assert r.json()["checks"]["configurazione"] == "non_configurato"
        # Senza contesto anche le API rispondono 503 dal catalogo, mai 500
        r = await c.get("/api/v1/testimone/checkpoint")
        assert r.status_code == 503 and r.json()["codice"] == "TST-503"
        assert "verumdoc_testimone_compromesso" in (await c.get("/metrics")).text


@pytest.mark.integration
@richiede_pg
async def test_readiness_con_database_200(client) -> None:
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 200, r.json()


@pytest.mark.integration
@richiede_pg
async def test_migrazioni_su_database_vuoto_check_e_downgrade(db_vuoto: str) -> None:
    """Il servizio migra da sé un database vuoto; modelli = schema; downgrade funzionante."""
    from alembic import command
    from alembic.config import Config

    def _esegui() -> None:
        migra(db_vuoto)
        cfg = Config(str(SERVIZIO / "alembic.ini"))
        cfg.set_main_option("script_location", str(SERVIZIO / "alembic"))
        cfg.attributes["database_url"] = db_vuoto
        cfg.attributes["senza_log"] = True
        command.check(cfg)
        command.downgrade(cfg, "base")
        migra(db_vuoto)

    await asyncio.to_thread(_esegui)
    tabelle = await sql(
        db_vuoto, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    assert tabelle == [], "il testimone non crea nulla nello schema public"


@pytest.mark.integration
@richiede_pg
async def test_operatore_cli_stato_e_chiave_pubblica(
    db_url, configurazione, chiavi, monkeypatch, capsys
) -> None:
    from app import operatore

    monkeypatch.setattr(operatore.settings, "database_url", db_url)
    monkeypatch.setattr(
        operatore.settings,
        "testimone_chiave_privata",
        type(operatore.settings.testimone_chiave_privata)(chiavi["testimone_privata"]),
    )
    monkeypatch.setattr(operatore.settings, "testimone_chiavi_registro", chiavi["registro"])
    monkeypatch.setattr(operatore.settings, "testimone_origini_ammesse", ORIGINE)
    monkeypatch.setattr(
        operatore.settings, "testimone_impronte_token_registro", hashlib.sha256(b"t").hexdigest()
    )
    assert await asyncio.to_thread(operatore.main, ["chiave-pubblica"]) == 0
    assert capsys.readouterr().out.strip() == chiavi["testimone"]
    assert await asyncio.to_thread(operatore.main, ["stato"]) == 0
    assert "nessun checkpoint" in capsys.readouterr().out
    assert (
        await asyncio.to_thread(
            operatore.main, ["sblocca", "--operatore", "x", "--motivo", "prova di sblocco inutile"]
        )
        == 1
    )
    assert "non è bloccato" in capsys.readouterr().out
