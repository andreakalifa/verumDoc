# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""libs/shared/security/input_validation.py — spostato da tests/test_fase4/test_security.py
(g4-chiusura: alla radice non veniva mai eseguito). XSS e SQL injection sono già
provati nel gateway (tests/test_sprint1_tenant_isolation.py); qui i casi restanti."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from libs.shared.security.input_validation import (
    sanitize_string,
    validate_file_extension,
    validate_no_ssrf,
)


def _codice(chiamata) -> str:
    with pytest.raises(HTTPException) as exc:
        chiamata()
    assert exc.value.status_code == 400
    return exc.value.detail["type"]


def test_stringa_normale_invariata() -> None:
    assert sanitize_string("Fattura 2026 valida") == "Fattura 2026 valida"


def test_stringhe_rifiutate() -> None:
    assert _codice(lambda: sanitize_string("SELECT * FROM users WHERE 1=1")) == "SEC-002"
    assert _codice(lambda: sanitize_string("<script>alert(1)</script>")) == "SEC-003"
    assert _codice(lambda: sanitize_string("../../etc/passwd")) == "SEC-004"
    assert _codice(lambda: sanitize_string("x" * 1001, max_length=1000)) == "SEC-001"


@pytest.mark.parametrize("url", ["http://localhost:8080/internal", "http://192.168.1.1/admin"])
def test_ssrf_verso_indirizzi_interni_rifiutato(url) -> None:
    assert _codice(lambda: validate_no_ssrf(url)) == "SEC-005"


def test_url_esterno_ammesso() -> None:
    assert validate_no_ssrf("https://api.example.com/webhook") == "https://api.example.com/webhook"


def test_estensioni() -> None:
    assert validate_file_extension("documento.pdf") == "documento.pdf"
    assert _codice(lambda: validate_file_extension("malware.exe")) == "SEC-006"
    assert validate_file_extension("report.csv", allowed={".csv", ".xlsx"}) == "report.csv"
