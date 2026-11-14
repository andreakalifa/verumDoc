# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifiche di configurazione della dashboard-api (VD-C-040).

Sostituiscono le verifiche utili di test_aggregator.py e test_bugfix_review.py,
eliminati insieme al modulo morto app/services/aggregator.py. Qui si controlla il
comportamento reale delle Settings e delle rotte, non il testo del sorgente.
Le variabili d'ambiente provate sono quelle che impostano docker-compose e le
ConfigMap di Kubernetes/Helm (infra/).
"""
from __future__ import annotations

from urllib.parse import urlparse

import pytest
from app.config import Settings

SERVIZI_A_VALLE = {
    "gateway_url": "gateway",
    "onboarding_url": "onboarding",
    "hashchain_url": "hashchain",
    "distribution_url": "distribution",
}


@pytest.fixture
def ambiente_pulito(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for campo in [*SERVIZI_A_VALLE, "cors_origins"]:
        monkeypatch.delenv(campo.upper(), raising=False)
    return monkeypatch


class TestUrlServizi:
    @pytest.mark.parametrize(("campo", "host"), sorted(SERVIZI_A_VALLE.items()))
    def test_predefinito_nome_servizio_porta_8000(
        self, ambiente_pulito: pytest.MonkeyPatch, campo: str, host: str
    ) -> None:
        """Default = nome DNS del servizio sulla porta applicativa 8000 (compose e k8s)."""
        url = urlparse(getattr(Settings(_env_file=None), campo))
        assert url.scheme == "http"
        assert url.hostname == host
        assert url.port == 8000

    @pytest.mark.parametrize("campo", sorted(SERVIZI_A_VALLE))
    def test_sovrascrivibile_da_ambiente(
        self, ambiente_pulito: pytest.MonkeyPatch, campo: str
    ) -> None:
        ambiente_pulito.setenv(campo.upper(), "http://altro-host:9123")
        assert getattr(Settings(_env_file=None), campo) == "http://altro-host:9123"


class TestCors:
    def test_predefinito_lista_non_vuota(self, ambiente_pulito: pytest.MonkeyPatch) -> None:
        origini = Settings(_env_file=None).cors_origins
        assert isinstance(origini, list)
        assert origini
        assert all(urlparse(o).scheme in ("http", "https") for o in origini)

    def test_lista_json_da_ambiente(self, ambiente_pulito: pytest.MonkeyPatch) -> None:
        """Formato usato nelle ConfigMap: lista JSON."""
        ambiente_pulito.setenv(
            "CORS_ORIGINS", '["https://app.verumdoc.it","https://staging.verumdoc.it"]'
        )
        assert Settings(_env_file=None).cors_origins == [
            "https://app.verumdoc.it",
            "https://staging.verumdoc.it",
        ]


class TestRotte:
    ROTTE_ATTESE = {
        "/api/dashboard/overview",
        "/api/dashboard/versamenti",
        "/api/dashboard/aip",
        "/api/dashboard/hashchain",
        "/api/dashboard/search",
        "/api/dashboard/distribution",
        "/api/dashboard/tenants",
    }

    def test_rotte_dashboard_pubblicate(self) -> None:
        from app.main import app

        percorsi = set(app.openapi()["paths"])
        assert self.ROTTE_ATTESE <= percorsi, self.ROTTE_ATTESE - percorsi

    def test_health_live_risponde(self) -> None:
        """Percorso usato dalle probe Kubernetes/Helm (infra/)."""
        from app.main import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            assert client.get("/api/health/live").status_code == 200
