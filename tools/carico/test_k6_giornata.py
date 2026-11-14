# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (roadmap R12): lo script k6 della giornata da 1 milione di documenti
(``tools/carico/k6/giornata_milione.js``) si esegue davvero, in forma ridotta e
compressa, contro un'API FINTA locale che controlla le richieste (multipart con più
parti «files», indice, Idempotency-Key, stato dei campioni). Serve il binario k6
(``VD_K6`` o ``k6`` nel PATH, release GitHub con SHA-256 verificato); altrimenti il
test si salta con il motivo. La prova vera è quella del giorno 1 di staging.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "k6" / "giornata_milione.js"
K6 = os.environ.get("VD_K6") or shutil.which("k6")


class _ApiFinta(BaseHTTPRequestHandler):
    ricevuti: list[dict] = []
    chiavi: set[str] = set()

    def log_message(self, *a) -> None:  # noqa: ANN002
        pass

    def _json(self, codice: int, corpo: dict) -> None:
        dati = json.dumps(corpo).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/v1/health/ready":
            self._json(200, {"stato": "pronto"})
        elif self.path.startswith("/api/v1/versamento/"):
            self._json(200, {"stato": "CONSERVATO"})
        else:
            self._json(404, {})

    def do_POST(self) -> None:  # noqa: N802
        corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        tipo = self.headers.get("Content-Type", "")
        chiave = self.headers.get("Idempotency-Key", "")
        valido = (self.path == "/api/v1/versamento/ud" and tipo.startswith("multipart/form-data")
                  and b'name="indice"' in corpo and chiave and chiave not in self.chiavi)
        self.chiavi.add(chiave)
        self.ricevuti.append({"parti_files": corpo.count(b'name="files"'), "valido": bool(valido),
                              "pdf": corpo.count(b"%PDF-1.7")})
        if valido:
            self._json(202, {"pdv_id": str(uuid.uuid4())})
        else:
            self._json(422, {"errore": "richiesta non valida"})


@pytest.mark.skipif(not K6, reason="k6 non disponibile (VD_K6 o k6 nel PATH)")
def test_giornata_ridotta_contro_api_finta(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiFinta)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ambiente = {**os.environ, "K6_NO_USAGE_REPORT": "true"}
        argomenti = {"VD_URL": f"http://127.0.0.1:{server.server_port}", "VD_TOKEN": "prova",
                     "DOCUMENTI_GIORNO": "20000", "DOCUMENTI_PER_VERSAMENTO": "3",
                     "COMPRESSIONE": "720", "ORE": "2", "ORA_INIZIO": "9",
                     "DIMENSIONE_KB": "4", "CAMPIONE_PERCENTO": "20", "POLL_S": "0.2",
                     "ATTESA_MAX_S": "5"}
        esito = subprocess.run(  # noqa: S603 — binario k6 verificato, argomenti nostri
            [K6, "run", "--quiet", "--summary-export", str(tmp_path / "riassunto.json"),
             *[x for k, v in argomenti.items() for x in ("-e", f"{k}={v}")], str(SCRIPT)],
            capture_output=True, text=True, timeout=600, env=ambiente)
    finally:
        server.shutdown()
    assert esito.returncode == 0, esito.stdout[-3000:] + esito.stderr[-3000:]
    ricevuti = _ApiFinta.ricevuti
    assert ricevuti and all(r["valido"] for r in ricevuti)
    assert all(r["parti_files"] == 3 and r["pdf"] == 3 for r in ricevuti)
    riassunto = json.loads((tmp_path / "riassunto.json").read_text())
    versati = riassunto["metrics"]["documenti_versati"]["count"]
    assert versati == 3 * len(ricevuti)
    assert riassunto["metrics"]["conservazione"]["max"] > 0          # campioni seguiti
    assert riassunto["metrics"]["campioni_non_conservati"]["value"] == 0
