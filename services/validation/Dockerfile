# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Multi-stage build — validation service
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

COPY services/validation/pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e "."

# R08 (D02): Siegfried 1.11.1 (Apache-2.0) e firme PRONOM DROID V118 (The National
# Archives, Open Government Licence), scaricati con SHA-256 verificato.
FROM python:3.12-slim AS siegfried
ARG SF_VERSIONE=1.11.1
ARG SF_ZIP_SHA256=4aa45926a41ed4374d83f15b34aa2dd4fdafbe5d93058bafd155343ea8c45734
ARG SF_FIRME_SHA256=5c7db6b16edd76aa6ca509ddb082abe29c8c6bd3f9e72307219edae77b86f133
RUN python - <<'PY'
import hashlib, os, urllib.request, zipfile
v = os.environ["SF_VERSIONE"]
zip_url = f"https://github.com/richardlehane/siegfried/releases/download/v{v}/siegfried_{v.replace('.', '-')}_linux64.zip"
sig_url = f"https://raw.githubusercontent.com/richardlehane/siegfried/v{v}/cmd/roy/data/default.sig"
os.makedirs("/opt/siegfried", exist_ok=True)
for url, dest, atteso in ((zip_url, "/tmp/sf.zip", os.environ["SF_ZIP_SHA256"]),
                          (sig_url, "/opt/siegfried/default.sig", os.environ["SF_FIRME_SHA256"])):
    dati = urllib.request.urlopen(url, timeout=120).read()
    assert hashlib.sha256(dati).hexdigest() == atteso, f"SHA-256 diverso per {url}"
    open(dest, "wb").write(dati)
with zipfile.ZipFile("/tmp/sf.zip") as z:
    open("/opt/siegfried/sf", "wb").write(z.read("sf"))
os.chmod("/opt/siegfried/sf", 0o755)
PY

FROM python:3.12-slim AS runtime

RUN groupadd -r verumdoc && useradd -r -g verumdoc -u 1000 verumdoc

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

COPY services/validation/app/ ./app/
COPY libs/ ./libs/
COPY --from=siegfried /opt/siegfried /opt/siegfried

ENV PYTHONPATH=/app \
    SIEGFRIED_BIN=/opt/siegfried/sf \
    SIEGFRIED_HOME=/opt/siegfried

RUN chown -R verumdoc:verumdoc /app
USER verumdoc

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=3)"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
