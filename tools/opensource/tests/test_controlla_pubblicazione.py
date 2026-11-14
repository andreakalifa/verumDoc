# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R18 (VD-P-190) — tools/opensource/controlla_pubblicazione.py su repository git finti.

Ogni prova costruisce un repository vero (``git init``) in una cartella temporanea, con la
configurazione pubblica del progetto più, dove serve, un file locale di termini riservati
inventati, ed esegue il controllo come lo esegue ``make verifiche``."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "controlla_pubblicazione",
    Path(__file__).resolve().parents[1] / "controlla_pubblicazione.py")
cp = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cp  # richiesto dalle dataclass
_SPEC.loader.exec_module(cp)  # type: ignore[union-attr]

INTESTAZIONE_PY = ("# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc\n"
                   "# SPDX-License-Identifier: EUPL-1.2\n")


def _git(radice: Path, *argomenti: str) -> None:
    subprocess.run(["git", "-C", str(radice), *argomenti], check=True, capture_output=True)


def _scrivi(radice: Path, rel: str, testo: str | bytes) -> Path:
    p = radice / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(testo, bytes):
        p.write_bytes(testo)
    else:
        p.write_text(testo, encoding="utf-8", newline="")
    return p


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _scrivi(tmp_path, "LICENSE", "EUROPEAN UNION PUBLIC LICENCE v. 1.2\n")
    _scrivi(tmp_path, ".gitignore", "infra/*.env\n*.pem\n")
    _scrivi(tmp_path, "services/gateway/app/main.py", INTESTAZIONE_PY + '"""Servizio."""\n')
    _scrivi(tmp_path, "README.md", "# Progetto\n")
    return tmp_path


def _conf(tmp_path: Path, locale: str | None = None) -> cp.Configurazione:
    file_locale = None
    if locale is not None:
        file_locale = _scrivi(tmp_path, "_conf/pubblicazione.locale.toml", locale)
    return cp.leggi_configurazione(cp.CONFIGURAZIONE, file_locale)


def _controlla(repo: Path, conf: cp.Configurazione | None = None) -> list[str]:
    _git(repo, "add", "-A")
    return [str(p) for p in cp.controlla(repo, conf or _conf(repo, None))]


def test_repository_pulito_nessun_problema(repo: Path) -> None:
    assert _controlla(repo) == []


def test_sorgente_senza_intestazione_segnalato(repo: Path) -> None:
    _scrivi(repo, "libs/shared/nuovo.py", "x = 1\n")
    _scrivi(repo, "frontend/src/Nuovo.tsx", "export const a = 1;\n")
    problemi = _controlla(repo)
    assert any(p.startswith("libs/shared/nuovo.py: intestazione SPDX assente") for p in problemi)
    assert any(p.startswith("frontend/src/Nuovo.tsx: intestazione SPDX assente") for p in problemi)


def test_file_fuori_dalle_cartelle_di_sorgente_non_richiede_intestazione(repo: Path) -> None:
    _scrivi(repo, "docs/esempio.py", "x = 1\n")
    _scrivi(repo, "libs/shared/dati.json", "{}\n")
    assert _controlla(repo) == []


def test_licenza_assente_segnalata(repo: Path) -> None:
    (repo / "LICENSE").unlink()
    assert any(p.startswith("LICENSE: licenza") for p in _controlla(repo))


def test_file_ignorato_ma_tracciato_segnalato_senza_leggerlo(repo: Path) -> None:
    # un file di segreti forzato nell'indice: segnalato per nome, contenuto mai letto
    _scrivi(repo, "infra/dev_jwt.env", b"\xff\xfe non leggibile come testo")
    _git(repo, "add", "-f", "infra/dev_jwt.env")
    problemi = [str(p) for p in cp.controlla(repo, _conf(repo, None))]
    assert any(p.startswith("infra/dev_jwt.env: file ignorato tracciato") for p in problemi)
    assert any(p.startswith("infra/dev_jwt.env: file vietato") for p in problemi)


def test_nome_vietato_anche_se_non_ignorato(repo: Path) -> None:
    _scrivi(repo, "tools/x/chiave.p12", b"\x00\x01")
    assert any(p.startswith("tools/x/chiave.p12: file vietato") for p in _controlla(repo))


def test_documento_d_ufficio_vietato(repo: Path) -> None:
    _scrivi(repo, "docs/storico/piano.docx", b"PK\x03\x04\x00")
    assert any(p.startswith("docs/storico/piano.docx: file vietato") for p in _controlla(repo))


def test_modello_d_esempio_ammesso(repo: Path) -> None:
    _scrivi(repo, "infra/.env.example", "CHIAVE=\n")
    assert _controlla(repo) == []


def test_blocco_pem_con_contenuto_segnalato(repo: Path) -> None:
    corpo = "A" * 64
    _scrivi(repo, "libs/shared/chiave.py", INTESTAZIONE_PY +
            f'K = """-----BEGIN PRIVATE KEY-----\n{corpo}\n-----END PRIVATE KEY-----"""\n')
    problemi = _controlla(repo)
    assert any(p.startswith("libs/shared/chiave.py:3: segreto evidente") for p in problemi)


def test_intestazione_pem_citata_senza_contenuto_non_segnalata(repo: Path) -> None:
    _scrivi(repo, "docs/nota.md", "Il file non deve contenere `-----BEGIN PRIVATE KEY-----...`.\n")
    assert _controlla(repo) == []


def test_token_jwt_completo_segnalato(repo: Path) -> None:
    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJwcm92YSJ9." + "s" * 40
    _scrivi(repo, "docs/token.md", f"token: {token}\n")
    assert any("docs/token.md:1: segreto evidente" in p for p in _controlla(repo))


