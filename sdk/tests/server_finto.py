# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Server finto dell'API VerumDoc per i test dell'SDK (httpx.MockTransport).

Riproduce il contratto delle rotte usate dall'SDK con le stesse forme di
risposta dei servizi reali (gateway, distribution, notification, dashboard-api):
sessioni di caricamento con parti, impronte e verifica; versamento multipart e
per riferimento con Idempotency-Key; stato e rapporto; eventi; webhook;
ricerca; PdD; elenco versamenti; errori problem+json del catalogo.

La conformità dei modelli alle app FastAPI VERE è provata a parte, in
``services/gateway/tests/test_sdk_contratto.py``.
"""
from __future__ import annotations

import asyncio
import email.parser
import email.policy
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

PROBLEMA_BASE = "https://docs.verumdoc.it/errori/"


def problema(status: int, codice: str, titolo: str, *, esito: str = "NON_VALIDO",
             ritentabile: bool = False, dettagli: dict | None = None,
             campi: list | None = None, intestazioni: dict | None = None) -> httpx.Response:
    corpo = {"type": PROBLEMA_BASE + codice, "title": titolo, "status": status,
             "detail": None, "instance": None, "codice": codice, "esito": esito,
             "ritentabile": ritentabile, "dettagli": dettagli or {}, "campi": campi or [],
             "id_richiesta": uuid.uuid4().hex, "messaggio": titolo}
    return httpx.Response(status, json=corpo, headers={
        "Content-Type": "application/problem+json", **(intestazioni or {})})


def leggi_multipart(richiesta: httpx.Request) -> tuple[dict[str, str], dict[str, bytes]]:
    """Campi e file di un corpo multipart/form-data (parser della libreria standard)."""
    testa = f"Content-Type: {richiesta.headers['content-type']}\r\n\r\n".encode()
    messaggio = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        testa + richiesta.content)
    campi: dict[str, str] = {}
    file: dict[str, bytes] = {}
    for parte in messaggio.iter_parts():
        nome = parte.get_param("name", header="content-disposition")
        dati = parte.get_payload(decode=True) or b""
        nome_file = parte.get_param("filename", header="content-disposition")
        if nome_file:
            file[nome_file] = dati
        else:
            campi[nome] = dati.decode()
    return campi, file


def versamento_in_elenco(pdv_id: str, stato: str = "CONSERVATO") -> dict[str, Any]:
    """Una voce di ``GET /api/dashboard/versamenti`` con la forma reale."""
    errore = stato == "ERRORE"
    return {"pdv_id": pdv_id, "stato": stato, "data_ricezione": "2026-10-21T10:00:00Z",
            "data_aggiornamento": None, "oggetto": None, "numero_protocollo": None,
            "classe_documentale": {"codice": "fattura", "nome": "Fattura"},
            "aip_id": None, "motivo_rifiuto": "Formato non ammesso" if errore else None,
            "motivi_rifiuto": [{"codice": "VLD-401", "messaggio": "Formato non ammesso",
                                "passo": "formato", "tipo": None}] if errore else []}


class ServerFinto:
    """Stato in memoria e gestore delle richieste."""

    def __init__(self, *, dimensione_parte: int = 8, verifica_immediata: bool = True) -> None:
        self.dimensione_parte = dimensione_parte
        self.verifica_immediata = verifica_immediata
        self.richieste: list[httpx.Request] = []
        self.caricamenti: dict[str, dict[str, Any]] = {}
        self.chiavi_caricamento: dict[str, str] = {}
        self.versamenti: dict[str, dict[str, Any]] = {}  # chiave idempotenza → esito
        self.stati_pdv: dict[str, list[str]] = {}
        self.eventi: list[dict[str, Any]] = []
        self.rdv = b"<RdV>rapporto</RdV>"
        self.impronta_rdv: str | None = None
        self.guasti: list[Callable[[httpx.Request], httpx.Response | None]] = []
        self.in_corso = 0
        self.massimo_in_corso = 0
        self.ritardo_parti = 0.0
        self._lucchetto = threading.Lock()
        self.sha_servizio_alterato = False
        # Elenco dei versamenti (dashboard-api, R03: cursore opaco, niente totale)
        self.elenco_versamenti: list[dict[str, Any]] = [versamento_in_elenco("p1", "ERRORE")]
        self.tenant_identita = "t1"
        self.identita: dict[str, Any] = {
            "tenant_id": "t1", "user_id": "u1", "ruolo": "API_SYSTEM",
            "scopes": ["versamento:ud", "read"],
            "piano": "business", "auth_method": "api_key", "canale": "REST",
            "ente": {"id": "t1", "ragione_sociale": "Comune di Prova", "stato": "ATTIVO"},
            "chiave_api": {"id": "k1", "responsabile": {"user_id": "u1", "nome": "Ada",
                                                        "cognome": "Rossi"}}}

    # ── Ingresso ─────────────────────────────────────────────────────
    def __call__(self, richiesta: httpx.Request) -> httpx.Response:
        richiesta.read()
        with self._lucchetto:
            self.richieste.append(richiesta)
            for guasto in list(self.guasti):
                risposta = guasto(richiesta)
                if risposta is not None:
                    return risposta
        if richiesta.method == "PUT" and self.ritardo_parti:
            with self._lucchetto:
                self.in_corso += 1
                self.massimo_in_corso = max(self.massimo_in_corso, self.in_corso)
            time.sleep(self.ritardo_parti)
            with self._lucchetto:
                self.in_corso -= 1
        with self._lucchetto:
            return self._instrada(richiesta)

    async def asincrono(self, richiesta: httpx.Request) -> httpx.Response:
        await richiesta.aread()
        with self._lucchetto:
            self.richieste.append(richiesta)
            for guasto in list(self.guasti):
                risposta = guasto(richiesta)
                if risposta is not None:
                    return risposta
        if richiesta.method == "PUT" and self.ritardo_parti:
            self.in_corso += 1
            self.massimo_in_corso = max(self.massimo_in_corso, self.in_corso)
            await asyncio.sleep(self.ritardo_parti)
            self.in_corso -= 1
        with self._lucchetto:
            return self._instrada(richiesta)

    def richieste_di(self, metodo: str, prefisso: str) -> list[httpx.Request]:
        return [r for r in self.richieste
                if r.method == metodo and r.url.path.startswith(prefisso)]

    # ── Instradamento ────────────────────────────────────────────────
    def _instrada(self, r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if not (r.headers.get("x-api-key") or r.headers.get("authorization")
                or r.headers.get("x-api-key-secret")):
            return problema(401, "AUTH-000", "Autenticazione richiesta")
        parti = p.strip("/").split("/")
        if p == "/api/v1/caricamenti" and r.method == "POST":
            return self._apri(r)
        if p.startswith("/api/v1/caricamenti/"):
            cid = parti[3]
            if cid not in self.caricamenti:
                return problema(404, "UPL-404", "Caricamento non trovato")
            if r.method == "PUT":
                return self._parte(r, cid, int(parti[5]))
            if r.method == "GET":
                return httpx.Response(200, json=self._vista(cid))
            if r.method == "DELETE":
                self.caricamenti[cid]["stato"] = "ANNULLATO"
                return httpx.Response(204)
            if parti[-1] == "completa":
                return self._completa(cid)
        if p == "/api/v1/versamento/ud":
            return self._versa(r)
        if p.startswith("/api/v1/versamento/"):
            pdv = parti[3]
            if p.endswith("/rdv"):
                impronta = self.impronta_rdv or hashlib.sha256(self.rdv).hexdigest()
                return httpx.Response(200, content=self.rdv, headers={
                    "Content-Type": "application/xml", "X-Impronta-SHA256": impronta})
            sequenza = self.stati_pdv.get(pdv, ["RICEVUTO"])
            stato = sequenza.pop(0) if len(sequenza) > 1 else sequenza[0]
            return httpx.Response(200, json={
                "pdv_id": pdv, "stato": stato, "classe_documentale": "fattura",
                "data_ricezione": "2026-10-21T10:00:00Z", "data_aggiornamento": None,
                "errori": [], "rdv_preliminare": None, "rdv_definitivo": None,
                "aip_id": None, "urn": None})
        if p == "/api/v1/eventi":
            dopo = int(r.url.params.get("dopo", 0))
            limite = int(r.url.params.get("limite", 2))
            tipi = r.url.params.get_list("tipo")
            # R03: il cursore è la posizione (assegnata alla pubblicazione), non l'id
            scelti = [e for e in self.eventi
                      if e.get("posizione", e["id"]) > dopo and (not tipi or e["tipo"] in tipi)]
            pagina = scelti[:limite]
            return httpx.Response(200, json={
                "eventi": pagina,
                "cursore": pagina[-1].get("posizione", pagina[-1]["id"]) if pagina else dopo,
                "altri": len(scelti) > limite})
        if p.startswith("/api/v1/webhook"):
            return self._webhook(r, parti)
        if p == "/api/v2/distribution/search":
            corpo = json.loads(r.content)
            campi = ["oggetto", "produttore", "nomi_file"]
            if corpo.get("cursore") == "c2":
                return httpx.Response(200, json={"total": 3, "total_esatto": True, "items": [
                    {"aip_id": "a3", "stato": "CONSERVATO"}], "cursore": None, "altri": False,
                    "campi_testo": campi})
            return httpx.Response(200, json={"total": 3, "total_esatto": True, "items": [
                {"aip_id": "a1", "stato": "CONSERVATO", "urn": "urn:x:1",
                 "nomi_file": ["fattura.pdf"]}, {"aip_id": "a2", "stato": "CONSERVATO"}],
                "cursore": "c2", "altri": True, "campi_testo": campi})
        if p == "/api/v2/distribution/richiedi":
            corpo = json.loads(r.content)
            return httpx.Response(200, json=self._pdd(corpo["aip_id"], corpo["tipo"],
                                                      "IN_PREPARAZIONE"))
        if p.startswith("/api/v2/distribution/pdd/"):
            return httpx.Response(200, json=self._pdd("a1", "COMPLETO", "PRONTO"))
        if p == "/api/dashboard/versamenti":
            return self._elenco_versamenti(r)
        if p == "/api/v1/me":
            return httpx.Response(200, json=self.identita)
        return problema(404, "HTTP-404", "Percorso inesistente")

    # ── Elenco dei versamenti (dashboard-api) ───────────────────────
    def _elenco_versamenti(self, r: httpx.Request) -> httpx.Response:
        """Come dashboard-api R03: ente dall'identità se manca ``tenant_id``,
        ``skip`` → 422 DSH-422, cursore alterato → 422 DSH-104, ``limit+1``."""
        q = r.url.params
        if q.get("skip") not in (None, "0"):
            return problema(422, "DSH-422", "Parametro skip non più supportato: usare cursore")
        if q.get("tenant_id", self.tenant_identita) != self.tenant_identita:
            return problema(403, "AUTH-020", "Ente non consentito", esito="RIFIUTATO")
        limite = int(q.get("limit", 20))
        if not 1 <= limite <= 100:
            return problema(422, "VAL-422", "limit fuori intervallo")
        voci = [v for v in self.elenco_versamenti
                if not q.get("stato") or v["stato"] == q["stato"]]
        inizio = 0
        if q.get("cursore"):
            if not q["cursore"].startswith("cur-"):
                return problema(422, "DSH-104", "Cursore non valido")
            inizio = int(q["cursore"][4:])
        pagina = voci[inizio:inizio + limite + 1]
        altri = len(pagina) > limite
        return httpx.Response(200, json={
            "items": pagina[:limite], "limit": limite,
            "cursore": f"cur-{inizio + limite}" if altri else None, "altri": altri})

    # ── Caricamenti ──────────────────────────────────────────────────
    def _apri(self, r: httpx.Request) -> httpx.Response:
        corpo = json.loads(r.content)
        chiave = r.headers.get("idempotency-key")
        if chiave and chiave in self.chiavi_caricamento:
            cid = self.chiavi_caricamento[chiave]
            return httpx.Response(200, json=self._creata(cid),
                                  headers={"Idempotent-Replayed": "true"})
        cid = str(uuid.uuid4())
        dim = corpo["dimensione"]
        self.caricamenti[cid] = {
            "nome_file": corpo["nome_file"], "dimensione": dim, "stato": "APERTO",
            "parti": {}, "numero_parti": max(1, -(-dim // self.dimensione_parte)),
            "impronta": (corpo.get("impronta") or {}).get("valore")}
        if chiave:
            self.chiavi_caricamento[chiave] = cid
        return httpx.Response(201, json=self._creata(cid))

    def _link(self, cid: str) -> dict[str, str]:
        base = f"/api/v1/caricamenti/{cid}"
        return {"self": base, "parti": base + "/parti/{n}", "completa": base + "/completa"}

    def _creata(self, cid: str) -> dict[str, Any]:
        c = self.caricamenti[cid]
        return {"caricamento_id": cid, "stato": c["stato"],
                "dimensione_parte": self.dimensione_parte, "numero_parti": c["numero_parti"],
                "scade_il": "2026-10-22T10:00:00Z", "link": self._link(cid)}

    def _vista(self, cid: str) -> dict[str, Any]:
        c = self.caricamenti[cid]
        impronte = None
        if c["stato"] == "COMPLETO":
            dati = b"".join(c["parti"][n] for n in sorted(c["parti"]))
            sha = hashlib.sha256(dati).hexdigest()
            if self.sha_servizio_alterato:
                sha = "0" * 64
            impronte = {"SHA-256": sha, "SHA-512": hashlib.sha512(dati).hexdigest(),
                        "SHA3-256": hashlib.sha3_256(dati).hexdigest()}
        return {"caricamento_id": cid, "stato": c["stato"], "nome_file": c["nome_file"],
                "dimensione": c["dimensione"], "dimensione_parte": self.dimensione_parte,
                "numero_parti": c["numero_parti"], "parti_ricevute": sorted(c["parti"]),
                "scade_il": "2026-10-22T10:00:00Z", "impronte": impronte,
                "formato_rilevato": "application/pdf" if impronte else None,
                "errore": c.get("errore"), "pdv_id": c.get("pdv_id"), "link": self._link(cid)}

    def _parte(self, r: httpx.Request, cid: str, n: int) -> httpx.Response:
        c = self.caricamenti[cid]
        dati = r.content
        dichiarata = r.headers.get("x-impronta-parte")
        if dichiarata != "sha256=" + hashlib.sha256(dati).hexdigest():
            return problema(400, "UPL-400", "Impronta della parte errata")
        attesa = min(self.dimensione_parte, c["dimensione"] - (n - 1) * self.dimensione_parte)
        if n < 1 or n > c["numero_parti"] or len(dati) != attesa:
            return problema(400, "UPL-002", "Parte di dimensione errata")
        if n in c["parti"] and c["parti"][n] != dati:
            return problema(409, "UPL-004", "Parte già ricevuta con altro contenuto")
        c["parti"][n] = dati
        return httpx.Response(200, json={"parte": n, "dimensione": len(dati),
                                         "ricevute": sorted(c["parti"])})

    def _completa(self, cid: str) -> httpx.Response:
        c = self.caricamenti[cid]
        mancanti = [n for n in range(1, c["numero_parti"] + 1) if n not in c["parti"]]
        if mancanti:
            return problema(409, "UPL-005", "Parti mancanti",
                            dettagli={"parti_mancanti": mancanti})
        if c["stato"] == "COMPLETO":
            return httpx.Response(200, json=self._vista(cid))
        dati = b"".join(c["parti"][n] for n in sorted(c["parti"]))
        if c["impronta"] and c["impronta"] != hashlib.sha256(dati).hexdigest():
            c["stato"] = "ERRORE"
            c["errore"] = {"codice": "UPL-006", "title": "Impronta diversa", "status": 422,
                           "esito": "NON_VALIDO", "ritentabile": False,
                           "type": PROBLEMA_BASE + "UPL-006"}
        else:
            c["stato"] = "COMPLETO" if self.verifica_immediata else "IN_VERIFICA"
        return httpx.Response(202, json=self._vista(cid))

    def verifica_in_sospeso(self) -> None:
        for c in self.caricamenti.values():
            if c["stato"] == "IN_VERIFICA":
                c["stato"] = "COMPLETO"

    # ── Versamento ───────────────────────────────────────────────────
    def _versa(self, r: httpx.Request) -> httpx.Response:
        chiave = r.headers.get("idempotency-key") or ""
        if r.headers["content-type"].startswith("application/json"):
            corpo = json.loads(r.content)
            indice, file = corpo["indice"], corpo["file"]
            contenuti = {}
            for nome, cid in file.items():
                c = self.caricamenti.get(cid)
                if c is None or c["stato"] != "COMPLETO":
                    return problema(409, "UPL-009", "Sessione non completa", esito="ERRORE",
                                    ritentabile=True, intestazioni={"Retry-After": "1"})
                contenuti[nome] = b"".join(c["parti"][n] for n in sorted(c["parti"]))
        else:
            campi, contenuti = leggi_multipart(r)
            indice = json.loads(campi["indice"])
        for doc in indice["documenti"]:
            atteso = doc["hash"]["impronta"]
            if hashlib.sha256(contenuti[doc["nome_file"]]).hexdigest() != atteso:
                return problema(400, "VRS-102", "Impronta del file errata")
        impronta_indice = hashlib.sha256(json.dumps(indice, sort_keys=True).encode()).hexdigest()
        if chiave in self.versamenti:
            precedente = self.versamenti[chiave]
            if precedente["impronta"] != impronta_indice:
                return problema(409, "VRS-121", "Stessa chiave, indice diverso",
                                esito="RIFIUTATO")
            return httpx.Response(200, json=precedente["corpo"], headers={
                "Idempotent-Replayed": "true",
                "Location": f"/api/v1/versamento/{precedente['corpo']['pdv_id']}"})
        pdv = str(uuid.uuid4())
        corpo_risposta = {"pdv_id": pdv, "stato": "RICEVUTO",
                          "message": "Versamento ricevuto. La validazione è in corso.",
                          "avvisi": [], "link_stato": f"/api/v1/versamento/{pdv}",
                          "rdv_preliminare_disponibile": True, "nota_rdv": "nota"}
        self.versamenti[chiave] = {"impronta": impronta_indice, "corpo": corpo_risposta,
                                   "indice": indice, "contenuti": contenuti}
        return httpx.Response(202, json=corpo_risposta,
                              headers={"Location": f"/api/v1/versamento/{pdv}"})

    # ── Webhook ──────────────────────────────────────────────────────
    def _webhook(self, r: httpx.Request, parti: list[str]) -> httpx.Response:
        sott = {"id": "w1", "url": "https://cliente.example.org/eventi",
                "tipi": ["versamento.conservato"], "descrizione": None, "attiva": True,
                "segreto_suffisso": "abcd", "creata_il": "2026-10-21T10:00:00Z",
                "aggiornata_il": "2026-10-21T10:00:00Z"}
        if len(parti) == 3:
            if r.method == "POST":
                corpo = json.loads(r.content)
                return httpx.Response(201, json={**sott, **corpo, "segreto": "whsec_xyzabcd"})
            return httpx.Response(200, json={"sottoscrizioni": [sott]})
        if len(parti) == 4:
            if r.method == "DELETE":
                return httpx.Response(204)
            if r.method == "PATCH":
                return httpx.Response(200, json={**sott, **json.loads(r.content)})
            return httpx.Response(200, json=sott)
        if parti[-1] == "riprova":
            return httpx.Response(202, json={"id": int(parti[5]), "evento_id": 7,
                                             "stato": "IN_CODA", "tentativi": 0, "riprove": 1,
                                             "prossimo_tentativo": "2026-10-21T10:00:00Z"})
        return httpx.Response(200, json={"consegne": [{
            "id": 3, "evento_id": 7, "tipo_evento": "versamento.conservato", "stato": "FALLITA",
            "tentativi": 5, "riprove": 0, "ultimo_codice": "WH-103"}], "cursore": 3,
            "altri": False})

    def _pdd(self, aip: str, tipo: str, stato: str) -> dict[str, Any]:
        return {"pdd_id": "d1", "aip_id": aip, "tipo": tipo, "stato": stato, "consegna": "ZIP",
                "documenti": [{"nome_file": "fattura.pdf", "tipo": "DOCUMENTO",
                               "formato_mime": "application/pdf", "hash_sha256": "ab",
                               "dimensione_bytes": 3, "s3_key": None, "signed_url": None,
                               "impronta_verificata": False}],
                "sincro_xml_incluso": True, "hash_pdd": None, "created_at": "2026-10-21",
                "url_scadenza": None, "link_stato": "/api/v2/distribution/pdd/d1",
                "zip": {"url": "https://s3/zip", "dimensione_bytes": 10, "hash_sha256": "cd"}
                if stato == "PRONTO" else None, "errore": None, "messaggio": None}
