# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/validation/tests/test_sprint2a_step7.py
Sprint 2A FIX-014 → R08 VD-C-017: Step7 non accetta mai classi sconosciute.

Comportamento provato eseguendo il codice (niente ricerca di stringhe nel
sorgente, REGOLE §23.1), uguale in sviluppo e in produzione:
  - classe inesistente → FAIL (VLD-703)
  - errore DB → ErroreVerificaTransitorio (il consumer ritenta, poi DLQ)
  - classe inattiva → FAIL (VLD-702)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config import settings
from app.validators.base import ErroreVerificaTransitorio, ValidationContext
from app.validators.steps import Step7ClasseDocumentaleOPA

TENANT = "00000000-0000-0000-0000-000000000001"


def _ctx(classe: str = "fattura") -> ValidationContext:
    return ValidationContext(pdv_id="p", tenant_id=TENANT,
                             indice={"classe_documentale": classe, "documenti": []})


def _sessione(riga):
    sessione = AsyncMock()
    risultato = MagicMock()
    risultato.fetchone.return_value = riga
    sessione.execute.return_value = risultato
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=sessione)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestStep7ProductionBehavior:
    """Stesso comportamento in ogni ambiente (nessuna scorciatoia di sviluppo)."""

    @pytest.mark.parametrize("ambiente", ["development", "production"])
    async def test_step7_classe_sconosciuta_rifiutata_in_ogni_ambiente(self, ambiente, monkeypatch):
        monkeypatch.setattr(settings, "env", ambiente)
        with patch("app.validators.steps.get_async_session", return_value=_sessione(None)):
            r = await Step7ClasseDocumentaleOPA().validate(_ctx("inventata_xyz"))
        assert not r.ok and r.codice_errore == "VLD-703"

    @pytest.mark.parametrize("ambiente", ["development", "production"])
    async def test_step7_errore_db_transitorio_in_ogni_ambiente(self, ambiente, monkeypatch):
        monkeypatch.setattr(settings, "env", ambiente)
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=OSError("DB giù"))
        cm.__aexit__ = AsyncMock(return_value=False)
        with patch("app.validators.steps.get_async_session", return_value=cm):
            with pytest.raises(ErroreVerificaTransitorio):
                await Step7ClasseDocumentaleOPA().validate(_ctx())

    async def test_step7_classe_inattiva_vld702(self):
        with patch("app.validators.steps.get_async_session",
                   return_value=_sessione(("aaaaaaaa-0000-0000-0000-000000000001", False))):
            r = await Step7ClasseDocumentaleOPA().validate(_ctx())
        assert not r.ok and r.codice_errore == "VLD-702"
        assert "non attiva" in r.messaggio

    async def test_step7_classe_attiva_ok(self):
        with patch("app.validators.steps.get_async_session",
                   return_value=_sessione(("aaaaaaaa-0000-0000-0000-000000000001", True))):
            r = await Step7ClasseDocumentaleOPA().validate(_ctx())
        assert r.ok and r.dettagli["classe_id"].startswith("aaaaaaaa")


class TestValidationPipelineIntegrity:
    """Verifica che la pipeline dei 9 step sia ancora integra."""

    def test_pipeline_has_11_steps(self):
        """VALIDATION_PIPELINE: 9 step AgID più formato e firme (R08)."""
        from app.validators.steps import VALIDATION_PIPELINE
        assert len(VALIDATION_PIPELINE) == 11, (
            f"Pipeline ha {len(VALIDATION_PIPELINE)} step, attesi 11"
        )

    def test_pipeline_order(self):
        """Gli step devono essere nell'ordine 1-9."""
        from app.validators.steps import VALIDATION_PIPELINE
        for i, step in enumerate(VALIDATION_PIPELINE):
            assert step.step == i + 1, (
                f"Step {i} ha numero {step.step}, atteso {i + 1}"
            )

    def test_step_names(self):
        """Ogni step deve avere un nome non vuoto."""
        from app.validators.steps import VALIDATION_PIPELINE
        for step in VALIDATION_PIPELINE:
            assert step.nome, f"Step {step.step} ha nome vuoto"
