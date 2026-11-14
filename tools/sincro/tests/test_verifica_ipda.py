# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R11 (D03) — riga di comando ``tools/sincro/verifica_ipda.py``: stato, registrazione e
verifica con i codici di uscita (2 = schema ufficiale non disponibile o di prova).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI.parent))
sys.path.insert(0, str(QUI.parents[2]))

import verifica_ipda as v  # noqa: E402

PROVA = QUI.parents[2] / "libs" / "sincro" / "tests" / "dati" / "schema_prova_NON_UFFICIALE"
NS = "https://schemi.verumdoc.it/ipda/provvisorio/1"
IPDA = (f'<SInCRO xmlns="{NS}" versione="2.0" tipo="DAI"><Intestazione>'
        "<IdentificativoAIP>a</IdentificativoAIP><IdentificativoPdV>p</IdentificativoPdV>"
        "<TipologiaOggetto>DAI</TipologiaOggetto></Intestazione><FileGroup/></SInCRO>")


def test_stato_senza_schema(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert v.main(["--schemi", str(tmp_path), "stato"]) == 2
    assert "schema ufficiale non disponibile" in capsys.readouterr().out


def test_registra_e_verifica_con_schema_di_prova(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    schemi = tmp_path / "schemi"
    shutil.copytree(PROVA, schemi / "prova")
    assert v.main(["registra", "--cartella", str(schemi / "prova"), "--principale",
                   "ipda_prova_NON_UFFICIALE.xsd", "--versione", "prova",
                   "--fonte", "schema di prova NON ufficiale"]) == 0
    ipda = tmp_path / "ipda.xml"
    ipda.write_text(IPDA, encoding="utf-8")
    capsys.readouterr()
    assert v.main(["--schemi", str(schemi), "verifica", str(ipda)]) == 2
    uscita = json.loads(capsys.readouterr().out)
    assert uscita["esito"] == "VALIDO_SCHEMA_DI_PROVA" and uscita["conforme"] is False
    assert v.main(["--schemi", str(schemi), "stato"]) == 2  # solo schema di prova
    (schemi / "prova" / "ipda_prova_NON_UFFICIALE.xsd").write_text("<alterato/>")
    capsys.readouterr()
    assert v.main(["--schemi", str(schemi), "verifica", str(ipda)]) == 1
    assert v.main(["--schemi", str(schemi), "stato"]) == 1


def test_errori_d_uso(tmp_path: Path) -> None:
    assert v.main(["verifica", str(tmp_path / "manca.xml")]) == 3
    assert v.main(["registra", "--cartella", str(tmp_path), "--principale", "x.xsd",
                   "--versione", "v", "--fonte", "f"]) == 3


def test_ufficiale_rifiutato_senza_impronte_nel_codice(tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """Revisione T4: «registra --ufficiale» sullo schema di prova è rifiutato (uscita 3)
    e lo stato resta «schema ufficiale non disponibile»."""
    schemi = tmp_path / "schemi"
    shutil.copytree(PROVA, schemi / "uni11386-2020")
    assert v.main(["registra", "--cartella", str(schemi / "uni11386-2020"), "--principale",
                   "ipda_prova_NON_UFFICIALE.xsd", "--versione", "uni11386-2020",
                   "--ufficiale", "--fonte", "tentativo"]) == 3
    assert v.main(["impronte", "--cartella", str(schemi / "uni11386-2020")]) == 0
    impronte = json.loads(capsys.readouterr().out)
    assert set(impronte) == {"ipda_prova_NON_UFFICIALE.xsd"}
    assert v.main(["--schemi", str(schemi), "stato"]) == 2
