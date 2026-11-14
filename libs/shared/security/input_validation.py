# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
F4.4 — Input validation e sanitizzazione.
Protezione da: SQL Injection, XSS, Path Traversal, SSRF.
"""
from __future__ import annotations

import re

from fastapi import HTTPException

# Pattern XSS — controllato PRIMA di SQL (evita falsi positivi su <script>)
_XSS_PATTERNS = re.compile(
    r"(<script|</script|javascript:|onerror=|onload=|eval\(|alert\()",
    re.IGNORECASE,
)

# Pattern SQL injection — escluso SCRIPT (gestito da XSS)
_SQL_PATTERNS = re.compile(
    r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|UNION|"
    r"DECLARE|CAST|CONVERT)\b|--|;\s*--|/\*|\*/|xp_|WAITFOR)",
    re.IGNORECASE,
)

# Path traversal
_PATH_TRAVERSAL = re.compile(r"(\.\./|\.\.\\|%2e%2e)", re.IGNORECASE)

# URL interne (SSRF)
_SSRF_PATTERNS = re.compile(
    r"(localhost|127\.0\.0\.1|0\.0\.0\.0|169\.254\.|"
    r"10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)",
)


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """Sanitizza una stringa input. Lancia HTTPException 400 se sospetta."""
    if len(value) > max_length:
        raise HTTPException(400, {"type": "SEC-001",
                                   "title": f"Input troppo lungo (max {max_length} caratteri)"})
    # XSS prima di SQL (evita falsi positivi su keyword come SCRIPT)
    if _XSS_PATTERNS.search(value):
        raise HTTPException(400, {"type": "SEC-003",
                                   "title": "Input contiene pattern XSS non ammessi"})
    if _SQL_PATTERNS.search(value):
        raise HTTPException(400, {"type": "SEC-002",
                                   "title": "Input contiene pattern SQL non ammessi"})
    if _PATH_TRAVERSAL.search(value):
        raise HTTPException(400, {"type": "SEC-004",
                                   "title": "Path traversal non ammesso"})
    return value


def validate_no_ssrf(url: str) -> str:
    """Verifica che un URL non punti a risorse interne (SSRF protection)."""
    if _SSRF_PATTERNS.search(url):
        raise HTTPException(400, {"type": "SEC-005",
                                   "title": "URL non ammesso — indirizzo interno rilevato"})
    return url


def validate_file_extension(filename: str, allowed: set[str] | frozenset[str] | None = None) -> str:
    """Verifica l'estensione del file contro le estensioni ammesse.

    R03 f1 (difetto 4 del rapporto c2): senza ``allowed`` valgono le estensioni del
    registro dei formati (``libs.verumdoc_rules.formati``), non più un elenco fisso.
    Il versamento passa le estensioni derivate dai formati della classe."""
    if allowed is None:
        from libs.verumdoc_rules.formati import REGISTRO_FORMATI

        allowed = REGISTRO_FORMATI.tutte_le_estensioni()
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in allowed:
        raise HTTPException(400, {
            "type": "SEC-006",
            "title": f"Estensione file non ammessa: {ext!r}",
            "detail": f"Estensioni ammesse: {sorted(allowed)}",
        })
    return filename
