# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test di tools/pulizia/pulisci_progetto.py su un albero finto."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "pulisci_progetto", Path(__file__).with_name("pulisci_progetto.py")
)
pulizia = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pulizia)  # type: ignore[union-attr]


def _scrivi(p: Path, testo: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(testo, encoding="utf-8")
    return p


@pytest.fixture
def albero(tmp_path, monkeypatch):
    _scrivi(tmp_path / "docker-compose.yml")
    (tmp_path / "services").mkdir()
    monkeypatch.setattr(pulizia, "RADICE", tmp_path)
    # Da cancellare
    _scrivi(tmp_path / "docs/design/R05/console-1440-chiaro.png")
    _scrivi(tmp_path / "docs/design/R02_partenze/desktop.png")
    _scrivi(tmp_path / "docs/design/R02_partenze/foto_finali/a.png")
    _scrivi(tmp_path / "services/gateway/app/__pycache__/x.pyc")
    _scrivi(tmp_path / "tools/pulizia/elimina_R04.sh")
    _scrivi(tmp_path / "frontend/test-results/r.json")
    _scrivi(tmp_path / "libs/policy-engine/policies/agid/allegato2_formati.rego")  # R07 D11
    _scrivi(tmp_path / "services/gateway/app/services/formato_reale.py")  # R08 D02
    _scrivi(tmp_path / "libs/sincro/schemas/.gitkeep", "")  # R11 D03
    # R18 (VD-P-190): documenti interni non pubblicati, THIRD_PARTY.md rinominato
    _scrivi(tmp_path / "docs/audit/allegati/audit_A_versamento_validazione.md")
    _scrivi(tmp_path / "docs/audit/allegati/audit_B_conservazione.md")
    _scrivi(tmp_path / "docs/audit/allegati/audit_C_servizi_infrastruttura.md")
    _scrivi(tmp_path / "docs/design/allegati/design_A_review.md")
    _scrivi(tmp_path / "docs/design/allegati/design_B_detector.md")
    _scrivi(tmp_path / "docs/storico/Piano_Implementazione_Tecnico_Completo_v5.docx")
    _scrivi(tmp_path / "THIRD_PARTY.md")
    # R18.2 (VD-P-191): adattatori di compatibilità tolti
    _scrivi(tmp_path / "services/gateway/app/compat/__init__.py")
    _scrivi(tmp_path / "services/gateway/app/compat/corpo.py")
    _scrivi(tmp_path / "services/gateway/app/compat/packer.py")
    _scrivi(tmp_path / "services/gateway/app/compat/parer.py")
    _scrivi(tmp_path / "services/gateway/app/routers/compat_packer.py")
    _scrivi(tmp_path / "services/gateway/app/routers/compat_parer.py")
    _scrivi(tmp_path / "services/gateway/tests/test_compat_packer.py")
    _scrivi(tmp_path / "services/gateway/tests/test_compat_parer.py")
    _scrivi(tmp_path / "docs/integrazione/compat_packer.md")
    _scrivi(tmp_path / "docs/integrazione/compat_parer.md")
    # Da conservare
    _scrivi(tmp_path / "services/gateway/app/services/corpo.py")  # R18.2: spostato qui
    _scrivi(tmp_path / "services/gateway/app/routers/caricamenti.py")
    _scrivi(tmp_path / "docs/integrazione/caricamenti.md")
    _scrivi(tmp_path / "libs/verumdoc_rules/impostazioni.py")
    _scrivi(tmp_path / "docs/design/AUDIT_GRAFICO_2026-10-20.md")
    _scrivi(tmp_path / "docs/design/R02_partenze/LEGGIMI.md")
    _scrivi(tmp_path / "services/gateway/app/main.py")
    _scrivi(tmp_path / "services/gateway/app/services/versamento_service.py")
    _scrivi(tmp_path / "libs/shared/formati/firma_binaria.py")
    _scrivi(tmp_path / "libs/sincro/schemas/LEGGIMI.md")
    _scrivi(tmp_path / "frontend/node_modules/pkg/__pycache__/y.pyc")
    _scrivi(tmp_path / "infra/dev_jwt.env")
    _scrivi(tmp_path / "frontend/src/logo.png")
    _scrivi(tmp_path / "docs/audit/allegati/ricerca_normativa.md")  # R18: resta pubblicata
    _scrivi(tmp_path / "docs/audit/AUDIT_FUNZIONALE_2026-10-20.md")
    _scrivi(tmp_path / "THIRD_PARTY_NOTICES.md")
    return tmp_path


def _conservati(radice: Path) -> None:
    for rel in [
        "docs/design/AUDIT_GRAFICO_2026-10-20.md",
        "docs/design/R02_partenze/LEGGIMI.md",
        "services/gateway/app/main.py",
        "services/gateway/app/services/versamento_service.py",
        "libs/shared/formati/firma_binaria.py",
        "frontend/node_modules/pkg/__pycache__/y.pyc",
        "infra/dev_jwt.env",
        "frontend/src/logo.png",
        "libs/verumdoc_rules/impostazioni.py",
        "libs/sincro/schemas/LEGGIMI.md",
        "docs/audit/allegati/ricerca_normativa.md",
        "docs/audit/AUDIT_FUNZIONALE_2026-10-20.md",
        "THIRD_PARTY_NOTICES.md",
        "services/gateway/app/services/corpo.py",
        "services/gateway/app/routers/caricamenti.py",
        "docs/integrazione/caricamenti.md",
    ]:
        assert (radice / rel).exists(), rel


def test_prova_non_cancella_nulla(albero):
    assert pulizia.main(["--prova"]) == 0
    assert (albero / "docs/design/R05/console-1440-chiaro.png").exists()
    _conservati(albero)


def test_cancella_solo_il_superfluo(albero):
    assert pulizia.main(["--si"]) == 0
    for rel in [
        "docs/design/R05",
        "docs/design/R02_partenze/desktop.png",
        "docs/design/R02_partenze/foto_finali",
        "services/gateway/app/__pycache__",
        "tools/pulizia/elimina_R04.sh",
        "frontend/test-results",
        "libs/policy-engine",
        "services/gateway/app/services/formato_reale.py",
        "libs/sincro/schemas/.gitkeep",
        "docs/audit/allegati/audit_A_versamento_validazione.md",
        "docs/audit/allegati/audit_B_conservazione.md",
        "docs/audit/allegati/audit_C_servizi_infrastruttura.md",
        "docs/design/allegati",
        "docs/storico/Piano_Implementazione_Tecnico_Completo_v5.docx",
        "THIRD_PARTY.md",
        "services/gateway/app/compat",
        "services/gateway/app/routers/compat_packer.py",
        "services/gateway/app/routers/compat_parer.py",
        "services/gateway/tests/test_compat_packer.py",
        "services/gateway/tests/test_compat_parer.py",
        "docs/integrazione/compat_packer.md",
        "docs/integrazione/compat_parer.md",
    ]:
        assert not (albero / rel).exists(), rel
    _conservati(albero)


def test_senza_conferma_non_cancella(albero, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _msg: "n")
    assert pulizia.main([]) == 0
    assert (albero / "docs/design/R05").exists()


def test_rifiuta_una_cartella_che_non_e_il_progetto(tmp_path, monkeypatch):
    monkeypatch.setattr(pulizia, "RADICE", tmp_path)
    with pytest.raises(SystemExit):
        pulizia.main(["--prova"])
