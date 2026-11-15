# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Multi-stage build — immagine finale minimale
FROM python:3.15-slim AS builder

WORKDIR /app

# Installa dipendenze di build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia e installa dipendenze Python (layer cachato separatamente)
COPY services/signature/pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e "."

# Stage finale — non-root, read-only filesystem
FROM python:3.15-slim AS runtime

# Sicurezza: utente non-root
RUN groupadd -r verumdoc && useradd -r -g verumdoc -u 1000 verumdoc

WORKDIR /app

# Copia dipendenze installate dallo stage builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

# Copia codice applicativo e librerie condivise (app/auth/jwt_handler.py
# importa libs.shared.security: senza libs/ l'immagine non parte)
COPY services/signature/app/ ./app/
COPY libs/ ./libs/

ENV PYTHONPATH=/app

# Permessi e utente
RUN chown -R verumdoc:verumdoc /app
USER verumdoc

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=3)"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
