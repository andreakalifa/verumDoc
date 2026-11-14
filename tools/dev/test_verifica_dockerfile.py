# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prove di tools/dev/verifica_dockerfile.py: sul repository reale e su servizi sintetici."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verifica_dockerfile as vd  # noqa: E402

VALUES = {"services": {"finto": {"health": {"live": "/api/v1/health/live"}}}}

HEALTHCHECK_STDLIB = ("CMD python -c \"import urllib.request; urllib.request.urlopen("
                      "'http://localhost:8000/api/v1/health/live', timeout=3)\"")

DOCKERFILE_BUONO = """FROM python:3.12-slim
WORKDIR /app
COPY services/finto/app/ ./app/
COPY libs/ ./libs/
ENV PYTHONPATH=/app
HEALTHCHECK --interval=10s \\
    """ + HEALTHCHECK_STDLIB + """
CMD ["uvicorn", "app.main:app"]
"""


def _repo(tmp_path: Path, dockerfile: str, dipendenze: list[str], codice: str) -> Path:
    svc = tmp_path / "services" / "finto"
    (svc / "app").mkdir(parents=True)
    (svc / "app" / "main.py").write_text(codice)
    (svc / "Dockerfile").write_text(dockerfile)
    righe = ", ".join(f'"{d}"' for d in dipendenze)
    (svc / "pyproject.toml").write_text(f'[project]\nname = "finto"\ndependencies = [{righe}]\n')
    libs = tmp_path / "libs" / "shared"
    libs.mkdir(parents=True)
    (tmp_path / "libs" / "__init__.py").write_text("")
    (libs / "__init__.py").write_text("")
    (libs / "sicurezza.py").write_text("import jwt\n")
    return svc


def test_repository_reale_ok() -> None:
    esito = subprocess.run([sys.executable, str(Path(vd.__file__))], capture_output=True, text=True)  # noqa: S603 — comando e argomenti costruiti dallo strumento
    assert esito.returncode == 0, esito.stdout + esito.stderr


def test_servizio_corretto(tmp_path: Path) -> None:
    svc = _repo(tmp_path, DOCKERFILE_BUONO, ["fastapi", "PyJWT[crypto]>=2"],
                "import fastapi\nfrom libs.shared.sicurezza import x\n")
    errori, _ = vd.verifica_servizio(svc, VALUES, tmp_path)
    assert errori == []


def test_libs_non_copiato(tmp_path: Path) -> None:
    df = DOCKERFILE_BUONO.replace("COPY libs/ ./libs/\n", "").replace("ENV PYTHONPATH=/app\n", "")
    svc = _repo(tmp_path, df, ["PyJWT"], "from libs.shared.sicurezza import x\n")
    errori, _ = vd.verifica_servizio(svc, VALUES, tmp_path)
    assert any("non copia libs/" in e for e in errori)
    assert any("PYTHONPATH" in e for e in errori)


def test_dipendenza_transitiva_di_libs_mancante(tmp_path: Path) -> None:
    """libs/shared/sicurezza.py importa jwt: il servizio deve dichiarare PyJWT."""
    svc = _repo(tmp_path, DOCKERFILE_BUONO, ["fastapi"], "from libs.shared.sicurezza import x\n")
    errori, _ = vd.verifica_servizio(svc, VALUES, tmp_path)
    assert any("pyjwt" in e for e in errori)


@pytest.mark.parametrize("comando", [
    "CMD python -c \"import httpx; httpx.get('http://localhost:8000/api/v1/health/live')\"",
    "CMD curl -sf http://localhost:8000/api/v1/health/live",
])
def test_healthcheck_con_strumenti_non_garantiti(tmp_path: Path, comando: str) -> None:
    df = DOCKERFILE_BUONO.replace(HEALTHCHECK_STDLIB, comando)
    svc = _repo(tmp_path, df, ["fastapi"], "import fastapi\n")
    errori, _ = vd.verifica_servizio(svc, VALUES, tmp_path)
    assert any("HEALTHCHECK usa strumenti" in e for e in errori)


def test_healthcheck_su_percorso_sbagliato(tmp_path: Path) -> None:
    df = DOCKERFILE_BUONO.replace("/api/v1/health/live", "/health")
    svc = _repo(tmp_path, df, ["fastapi"], "import fastapi\n")
    errori, _ = vd.verifica_servizio(svc, VALUES, tmp_path)
    assert any("non interroga /api/v1/health/live" in e for e in errori)
