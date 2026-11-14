# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
OpenSearch finto per i test (R03 b4): un vero server HTTP in un thread che
realizza le chiamate REST usate da ``app.services.search_index`` con la stessa
semantica essenziale:

  HEAD/PUT /{indice}           esistenza e creazione dell'indice
  POST /_bulk                  azioni «index» NDJSON (id, routing), errori per voce
  POST /{indice}/_search       bool.filter (term, range su data_versamento),
                               bool.must (multi_match: tutte le parole nei campi,
                               senza distinzione di maiuscole), sort
                               data_versamento desc + aip_id asc, search_after,
                               from/size, routing obbligatorio uguale all'ente.

Registra corpi e parametri ricevuti; ``guasto=True`` risponde 503;
``rifiuta_ids`` fa fallire nella bulk le voci con quegli id.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OpenSearchFinto:
    def __init__(self) -> None:
        self.indici: dict[str, dict] = {}
        self.documenti: dict[str, dict[str, Any]] = {}
        self.routing: dict[str, str] = {}
        self.ricerche: list[tuple[dict, dict]] = []
        self.bulk_ricevute = 0
        self.guasto = False
        self.rifiuta_ids: set[str] = set()
        self.porta = _porta_libera()
        finto = self

        class Gestore(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # silenzio
                return

            def _rispondi(self, stato: int, corpo: Any = None) -> None:
                dati = json.dumps(corpo if corpo is not None else {}).encode()
                self.send_response(stato)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(dati)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(dati)

            def _corpo(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))

            def do_HEAD(self) -> None:  # noqa: N802
                nome = urlsplit(self.path).path.strip("/")
                self._rispondi(200 if nome in finto.indici else 404)

            def do_PUT(self) -> None:  # noqa: N802
                finto.indici[urlsplit(self.path).path.strip("/")] = json.loads(self._corpo())
                self._rispondi(200, {"acknowledged": True})

            def do_POST(self) -> None:  # noqa: N802
                if finto.guasto:
                    self._rispondi(503, {"error": "non disponibile"})
                    return
                parti = urlsplit(self.path)
                if parti.path == "/_bulk":
                    self._rispondi(200, finto._bulk(self._corpo().decode()))
                elif parti.path.endswith("/_search"):
                    parametri = {k: v[0] for k, v in parse_qs(parti.query).items()}
                    self._rispondi(200, finto._cerca(json.loads(self._corpo()), parametri))
                else:
                    self._rispondi(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.porta), Gestore)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def avvia(self) -> OpenSearchFinto:
        self._thread.start()
        return self

    def ferma(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # ── semantica ──
    def _bulk(self, ndjson: str) -> dict:
        self.bulk_ricevute += 1
        righe = [json.loads(r) for r in ndjson.splitlines() if r.strip()]
        voci, errori = [], False
        for azione, doc in zip(righe[::2], righe[1::2], strict=True):
            meta = azione["index"]
            if meta["_id"] in self.rifiuta_ids:
                errori = True
                voci.append({"index": {"_id": meta["_id"], "status": 400,
                                       "error": {"type": "mapper_parsing_exception"}}})
                continue
            self.documenti[meta["_id"]] = doc
            self.routing[meta["_id"]] = meta.get("routing")
            voci.append({"index": {"_id": meta["_id"], "status": 201}})
        return {"errors": errori, "items": voci}

    @staticmethod
    def _filtro(doc: dict, f: dict) -> bool:
        if "term" in f:
            [(campo, valore)] = f["term"].items()
            return doc.get(campo) == valore
        if "range" in f:
            [(campo, limiti)] = f["range"].items()
            v = doc.get(campo) or ""
            return (("gte" not in limiti or v >= limiti["gte"])
                    and ("lte" not in limiti or v <= limiti["lte"]))
        raise AssertionError(f"filtro non supportato: {f}")

    @staticmethod
    def _testo(doc: dict, m: dict) -> bool:
        if "match_all" in m:
            return True
        mm = m["multi_match"]
        testo = " ".join(json.dumps(doc.get(c, ""), ensure_ascii=False) for c in mm["fields"])
        return all(p.lower() in testo.lower() for p in mm["query"].split())

    def _cerca(self, corpo: dict, parametri: dict) -> dict:
        self.ricerche.append((corpo, parametri))
        b = corpo["query"]["bool"]
        trovati = [d for d in self.documenti.values()
                   if all(self._filtro(d, f) for f in b["filter"])
                   and all(self._testo(d, m) for m in b["must"])
                   and self.routing.get(d["aip_id"]) == parametri.get("routing")]
        trovati.sort(key=lambda d: d["aip_id"])
        trovati.sort(key=lambda d: d.get("data_versamento") or "", reverse=True)
        if "search_after" in corpo:
            data, aid = corpo["search_after"]
            trovati = [d for d in trovati if (d.get("data_versamento") or "") < data
                       or ((d.get("data_versamento") or "") == data and d["aip_id"] > aid)]
        else:
            trovati = trovati[corpo.get("from", 0):]
        pagina = trovati[: corpo["size"]]
        return {"hits": {"total": {"value": len(trovati), "relation": "eq"}, "hits": [
            {"_id": d["aip_id"], "_source": d, "sort": [d.get("data_versamento"), d["aip_id"]]}
            for d in pagina]}}
