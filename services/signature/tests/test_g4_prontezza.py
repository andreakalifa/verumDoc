# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""g4-chiusura: readiness di signature. Il servizio non usa DB, Kafka né S3:
è pronto se il servizio di firma si costruisce dalla configurazione."""
from __future__ import annotations

import pytest
from app.config import settings
from app.main import app
from app.routers.signature import reimposta_servizio

from libs.shared.observability.tests.prova_prontezza import chiedi, senza_dettagli_tecnici

PRONTO = "/api/v1/health/ready"


@pytest.fixture(autouse=True)
def servizio_nuovo():
    reimposta_servizio(None)
    yield
    reimposta_servizio(None)


async def test_configurazione_predefinita_pronta_con_problemi_riportati(monkeypatch) -> None:
    monkeypatch.setattr(settings, "firma_fornitore", "non_configurato")
    monkeypatch.setattr(settings, "marca_fornitore", "non_configurato")
    r = await chiedi(app, PRONTO)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["checks"]["servizio_firma"] == "ok"
    assert corpo["facoltativi"] == ["configurazione_firma"]


async def test_adattatore_di_sviluppo_in_produzione_non_pronto(monkeypatch) -> None:
    """In produzione l'adattatore di sviluppo è vietato: il servizio non si
    costruisce e la probe risponde 503 senza il testo dell'eccezione."""
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "firma_fornitore", "sviluppo")
    r = await chiedi(app, PRONTO)
    assert r.status_code == 503
    assert r.json()["checks"] == {"servizio_firma": "errore",
                                  "configurazione_firma": "problemi_configurazione"}
    senza_dettagli_tecnici(r, "adattatore di sviluppo")


async def test_configurazione_pulita_tutto_ok(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "firma_fornitore", "sviluppo")
    monkeypatch.setattr(settings, "marca_fornitore", "sviluppo")
    monkeypatch.setattr(settings, "sviluppo_dir_chiavi", str(tmp_path))
    from app.services.registro import problemi_configurazione

    assert problemi_configurazione(settings) == []
    r = await chiedi(app, PRONTO)
    assert r.status_code == 200
    assert r.json()["checks"] == {"servizio_firma": "ok", "configurazione_firma": "ok"}
