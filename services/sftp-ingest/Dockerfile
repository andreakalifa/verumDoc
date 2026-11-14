# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY services/sftp-ingest/pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e "."

FROM python:3.12-slim AS runtime
RUN groupadd -r verumdoc && useradd -r -g verumdoc -u 1000 verumdoc
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin
COPY services/sftp-ingest/app/ ./app/
COPY libs/ ./libs/
ENV PYTHONPATH=/app
# Volume delle aree SFTP (in esercizio: volume condiviso fra server e lavoratori)
ENV SFTP_RADICE=/var/lib/verumdoc/sftp
RUN mkdir -p /var/lib/verumdoc/sftp && chown -R verumdoc:verumdoc /app /var/lib/verumdoc
VOLUME ["/var/lib/verumdoc/sftp"]
USER verumdoc
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=3)"
# 8000: salute e metriche (interno); 2222: SFTP (esposto ai clienti tramite il bilanciatore TCP)
EXPOSE 8000 2222
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
