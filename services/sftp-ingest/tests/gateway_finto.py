# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Gateway finto per i test del canale SFTP: realizza il contratto C1 (sessioni di
caricamento) e il versamento per riferimento, come li descrive
``contratti_ondata_b.md``, con i controlli che il canale deve rispettare:

- token di servizio C2 verificato con la chiave pubblica del canale
  (sub «servizio:sftp-ingest», canale SFTP, tenant_id, user_id, ruolo);
- ``Idempotency-Key`` su apertura e versamento (stessa chiave → stessa risorsa);
- parti in qualsiasi ordine, idempotenti, ``X-Impronta-Parte`` verificata,
  lunghezza della parte verificata; corpo letto in streaming;
- completamento asincrono (202 IN_VERIFICA, poi COMPLETO alla lettura dello
  stato) con confronto dell'impronta dichiarata;
- caricamento usato da un PdV non riusabile (409);
- stessa chiave d'apertura con una richiesta diversa → 409 UPL-007 (R03 i3);
- ``GET /api/v1/versamento/{pdv_id}`` con lo stato del PdV; ``stati_pdv`` lo
  cambia (es. ERRORE dopo un rifiuto della verifica) e un versamento ripetuto
  su un PdV in ERRORE/ANNULLATO riceve 409 VRS-127, come il gateway (R03 i3).
