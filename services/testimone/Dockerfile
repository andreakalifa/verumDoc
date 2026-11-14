# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Testimone indipendente della catena delle impronte (R04, contratto K3).
# Immagine autosufficiente: nessuna dipendenza dal gateway né dal suo database;
# usa libs/catena e libs/shared (solo codice: errori, metriche, pool del DB) e
# le PROPRIE migrazioni Alembic. In produzione gira in un account separato (vedi
# docs/integrazione/verifica_indipendente.md e il rapporto R04 del testimone).
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY services/testimone/pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir "."

FROM python:3.12-slim AS runtime
RUN groupadd -r verumdoc && useradd -r -g verumdoc -u 1000 verumdoc
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin
COPY services/testimone/app/ ./app/
COPY services/testimone/alembic/ ./alembic/
COPY services/testimone/alembic.ini services/testimone/entrypoint.sh ./
COPY libs/ ./libs/
ENV PYTHONPATH=/app
RUN chmod 0555 /app/entrypoint.sh && chown -R verumdoc:verumdoc /app
USER verumdoc
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=3)"
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
