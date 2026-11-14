# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Multi-stage build — immagine finale minimale
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia dipendenze Python del servizio
COPY services/gateway/pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "."

# Stage finale
FROM python:3.12-slim AS runtime

RUN groupadd -r verumdoc && useradd -r -g verumdoc -u 1000 verumdoc

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

# Copia codice applicativo
COPY services/gateway/app/ ./app/

# Copia migration Alembic e configurazione
COPY services/gateway/alembic/ ./alembic/
COPY services/gateway/alembic.ini ./alembic.ini

# Copia entrypoint
COPY services/gateway/entrypoint.sh ./entrypoint.sh

# FIX: copia libs/shared — necessario per "from libs.shared..."
COPY libs/ ./libs/

# PYTHONPATH = /app
ENV PYTHONPATH=/app

RUN chown -R verumdoc:verumdoc /app && chmod +x /app/entrypoint.sh
USER verumdoc

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=3)"

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