Iniezione di guasti: ``guasti["versamento"] = [503, ...]`` risponde con quei
codici alle prossime chiamate di versamento (problem+json ritentabile).
"""
from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from libs.shared.errori import ErroreApi
from libs.shared.security.jwt_rs256 import ErroreTokenNonValido, verifica_token
from libs.shared.security.token_canale import PUBBLICO_TOKEN_CANALE


def _problema(codice: str, dettaglio: str, status: int | None = None) -> JSONResponse:
    e = ErroreApi(codice, dettaglio, status=status)
    return JSONResponse(e.corpo(), status_code=e.status, media_type="application/problem+json",
                        headers=e.intestazioni())


@dataclass
class Sessione:
    id: str
    tenant_id: str
    nome_file: str
    dimensione: int
    impronta: dict[str, str] | None
    dimensione_parte: int
    numero_parti: int
    parti: dict[int, bytes] = field(default_factory=dict)
    stato: str = "APERTO"
    impronte: dict[str, str] | None = None
    errore: dict[str, Any] | None = None
    usata_da: str | None = None


@dataclass
class StatoGateway:
    chiave_pubblica: str
    dimensione_parte: int = 1000
    sessioni: dict[str, Sessione] = field(default_factory=dict)
    chiavi_sessioni: dict[tuple[str, str], str] = field(default_factory=dict)
    versamenti: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    chiamate_versamento: list[dict[str, Any]] = field(default_factory=list)
    parti_ricevute: list[tuple[str, int]] = field(default_factory=list)
    token_visti: list[dict[str, Any]] = field(default_factory=list)
    guasti: dict[str, list[int | str]] = field(default_factory=dict)
    #: pdv_id → stato attuale (assente = RICEVUTO)
    stati_pdv: dict[str, str] = field(default_factory=dict)
    letture_stato_pdv: list[str] = field(default_factory=list)


def crea_gateway(stato: StatoGateway) -> FastAPI:
    app = FastAPI()

    def identita(authorization: str | None) -> dict[str, Any] | JSONResponse:
        if not authorization or not authorization.startswith("Bearer "):
            return _problema("AUTH-000", "token assente")
        try:
            claims = verifica_token(
                authorization[7:], chiave_pubblica_pem=stato.chiave_pubblica,
                emittente="verumdoc.it", pubblico=PUBBLICO_TOKEN_CANALE,
                claim_obbligatori=("tenant_id", "user_id", "ruolo", "canale", "tipo_token"))
        except ErroreTokenNonValido as exc:
            return _problema(exc.codice, exc.dettaglio)
        if claims.get("sub") != "servizio:sftp-ingest" or claims.get("canale") != "SFTP":
            return _problema("AUTH-001", "token di canale non valido")
        stato.token_visti.append(claims)
        return claims

    def guasto(nome: str) -> JSONResponse | None:
        coda = stato.guasti.get(nome) or []
        if coda:
            codice = coda.pop(0)
            # un numero = errore generico HTTP-<n>; un testo = codice del catalogo
            # (es. «QTA-002», con stato e Retry-After del catalogo)
            nome = codice if isinstance(codice, str) else f"HTTP-{codice}"
            return _problema(nome, "guasto simulato")
        return None

    def sessione_dell_ente(cid: str, tenant: str) -> Sessione | None:
        s = stato.sessioni.get(cid)
        return s if s is not None and s.tenant_id == tenant else None

    def vista(s: Sessione) -> dict[str, Any]:
        return {"caricamento_id": s.id, "stato": s.stato, "parti_ricevute": sorted(s.parti),
                "dimensione": s.dimensione, "impronte": s.impronte,
                "formato_rilevato": None, "errore": s.errore}

    @app.post("/api/v1/caricamenti")
    async def apri(request: Request, authorization: str | None = Header(None),
                   idempotency_key: str | None = Header(None)) -> Any:
        chi = identita(authorization)
        if isinstance(chi, JSONResponse):
            return chi
        g = guasto("apertura")
        if g is not None:
            return g
        corpo = await request.json()
        tenant = chi["tenant_id"]
        if idempotency_key and (tenant, idempotency_key) in stato.chiavi_sessioni:
            s = stato.sessioni[stato.chiavi_sessioni[(tenant, idempotency_key)]]
            if (s.nome_file, s.dimensione, s.impronta) != (
                    corpo["nome_file"], int(corpo["dimensione"]), corpo.get("impronta")):
                return _problema("UPL-007", "Idempotency-Key già usata con una richiesta diversa")
        else:
            dim = int(corpo["dimensione"])
            s = Sessione(str(uuid.uuid4()), tenant, corpo["nome_file"], dim, corpo.get("impronta"),
                         stato.dimensione_parte, math.ceil(dim / stato.dimensione_parte))
            stato.sessioni[s.id] = s
            if idempotency_key:
                stato.chiavi_sessioni[(tenant, idempotency_key)] = s.id
        return JSONResponse({"caricamento_id": s.id, "stato": s.stato,
                             "dimensione_parte": s.dimensione_parte,
                             "numero_parti": s.numero_parti,
                             "scade_il": "2099-01-01T00:00:00+00:00", "link": {}},
                            status_code=201)

    @app.put("/api/v1/caricamenti/{cid}/parti/{n}")
    async def parte(cid: str, n: int, request: Request,
                    authorization: str | None = Header(None),
                    x_impronta_parte: str | None = Header(None)) -> Any:
        chi = identita(authorization)
        if isinstance(chi, JSONResponse):
            return chi
        s = sessione_dell_ente(cid, chi["tenant_id"])
        if s is None:
            return _problema("UPL-404", "sessione inesistente")
        h = hashlib.sha256()
        dati = bytearray()
        async for blocco in request.stream():
            h.update(blocco)
            dati += blocco
        if x_impronta_parte and x_impronta_parte != f"sha256={h.hexdigest()}":
            return _problema("UPL-422", "impronta della parte errata")
        attesa = min(s.dimensione_parte, s.dimensione - (n - 1) * s.dimensione_parte)
        if not 1 <= n <= s.numero_parti or len(dati) != attesa:
            return _problema("UPL-422", f"parte {n} di {len(dati)} byte, attesi {attesa}")
        if n in s.parti and s.parti[n] != bytes(dati):
            return _problema("HTTP-409", "parte già ricevuta con contenuto diverso")
        s.parti[n] = bytes(dati)
        stato.parti_ricevute.append((cid, n))
        return {"parte": n, "dimensione": len(dati), "ricevute": sorted(s.parti)}

    @app.get("/api/v1/caricamenti/{cid}")
    async def leggi(cid: str, authorization: str | None = Header(None)) -> Any:
        chi = identita(authorization)
        if isinstance(chi, JSONResponse):
            return chi
        s = sessione_dell_ente(cid, chi["tenant_id"])
        if s is None:
            return _problema("UPL-404", "sessione inesistente")
        if s.stato == "IN_VERIFICA":  # verifica «fuori richiesta»: conclusa alla lettura
            contenuto = b"".join(s.parti[i] for i in sorted(s.parti))
            s.impronte = {"SHA-256": hashlib.sha256(contenuto).hexdigest()}
            if s.impronta and s.impronta.get("valore") != s.impronte["SHA-256"]:
                s.stato = "ERRORE"
                s.errore = ErroreApi("VRS-102", "impronta dichiarata diversa").corpo()
            else:
                s.stato = "COMPLETO"
        return vista(s)

    @app.post("/api/v1/caricamenti/{cid}/completa")
    async def completa(cid: str, authorization: str | None = Header(None)) -> Any:
        chi = identita(authorization)
        if isinstance(chi, JSONResponse):
            return chi
        s = sessione_dell_ente(cid, chi["tenant_id"])
        if s is None:
            return _problema("UPL-404", "sessione inesistente")
        if s.stato == "COMPLETO":
            return JSONResponse({"stato": "COMPLETO"}, status_code=200)
        if len(s.parti) != s.numero_parti:
            return _problema("UPL-422", "parti mancanti")
        s.stato = "IN_VERIFICA"
        return JSONResponse({"stato": "IN_VERIFICA"}, status_code=202)

    @app.post("/api/v1/versamento/ud")
    async def versa(request: Request, authorization: str | None = Header(None),
                    idempotency_key: str | None = Header(None)) -> Any:
        chi = identita(authorization)
        if isinstance(chi, JSONResponse):
            return chi
        g = guasto("versamento")
        if g is not None:
            return g
        corpo = await request.json()
        stato.chiamate_versamento.append({"chiave": idempotency_key, **corpo})
        tenant = chi["tenant_id"]
        if (tenant, idempotency_key) in stato.versamenti:
            esistente = stato.versamenti[(tenant, idempotency_key)]
            attuale = stato.stati_pdv.get(esistente["pdv_id"], esistente["stato"])
            if attuale in ("ERRORE", "ANNULLATO"):
                return _problema("VRS-127", f"PdV {esistente['pdv_id']} nello stato {attuale}")
            return JSONResponse({**esistente, "stato": attuale, "message": "già ricevuto"},
                                status_code=200)
        if not corpo["indice"].get("documenti"):
            return _problema("VRS-001", "indice non conforme allo schema")
        for nome, cid in corpo["file"].items():
            s = sessione_dell_ente(cid, tenant)
            if s is None:
                return _problema("UPL-404", f"caricamento di {nome} inesistente")
            if s.stato != "COMPLETO":
                return _problema("UPL-422", f"caricamento di {nome} non completo")
            if s.usata_da is not None:
                return _problema("HTTP-409", "caricamento già usato")
        pdv = str(uuid.uuid4())
        for cid in corpo["file"].values():
            stato.sessioni[cid].usata_da = pdv
        risposta = {"pdv_id": pdv, "stato": "RICEVUTO", "avvisi": [
            {"codice": "RGL-103", "effetto": "AVVISA", "messaggio": "metadato non previsto"}],
            "link_stato": f"/api/v1/versamento/{pdv}"}
        stato.versamenti[(tenant, idempotency_key)] = risposta
        return JSONResponse(risposta, status_code=202)

    @app.get("/api/v1/versamento/{pdv_id}")
    async def stato_pdv(pdv_id: str, authorization: str | None = Header(None)) -> Any:
        chi = identita(authorization)
        if isinstance(chi, JSONResponse):
            return chi
        stato.letture_stato_pdv.append(pdv_id)
        for (tenant, _), risposta in stato.versamenti.items():
            if risposta["pdv_id"] == pdv_id and tenant == chi["tenant_id"]:
                return {"pdv_id": pdv_id,
                        "stato": stato.stati_pdv.get(pdv_id, risposta["stato"])}
        return _problema("VRS-404", f"PdV {pdv_id} non trovato")

    return app