def test_percorso_locale_segnalato_senza_stampare_il_testo(repo: Path) -> None:
    _scrivi(repo, "docs/guida.md", "Apri C:\\Users\\utenteprova\\progetto\n")
    problemi = _controlla(repo)
    assert problemi == ["docs/guida.md:1: riferimento vietato: modello 1"]
    assert "utenteprova" not in problemi[0]


def test_casella_personale_segnalata(repo: Path) -> None:
    _scrivi(repo, "docs/contatti.md", "Scrivere a prova.persona@gmail.com\n")
    assert any(p.startswith("docs/contatti.md:1: riferimento vietato") for p in _controlla(repo))


def test_termini_locali_si_aggiungono_ai_pubblici(repo: Path) -> None:
    _scrivi(repo, "docs/nota.md", "Lavoro svolto per Esempio Riservato S.p.A.\n")
    assert _controlla(repo) == []
    conf = _conf(repo, "[riferimenti]\nmodelli = ['esempio\\s+riservato']\n")
    problemi = _controlla(repo, conf)
    assert problemi == ["docs/nota.md:1: riferimento vietato: modello " +
                        str(len(conf.riferimenti))]
    assert "Riservato" not in problemi[0]


def test_eccezione_per_file_e_modello(repo: Path) -> None:
    _scrivi(repo, "services/gateway/tests/test_nomi.py",
            INTESTAZIONE_PY + 'OSTILE = "C:\\\\Users\\\\a.pdf"\n')
    assert any("riferimento vietato" in p for p in _controlla(repo))
    modello = _conf(repo, None).riferimenti[0].pattern
    conf = _conf(repo, "[riferimenti.eccezioni]\n"
                       f"\"services/gateway/tests/test_nomi.py\" = ['{modello}']\n")
    assert _controlla(repo, conf) == []


# ── intestazioni ───────────────────────────────────────────────────────

@pytest.mark.parametrize(("rel", "testo", "atteso"), [
    ("libs/a.py", '"""Doc."""\n', INTESTAZIONE_PY + '"""Doc."""\n'),
    ("tools/a.py", "#!/usr/bin/env python3\nx = 1\n",
     "#!/usr/bin/env python3\n" + INTESTAZIONE_PY + "x = 1\n"),
    ("infra/a.sh", "#!/bin/sh\necho\n", "#!/bin/sh\n" + INTESTAZIONE_PY + "echo\n"),
    ("services/x/Dockerfile", "# syntax=docker/dockerfile:1.7\nFROM scratch\n",
     "# syntax=docker/dockerfile:1.7\n" + INTESTAZIONE_PY + "FROM scratch\n"),
    ("frontend/src/a.ts", "export {};\n",
     "// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc\n"
     "// SPDX-License-Identifier: EUPL-1.2\nexport {};\n"),
    ("infra/postgres/a.sql", "SELECT 1;\n",
     "-- SPDX-FileCopyrightText: 2026 Contributori di VerumDoc\n"
     "-- SPDX-License-Identifier: EUPL-1.2\nSELECT 1;\n"),
    ("libs/vuoto/__init__.py", "", INTESTAZIONE_PY),
])
def test_aggiungi_intestazione(rel: str, testo: str, atteso: str) -> None:
    nuovo = cp.aggiungi_intestazione(testo, rel, "2026 Contributori di VerumDoc", "EUPL-1.2")
    assert nuovo == atteso
    assert cp.aggiungi_intestazione(nuovo, rel, "2026 Contributori di VerumDoc",
                                    "EUPL-1.2") == nuovo  # idempotente


def test_aggiungi_intestazione_conserva_bom_e_crlf() -> None:
    nuovo = cp.aggiungi_intestazione("\ufeffparam()\r\nWrite-Host 1\r\n", "tools/a.ps1",
                                     "2026 X", "EUPL-1.2")
    assert nuovo == ("\ufeff# SPDX-FileCopyrightText: 2026 X\r\n# SPDX-License-Identifier: "
                     "EUPL-1.2\r\nparam()\r\nWrite-Host 1\r\n")


def test_template_helm_commento_che_non_cambia_il_rendering() -> None:
    nuovo = cp.aggiungi_intestazione("{{- if .Values.x }}\napiVersion: v1\n{{- end }}\n",
                                     "infra/helm/templates/a.yaml", "2026 X", "EUPL-1.2")
    righe = nuovo.splitlines()
    assert righe[0] == "{{- /*" and righe[3] == "*/ -}}"
    assert righe[4] == "{{- if .Values.x }}"


def test_applica_intestazioni_e_poi_il_controllo_passa(repo: Path) -> None:
    _scrivi(repo, "libs/shared/nuovo.py", "x = 1\n")
    _scrivi(repo, "infra/helm/templates/cm.yaml", "apiVersion: v1\n")
    _git(repo, "add", "-A")
    conf = _conf(repo, None)
    modificati = cp.applica_intestazioni(repo, conf)
    assert modificati == ["infra/helm/templates/cm.yaml", "libs/shared/nuovo.py"]
    assert cp.applica_intestazioni(repo, conf) == []
    assert cp.controlla(repo, conf) == []


def test_main_uscita_1_con_problemi(repo: Path, capsys) -> None:
    _scrivi(repo, "libs/shared/nuovo.py", "x = 1\n")
    _git(repo, "add", "-A")
    assert cp.main(["--radice", str(repo)]) == 1
    assert "intestazione SPDX assente" in capsys.readouterr().out


def test_il_repository_del_progetto_supera_il_controllo() -> None:
    """Il controllo vero sul repository (stessa esecuzione di make verifiche)."""
    radice = Path(__file__).resolve().parents[3]
    if not (radice / ".git").exists():
        pytest.skip("non è un repository git (pacchetto estratto)")
    assert [str(p) for p in cp.controlla(radice)] == []
